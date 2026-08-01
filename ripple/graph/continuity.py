"""Continuity evidence retrieval, and the findings code can determine alone.

ERD section 7: retrieval is deterministic. Given the entities a proposal
changes, application code gathers earlier and later assertions, aliases, direct
neighbours, and the units supporting them, ranks them, and hands a bounded
packet to the continuity agent. The agent judges; it does not search.

One finding needs no agent. If an edit removes the only `establishes` edge for
an entity that later scenes still reference, that is arithmetic over the graph,
not a judgement, and PRD section 4 step 9 requires the application to offer the
user a choice about it. Determining it here means the warning survives a model
being unavailable, slow, or wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ripple.db.models import (
    Assertion,
    ContinuityFinding,
    Entity,
    EntityAlias,
    Scene,
    ScriptUnit,
)

logger = logging.getLogger(__name__)

# The packet is bounded because it goes into a prompt and the budget is real.
# Ranking decides what survives the cap, so the cut is the least relevant
# evidence rather than an arbitrary suffix.
MAX_EVIDENCE_ITEMS = 40
MAX_NEIGHBOURS = 20


@dataclass(frozen=True)
class EvidenceItem:
    """One retrieved assertion with the text that supports it."""

    assertion_id: str
    subject_label: str
    predicate: str
    object_label: str
    scene_id: str
    scene_number: str | None
    scene_index: int
    unit_id: str
    unit_text: str
    confidence: float
    relation: str
    rank: float

    @property
    def is_earlier(self) -> bool:
        return self.relation.startswith("earlier")


@dataclass
class EvidencePacket:
    """What the continuity agent is given, and nothing more."""

    entity_ids: list[str] = field(default_factory=list)
    entity_labels: dict[str, str] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    earlier: list[EvidenceItem] = field(default_factory=list)
    later: list[EvidenceItem] = field(default_factory=list)
    neighbours: list[str] = field(default_factory=list)
    open_findings: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def total_items(self) -> int:
        return len(self.earlier) + len(self.later)

    def as_prompt_payload(self) -> dict[str, Any]:
        """The packet as the agent sees it."""
        return {
            "entities": [self.entity_labels.get(i, i) for i in self.entity_ids],
            "aliases": self.aliases,
            "earlier_evidence": [_item_payload(item) for item in self.earlier],
            "later_evidence": [_item_payload(item) for item in self.later],
            "graph_neighbours": self.neighbours,
            "existing_open_findings": self.open_findings,
            "truncated": self.truncated,
        }


def _item_payload(item: EvidenceItem) -> dict[str, Any]:
    return {
        "scene": item.scene_number or str(item.scene_index + 1),
        "unit_id": item.unit_id,
        "text": item.unit_text,
        "edge": f"{item.subject_label} {item.predicate} {item.object_label}",
        "confidence": round(item.confidence, 2),
        "why_retrieved": item.relation,
    }


@dataclass(frozen=True)
class OrphanedReference:
    """An entity whose establishing reference a proposal would remove.

    The finding the ripple preview shows at high severity: later scenes still
    use something the script would no longer introduce.
    """

    entity_id: str
    entity_label: str
    establishing_scene: str | None
    later_scene_numbers: list[str]
    later_unit_ids: list[str]

    @property
    def message(self) -> str:
        scenes = ", ".join(self.later_scene_numbers)
        return (
            f"{len(self.later_unit_ids)} later unit(s) still reference "
            f"{self.entity_label}. Scenes {scenes} use it, and removing the "
            "establishing reference leaves those uses without an introduction."
        )


# Predicates that count as a later use of an entity. `establishes` is excluded
# deliberately: it is the introduction, not a use of one.
USE_PREDICATES = ("appears_in", "requires", "wears", "carries", "uses", "travels_by")


def _scene_order(session: Session, script_id) -> dict[Any, tuple[int, str | None]]:
    """Scene id to (sequence_index, display number), for ordering evidence."""
    rows = session.execute(
        select(Scene.id, Scene.sequence_index, Scene.display_scene_number).where(
            Scene.script_id == script_id
        )
    ).all()
    return {row[0]: (row[1], row[2]) for row in rows}


def _assertion_scene(assertion: Assertion, unit_scene: dict[Any, Any]) -> Any:
    """Which scene an assertion belongs to.

    An assertion's own scene endpoint when it has one, otherwise the scene of
    the unit that supports it. Every assertion cites a unit, so this is always
    answerable.
    """
    if assertion.subject_scene_id is not None:
        return assertion.subject_scene_id
    if assertion.object_scene_id is not None:
        return assertion.object_scene_id
    return unit_scene.get(assertion.source_unit_id)


def retrieve(
    session: Session,
    script_id,
    entity_ids: list[Any],
    origin_scene_index: int,
    predicates: tuple[str, ...] | None = None,
) -> EvidencePacket:
    """Gather bounded continuity evidence for a set of affected entities.

    Ranking follows ERD section 7: exact entity match first, then a relevant
    predicate, then a direct graph neighbour, then script proximity. The cap
    removes the tail, so what survives is what a coordinator would look at
    first.
    """
    packet = EvidencePacket(entity_ids=[str(i) for i in entity_ids])
    if not entity_ids:
        return packet

    entities = list(session.scalars(select(Entity).where(Entity.id.in_(entity_ids))))
    packet.entity_labels = {str(e.id): e.canonical_name for e in entities}
    for entity in entities:
        aliases = list(
            session.scalars(
                select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id)
            )
        )
        if aliases:
            packet.aliases[entity.canonical_name] = sorted(set(aliases))

    scene_order = _scene_order(session, script_id)
    unit_scene = dict(
        session.execute(
            select(ScriptUnit.id, ScriptUnit.scene_id)
            .join(Scene)
            .where(Scene.script_id == script_id)
        ).all()
    )
    unit_text = dict(
        session.execute(
            select(ScriptUnit.id, ScriptUnit.current_text)
            .join(Scene)
            .where(Scene.script_id == script_id)
        ).all()
    )

    related = list(
        session.scalars(
            select(Assertion).where(
                Assertion.script_id == script_id,
                Assertion.active.is_(True),
                (
                    Assertion.subject_entity_id.in_(entity_ids)
                    | Assertion.object_entity_id.in_(entity_ids)
                ),
            )
        )
    )

    labels = _label_map(session, script_id)
    scored: list[EvidenceItem] = []
    for assertion in related:
        scene_id = _assertion_scene(assertion, unit_scene)
        if scene_id not in scene_order:
            continue
        index, number = scene_order[scene_id]
        distance = abs(index - origin_scene_index)
        relevant = predicates is None or assertion.predicate in predicates

        rank = 100.0
        if relevant:
            rank += 20.0
        rank -= min(distance, 50) * 0.5
        relation = (
            "earlier_same_entity" if index < origin_scene_index else "later_same_entity"
        )
        if index == origin_scene_index:
            relation = "same_scene"

        scored.append(
            EvidenceItem(
                assertion_id=str(assertion.id),
                subject_label=labels.get(_endpoint_key(assertion, "subject"), "?"),
                predicate=assertion.predicate,
                object_label=labels.get(_endpoint_key(assertion, "object"), "?"),
                scene_id=str(scene_id),
                scene_number=number,
                scene_index=index,
                unit_id=str(assertion.source_unit_id),
                unit_text=unit_text.get(assertion.source_unit_id, ""),
                confidence=assertion.confidence,
                relation=relation,
                rank=rank,
            )
        )

    scored.sort(key=lambda item: (-item.rank, item.scene_index))
    if len(scored) > MAX_EVIDENCE_ITEMS:
        packet.truncated = True
        scored = scored[:MAX_EVIDENCE_ITEMS]

    packet.earlier = [i for i in scored if i.scene_index < origin_scene_index]
    packet.later = [i for i in scored if i.scene_index >= origin_scene_index]

    packet.neighbours = _neighbours(session, script_id, entity_ids, labels)
    packet.open_findings = list(
        session.scalars(
            select(ContinuityFinding.message)
            .where(ContinuityFinding.status == "open")
            .limit(10)
        )
    )
    return packet


def _endpoint_key(assertion: Assertion, side: str) -> Any:
    if side == "subject":
        return assertion.subject_entity_id or assertion.subject_scene_id
    return assertion.object_entity_id or assertion.object_scene_id


def _label_map(session: Session, script_id) -> dict[Any, str]:
    """Display labels for every node in one script."""
    labels: dict[Any, str] = {}
    for entity_id, name in session.execute(
        select(Entity.id, Entity.canonical_name).where(Entity.script_id == script_id)
    ):
        labels[entity_id] = name
    for scene_id, number, index in session.execute(
        select(Scene.id, Scene.display_scene_number, Scene.sequence_index).where(
            Scene.script_id == script_id
        )
    ):
        labels[scene_id] = f"Sc {number or index + 1}"
    return labels


def _neighbours(
    session: Session, script_id, entity_ids: list[Any], labels: dict[Any, str]
) -> list[str]:
    """Entities one edge away from the affected set."""
    rows = session.scalars(
        select(Assertion).where(
            Assertion.script_id == script_id,
            Assertion.active.is_(True),
            (
                Assertion.subject_entity_id.in_(entity_ids)
                | Assertion.object_entity_id.in_(entity_ids)
            ),
        )
    )
    affected = set(entity_ids)
    found: list[str] = []
    for assertion in rows:
        for endpoint in (assertion.subject_entity_id, assertion.object_entity_id):
            if endpoint is None or endpoint in affected:
                continue
            label = labels.get(endpoint)
            if label and label not in found:
                found.append(label)
    return found[:MAX_NEIGHBOURS]


def detect_orphaned_references(
    session: Session,
    script_id,
    removed_establishes: list[tuple[Any, str, Any]],
) -> list[OrphanedReference]:
    """Find entities a proposal would stop introducing but not stop using.

    `removed_establishes` is `(entity_id, label, assertion_id)` for each
    `establishes` edge the diff removes. The assertion id is required, not
    convenience: a proposal is not applied yet, so the edge being removed is
    still in the table, and a check for "is it established elsewhere" that did
    not exclude it would always find it and never warn.

    Deterministic on purpose. This is the warning the demo turns on, and it has
    to hold when the continuity agent is unavailable, slow, or wrong.
    """
    findings: list[OrphanedReference] = []
    scene_order = _scene_order(session, script_id)
    unit_scene = dict(
        session.execute(
            select(ScriptUnit.id, ScriptUnit.scene_id)
            .join(Scene)
            .where(Scene.script_id == script_id)
        ).all()
    )

    removed_ids = {
        assertion_id for _, _, assertion_id in removed_establishes if assertion_id
    }

    for entity_id, label, _ in removed_establishes:
        surviving = list(
            session.scalars(
                select(Assertion).where(
                    Assertion.script_id == script_id,
                    Assertion.active.is_(True),
                    Assertion.predicate.in_(USE_PREDICATES),
                    (
                        (Assertion.subject_entity_id == entity_id)
                        | (Assertion.object_entity_id == entity_id)
                    ),
                )
            )
        )
        # Another establishing edge, excluding the ones this proposal removes,
        # means the entity is still introduced and there is nothing to warn
        # about.
        other_establishes = session.scalar(
            select(Assertion.id).where(
                Assertion.script_id == script_id,
                Assertion.active.is_(True),
                Assertion.predicate == "establishes",
                Assertion.object_entity_id == entity_id,
                Assertion.id.notin_(removed_ids) if removed_ids else True,
            )
        )
        if other_establishes is not None:
            continue

        numbers: list[str] = []
        unit_ids: list[str] = []
        for assertion in surviving:
            scene_id = _assertion_scene(assertion, unit_scene)
            if scene_id not in scene_order:
                continue
            index, number = scene_order[scene_id]
            display = number or str(index + 1)
            if display not in numbers:
                numbers.append(display)
            unit_ids.append(str(assertion.source_unit_id))

        if unit_ids:
            findings.append(
                OrphanedReference(
                    entity_id=str(entity_id),
                    entity_label=label,
                    establishing_scene=None,
                    later_scene_numbers=numbers,
                    later_unit_ids=unit_ids,
                )
            )
    return findings
