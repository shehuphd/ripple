"""Linking an upload as a new draft and carrying its graph forward.

The aligner decides which scenes the new draft keeps; this module writes the
lineage and moves the graph. An unchanged scene's facts copy over at zero
model cost, evidence intact, and its extraction-cache rows ride along so a
later whole-script build re-bills nothing. A modified or inserted scene
carries nothing: it is queued for extraction, because inheriting a graph the
text may contradict would be a guess.

The predecessor draft is never mutated. Linking writes only to the new
script, so flipping back to the old draft is opening it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from traceact import ActionTrace

from ripple.db.models import (
    Assertion,
    Entity,
    EntityAlias,
    EntityAttribute,
    ExtractionRun,
    Scene,
    SceneExtraction,
    Script,
    ScriptUnit,
)
from ripple.graph.alignment import SceneContent, align_scenes, align_units
from ripple.tracing import ensure_configured

logger = logging.getLogger(__name__)


@dataclass
class DraftLink:
    """What linking a draft did."""

    script_id: str
    predecessor_script_id: str
    draft_number: int
    unchanged: int = 0
    modified: int = 0
    inserted: int = 0
    deleted: int = 0
    entities_carried: int = 0
    assertions_carried: int = 0
    attributes_carried: int = 0
    cache_rows_carried: int = 0
    #: Scene ids (new draft) that need extraction: modified plus inserted.
    to_extract: list[str] = field(default_factory=list)
    graph_status: str = "not_analysed"


class LinkRefused(Exception):
    """Linking cannot proceed; the message says why."""


def link_draft(session, script_id, predecessor_script_id) -> DraftLink:
    """Align, write lineage, and carry the unchanged scenes' graph."""
    ensure_configured()
    with ActionTrace.start(action="draft.link", kind="change") as trace:
        trace.input(
            {
                "script_id": str(script_id),
                "predecessor": str(predecessor_script_id),
            }
        )
        script = session.get(Script, script_id)
        old = session.get(Script, predecessor_script_id)
        if script is None or old is None:
            raise ValueError("no such script")
        if script.id == old.id:
            raise LinkRefused("A script cannot be its own predecessor.")
        if script.predecessor_script_id is not None:
            raise LinkRefused("This script is already linked to a predecessor.")
        if _in_chain(session, old, script.id):
            raise LinkRefused("Linking there would make the drafts a loop.")
        has_graph = session.scalar(
            select(Assertion.id).where(Assertion.script_id == script.id).limit(1)
        )
        if has_graph is not None:
            raise LinkRefused(
                "This script already has a graph; link a fresh import instead."
            )

        alignment = align_scenes(
            _contents(session, old.id), _contents(session, script.id)
        )
        result = DraftLink(
            script_id=str(script.id),
            predecessor_script_id=str(old.id),
            draft_number=old.draft_number + 1,
        )

        script.predecessor_script_id = old.id
        script.draft_number = old.draft_number + 1

        scene_map: dict = {}  # old scene id -> new scene id, unchanged only
        unit_map: dict = {}  # old unit id -> new unit id, unchanged scenes
        unchanged_pairs = []
        for pair in alignment.pairs:
            new_scene = session.get(Scene, _uuid(pair.new_id))
            new_scene.predecessor_scene_id = _uuid(pair.old_id)
            new_scene.lineage_kind = pair.kind
            if pair.kind == "unchanged":
                old_units = _units_of(session, _uuid(pair.old_id))
                new_units = _units_of(session, _uuid(pair.new_id))
                if len(old_units) != len(new_units):
                    # Content said identical but structure disagrees; the
                    # safe reading is modified, so the scene re-extracts.
                    new_scene.lineage_kind = "modified"
                    result.modified += 1
                    result.to_extract.append(pair.new_id)
                    continue
                unchanged_pairs.append(pair)
                scene_map[_uuid(pair.old_id)] = new_scene.id
                for old_unit, new_unit in zip(old_units, new_units, strict=True):
                    unit_map[old_unit.id] = new_unit.id
                    new_unit.predecessor_unit_id = old_unit.id
                result.unchanged += 1
            else:
                _link_units(session, pair.old_id, pair.new_id)
                result.modified += 1
                result.to_extract.append(pair.new_id)
        for scene_id in alignment.inserted:
            scene = session.get(Scene, _uuid(scene_id))
            scene.lineage_kind = "inserted"
            result.inserted += 1
            result.to_extract.append(scene_id)
        result.deleted = len(alignment.deleted)
        session.flush()

        entity_map = _carry_graph(session, script, old, scene_map, unit_map, result)
        _carry_speakers(session, unit_map, entity_map)
        result.cache_rows_carried = _carry_cache(
            session, script, scene_map
        )

        script.graph_status = "partially_ready" if result.to_extract else "ready"
        result.graph_status = script.graph_status
        session.flush()

        trace.step(
            f"{result.unchanged} carried, {len(result.to_extract)} to extract"
        )
        trace.output(result.__dict__ | {"to_extract": len(result.to_extract)})
        logger.info(
            "linked %s as draft %d of %s: %d unchanged, %d modified, "
            "%d inserted, %d deleted",
            script.title,
            result.draft_number,
            old.title,
            result.unchanged,
            result.modified,
            result.inserted,
            result.deleted,
        )
        return result


def _carry_graph(
    session, script: Script, old: Script, scene_map: dict, unit_map: dict, result
) -> dict:
    """Copy the active facts whose scenes and evidence carried over."""
    entity_map: dict = {}

    def mapped_entity(old_entity_id):
        if old_entity_id in entity_map:
            return entity_map[old_entity_id]
        source = session.get(Entity, old_entity_id)
        existing = session.scalar(
            select(Entity).where(
                Entity.script_id == script.id,
                Entity.entity_type == source.entity_type,
                Entity.normalized_name == source.normalized_name,
            )
        )
        if existing is not None:
            entity_map[old_entity_id] = existing.id
            return existing.id
        clone = Entity(
            script_id=script.id,
            entity_type=source.entity_type,
            canonical_name=source.canonical_name,
            normalized_name=source.normalized_name,
            description=source.description,
            predecessor_entity_id=source.id,
        )
        session.add(clone)
        session.flush()
        for alias in session.scalars(
            select(EntityAlias).where(EntityAlias.entity_id == source.id)
        ):
            session.add(
                EntityAlias(
                    entity_id=clone.id,
                    alias=alias.alias,
                    normalized_alias=alias.normalized_alias,
                )
            )
        entity_map[old_entity_id] = clone.id
        result.entities_carried += 1
        return clone.id

    for row in session.scalars(
        select(Assertion).where(
            Assertion.script_id == old.id, Assertion.active.is_(True)
        )
    ):
        if row.source_unit_id not in unit_map:
            continue
        if row.subject_scene_id and row.subject_scene_id not in scene_map:
            continue
        if row.object_scene_id and row.object_scene_id not in scene_map:
            continue
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind=row.subject_kind,
                subject_entity_id=(
                    mapped_entity(row.subject_entity_id)
                    if row.subject_entity_id
                    else None
                ),
                subject_scene_id=(
                    scene_map[row.subject_scene_id] if row.subject_scene_id else None
                ),
                predicate=row.predicate,
                object_kind=row.object_kind,
                object_entity_id=(
                    mapped_entity(row.object_entity_id)
                    if row.object_entity_id
                    else None
                ),
                object_scene_id=(
                    scene_map[row.object_scene_id] if row.object_scene_id else None
                ),
                source_unit_id=unit_map[row.source_unit_id],
                evidence_start=row.evidence_start,
                evidence_end=row.evidence_end,
                confidence=row.confidence,
                provenance=row.provenance,
                prompt_version=row.prompt_version,
                model_id=row.model_id,
            )
        )
        result.assertions_carried += 1
    session.flush()

    for attribute in session.scalars(
        select(EntityAttribute)
        .join(Entity, EntityAttribute.entity_id == Entity.id)
        .where(Entity.script_id == old.id, EntityAttribute.active.is_(True))
    ):
        if attribute.source_unit_id not in unit_map:
            continue
        session.add(
            EntityAttribute(
                entity_id=mapped_entity(attribute.entity_id),
                key=attribute.key,
                value=attribute.value,
                source_unit_id=unit_map[attribute.source_unit_id],
                evidence_start=attribute.evidence_start,
                evidence_end=attribute.evidence_end,
                confidence=attribute.confidence,
                provenance=attribute.provenance,
            )
        )
        result.attributes_carried += 1
    session.flush()
    return entity_map


def _link_units(session, old_scene_id, new_scene_id) -> None:
    """Unit lineage inside a modified scene: untouched lines keep their link.

    The graph is not carried here (the scene re-extracts), but the lineage
    lets a later pass tell an edited line from an untouched one.
    """
    old_units = _units_of(session, _uuid(old_scene_id))
    new_units = _units_of(session, _uuid(new_scene_id))
    mapping = align_units(
        [unit.current_text for unit in old_units],
        [unit.current_text for unit in new_units],
    )
    for index, source in enumerate(mapping):
        if source is not None:
            new_units[index].predecessor_unit_id = old_units[source].id


def _carry_speakers(session, unit_map: dict, entity_map: dict) -> None:
    """Remap resolved speakers on carried units."""
    if not unit_map:
        return
    for old_unit_id, new_unit_id in unit_map.items():
        old_unit = session.get(ScriptUnit, old_unit_id)
        if old_unit.speaker_entity_id and old_unit.speaker_entity_id in entity_map:
            session.get(ScriptUnit, new_unit_id).speaker_entity_id = entity_map[
                old_unit.speaker_entity_id
            ]
    session.flush()


def _carry_cache(session, script: Script, scene_map: dict) -> int:
    """Copy completed extraction-cache rows for carried scenes.

    Identical content hashes to an identical input_hash, so the copied row is
    the cache transferring with the content: a later whole-script build sees
    the scene as already extracted and bills nothing. Scenes whose
    predecessor was seeded rather than extracted have no row to copy.
    """
    rows = [
        row
        for row in session.scalars(
            select(SceneExtraction).where(
                SceneExtraction.scene_id.in_(list(scene_map)),
                SceneExtraction.status == "completed",
            )
        )
    ]
    if not rows:
        return 0
    now = datetime.now(UTC)
    run = ExtractionRun(
        script_id=script.id,
        status="ready",
        prompt_version=rows[0].prompt_version,
        model_id=rows[0].model_id,
        total_scenes=len(rows),
        completed_scenes=len(rows),
        started_at=now,
        completed_at=now,
    )
    session.add(run)
    session.flush()
    copied = 0
    seen: set = set()
    for row in rows:
        key = (row.scene_id, row.input_hash, row.prompt_version, row.model_id)
        if key in seen:
            continue
        seen.add(key)
        session.add(
            SceneExtraction(
                extraction_run_id=run.id,
                scene_id=scene_map[row.scene_id],
                status="completed",
                attempt_count=row.attempt_count,
                input_hash=row.input_hash,
                prompt_version=row.prompt_version,
                model_id=row.model_id,
                started_at=row.started_at,
                completed_at=row.completed_at,
            )
        )
        copied += 1
    session.flush()
    return copied


def draft_candidates(session, script: Script) -> list[Script]:
    """Scripts this upload could be a new draft of.

    A candidate shares the title (case-insensitive), is a different script,
    has no successor already, and has a graph to carry. The title is an
    invitation to ask the user, never a link by itself.
    """
    successors = set(
        session.scalars(
            select(Script.predecessor_script_id).where(
                Script.predecessor_script_id.isnot(None)
            )
        )
    )
    return [
        candidate
        for candidate in session.scalars(
            select(Script).where(Script.id != script.id)
        )
        if candidate.title.casefold() == script.title.casefold()
        and candidate.id not in successors
        and candidate.graph_status in ("ready", "partially_ready")
    ]


def _contents(session, script_id) -> list[SceneContent]:
    scenes = session.scalars(
        select(Scene)
        .where(Scene.script_id == script_id, Scene.omitted.is_(False))
        .order_by(Scene.sequence_index)
    )
    result = []
    for scene in scenes:
        texts = tuple(
            unit.current_text
            for unit in _units_of(session, scene.id)
            if unit.unit_type != "scene_heading"
        )
        result.append(
            SceneContent(
                scene_id=str(scene.id),
                number=scene.display_scene_number,
                heading=scene.heading,
                unit_texts=texts,
            )
        )
    return result


def _units_of(session, scene_id) -> list[ScriptUnit]:
    return list(
        session.scalars(
            select(ScriptUnit)
            .where(ScriptUnit.scene_id == scene_id)
            .order_by(ScriptUnit.sequence_index)
        )
    )


def _in_chain(session, start: Script, target_id) -> bool:
    """Whether target appears in start's predecessor chain."""
    current = start
    seen = set()
    while current is not None and current.predecessor_script_id is not None:
        if current.predecessor_script_id == target_id:
            return True
        if current.predecessor_script_id in seen:
            return False
        seen.add(current.predecessor_script_id)
        current = session.get(Script, current.predecessor_script_id)
    return False


def _uuid(value):
    import uuid as uuid_module

    return value if not isinstance(value, str) else uuid_module.UUID(value)
