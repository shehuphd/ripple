"""Detecting and applying a character rename across drafts.

A name is a label, never identity, so a renamed character has to be found by
evidence, and the strongest evidence is positional: dialogue lines that
carried over word-for-word but changed speaker. "4 of 5 of Mara's mapped
speaking positions now read NIA" is a count, not a similarity score.

Detection runs during the draft report, over the cast entities extraction
added. Strong evidence applies the rename automatically; middling evidence
becomes a `possible_rename` finding whose Confirm action applies it and
whose Dismiss declines it. Applying a rename joins the identities: the
survivor keeps one entity row, the old name becomes an alias so later
mentions of either name resolve to it, and any line still speaking under
the old name is reported as a `partial_rename` finding, the classic missed
instance.

Non-cast renames (a prop or location renamed) carry no speaker positions
and are out of this module's reach; they surface as an added plus a removed
entity on the report instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select, update

from ripple.db.models import (
    Assertion,
    ContinuityFinding,
    Entity,
    EntityAlias,
    EntityAttribute,
    FindingEvidence,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize

logger = logging.getLogger(__name__)

# Applying a rename nobody asked for must be safer than asking: it takes at
# least this many transferred speaking positions and this share of them.
AUTO_POSITIONS = 3
AUTO_SHARE = 0.8

# Below the automatic bar but above this, the evidence is honest enough to
# ask about; below it, silence.
SUGGEST_SHARE = 0.5


@dataclass
class RenameCandidate:
    """One suspected rename, with the counts that back it."""

    old_entity_id: str
    old_name: str
    new_entity_id: str
    new_name: str
    transferred: int
    total_mapped: int

    @property
    def share(self) -> float:
        return self.transferred / self.total_mapped if self.total_mapped else 0.0

    @property
    def automatic(self) -> bool:
        return self.transferred >= AUTO_POSITIONS and self.share >= AUTO_SHARE

    @property
    def message(self) -> str:
        return (
            f"{self.old_name} appears renamed to {self.new_name}: "
            f"{self.transferred} of {self.total_mapped} of "
            f"{self.old_name}'s carried speaking positions now read "
            f"{self.new_name}."
        )


def detect_renames(
    session, script: Script, old: Script, added: list[Entity]
) -> list[RenameCandidate]:
    """Suspect renames among the cast entities extraction added.

    For each added cast entity, its speaking positions are traced back
    through unit lineage: a dialogue line whose text carried over unchanged
    but whose speaker label moved from one name to another is a transferred
    position. The denominator is every one of the old speaker's positions
    that carried over at all, so lines still speaking under the old name
    (missed instances, or a wrong suspicion) lower the share.
    """
    candidates = []
    old_cast = {
        entity.id: entity
        for entity in session.scalars(
            select(Entity).where(
                Entity.script_id == old.id, Entity.entity_type == "cast"
            )
        )
    }
    old_alias_names = _alias_names(session, old_cast)

    for entity in added:
        if entity.entity_type != "cast":
            continue
        # A cue says "NIA" while extraction's canonical says "Nia Okonjo",
        # so the match goes through the entity's aliases and its given-name
        # token, the same resolution speakers get everywhere else.
        new_names = _names_of(session, entity)
        transferred_by_old: dict = {}
        for unit in session.scalars(
            select(ScriptUnit)
            .join(Scene, ScriptUnit.scene_id == Scene.id)
            .where(
                Scene.script_id == script.id,
                ScriptUnit.predecessor_unit_id.isnot(None),
                ScriptUnit.speaker_name.isnot(None),
            )
        ):
            if normalize(unit.speaker_name) not in new_names:
                continue
            source = session.get(ScriptUnit, unit.predecessor_unit_id)
            if source is None or not source.speaker_name:
                continue
            speaker = _resolve_old_speaker(
                source, old_cast, old_alias_names
            )
            if speaker is None or normalize(speaker.canonical_name) in new_names:
                continue
            transferred_by_old[speaker.id] = (
                transferred_by_old.get(speaker.id, 0) + 1
            )
        if not transferred_by_old:
            continue
        old_id = max(transferred_by_old, key=transferred_by_old.get)
        source_entity = old_cast[old_id]
        total = _mapped_positions(session, script, old, source_entity)
        candidate = RenameCandidate(
            old_entity_id=str(old_id),
            old_name=source_entity.canonical_name,
            new_entity_id=str(entity.id),
            new_name=entity.canonical_name,
            transferred=transferred_by_old[old_id],
            total_mapped=total,
        )
        if candidate.share >= SUGGEST_SHARE and candidate.transferred >= 1:
            candidates.append(candidate)
    return candidates


def apply_rename(session, script: Script, candidate: RenameCandidate) -> Entity:
    """Join the two identities into one entity carrying the new name.

    When the old character partially carried into this draft (some scenes
    were unchanged), that carried twin is the survivor: it keeps its row and
    its lineage, takes the new name, and absorbs the added entity's facts.
    Without a twin, the added entity takes the predecessor link directly.
    Either way the old name becomes an alias, so every later mention of
    either name resolves to the same identity, and lines still speaking
    under the old name are reported rather than smoothed over.
    """
    import uuid as uuid_module

    old_id = uuid_module.UUID(candidate.old_entity_id)
    new_entity = session.get(Entity, uuid_module.UUID(candidate.new_entity_id))
    old_entity = session.get(Entity, old_id)
    twin = session.scalar(
        select(Entity).where(
            Entity.script_id == script.id,
            Entity.predecessor_entity_id == old_id,
            Entity.id != new_entity.id,
        )
    )

    if twin is not None:
        survivor = twin
        _absorb(session, survivor, new_entity)
        new_name = new_entity.canonical_name
        # The absorbed row must be gone before the survivor takes its name,
        # or the unique (script, type, name) constraint fires mid-flush.
        session.delete(new_entity)
        session.flush()
        survivor.canonical_name = new_name
        survivor.normalized_name = normalize(new_name)
    else:
        survivor = new_entity
        survivor.predecessor_entity_id = old_id
    _ensure_alias(session, survivor, old_entity.canonical_name)
    session.flush()
    logger.info(
        "renamed %s to %s in %s",
        old_entity.canonical_name,
        survivor.canonical_name,
        script.title,
    )
    return survivor


def leftover_mentions(session, script: Script, survivor: Entity) -> list:
    """Units still speaking under a non-current name of this entity.

    The classic missed instance: the rename went through, two cue lines
    kept the old name. They resolve to the same identity through the alias,
    and they are reported, never silently absorbed.
    """
    current = normalize(survivor.canonical_name)
    current_first = normalize(
        survivor.canonical_name.split()[0] if survivor.canonical_name else ""
    )
    old_names = set()
    for alias in session.scalars(
        select(EntityAlias).where(EntityAlias.entity_id == survivor.id)
    ):
        old_names.add(alias.normalized_alias)
        # Cues use the given name alone; "Mara Okonjo" the alias means
        # "MARA" the cue is an old name too.
        first = alias.alias.split()[0] if alias.alias else ""
        if first:
            old_names.add(normalize(first))
    old_names -= {current, current_first}
    if not old_names:
        return []
    units = []
    for unit in session.scalars(
        select(ScriptUnit)
        .join(Scene, ScriptUnit.scene_id == Scene.id)
        .where(
            Scene.script_id == script.id,
            ScriptUnit.unit_type == "character",
            ScriptUnit.speaker_name.isnot(None),
        )
        .order_by(Scene.sequence_index, ScriptUnit.sequence_index)
    ):
        if normalize(unit.speaker_name) in old_names:
            units.append(unit)
    return units


def file_partial_finding(
    session, script: Script, change_set_id, survivor: Entity, old_name: str
) -> bool:
    """File the missed-instance finding for an applied rename, if any.

    Returns whether one was filed.
    """
    leftovers = leftover_mentions(session, script, survivor)
    if not leftovers:
        return False
    scenes = _scene_labels(session, leftovers)
    finding = ContinuityFinding(
        change_set_id=change_set_id,
        finding_type="partial_rename",
        severity="medium",
        message=(
            f"This character is {survivor.canonical_name} in this draft "
            f"but still {old_name} in scene(s) {scenes}. The lines resolve "
            "to the same character through the recorded alias."
        ),
        status="open",
    )
    session.add(finding)
    session.flush()
    for rank, unit in enumerate(leftovers[:8]):
        session.add(
            FindingEvidence(
                finding_id=finding.id,
                script_unit_id=unit.id,
                rank=rank,
                match_reason="old_name",
            )
        )
    session.flush()
    return True


def file_suggestion(session, change_set_id, candidate: RenameCandidate) -> None:
    """File a possible_rename finding whose Confirm action applies it."""
    session.add(
        ContinuityFinding(
            change_set_id=change_set_id,
            finding_type="possible_rename",
            severity="medium",
            message=candidate.message
            + " Confirm to join them, or dismiss to keep them separate.",
            status="open",
            payload_json={
                "old_entity_id": candidate.old_entity_id,
                "new_entity_id": candidate.new_entity_id,
                "old_name": candidate.old_name,
                "new_name": candidate.new_name,
                "transferred": candidate.transferred,
                "total_mapped": candidate.total_mapped,
            },
        )
    )
    session.flush()


def confirm_rename(session, finding: ContinuityFinding) -> Entity:
    """Apply the rename a possible_rename finding describes, and resolve it."""
    payload = finding.payload_json or {}
    required = ("old_entity_id", "new_entity_id", "old_name", "new_name")
    if any(not payload.get(key) for key in required):
        raise ValueError("This finding does not describe a rename.")
    script = session.get(Script, finding.change_set.script_id)
    candidate = RenameCandidate(
        old_entity_id=payload["old_entity_id"],
        old_name=payload["old_name"],
        new_entity_id=payload["new_entity_id"],
        new_name=payload["new_name"],
        transferred=int(payload.get("transferred", 0)),
        total_mapped=int(payload.get("total_mapped", 0)),
    )
    survivor = apply_rename(session, script, candidate)
    finding.status = "resolved"
    from ripple.services.changeset import _now

    finding.resolved_at = _now()
    file_partial_finding(
        session, script, finding.change_set_id, survivor, candidate.old_name
    )
    session.flush()
    return survivor


def _absorb(session, survivor: Entity, absorbed: Entity) -> None:
    """Move every reference from one entity row onto another.

    An edge the survivor already holds (same endpoints, predicate, and
    evidence) is deactivated on the absorbed side instead of moved, so the
    per-script dedupe key stays unique.
    """
    for row in session.scalars(
        select(Assertion).where(
            (Assertion.subject_entity_id == absorbed.id)
            | (Assertion.object_entity_id == absorbed.id)
        )
    ):
        if row.subject_entity_id == absorbed.id:
            row.subject_entity_id = survivor.id
        if row.object_entity_id == absorbed.id:
            row.object_entity_id = survivor.id
        replacement_key = row.compute_dedupe_key()
        duplicate = session.scalar(
            select(Assertion).where(
                Assertion.script_id == row.script_id,
                Assertion.dedupe_key == replacement_key,
                Assertion.id != row.id,
            )
        )
        if duplicate is not None:
            session.delete(row)
        else:
            row.dedupe_key = replacement_key
        session.flush()
    # Attributes move one at a time: only one active row per (entity, key)
    # may exist, so an absorbed value whose key the survivor already holds
    # deactivates instead of moving. The survivor's value wins; changing an
    # active value is the judgement engine's job, never a merge side effect.
    survivor_keys = set(
        session.scalars(
            select(EntityAttribute.key).where(
                EntityAttribute.entity_id == survivor.id,
                EntityAttribute.active.is_(True),
            )
        )
    )
    for attribute in session.scalars(
        select(EntityAttribute).where(EntityAttribute.entity_id == absorbed.id)
    ):
        if attribute.active and attribute.key in survivor_keys:
            attribute.active = False
        attribute.entity_id = survivor.id
    session.execute(
        update(ScriptUnit)
        .where(ScriptUnit.speaker_entity_id == absorbed.id)
        .values(speaker_entity_id=survivor.id)
    )
    for alias in session.scalars(
        select(EntityAlias).where(EntityAlias.entity_id == absorbed.id)
    ):
        _ensure_alias(session, survivor, alias.alias)
        session.delete(alias)
    session.flush()


def _ensure_alias(session, entity: Entity, name: str) -> None:
    normalized = normalize(name)
    if normalized == entity.normalized_name:
        return
    existing = session.scalar(
        select(EntityAlias).where(
            EntityAlias.entity_id == entity.id,
            EntityAlias.normalized_alias == normalized,
        )
    )
    if existing is None:
        session.add(
            EntityAlias(
                entity_id=entity.id, alias=name, normalized_alias=normalized
            )
        )


def _names_of(session, entity: Entity) -> set:
    """Every normalized name this entity answers to, given-name included."""
    names = {entity.normalized_name}
    first = entity.canonical_name.split()[0] if entity.canonical_name else ""
    if first:
        names.add(normalize(first))
    for alias in session.scalars(
        select(EntityAlias).where(EntityAlias.entity_id == entity.id)
    ):
        names.add(alias.normalized_alias)
    return names


def _alias_names(session, old_cast: dict) -> dict:
    names = {}
    for entity in old_cast.values():
        names[entity.normalized_name] = entity
    for alias in session.scalars(
        select(EntityAlias).where(EntityAlias.entity_id.in_(list(old_cast)))
    ):
        names.setdefault(alias.normalized_alias, old_cast[alias.entity_id])
    return names


def _resolve_old_speaker(source: ScriptUnit, old_cast: dict, names: dict):
    if source.speaker_entity_id and source.speaker_entity_id in old_cast:
        return old_cast[source.speaker_entity_id]
    return names.get(normalize(source.speaker_name or ""))


def _mapped_positions(session, script: Script, old: Script, entity: Entity) -> int:
    """How many of an old speaker's lines carried into the new draft at all."""
    entity_names = _names_of(session, entity)
    old_unit_ids = [
        unit.id
        for unit in session.scalars(
            select(ScriptUnit)
            .join(Scene, ScriptUnit.scene_id == Scene.id)
            .where(
                Scene.script_id == old.id, ScriptUnit.speaker_name.isnot(None)
            )
        )
        if _speaks_as(unit, entity, entity_names)
    ]
    if not old_unit_ids:
        return 0
    return len(
        list(
            session.scalars(
                select(ScriptUnit.id).where(
                    ScriptUnit.predecessor_unit_id.in_(old_unit_ids)
                )
            )
        )
    )


def _speaks_as(unit: ScriptUnit, entity: Entity, entity_names: set) -> bool:
    if unit.speaker_entity_id:
        return unit.speaker_entity_id == entity.id
    return normalize(unit.speaker_name or "") in entity_names


def _scene_labels(session, units: list) -> str:
    labels = []
    seen = set()
    for unit in units:
        scene = session.get(Scene, unit.scene_id)
        label = scene.display_scene_number or "—"
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return ", ".join(labels)
