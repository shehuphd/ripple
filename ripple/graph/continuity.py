"""Continuity evidence retrieval, and the findings code can determine alone.

Retrieval is deterministic. Given the entities a proposal
changes, application code gathers earlier and later assertions, aliases, direct
neighbours, and the units supporting them, ranks them, and hands a bounded
packet to the continuity agent. The agent judges; it does not search.

One finding needs no agent. If an edit removes the only `establishes` edge for
an entity that later scenes still reference, that is arithmetic over the graph,
not a judgement, and the application offers the
user a choice about it. Determining it here means the warning survives a model
being unavailable, slow, or wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ripple.adapters.base import parse_character_cue
from ripple.db.models import (
    Assertion,
    ChangeSet,
    ContinuityFinding,
    Entity,
    EntityAlias,
    Scene,
    ScriptUnit,
)
from ripple.db.naming import normalize
from ripple.db.repository import graph_labels

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
        # An unnumbered scene is shown as unnumbered: substituting its
        # position invents a number a numbered scene may already carry.
        "scene": item.scene_number or "—",
        # The assertion id is the one citable id: the validator accepts
        # citations against it, and the unit behind it is resolved by code.
        # Offering the unit id here as well invites the model to cite the
        # wrong one, which is how every finding of a run once died uncited.
        "assertion_id": item.assertion_id,
        "text": item.unit_text,
        "edge": f"{item.subject_label} {item.predicate} {item.object_label}",
        "confidence": round(item.confidence, 2),
        "why_retrieved": item.relation,
    }


@dataclass(frozen=True)
class CastRename:
    """A character cue an edit renames: the speaker on a line changes.

    Renaming a cue is structural, not a fact the model judges: MARGOT to
    MAGGIE contradicts no stored assertion, so the judgement pass holds every
    edge and reports nothing. The graph resolved speakers to entities when it
    was built and re-resolves none on an edit, so the old name persists on
    every other line and the new name never enters the graph until a rebuild.
    This deterministic finding is what says a character was renamed at all.
    """

    old_label: str
    new_label: str
    edited_unit_id: str
    edited_scene_number: str | None
    later_unit_ids: list[str]
    later_scene_numbers: list[str]

    @property
    def entity_label(self) -> str:
        return self.old_label

    @property
    def message(self) -> str:
        """What the script now says, in one line."""
        if not self.later_unit_ids:
            return (
                f"{self.old_label} is renamed {self.new_label} on this line, "
                f"the only line that named {self.old_label}. Rebuild the graph "
                f"to record {self.new_label} as the character."
            )
        lines = len(self.later_unit_ids)
        one_line = lines == 1
        # An unnumbered scene has "—" for a label, so a written script would
        # read "in scene —"; name the scenes only when they carry numbers.
        numbered = [n for n in self.later_scene_numbers if n and n != "—"]
        where = ""
        if numbered:
            one_scene = len(numbered) == 1
            where = f" in scene{'' if one_scene else 's'} {', '.join(numbered)}"
        return (
            f"{self.old_label} is renamed {self.new_label} on this line, but "
            f"{lines} other line{'' if one_line else 's'}{where} still "
            f"name{'s' if one_line else ''} {self.old_label}."
        )


def detect_cast_renames(
    session: Session,
    script_id,
    edits: list[tuple[Any, str]],
) -> list[CastRename]:
    """Find character cues an edit renames, and where the old name survives.

    `edits` is `(unit, proposed_text)`. A cue is a rename when the edited unit
    is a character cue whose current and proposed text both parse as cues and
    name different speakers. The later lines are every other active character
    cue in the script that still carries the old name, so the finding can say
    the rename is partial and point at the rest.
    """
    findings: list[CastRename] = []
    scene_order = _scene_order(session, script_id)
    for unit, proposed in edits:
        if unit.unit_type != "character":
            continue
        current_cue = parse_character_cue(unit.current_text or "")
        proposed_cue = parse_character_cue(proposed or "")
        if current_cue is None or proposed_cue is None:
            continue
        old_name, new_name = current_cue[0], proposed_cue[0]
        if normalize(old_name) == normalize(new_name):
            continue
        old_key = normalize(old_name)
        survivors = [
            other
            for other in session.scalars(
                select(ScriptUnit)
                .join(Scene, ScriptUnit.scene_id == Scene.id)
                .where(
                    Scene.script_id == script_id,
                    ScriptUnit.unit_type == "character",
                    ScriptUnit.id != unit.id,
                )
            )
            if (parse := parse_character_cue(other.current_text or ""))
            and normalize(parse[0]) == old_key
        ]
        edited_scene = session.get(Scene, unit.scene_id)
        later_scenes = _ordered_scene_numbers(
            session, [s.scene_id for s in survivors], scene_order
        )
        findings.append(
            CastRename(
                old_label=old_name,
                new_label=new_name,
                edited_unit_id=str(unit.id),
                edited_scene_number=edited_scene.label if edited_scene else None,
                later_unit_ids=[str(s.id) for s in survivors],
                later_scene_numbers=later_scenes,
            )
        )
    return findings


def _ordered_scene_numbers(session, scene_ids, scene_order) -> list[str]:
    """Distinct scene labels for the given scenes, in script order."""
    seen: dict[Any, str] = {}
    for scene_id in scene_ids:
        if scene_id in seen:
            continue
        scene = session.get(Scene, scene_id)
        if scene is not None:
            seen[scene_id] = scene.label
    ordered = sorted(
        seen.items(),
        key=lambda kv: scene_order.get(kv[0], (1_000_000, None))[0],
    )
    return _ordered_unique_labels([label for _, label in ordered])


def _ordered_unique_labels(labels: list[str]) -> list[str]:
    """Distinct labels, first occurrence order preserved."""
    seen: list[str] = []
    for label in labels:
        if label not in seen:
            seen.append(label)
    return seen


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
        """What the script now says, in one line.

        A finding is read in a list and acted on, so it states the new state
        of the script rather than arguing that the state is wrong. The
        conflict is in the sentence: something is used where nothing
        introduces it.
        """
        scenes = ", ".join(self.later_scene_numbers)
        one = len(self.later_scene_numbers) == 1
        return (
            f"{self.entity_label} is used in scene{'' if one else 's'} "
            f"{scenes} but no longer introduced."
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

    Ranking: exact entity match first, then a relevant
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
    # Findings hang off change sets, so scoping to this script goes through
    # the change set's script_id: unscoped, another script's findings would
    # enter this script's prompt.
    packet.open_findings = list(
        session.scalars(
            select(ContinuityFinding.message)
            .join(ChangeSet, ContinuityFinding.change_set_id == ChangeSet.id)
            .where(
                ChangeSet.script_id == script_id,
                ContinuityFinding.status == "open",
            )
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
    return graph_labels(session, script_id)


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
    removed_assertion_ids: set[str] | None = None,
) -> list[OrphanedReference]:
    """Find entities a proposal would stop introducing but not stop using.

    `removed_establishes` is `(entity_id, label, assertion_id)` for each
    `establishes` edge the diff removes. The assertion id is required, not
    convenience: a proposal is not applied yet, so the edge being removed is
    still in the table, and a check for "is it established elsewhere" that did
    not exclude it would always find it and never warn.

    `removed_assertion_ids` is every assertion id the same diff removes,
    across every predicate, not just `establishes`. A "use" this same edit
    also removes is not a later reference: it's still `active` in the table
    only because the proposal hasn't been applied yet, and without excluding
    it here, an edit that drops an entity's only establishing line and its
    only use in one unit warns about itself, citing the unit being edited as
    if it were a surviving later reference.

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
    also_removed = {str(i) for i in (removed_assertion_ids or ())}

    # An entry with no assertion id claims a removal it cannot name, so the
    # exclusion below cannot work for it: the still-present edge would be
    # found as "established elsewhere" or not, on data the caller did not
    # state. The contract in the docstring makes the id required; an entry
    # without one is dropped rather than half-checked.
    named = [entry for entry in removed_establishes if entry[2] is not None]

    for entity_id, label, _ in named:
        surviving = [
            assertion
            for assertion in session.scalars(
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
            if str(assertion.id) not in also_removed
        ]
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
            _, number = scene_order[scene_id]
            # Unnumbered scenes show as unnumbered rather than by position.
            display = number or "—"
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
