"""Suspected duplicate entities, and the reviewed merge that joins them.

Repeated extraction generations can fork one object into near-duplicate
rows: "Monitors" beside "Dispatch monitors", "Parka" beside "Grey parka".
The detector finds same-type pairs whose names or aliases overlap enough
to suspect one identity; a person decides. Merge moves every fact onto the
survivor through the same absorption the rename machinery uses, records
the decision as an accepted change set on the Reports page, and keeps the
absorbed name as an alias so later mentions resolve to the survivor. Keep
separate records the pair as distinct, and the detector stops offering it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from traceact import ActionTrace

from ripple.db.models import (
    Assertion,
    ChangeOperation,
    ChangeSet,
    Entity,
    EntityAlias,
    EntityDistinction,
    Script,
)
from ripple.services.renames import _absorb, _ensure_alias
from ripple.tracing import ensure_configured

logger = logging.getLogger(__name__)


class MergeRefused(Exception):
    """The merge cannot proceed; the message says why."""


# Surface forms that identify nobody. An extraction sometimes records a
# pronoun as an alias ("She" on Mara), and matching identities through one
# would pair every cast member with every other.
PRONOUNS = frozenset(
    {
        "i", "me", "my", "mine", "we", "us", "our", "you", "your",
        "he", "him", "his", "she", "her", "hers", "it", "its",
        "they", "them", "their", "theirs", "who", "whom",
    }
)


def _offers_evidence(normalized: str) -> bool:
    """Whether a normalized surface form can identify an entity."""
    return bool(normalized) and normalized not in PRONOUNS


@dataclass(frozen=True)
class DuplicatePair:
    """One suspected duplicate: keep is the suggested survivor."""

    keep: Entity
    absorb: Entity
    reason: str


def detect(session: Session, script_id) -> list[DuplicatePair]:
    """Same-type entity pairs that look like one identity.

    Two signals, both deterministic. Phrase containment: one canonical
    name appears whole inside the other ("Monitors" inside "Dispatch
    monitors" — word sets are not enough, "container door" is not inside
    "Container 4-4-1"). Shared name: one entity's canonical name is a
    recorded surface form of the other. Pronouns identify nobody, so a
    pronoun name or alias offers no evidence and is ignored.
    A pair a person has kept separate is never offered again.
    The suggested survivor is the entity more of the graph cites, with
    the longer name breaking a tie, and pairs whose entities match
    nothing else come first: a generic name matching several entities
    ("door" against every named door) is the least safe merge, so its
    pairs trail the clean decisions.
    """
    entities = list(
        session.scalars(select(Entity).where(Entity.script_id == script_id))
    )
    if len(entities) < 2:
        return []

    canon: dict = {}
    surfaces: dict = {}
    for entity in entities:
        canon[entity.id] = entity.normalized_name
        surfaces[entity.id] = {entity.normalized_name}
    for alias in session.scalars(
        select(EntityAlias)
        .join(Entity, EntityAlias.entity_id == Entity.id)
        .where(Entity.script_id == script_id)
    ):
        if _offers_evidence(alias.normalized_alias):
            surfaces[alias.entity_id].add(alias.normalized_alias)

    kept_separate = set()
    for row in session.scalars(
        select(EntityDistinction).where(EntityDistinction.script_id == script_id)
    ):
        kept_separate.add((row.entity_a_id, row.entity_b_id))

    def contains(inner: str, outer: str) -> bool:
        return inner != outer and f" {inner} " in f" {outer} "

    raw: list[tuple[Entity, Entity, str]] = []
    matches: dict = {}
    by_type: dict = {}
    for entity in entities:
        by_type.setdefault(entity.entity_type, []).append(entity)
    for group in by_type.values():
        for index, a in enumerate(group):
            for b in group[index + 1 :]:
                key = tuple(sorted((a.id, b.id), key=str))
                if key in kept_separate:
                    continue
                name_a, name_b = canon[a.id], canon[b.id]
                if not (
                    _offers_evidence(name_a) and _offers_evidence(name_b)
                ):
                    continue
                if contains(name_a, name_b) or contains(name_b, name_a):
                    reason = "one name contains the other"
                elif name_a in surfaces[b.id] or name_b in surfaces[a.id]:
                    reason = "they share a name"
                else:
                    continue
                raw.append((a, b, reason))
                matches[a.id] = matches.get(a.id, 0) + 1
                matches[b.id] = matches.get(b.id, 0) + 1

    pairs = []
    for a, b, reason in raw:
        keep, absorb = _rank(session, a, b)
        pairs.append(DuplicatePair(keep=keep, absorb=absorb, reason=reason))
    pairs.sort(key=lambda pair: max(matches[pair.keep.id], matches[pair.absorb.id]))
    return pairs


def _cited_count(session: Session, entity: Entity) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(
                Assertion.active.is_(True),
                (Assertion.subject_entity_id == entity.id)
                | (Assertion.object_entity_id == entity.id),
            )
        )
        or 0
    )


def _rank(session: Session, a: Entity, b: Entity) -> tuple[Entity, Entity]:
    """(keep, absorb): the more-cited entity survives, longer name on a tie."""
    cited_a, cited_b = _cited_count(session, a), _cited_count(session, b)
    if cited_a != cited_b:
        return (a, b) if cited_a > cited_b else (b, a)
    if len(a.canonical_name) != len(b.canonical_name):
        return (
            (a, b)
            if len(a.canonical_name) > len(b.canonical_name)
            else (b, a)
        )
    return (a, b)


def merge(session: Session, keep_id, absorb_id) -> ChangeSet:
    """Join two entities: every fact moves onto the survivor, recorded.

    The absorbed row's facts, aliases, and speaker links move through the
    rename machinery's absorption; its name becomes an alias of the
    survivor, and the row is deleted. The decision is recorded as an
    accepted change set (kind merge_entities) whose operations snapshot
    what moved, and the script version bumps so a preview drafted against
    the pre-merge graph refuses as stale rather than applying.
    """
    ensure_configured()
    keep = session.get(Entity, keep_id)
    absorbed = session.get(Entity, absorb_id)
    if keep is None or absorbed is None:
        raise MergeRefused("No such entity.")
    if keep.id == absorbed.id:
        raise MergeRefused("An entity cannot be merged with itself.")
    if keep.script_id != absorbed.script_id:
        raise MergeRefused("These entities belong to different scripts.")
    if keep.entity_type != absorbed.entity_type:
        raise MergeRefused(
            f"These entities have different types "
            f"({keep.entity_type} and {absorbed.entity_type})."
        )

    with ActionTrace.start(action="entities.merge", kind="change") as trace:
        trace.input(
            {
                "keep": f"{keep.canonical_name} ({keep.id})",
                "absorb": f"{absorbed.canonical_name} ({absorbed.id})",
            }
        )
        script = session.get(Script, keep.script_id)
        absorbed_snapshot = {
            "entity_id": str(absorbed.id),
            "canonical_name": absorbed.canonical_name,
            "entity_type": absorbed.entity_type,
            "aliases": sorted(
                alias.alias
                for alias in session.scalars(
                    select(EntityAlias).where(
                        EntityAlias.entity_id == absorbed.id
                    )
                )
            ),
            "active_assertions": _cited_count(session, absorbed),
        }
        absorbed_name = absorbed.canonical_name

        change = ChangeSet(
            script_id=script.id,
            kind="merge_entities",
            status="accepted",
            base_script_version=script.current_version,
        )
        session.add(change)
        session.flush()
        session.add(
            ChangeOperation(
                change_set_id=change.id,
                sequence_index=0,
                operation_type="update_entity",
                target_type="entity",
                target_id=keep.id,
                before_json=absorbed_snapshot,
                after_json={
                    "merged_into": str(keep.id),
                    "surviving_name": keep.canonical_name,
                },
            )
        )
        session.flush()

        _absorb(session, keep, absorbed)
        session.delete(absorbed)
        session.flush()
        _ensure_alias(session, keep, absorbed_name)

        script.current_version += 1
        change.accepted_at = change.created_at
        session.flush()

        trace.output(
            {
                "survivor": keep.canonical_name,
                "absorbed": absorbed_name,
                "assertions_carried": absorbed_snapshot["active_assertions"],
            }
        )
        logger.info(
            "merged %s into %s in %s",
            absorbed_name,
            keep.canonical_name,
            script.title,
        )
        return change


def keep_separate(session: Session, entity_a_id, entity_b_id) -> None:
    """Record that a suggested pair is two entities, and stop offering it."""
    a = session.get(Entity, entity_a_id)
    b = session.get(Entity, entity_b_id)
    if a is None or b is None:
        raise MergeRefused("No such entity.")
    if a.script_id != b.script_id:
        raise MergeRefused("These entities belong to different scripts.")
    first, second = sorted((a.id, b.id), key=str)
    existing = session.scalar(
        select(EntityDistinction.id).where(
            EntityDistinction.entity_a_id == first,
            EntityDistinction.entity_b_id == second,
        )
    )
    if existing is not None:
        return
    session.add(
        EntityDistinction(
            script_id=a.script_id, entity_a_id=first, entity_b_id=second
        )
    )
    session.flush()
