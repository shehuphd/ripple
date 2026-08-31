"""Structural scene edits: insertion and omission.

Insertion is additive: a new scene row, its parsed units, and a version bump.
Nothing is removed, so the reversal path for a mistaken insertion is omission.

Omission follows the production convention. The scene keeps its row and its
number and reads OMITTED; its facts deactivate through an accepted change set,
so the deactivation is atomic, audited, and reversible, and every entity the
scene introduced that later scenes still use becomes an open finding.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from traceact import ActionTrace

from ripple.adapters.fountain import parse_scene_text
from ripple.db.models import (
    Assertion,
    ChangeOperation,
    ChangeSet,
    ContinuityFinding,
    Entity,
    EntityAttribute,
    FindingEvidence,
    RippleReport,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.graph.continuity import detect_orphaned_references
from ripple.services import changeset
from ripple.services.changeset import InvalidOperation
from ripple.tracing import ensure_configured

logger = logging.getLogger(__name__)

# Far above any script's scene count, so the two-phase index shift can never
# collide with a live sequence_index.
_SHIFT = 1_000_000


@dataclass
class InsertedScene:
    """What an insertion created."""

    scene_id: str
    display_number: str | None
    heading: str
    unit_count: int
    script_version: int


@dataclass
class OmittedScene:
    """What an omission deactivated and warned about."""

    scene_id: str
    change_set_id: str
    display_number: str | None
    heading: str
    assertions_deactivated: int
    attributes_deactivated: int
    findings: list[str] = field(default_factory=list)
    severity: str = "low"
    script_version: int = 0


def insert_scene(
    session: Session,
    script_id,
    heading: str,
    body: str,
    after_scene_id=None,
) -> InsertedScene:
    """Create one scene at the chosen position and renumber what follows.

    Identity is the row, so shifting `sequence_index` detaches nothing. The
    display number follows the locked-numbering convention when the document
    is numbered (after scene 12 comes 12A) and stays empty when it is not.
    The scene's graph comes from extraction, started by the caller; until it
    runs, the scene reports no requirements.
    """
    ensure_configured()
    with ActionTrace.start(action="scene.insert", kind="change") as trace:
        trace.input({"script_id": str(script_id), "heading": heading})
        script = session.get(Script, script_id)
        if script is None:
            raise ValueError(f"no script with id {script_id}")

        after = None
        if after_scene_id is not None:
            after = session.get(Scene, after_scene_id)
            if after is None or after.script_id != script.id:
                raise ValueError("The scene to insert after is not in this script.")

        parsed = parse_scene_text(heading, body)

        insert_index = after.sequence_index + 1 if after is not None else 0
        # Two phases keep (script_id, sequence_index) unique throughout: a
        # single +1 update would collide row by row under SQLite's per-row
        # constraint checks.
        session.execute(
            update(Scene)
            .where(Scene.script_id == script.id, Scene.sequence_index >= insert_index)
            .values(sequence_index=Scene.sequence_index + _SHIFT)
        )
        session.execute(
            update(Scene)
            .where(Scene.script_id == script.id, Scene.sequence_index >= _SHIFT)
            .values(sequence_index=Scene.sequence_index - _SHIFT + 1)
        )

        scene = Scene(
            script_id=script.id,
            display_scene_number=_display_number(session, script, after),
            sequence_index=insert_index,
            heading=parsed.heading,
            int_ext=parsed.int_ext,
            time_of_day=parsed.time_of_day,
            current_version=1,
        )
        session.add(scene)
        session.flush()

        for unit in parsed.units:
            session.add(
                ScriptUnit(
                    scene_id=scene.id,
                    unit_type=unit.unit_type.value,
                    sequence_index=unit.sequence_index,
                    speaker_name=unit.speaker_name,
                    current_text=unit.text,
                    current_version=1,
                    parser_confidence=unit.parser_confidence,
                    parser_method=unit.parser_method.value,
                )
            )

        # A structural change moves the script version, so pending unit
        # proposals computed against the old shape go stale instead of
        # applying against a script they never saw.
        script.current_version += 1
        session.flush()

        trace.step(f"Inserted at index {insert_index}")
        trace.output(
            {"scene_id": str(scene.id), "units": len(parsed.units)}
        )
        return InsertedScene(
            scene_id=str(scene.id),
            display_number=scene.display_scene_number,
            heading=scene.heading,
            unit_count=len(parsed.units),
            script_version=script.current_version,
        )


def _display_number(session: Session, script: Script, after: Scene | None) -> str | None:
    """The new scene's display number under locked numbering, or None.

    An unnumbered document stays unnumbered. In a numbered one, the scene
    after 12 is 12A (then 12B, skipping numbers already in use), and a scene
    inserted before the first is A-prefixed (A1), both per the production
    convention for locked pages.
    """
    used = {
        number
        for number in session.scalars(
            select(Scene.display_scene_number).where(Scene.script_id == script.id)
        )
        if number
    }
    if not used:
        return None
    if after is None or not after.display_scene_number:
        first = session.scalar(
            select(Scene.display_scene_number)
            .where(Scene.script_id == script.id, Scene.display_scene_number.isnot(None))
            .order_by(Scene.sequence_index)
        )
        if not first:
            return None
        for prefix_count in range(1, 27):
            candidate = "A" * prefix_count + first
            if candidate not in used:
                return candidate
        return None
    base = after.display_scene_number
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = f"{base}{letter}"
        if candidate not in used:
            return candidate
    return None


def omit_scene(session: Session, scene_id) -> OmittedScene:
    """Mark a scene OMITTED and deactivate its facts, atomically.

    Runs as an accepted change set: one `set_scene_omitted` marker plus a
    removal per active assertion and attribute the scene supports. Orphan
    detection runs before anything deactivates, and each entity the scene
    introduced that later scenes still use persists as an open finding citing
    the surviving lines.
    """
    ensure_configured()
    with ActionTrace.start(action="scene.omit", kind="change") as trace:
        trace.input({"scene_id": str(scene_id)})
        scene = session.get(Scene, scene_id)
        if scene is None:
            raise ValueError(f"no scene with id {scene_id}")
        if scene.omitted:
            raise InvalidOperation("This scene is already omitted.")
        script = session.get(Script, scene.script_id)

        unit_ids = list(
            session.scalars(
                select(ScriptUnit.id).where(ScriptUnit.scene_id == scene.id)
            )
        )
        assertions = list(
            session.scalars(
                select(Assertion).where(
                    Assertion.script_id == script.id,
                    Assertion.active.is_(True),
                    (Assertion.subject_scene_id == scene.id)
                    | (Assertion.object_scene_id == scene.id)
                    | (Assertion.source_unit_id.in_(unit_ids or [scene.id])),
                )
            )
        )
        attributes = list(
            session.scalars(
                select(EntityAttribute).where(
                    EntityAttribute.active.is_(True),
                    EntityAttribute.source_unit_id.in_(unit_ids or [scene.id]),
                )
            )
        )

        labels = _entity_labels(session, assertions)
        removed_ids = {str(row.id) for row in assertions}
        removed_establishes = [
            (row.object_entity_id, labels.get(row.object_entity_id, "entity"), row.id)
            for row in assertions
            if row.predicate == "establishes" and row.object_entity_id
        ]
        # Before anything deactivates: the detector expects a proposal-shaped
        # world where the removed edges are still in the table.
        orphans = detect_orphaned_references(
            session, script.id, removed_establishes, removed_ids
        )

        proposal = ChangeSet(
            script_id=script.id,
            kind="omit_scene",
            status="pending",
            base_script_version=script.current_version,
        )
        session.add(proposal)
        session.flush()

        operations: list[dict] = [
            {
                "operation_type": "set_scene_omitted",
                "target_type": "scene",
                "target_id": scene.id,
                "before_json": {"omitted": False},
                "after_json": {"omitted": True},
            }
        ]
        for row in assertions:
            operations.append(
                {
                    "operation_type": "remove_assertion",
                    "target_type": "assertion",
                    "target_id": row.id,
                    "before_json": {
                        "predicate": row.predicate,
                        "subject": labels.get(
                            row.subject_entity_id, "scene"
                        ),
                        "object": labels.get(row.object_entity_id, "scene"),
                    },
                    "after_json": None,
                }
            )
        for row in attributes:
            operations.append(
                {
                    "operation_type": "remove_entity_attribute",
                    "target_type": "entity_attribute",
                    "target_id": row.id,
                    "before_json": {
                        "entity_id": str(row.entity_id),
                        "key": row.key,
                        "value": row.value,
                        "source_unit_id": str(row.source_unit_id)
                        if row.source_unit_id
                        else None,
                        "confidence": row.confidence,
                    },
                    "after_json": None,
                }
            )
        for index, operation in enumerate(operations):
            session.add(
                ChangeOperation(
                    change_set_id=proposal.id,
                    sequence_index=index,
                    operation_type=operation["operation_type"],
                    target_type=operation["target_type"],
                    target_id=operation["target_id"],
                    before_json=operation["before_json"],
                    after_json=operation["after_json"],
                )
            )
        session.flush()

        changeset.accept(session, proposal.id)

        severity = "high" if orphans else ("medium" if assertions else "low")
        messages: list[str] = []
        for orphan in orphans:
            finding = ContinuityFinding(
                change_set_id=proposal.id,
                finding_type="orphaned_reference",
                severity="high",
                message=orphan.message,
                status="open",
            )
            session.add(finding)
            session.flush()
            for rank, unit_id in enumerate(dict.fromkeys(orphan.later_unit_ids)):
                session.add(
                    FindingEvidence(
                        finding_id=finding.id,
                        script_unit_id=uuid_module.UUID(unit_id),
                        rank=rank,
                        match_reason="later_reference",
                    )
                )
            messages.append(orphan.message)

        label = (
            f"Scene {scene.display_scene_number}"
            if scene.display_scene_number
            else "The scene"
        )
        summary = (
            f"{label} is omitted. {len(assertions)} fact(s) and "
            f"{len(attributes)} attribute(s) it supported are deactivated."
        )
        if orphans:
            noun = "entity" if len(orphans) == 1 else "entities"
            summary += (
                f" {len(orphans)} {noun} it introduced are still used by "
                "later scenes; see the findings."
            )
        session.add(
            RippleReport(
                change_set_id=proposal.id,
                summary=summary,
                severity=severity,
            )
        )
        session.flush()

        trace.step(f"Deactivated {len(assertions)} assertions")
        trace.output({"change_set": str(proposal.id), "orphans": len(orphans)})
        return OmittedScene(
            scene_id=str(scene.id),
            change_set_id=str(proposal.id),
            display_number=scene.display_scene_number,
            heading=scene.heading,
            assertions_deactivated=len(assertions),
            attributes_deactivated=len(attributes),
            findings=messages,
            severity=severity,
            script_version=script.current_version,
        )


def restore_scene(session: Session, scene_id) -> OmittedScene:
    """Reverse an omission: reactivate the facts and reopen nothing.

    Builds the inverse of the omission's change set (the same way undo does),
    applies it, marks the omission reverted, and resolves the findings the
    omission opened, because the condition they warned about no longer holds.
    """
    ensure_configured()
    with ActionTrace.start(action="scene.restore", kind="change") as trace:
        trace.input({"scene_id": str(scene_id)})
        scene = session.get(Scene, scene_id)
        if scene is None:
            raise ValueError(f"no scene with id {scene_id}")
        if not scene.omitted:
            raise InvalidOperation("This scene is not omitted.")
        script = session.get(Script, scene.script_id)

        omission = session.scalar(
            select(ChangeSet)
            .join(ChangeOperation)
            .where(
                ChangeSet.script_id == script.id,
                ChangeSet.kind == "omit_scene",
                ChangeSet.status == "accepted",
                ChangeOperation.operation_type == "set_scene_omitted",
                ChangeOperation.target_id == scene.id,
            )
            .order_by(ChangeSet.accepted_at.desc())
            .limit(1)
        )
        if omission is None:
            raise InvalidOperation(
                "No accepted omission found for this scene to restore."
            )

        inverse = ChangeSet(
            script_id=script.id,
            reverts_change_set_id=omission.id,
            kind="undo",
            status="pending",
            base_script_version=script.current_version,
        )
        session.add(inverse)
        session.flush()
        for index, operation in enumerate(reversed(omission.operations)):
            session.add(
                ChangeOperation(
                    change_set_id=inverse.id,
                    sequence_index=index,
                    operation_type=changeset._inverse_type(operation),
                    target_type=operation.target_type,
                    target_id=operation.target_id,
                    before_json=operation.after_json,
                    after_json=operation.before_json,
                )
            )
        session.flush()

        changeset.accept(session, inverse.id)
        omission.status = "reverted"
        resolved = 0
        for finding in session.scalars(
            select(ContinuityFinding).where(
                ContinuityFinding.change_set_id == omission.id,
                ContinuityFinding.status == "open",
            )
        ):
            finding.status = "resolved"
            finding.resolved_at = changeset._now()
            resolved += 1
        session.flush()

        trace.step(f"Restored and resolved {resolved} findings")
        trace.output({"reverted": str(omission.id), "inverse": str(inverse.id)})
        return OmittedScene(
            scene_id=str(scene.id),
            change_set_id=str(inverse.id),
            display_number=scene.display_scene_number,
            heading=scene.heading,
            assertions_deactivated=0,
            attributes_deactivated=0,
            severity="low",
            script_version=script.current_version,
        )


def _entity_labels(session: Session, assertions: list[Assertion]) -> dict:
    """Entity id to canonical name, for readable operation snapshots."""
    ids = {
        entity_id
        for row in assertions
        for entity_id in (row.subject_entity_id, row.object_entity_id)
        if entity_id
    }
    if not ids:
        return {}
    return {
        entity.id: entity.canonical_name
        for entity in session.scalars(select(Entity).where(Entity.id.in_(ids)))
    }
