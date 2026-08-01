"""Persistence for parsed screenplays and the deletion operations.

Adapters return data; this module is the only place import results become rows.
Every function takes a session and performs no commit of its own, so a caller
can compose several writes into one transaction. `session_scope` owns the
commit and the rollback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ripple.adapters.base import ImportResult, ParsedScene
from ripple.db.models import (
    AppConfiguration,
    Assertion,
    ChangeSet,
    ContinuityFinding,
    Entity,
    EntityAlias,
    ExtractionRun,
    Import,
    RippleReport,
    Scene,
    SceneExtraction,
    Script,
    ScriptUnit,
    SourceAnchor,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeletionCounts:
    """What a destructive operation removed, or would remove.

    PRD section 9 requires counts before confirmation, so the same structure
    serves the preview and the result.
    """

    scripts: int = 0
    scenes: int = 0
    units: int = 0
    entities: int = 0
    assertions: int = 0
    extraction_runs: int = 0
    change_sets: int = 0
    findings: int = 0

    @property
    def total(self) -> int:
        return (
            self.scripts
            + self.scenes
            + self.units
            + self.entities
            + self.assertions
            + self.extraction_runs
            + self.change_sets
            + self.findings
        )


def _title_for(result: ImportResult) -> str:
    """The script title: the document's own, else the uploaded filename stem."""
    if result.title:
        return result.title
    stem = PurePosixPath(result.source_name).stem
    return stem.replace("-", " ").replace("_", " ").strip() or "Untitled"


def persist_import(session: Session, result: ImportResult) -> Script:
    """Write a parsed screenplay and its provenance.

    Rejected results are not persisted: there is no screenplay to store, and a
    row for a refused upload would pollute the library.
    """
    if not result.accepted:
        raise ValueError(f"cannot persist a rejected import ({result.rejection_code})")

    script = Script(
        title=_title_for(result),
        import_status=result.outcome.value,
        graph_status="not_analysed",
        current_version=1,
    )
    session.add(script)
    session.flush()

    record = Import(
        script_id=script.id,
        detected_format=result.detected_format.value,
        adapter_name=result.adapter_name,
        source_name=result.source_name,
        content_hash=result.content_hash,
        outcome=result.outcome.value,
        warnings_json=[
            {
                "code": warning.code,
                "message": warning.message,
                "scene_index": warning.scene_index,
            }
            for warning in result.warnings
        ],
    )
    session.add(record)
    session.flush()

    for parsed_scene in result.scenes:
        _persist_scene(session, script, record, parsed_scene)

    session.flush()
    logger.info(
        "persisted script %s: %d scenes, %d units",
        script.id,
        result.scene_count,
        result.unit_count,
    )
    return script


def _persist_scene(
    session: Session, script: Script, record: Import, parsed: ParsedScene
) -> Scene:
    """Write one scene, its units, and their source anchors."""
    scene = Scene(
        script_id=script.id,
        display_scene_number=parsed.display_scene_number,
        sequence_index=parsed.sequence_index,
        heading=parsed.heading,
        int_ext=parsed.int_ext,
        time_of_day=parsed.time_of_day,
        current_version=1,
    )
    session.add(scene)
    session.flush()

    for parsed_unit in parsed.units:
        unit = ScriptUnit(
            scene_id=scene.id,
            unit_type=parsed_unit.unit_type.value,
            sequence_index=parsed_unit.sequence_index,
            speaker_name=parsed_unit.speaker_name,
            current_text=parsed_unit.text,
            current_version=1,
            parser_confidence=parsed_unit.parser_confidence,
            parser_method=parsed_unit.parser_method.value,
        )
        session.add(unit)
        session.flush()

        anchor = parsed_unit.anchor
        if anchor is None:
            continue
        session.add(
            SourceAnchor(
                script_unit_id=unit.id,
                import_id=record.id,
                source_page_number=anchor.page_number,
                source_block_index=anchor.block_index,
                source_start_offset=anchor.start_offset,
                source_end_offset=anchor.end_offset,
                bounding_box_json=(
                    {
                        "x0": anchor.bounding_box[0],
                        "y0": anchor.bounding_box[1],
                        "x1": anchor.bounding_box[2],
                        "y1": anchor.bounding_box[3],
                    }
                    if anchor.bounding_box
                    else None
                ),
                extraction_method=anchor.extraction_method,
            )
        )
    return scene


def _count(session: Session, model, script_id=None) -> int:
    """Count rows of a model, optionally scoped to one script."""
    statement = select(func.count()).select_from(model)
    if script_id is not None and hasattr(model, "script_id"):
        statement = statement.where(model.script_id == script_id)
    return session.scalar(statement) or 0


def deletion_preview(session: Session, script_id=None) -> DeletionCounts:
    """What deleting one script, or all scripts, would remove."""
    scene_filter = select(Scene.id)
    if script_id is not None:
        scene_filter = scene_filter.where(Scene.script_id == script_id)
    scene_ids = list(session.scalars(scene_filter))

    units = 0
    if scene_ids:
        units = (
            session.scalar(
                select(func.count())
                .select_from(ScriptUnit)
                .where(ScriptUnit.scene_id.in_(scene_ids))
            )
            or 0
        )

    return DeletionCounts(
        scripts=1 if script_id is not None else _count(session, Script),
        scenes=len(scene_ids),
        units=units,
        entities=_count(session, Entity, script_id),
        assertions=_count(session, Assertion, script_id),
        extraction_runs=_count(session, ExtractionRun, script_id),
        change_sets=_count(session, ChangeSet, script_id),
        findings=_count(session, ContinuityFinding),
    )


def delete_script(session: Session, script_id) -> DeletionCounts:
    """Delete one script and everything under it. ERD section 10."""
    counts = deletion_preview(session, script_id)
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"no script with id {script_id}")
    session.delete(script)
    session.flush()
    return counts


def delete_all_scripts(session: Session) -> DeletionCounts:
    """Delete every script. Application configuration is untouched."""
    counts = deletion_preview(session)
    for script in session.scalars(select(Script)):
        session.delete(script)
    session.flush()
    return counts


def clear_all_graphs(session: Session) -> DeletionCounts:
    """Delete graph data while preserving scripts, scenes, units, and anchors.

    Query log rows survive, because a question concerns the script rather than
    one extraction run. ERD section 9.
    """
    counts = DeletionCounts(
        entities=_count(session, Entity),
        assertions=_count(session, Assertion),
        extraction_runs=_count(session, ExtractionRun),
        change_sets=_count(session, ChangeSet),
        findings=_count(session, ContinuityFinding),
    )

    # Units reference entities as speakers; clear the link before the entities
    # go, so the delete is not order-dependent on database cascade behaviour.
    session.execute(ScriptUnit.__table__.update().values(speaker_entity_id=None))
    for model in (
        RippleReport,
        ContinuityFinding,
        ChangeSet,
        Assertion,
        SceneExtraction,
        ExtractionRun,
        EntityAlias,
        Entity,
    ):
        session.execute(delete(model))

    session.execute(Script.__table__.update().values(graph_status="not_analysed"))
    session.flush()
    # Core updates bypass the identity map, so objects already loaded in this
    # session would keep reporting their old graph_status.
    session.expire_all()
    return counts


def set_active_model(session: Session, provider_id: str, model_id: str) -> None:
    """Record the selected provider and model. Never a credential."""
    row = session.get(AppConfiguration, "active_model")
    if row is None:
        row = AppConfiguration(key="active_model")
        session.add(row)
    row.provider_id = provider_id
    row.model_id = model_id
    session.flush()


def get_active_model(session: Session) -> tuple[str | None, str | None]:
    """The selected provider and model, or (None, None) before a choice."""
    row = session.get(AppConfiguration, "active_model")
    return (row.provider_id, row.model_id) if row else (None, None)


__all__ = [
    "DeletionCounts",
    "clear_all_graphs",
    "delete_all_scripts",
    "delete_script",
    "deletion_preview",
    "get_active_model",
    "persist_import",
    "set_active_model",
]
