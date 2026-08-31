"""The cross-draft ripple report.

After a draft links and its changed scenes extract, this report answers the
question the link exists to answer: what does this revision change? It is
built in two layers. The deterministic layer compares the two drafts' graphs
through entity lineage: entities added and removed, attribute values that
moved, and introductions that vanished while their dependants survive. The
judged layer runs the continuity judge once per changed scene over the same
evidence packet the ripple preview uses; it is advisory, so a failed call
costs a note, never the report.

The report anchors on a `link_draft` change set carrying no operations:
the RippleReport row puts it on the Reports page, findings put the
conflicts on the Findings page with Review and Dismiss, and the audit rows
attach to it like any judged preview's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from traceact import ActionTrace

from ripple.db.models import (
    Assertion,
    ChangeSet,
    ContinuityFinding,
    Entity,
    EntityAlias,
    EntityAttribute,
    FindingEvidence,
    ModelCall,
    RippleReport,
    Scene,
    SceneExtraction,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize
from ripple.extraction.continuity_judge import (
    CONTINUITY_PROMPT_VERSION as CONTINUITY_VERSION,
)
from ripple.graph.continuity import USE_PREDICATES, retrieve
from ripple.services import preview as preview_service
from ripple.services import renames
from ripple.tracing import ensure_configured

logger = logging.getLogger(__name__)


@dataclass
class DraftReport:
    """What comparing the drafts found."""

    change_set_id: str
    summary: str
    severity: str
    entities_added: list[str] = field(default_factory=list)
    entities_removed: list[str] = field(default_factory=list)
    attribute_changes: list[dict] = field(default_factory=list)
    lost_introductions: list[str] = field(default_factory=list)
    conflicts: int = 0
    continuity_notes: list[str] = field(default_factory=list)
    renames_applied: list[str] = field(default_factory=list)
    renames_suggested: int = 0
    already_existed: bool = False


class ReportRefused(Exception):
    """The report cannot be built; the message says why."""


def build_draft_report(
    session, script_id, provider=None, model_id: str | None = None
) -> DraftReport:
    """Build (or return) the report comparing a linked draft to its predecessor.

    Idempotent: a report already built for this draft comes back as it was.
    `provider` and `model_id` are optional; without them the judged layer is
    skipped and the report says so.
    """
    ensure_configured()
    with ActionTrace.start(action="draft.report", kind="change") as trace:
        trace.input({"script_id": str(script_id)})
        script = session.get(Script, script_id)
        if script is None:
            raise ValueError(f"no script with id {script_id}")
        old = (
            session.get(Script, script.predecessor_script_id)
            if script.predecessor_script_id
            else None
        )
        if old is None:
            raise ReportRefused(
                "This script is not linked to a predecessor draft."
            )

        existing = session.scalar(
            select(ChangeSet)
            .where(
                ChangeSet.script_id == script.id, ChangeSet.kind == "link_draft"
            )
            .order_by(ChangeSet.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            report_row = session.scalar(
                select(RippleReport).where(
                    RippleReport.change_set_id == existing.id
                )
            )
            trace.step("Returned the existing report")
            return DraftReport(
                change_set_id=str(existing.id),
                summary=report_row.summary if report_row else "",
                severity=report_row.severity if report_row else "low",
                already_existed=True,
            )

        pending = _unextracted_changed_scenes(session, script)
        if pending:
            raise ReportRefused(
                f"{pending} changed scene(s) are not extracted yet, so the "
                "comparison would mistake missing extraction for missing "
                "content. Build the graph, then request the report."
            )

        pairs, added, removed = _resolve_entities(session, script, old)

        # Rename detection runs on the added cast before anything else is
        # judged: an applied rename joins two identities, and the attribute
        # and introduction comparisons should see the joined one.
        candidates = renames.detect_renames(session, script, old, added)
        auto_applied: list[tuple] = []
        for candidate in candidates:
            if candidate.automatic:
                survivor = renames.apply_rename(session, script, candidate)
                auto_applied.append((candidate, survivor))
        if auto_applied:
            pairs, added, removed = _resolve_entities(session, script, old)

        attribute_changes = _attribute_changes(session, pairs)
        lost = _lost_introductions(session, script, old, pairs)

        anchor = ChangeSet(
            script_id=script.id,
            kind="link_draft",
            status="accepted",
            base_script_version=script.current_version,
        )
        session.add(anchor)
        session.flush()

        report = DraftReport(change_set_id=str(anchor.id), summary="", severity="low")
        report.entities_added = [entity.canonical_name for entity in added]
        report.entities_removed = [label for label, _ in removed]
        report.attribute_changes = attribute_changes
        report.lost_introductions = [message for message, _, _ in lost]

        for message, _entity_label, use_unit_ids in lost:
            finding = ContinuityFinding(
                change_set_id=anchor.id,
                finding_type="lost_introduction",
                severity="high",
                message=message,
                status="open",
            )
            session.add(finding)
            session.flush()
            for rank, unit_id in enumerate(use_unit_ids[:8]):
                session.add(
                    FindingEvidence(
                        finding_id=finding.id,
                        script_unit_id=unit_id,
                        rank=rank,
                        match_reason="later_reference",
                    )
                )
        for label, scenes in removed:
            if not scenes:
                continue
            finding = ContinuityFinding(
                change_set_id=anchor.id,
                finding_type="entity_removed",
                severity="medium",
                message=(
                    f"{label} is gone in this draft; the previous draft used "
                    f"it in scene(s) {', '.join(scenes)}."
                ),
                status="open",
            )
            session.add(finding)
        session.flush()

        for candidate, survivor in auto_applied:
            report.renames_applied.append(
                f"{candidate.old_name} to {survivor.canonical_name}"
            )
            renames.file_partial_finding(
                session, script, anchor.id, survivor, candidate.old_name
            )
        for candidate in candidates:
            if not candidate.automatic:
                renames.file_suggestion(session, anchor.id, candidate)
                report.renames_suggested += 1

        if provider is not None and model_id:
            report.conflicts, report.continuity_notes = _judge_changed_scenes(
                session, script, old, anchor, provider, model_id
            )
        else:
            report.continuity_notes = [
                "No model is selected, so the judged continuity pass did "
                "not run; the deterministic comparison above is complete."
            ]

        report.severity = (
            "high"
            if lost or report.conflicts
            else (
                "medium"
                if removed
                or attribute_changes
                or report.renames_applied
                or report.renames_suggested
                else "low"
            )
        )
        report.summary = _summary(session, script, report)
        session.add(
            RippleReport(
                change_set_id=anchor.id,
                summary=report.summary,
                severity=report.severity,
                model_id=model_id,
                prompt_version=CONTINUITY_VERSION if model_id else None,
            )
        )
        session.flush()
        trace.step(
            f"{len(report.entities_removed)} removed, "
            f"{len(report.lost_introductions)} lost introductions, "
            f"{report.conflicts} conflicts"
        )
        trace.output({"change_set": str(anchor.id), "severity": report.severity})
        return report


def _unextracted_changed_scenes(session, script: Script) -> int:
    """Changed scenes with no completed extraction.

    Their absence from the graph says nothing about the text, so the report
    refuses rather than reading it as removal.
    """
    count = 0
    for scene in session.scalars(
        select(Scene).where(
            Scene.script_id == script.id,
            Scene.lineage_kind.in_(["modified", "inserted"]),
            Scene.omitted.is_(False),
        )
    ):
        done = session.scalar(
            select(SceneExtraction.id)
            .where(
                SceneExtraction.scene_id == scene.id,
                SceneExtraction.status == "completed",
            )
            .limit(1)
        )
        if done is None:
            count += 1
    return count


def _resolve_entities(session, script: Script, old: Script):
    """Pair the drafts' entities and complete the lineage while doing it.

    Carried entities already point at their predecessor. Extracted entities
    are resolved by normalized name and type, then by the predecessor's
    aliases; a resolution writes `predecessor_entity_id`, so the lineage
    ends complete for renames and later drafts to build on.
    """
    old_entities = list(
        session.scalars(select(Entity).where(Entity.script_id == old.id))
    )
    by_name = {
        (entity.entity_type, entity.normalized_name): entity
        for entity in old_entities
    }
    by_alias: dict = {}
    for alias in session.scalars(
        select(EntityAlias)
        .join(Entity, EntityAlias.entity_id == Entity.id)
        .where(Entity.script_id == old.id)
    ):
        owner = session.get(Entity, alias.entity_id)
        by_alias.setdefault(
            (owner.entity_type, alias.normalized_alias), owner
        )

    pairs: list[tuple[Entity, Entity]] = []
    added: list[Entity] = []
    matched_old: set = set()
    for entity in session.scalars(
        select(Entity).where(Entity.script_id == script.id)
    ):
        source = None
        if entity.predecessor_entity_id:
            source = session.get(Entity, entity.predecessor_entity_id)
        if source is None:
            source = by_name.get((entity.entity_type, entity.normalized_name))
        if source is None:
            source = by_alias.get(
                (entity.entity_type, normalize(entity.canonical_name))
            )
        if source is None:
            added.append(entity)
            continue
        if entity.predecessor_entity_id is None:
            entity.predecessor_entity_id = source.id
        pairs.append((source, entity))
        matched_old.add(source.id)
    session.flush()

    removed: list[tuple[str, list[str]]] = []
    for entity in old_entities:
        if entity.id in matched_old:
            continue
        scenes = _scenes_using(session, old, entity)
        if scenes or _has_active_facts(session, entity):
            removed.append((entity.canonical_name, scenes))
    return pairs, added, removed


def _has_active_facts(session, entity: Entity) -> bool:
    return (
        session.scalar(
            select(Assertion.id)
            .where(
                Assertion.active.is_(True),
                (Assertion.subject_entity_id == entity.id)
                | (Assertion.object_entity_id == entity.id),
            )
            .limit(1)
        )
        is not None
    )


def _scenes_using(session, script: Script, entity: Entity) -> list[str]:
    """Display numbers of the scenes whose lines cite this entity."""
    unit_ids = session.scalars(
        select(Assertion.source_unit_id).where(
            Assertion.active.is_(True),
            (Assertion.subject_entity_id == entity.id)
            | (Assertion.object_entity_id == entity.id),
        )
    )
    numbers = []
    seen = set()
    for unit_id in unit_ids:
        unit = session.get(ScriptUnit, unit_id)
        if unit is None:
            continue
        scene = session.get(Scene, unit.scene_id)
        label = scene.display_scene_number or "—"
        if label not in seen:
            seen.add(label)
            numbers.append(label)
    return numbers


def _attribute_changes(session, pairs) -> list[dict]:
    """Active attribute values that moved between the drafts, by key."""
    changes = []
    for source, entity in pairs:
        old_values = {
            attribute.key: attribute.value
            for attribute in session.scalars(
                select(EntityAttribute).where(
                    EntityAttribute.entity_id == source.id,
                    EntityAttribute.active.is_(True),
                )
            )
        }
        new_values = {
            attribute.key: attribute.value
            for attribute in session.scalars(
                select(EntityAttribute).where(
                    EntityAttribute.entity_id == entity.id,
                    EntityAttribute.active.is_(True),
                )
            )
        }
        for key, before in old_values.items():
            after = new_values.get(key)
            if after is not None and after != before:
                changes.append(
                    {
                        "entity": entity.canonical_name,
                        "key": key,
                        "before": before,
                        "after": after,
                    }
                )
    return changes


def _lost_introductions(session, script: Script, old: Script, pairs):
    """Entities whose introduction vanished while their uses survive.

    Judged on the new draft's own graph: the predecessor established the
    entity, this draft still uses it, and no scene here establishes it.
    The deleted or rewritten scene that used to introduce it is named when
    lineage knows it.
    """
    lost = []
    for source, entity in pairs:
        old_intro = session.scalar(
            select(Assertion).where(
                Assertion.script_id == old.id,
                Assertion.predicate == "establishes",
                Assertion.object_entity_id == source.id,
                Assertion.active.is_(True),
            )
        )
        if old_intro is None:
            continue
        new_intro = session.scalar(
            select(Assertion.id)
            .where(
                Assertion.script_id == script.id,
                Assertion.predicate == "establishes",
                Assertion.object_entity_id == entity.id,
                Assertion.active.is_(True),
            )
            .limit(1)
        )
        if new_intro is not None:
            continue
        uses = list(
            session.scalars(
                select(Assertion).where(
                    Assertion.script_id == script.id,
                    Assertion.active.is_(True),
                    Assertion.predicate.in_(USE_PREDICATES),
                    (Assertion.subject_entity_id == entity.id)
                    | (Assertion.object_entity_id == entity.id),
                )
            )
        )
        if not uses:
            continue
        intro_scene = (
            session.get(Scene, old_intro.subject_scene_id)
            if old_intro.subject_scene_id
            else None
        )
        where = (
            f"scene {intro_scene.display_scene_number}"
            if intro_scene and intro_scene.display_scene_number
            else "a scene"
        )
        use_scenes = _scenes_using(session, script, entity)
        message = (
            f"{entity.canonical_name} lost its introduction: the previous "
            f"draft established it in {where}, this draft still uses it in "
            f"scene(s) {', '.join(use_scenes)}, and no scene here "
            "introduces it."
        )
        lost.append(
            (message, entity.canonical_name, [use.source_unit_id for use in uses])
        )
    return lost


def _judge_changed_scenes(
    session, script: Script, old: Script, anchor: ChangeSet, provider, model_id
):
    """One continuity judgement per changed scene, advisory.

    The edit payload is the scene's line-level before and after, from unit
    lineage; the packet is the same earlier-and-later evidence retrieval
    the ripple preview uses. Conflicts persist as findings on the report's
    change set. Failures become notes, never a failed report.
    """
    conflicts_total = 0
    notes: list[str] = []
    audit: list[ModelCall] = []
    changed = list(
        session.scalars(
            select(Scene)
            .where(
                Scene.script_id == script.id,
                Scene.lineage_kind.in_(["modified", "inserted"]),
            )
            .order_by(Scene.sequence_index)
        )
    )
    for scene in changed:
        units = list(
            session.scalars(
                select(ScriptUnit)
                .where(ScriptUnit.scene_id == scene.id)
                .order_by(ScriptUnit.sequence_index)
            )
        )
        edits_payload = []
        for unit in units:
            before = ""
            if unit.predecessor_unit_id:
                source = session.get(ScriptUnit, unit.predecessor_unit_id)
                before = source.current_text if source else ""
            if before == unit.current_text:
                continue
            edits_payload.append(
                {
                    "unit_id": str(unit.id),
                    "current_text": before,
                    "proposed_text": unit.current_text,
                }
            )
        if not edits_payload:
            continue

        unit_ids = [unit.id for unit in units]
        affected = _cited_entities(session, unit_ids)
        # The rewrite's continuity question involves what the scene used to
        # state too: the predecessor scene's entities, mapped forward
        # through lineage, join the retrieval.
        if scene.predecessor_scene_id:
            old_unit_ids = list(
                session.scalars(
                    select(ScriptUnit.id).where(
                        ScriptUnit.scene_id == scene.predecessor_scene_id
                    )
                )
            )
            old_cited = _cited_entities(session, old_unit_ids)
            if old_cited:
                affected += list(
                    session.scalars(
                        select(Entity.id).where(
                            Entity.script_id == script.id,
                            Entity.predecessor_entity_id.in_(old_cited),
                        )
                    )
                )
        packet = retrieve(session, script.id, affected, scene.sequence_index)
        if packet.total_items == 0:
            continue
        label = scene.display_scene_number or "?"
        diff_payload = {
            "removed": [],
            "added": [],
            "changed": [f"scene {label} was rewritten in this draft"],
        }
        conflicts, error = preview_service.judge_continuity_payloads(
            session,
            script,
            scene,
            edits_payload,
            diff_payload,
            packet,
            provider,
            model_id,
            audit,
        )
        if error:
            notes.append(f"Scene {label}: {error}")
            continue
        packet_units = {
            item.assertion_id: item.unit_id
            for item in (*packet.earlier, *packet.later)
        }
        for conflict in conflicts:
            finding = ContinuityFinding(
                change_set_id=anchor.id,
                finding_type="continuity_conflict",
                severity=conflict.severity,
                message=f"Scene {label}: {conflict.message}",
                status="open",
            )
            session.add(finding)
            session.flush()
            for rank, assertion_id in enumerate(conflict.evidence_ids):
                unit_id = packet_units.get(assertion_id)
                if unit_id is None:
                    continue
                session.add(
                    FindingEvidence(
                        finding_id=finding.id,
                        script_unit_id=_uuid(unit_id),
                        rank=rank,
                        match_reason="continuity",
                    )
                )
            conflicts_total += 1
    for call in audit:
        call.change_set_id = anchor.id
    session.flush()
    return conflicts_total, notes


def _cited_entities(session, unit_ids: list) -> list:
    """Entity ids on either end of the active assertions citing these units."""
    if not unit_ids:
        return []
    result = []
    for column in (Assertion.subject_entity_id, Assertion.object_entity_id):
        result += [
            entity_id
            for entity_id in session.scalars(
                select(column).where(
                    Assertion.source_unit_id.in_(unit_ids),
                    Assertion.active.is_(True),
                )
            )
            if entity_id
        ]
    return result


def _summary(session, script: Script, report: DraftReport) -> str:
    kinds = {"unchanged": 0, "modified": 0, "inserted": 0}
    for kind in session.scalars(
        select(Scene.lineage_kind).where(Scene.script_id == script.id)
    ):
        if kind in kinds:
            kinds[kind] += 1
    deleted = session.scalar(
        select(Script).where(Script.id == script.predecessor_script_id)
    )
    parts = [
        f"Draft {script.draft_number} against draft "
        f"{deleted.draft_number if deleted else script.draft_number - 1}: "
        f"{kinds['unchanged']} scene(s) unchanged, {kinds['modified']} "
        f"rewritten, {kinds['inserted']} new."
    ]
    if report.entities_added:
        parts.append(
            f"New: {', '.join(report.entities_added[:6])}"
            + ("…" if len(report.entities_added) > 6 else "")
            + "."
        )
    if report.entities_removed:
        parts.append(
            f"Gone: {', '.join(report.entities_removed[:6])}"
            + ("…" if len(report.entities_removed) > 6 else "")
            + "."
        )
    if report.attribute_changes:
        bits = ", ".join(
            f"{change['entity']} {change['key']}: {change['before']} to "
            f"{change['after']}"
            for change in report.attribute_changes[:4]
        )
        parts.append(f"Changed: {bits}.")
    if report.renames_applied:
        parts.append(f"Renamed: {', '.join(report.renames_applied)}.")
    if report.renames_suggested:
        noun = "rename" if report.renames_suggested == 1 else "renames"
        parts.append(
            f"{report.renames_suggested} possible {noun} await confirmation "
            "on the findings page."
        )
    if report.lost_introductions:
        parts.append(
            f"{len(report.lost_introductions)} entit"
            + ("y" if len(report.lost_introductions) == 1 else "ies")
            + " lost their introduction; see the findings."
        )
    if report.conflicts:
        parts.append(f"{report.conflicts} continuity conflict(s) judged.")
    if report.continuity_notes:
        parts.append(report.continuity_notes[0])
    return " ".join(parts)


def _uuid(value):
    import uuid as uuid_module

    return value if not isinstance(value, str) else uuid_module.UUID(value)
