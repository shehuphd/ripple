"""The ripple preview: graph-anchored judgement over a set of unit edits.

The engine hands the model the structure the database
already holds (the edited units, the assertions citing them, the touched
entities' attributes) and receives a verdict per item plus anything new,
one call per affected scene. Application code verifies every verdict,
builds the exact operation list, and computes severity. Nothing here is
persisted when a model call fails: a failed preview is a failure state with
a retry, never an empty result.

Cost properties this module owns:
- One judgement call per affected scene per preview, however many units the
  proposal edits.
- A repeated preview of an identical proposal returns the stored pending
  change set instead of calling the model again.
- Acceptance applies the judged operations; nothing here or downstream
  re-extracts an accepted edit.
"""

from __future__ import annotations

import logging
import time
import uuid as uuid_module
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
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
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize, normalize_key
from ripple.db.repository import (
    clear_model_unavailable,
    get_fallback_model,
    graph_labels,
    mark_model_unavailable,
)
from ripple.extraction.continuity_judge import (
    CONTINUITY_PROMPT_VERSION,
    CONTINUITY_SCHEMA,
    CONTINUITY_SYSTEM,
    ContinuityConflict,
    build_continuity_prompt,
    validate_continuity,
)
from ripple.extraction.judge import (
    JUDGE_PROMPT_VERSION,
    JUDGE_SCHEMA,
    JUDGE_SYSTEM,
    JudgementReport,
    _significant_words,
    build_judge_prompt,
    validate_judgement,
)
from ripple.extraction.validate import MalformedResponse
from ripple.graph.continuity import (
    CastRename,
    EvidencePacket,
    detect_cast_renames,
    detect_orphaned_references,
    retrieve,
)
from ripple.graph.diff import Edge, EdgeRef, GraphDiff, diff_edges, to_operations
from ripple.graph.predicates import SIGNATURES
from ripple.llm.base import (
    AVAILABILITY_CODES,
    GRAPH_SEED,
    GenerationResult,
    LLMProvider,
    ProviderError,
)
from ripple.services import changeset, spend
from ripple.services.synthesizer import deterministic_summary, severity_for
from ripple.tracing import ensure_configured, model_event

logger = logging.getLogger(__name__)

# One retry for a failure that is the provider's moment rather than the
# request's content. A malformed reply is not retried here; the judgement
# validator already survives partial output, and a second identical call to a
# model that produced garbage usually produces garbage again.
RETRYABLE_CODES = {"rate_limited", "provider_unavailable", "timeout", "network_error"}
# Matches extraction's cap. Models that reason before answering bill the
# hidden reasoning to this same budget, and 4096 proved small enough for
# gemini-flash to spend it before finishing a verdict list. Only tokens
# produced are billed, so the headroom costs nothing on models that
# answer within it.
MAX_OUTPUT_TOKENS = 24576


class PreviewRefused(Exception):
    """The preview cannot run, for a reason the user can act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        # The TraceAct trace id for the run that raised, filled in by
        # preview_changes so the error surface can open the trace viewer.
        self.trace_id: str | None = None


class PreviewFailed(Exception):
    """The model call failed. Nothing was persisted; the client may retry."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.trace_id: str | None = None


@dataclass(frozen=True)
class UnitEdit:
    unit_id: str
    proposed_text: str


@dataclass
class AttributeChange:
    """One attribute-level change the judgement found."""

    entity_label: str
    key: str
    before: str | None
    after: str | None
    confidence: float

    def payload(self) -> dict[str, Any]:
        return {
            "entity": self.entity_label,
            "key": self.key,
            "before": self.before,
            "after": self.after,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class PreviewResult:
    """Everything the preview page renders."""

    change_set: ChangeSet
    diff: GraphDiff
    attribute_changes: list[AttributeChange]
    findings: list[Any]
    summary: str
    severity: str
    evidence_count: int
    stages: list[dict[str, Any]]
    edits: list[dict[str, Any]]
    cached: bool = False
    judgement: JudgementReport | None = None
    continuity_error: str | None = None
    trace_id: str | None = None
    # "model" once an explanation has been written for the stored report,
    # "deterministic" until then.
    summary_source: str = "deterministic"


def word_diff(before: str, after: str) -> list[dict[str, str]]:
    """A word-level diff as ordered segments, for <mark> rendering.

    Segments carry `op` of equal, del, or ins. Splitting on spaces keeps the
    join lossless: each word carries its trailing space.
    """
    before_words = before.split(" ")
    after_words = after.split(" ")
    matcher = SequenceMatcher(a=before_words, b=after_words, autojunk=False)
    segments: list[dict[str, str]] = []
    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag in ("equal", "delete") and a_start < a_end:
            segments.append(
                {
                    "op": "equal" if tag == "equal" else "del",
                    "text": " ".join(before_words[a_start:a_end]),
                }
            )
        if tag in ("replace", "insert") and b_start < b_end:
            segments.append({"op": "ins", "text": " ".join(after_words[b_start:b_end])})
        if tag == "replace" and a_start < a_end:
            segments.insert(
                -1 if b_start < b_end else len(segments),
                {"op": "del", "text": " ".join(before_words[a_start:a_end])},
            )
    return segments


def preview_changes(
    session: Session,
    edits: list[UnitEdit],
    provider: LLMProvider | None,
    model_id: str | None,
) -> PreviewResult:
    """Run the preview pipeline over any number of edited units.

    Raises PreviewRefused for a state the user must change first (no edits,
    no graph, no model) and PreviewFailed when the judgement call failed, in
    which case nothing was persisted.
    """
    ensure_configured()
    with ActionTrace.start(action="ripple.preview", kind="change") as trace:
        trace.input(
            {
                "model_id": model_id,
                "edits": [
                    {
                        "unit_id": edit.unit_id,
                        "proposed_text": edit.proposed_text,
                    }
                    for edit in edits
                ],
            }
        )
        audit: list[ModelCall] = []
        try:
            result = _preview(session, edits, provider, model_id, audit)
        except (PreviewFailed, PreviewRefused) as error:
            # The request's transaction is about to unwind, and the audit rows
            # for calls already made and billed would unwind with it. Only they
            # survive: a failed preview persists nothing else.
            _persist_audit(session, audit)
            # The trace records what led up to the failure, so the error
            # response carries its id and the UI can open it in the viewer.
            # With tracing off, start() yields a no-op with no id.
            error.trace_id = getattr(trace, "trace_id", None)
            raise
        # The same trace id the failure path carries, on the success path too,
        # so the inline judgement card can open this run in the viewer.
        result.trace_id = getattr(trace, "trace_id", None)
        trace.output(
            {
                "change_set_id": str(result.change_set.id),
                "cached": result.cached,
                "operations": result.diff.operation_count,
                "attribute_changes": len(result.attribute_changes),
                "findings": len(result.findings),
                "severity": result.severity,
            }
        )
        return result


def _persist_audit(session: Session, calls: list[ModelCall]) -> None:
    """Commit what a failing preview has written, before the rollback.

    At every point the preview can fail, the session's pending writes are the
    model-call audit rows and the model-availability marks, and both must
    survive the failure: without this commit, the request's rollback erases
    the record of calls already made and billed. The proposal, its findings,
    and its report cannot be caught by this, because they are created only
    after every scene is judged and nothing after that point raises.
    """
    if not calls:
        return
    try:
        session.commit()
    except Exception:  # losing audit rows must not mask the original failure
        logger.exception("could not persist %d audit row(s)", len(calls))


def _preview(
    session: Session,
    edits: list[UnitEdit],
    provider: LLMProvider | None,
    model_id: str | None,
    audit: list[ModelCall],
) -> PreviewResult:
    started = time.perf_counter()
    stages: list[dict[str, Any]] = []

    def stage(name: str, mark: float) -> None:
        stages.append({"name": name, "seconds": round(time.perf_counter() - mark, 2)})

    mark = time.perf_counter()
    units, script = _resolve_units(session, edits)
    live_edits = [
        (unit, proposed) for unit, proposed in units if unit.current_text != proposed
    ]
    if not live_edits:
        raise PreviewRefused(
            "no_change",
            "Nothing differs from the accepted text yet. Edit a line first.",
        )

    _require_baseline(session, script)

    cached = _find_cached(session, script, live_edits)
    if cached is not None:
        return _rebuild(session, script, cached, live_edits, started)

    by_scene: dict[Any, list[tuple[ScriptUnit, str]]] = {}
    for unit, proposed in live_edits:
        by_scene.setdefault(unit.scene_id, []).append((unit, proposed))
    stage("Assemble context", mark)

    if provider is None or not model_id:
        raise PreviewRefused(
            "no_model",
            "No model is selected, so the edit cannot be judged against the "
            "graph. Pick one in Settings.",
        )

    mark = time.perf_counter()
    accepted: list[Edge] = []
    proposed_edges: list[Edge] = []
    attribute_changes: list[AttributeChange] = []
    operations: list[dict[str, Any]] = []
    judgements: list[JudgementReport] = []
    labels = graph_labels(session, script.id)

    used_models: set[str] = set()
    listed_total = 0
    for scene_id, scene_edits in sorted(by_scene.items(), key=lambda kv: str(kv[0])):
        scene = session.get(Scene, scene_id)
        listed_assertions, listed_attributes = _listed(session, scene_edits)
        listed_total += len(listed_assertions) + len(listed_attributes)
        judgement, model_used = _judge_scene(
            session,
            script,
            scene,
            scene_edits,
            listed_assertions,
            listed_attributes,
            provider,
            model_id,
            audit,
        )
        used_models.add(model_used)
        judgements.append(judgement)
        scene_accepted, scene_proposed = _edges_from_verdicts(
            session, scene, judgement, listed_assertions, labels
        )
        accepted.extend(scene_accepted)
        proposed_edges.extend(scene_proposed)
        attribute_changes.extend(
            _attribute_changes(
                session, judgement, listed_attributes, operations, labels
            )
        )
    stage("Judge against the graph", mark)

    mark = time.perf_counter()
    diff = diff_edges(accepted, proposed_edges)
    operations = [*to_operations(diff), *operations]
    stage(f"Diff against base v{script.current_version}", mark)

    mark = time.perf_counter()
    accepted_rows = [
        row
        for listed in ([_row(session, i) for i in _all_listed_ids(judgements)],)
        for row in listed
        if row is not None
    ]
    removed_ids = {edge.assertion_id for edge in diff.removed if edge.assertion_id}
    removed_establishes = [
        (row.object_entity_id, labels.get(row.object_entity_id, "?"), row.id)
        for row in accepted_rows
        if row.predicate == "establishes"
        and row.object_entity_id
        and str(row.id) in removed_ids
    ]
    orphans = detect_orphaned_references(
        session, script.id, removed_establishes, removed_ids
    )
    # A character-cue rename changes no stored fact, so nothing above catches
    # it. Detected here, deterministically, so an edit that renames a
    # character is never reported as no change.
    renames = detect_cast_renames(session, script.id, live_edits)
    orphans = [*orphans, *renames]
    affected = [
        row.object_entity_id or row.subject_entity_id
        for row in accepted_rows
        if row.object_entity_id or row.subject_entity_id
    ]
    # The earliest edited scene in script order, not whichever edit came
    # first in the request: retrieval's earlier/later split hangs on it.
    first_scene = min(
        (session.get(Scene, scene_id) for scene_id in by_scene),
        key=lambda scene: scene.sequence_index,
    )
    packet = retrieve(session, script.id, affected, first_scene.sequence_index)
    stage("Continuity sweep", mark)

    mark = time.perf_counter()
    conflicts, continuity_error = _judge_continuity(
        session,
        script,
        first_scene,
        live_edits,
        diff,
        packet,
        provider,
        model_id,
        audit,
    )
    stage("Judge continuity", mark)

    # Severity stays computed in code from the diff and the deterministic
    # findings, so it cannot vary between runs; the model's conflicts carry
    # their own severities on their own cards.
    severity = severity_for(diff, orphans)
    if attribute_changes and severity in ("none", "low"):
        # A changed attribute is a department deliverable changing, which is
        # never a non-event even when no edge moved.
        severity = "medium"
    if renames and severity in ("none", "low"):
        # Renaming a character is never a non-event, even a sole-occurrence
        # rename that leaves the graph with no split to reconcile.
        severity = "medium"
    summary = deterministic_summary(diff, orphans)
    # An attribute change IS the graph change; "No graph change." beside it
    # reads as a contradiction.
    if attribute_changes and summary.startswith("No graph change."):
        summary = summary.removeprefix("No graph change.").strip()
    # "No graph change." alone reads as "this edit has no impact", which is a
    # wider claim than the engine makes. Say what was judged; and when the
    # edited lines support nothing stored, say that, because an absence in
    # the graph is not an absence of impact.
    if not diff.operation_count and not orphans and not attribute_changes:
        if listed_total:
            summary = (
                f"All {listed_total} stored fact(s) the edited lines support "
                "still hold. No graph change."
            )
        else:
            summary = (
                "The graph holds nothing extracted from the edited lines, so "
                "there was nothing to judge this edit against. If the line "
                "names things the graph should track, the graph is "
                "incomplete, not the edit unimportant."
            )
    if attribute_changes:
        changed_bits = ", ".join(
            f"{change.entity_label} {change.key}: "
            f"{change.before or '—'} → {change.after or '—'}"
            for change in attribute_changes[:3]
        )
        summary = f"{summary} Attribute change(s): {changed_bits}."

    proposal = changeset.create_multi_proposal(
        session,
        [(unit.id, proposed) for unit, proposed in live_edits],
        operations,
    )
    proposal.severity = severity
    finding_rows: list[tuple[Any, ContinuityFinding]] = []
    for orphan in orphans:
        is_rename = isinstance(orphan, CastRename)
        row = ContinuityFinding(
            change_set_id=proposal.id,
            finding_type="cast_rename" if is_rename else "orphaned_reference",
            # A rename that leaves the old name on other lines is a split
            # identity (high); a sole-occurrence rename is a clean change the
            # graph has yet to catch up with (medium).
            severity=(
                ("high" if orphan.later_unit_ids else "medium")
                if is_rename
                else "high"
            ),
            message=orphan.message,
            status="open",
        )
        session.add(row)
        finding_rows.append((orphan, row))
    conflict_rows: list[tuple[ContinuityConflict, ContinuityFinding]] = []
    for conflict in conflicts:
        row = ContinuityFinding(
            change_set_id=proposal.id,
            finding_type="continuity_conflict",
            severity=conflict.severity,
            message=conflict.message,
            status="open",
        )
        session.add(row)
        conflict_rows.append((conflict, row))
    session.add(
        RippleReport(
            change_set_id=proposal.id,
            summary=summary,
            severity=severity,
            # The model that answered, which the fallback may have been. Two
            # scenes answered by different models fall back to the selection;
            # the per-call truth is in model_calls either way.
            model_id=used_models.pop() if len(used_models) == 1 else model_id,
            prompt_version=JUDGE_PROMPT_VERSION,
        )
    )
    session.flush()
    # This preview's own calls, and no other request's: the tracked list is
    # precise where a newest-unlinked query is not.
    for call in audit:
        call.change_set_id = proposal.id

    # Citations are persisted, so a rebuilt preview and the findings page can
    # still say which units a finding hangs on.
    packet_units = {
        item.assertion_id: (item.unit_id, item.scene_number or "—")
        for item in (*packet.earlier, *packet.later)
    }
    for orphan, row in finding_rows:
        # A rename cites the line it renames first, so Review units opens on
        # the edit itself before walking the lines that still hold the old
        # name; a sole-occurrence rename cites only the edited line.
        cited_units = _ordered_unique(orphan.later_unit_ids)
        reasons = ["later_reference"] * len(cited_units)
        if isinstance(orphan, CastRename):
            cited_units = [orphan.edited_unit_id, *cited_units]
            reasons = ["renamed_line", *reasons]
        for rank, (unit_id, reason) in enumerate(zip(cited_units, reasons, strict=True)):
            session.add(
                FindingEvidence(
                    finding_id=row.id,
                    script_unit_id=uuid_module.UUID(unit_id),
                    rank=rank,
                    match_reason=reason,
                )
            )
    for conflict, row in conflict_rows:
        for rank, assertion_id in enumerate(conflict.evidence_ids):
            unit_id, _ = packet_units[assertion_id]
            session.add(
                FindingEvidence(
                    finding_id=row.id,
                    assertion_id=uuid_module.UUID(assertion_id),
                    script_unit_id=uuid_module.UUID(unit_id),
                    rank=rank,
                    match_reason="model_cited",
                )
            )
    session.flush()

    # The overlay's Dismiss button needs the persisted row's id, and its
    # Review button needs the cited units, which only the in-memory finding
    # carries. The persisted rows are returned with the citations attached,
    # matching the shape the rebuild path returns.
    for orphan, row in finding_rows:
        row.entity_label = orphan.entity_label
        if isinstance(orphan, CastRename):
            row.later_unit_ids = _ordered_unique(
                [orphan.edited_unit_id, *orphan.later_unit_ids]
            )
            row.later_scene_numbers = _ordered_unique(
                [
                    number
                    for number in (
                        orphan.edited_scene_number,
                        *orphan.later_scene_numbers,
                    )
                    if number
                ]
            )
        else:
            row.later_unit_ids = orphan.later_unit_ids
            row.later_scene_numbers = orphan.later_scene_numbers
    for conflict, row in conflict_rows:
        cited = [packet_units[i] for i in conflict.evidence_ids]
        row.later_unit_ids = _ordered_unique([unit for unit, _ in cited])
        row.later_scene_numbers = _ordered_unique([scene for _, scene in cited])

    return PreviewResult(
        change_set=proposal,
        diff=diff,
        attribute_changes=attribute_changes,
        findings=[row for _, row in finding_rows] + [row for _, row in conflict_rows],
        summary=summary,
        severity=severity,
        evidence_count=packet.total_items,
        continuity_error=continuity_error,
        stages=stages,
        edits=[
            {
                "unit_id": str(unit.id),
                "accepted_text": unit.current_text,
                "proposed_text": proposed,
                "segments": word_diff(unit.current_text, proposed),
            }
            for unit, proposed in live_edits
        ],
        judgement=_merge_judgements(judgements),
    )


def _merge_judgements(
    judgements: list[JudgementReport],
) -> JudgementReport | None:
    """One combined report across every affected scene, for display.

    A preview over lines in more than one scene makes one judgement call per
    scene. The inline card shows the verdict tally and what verification
    dropped, so the reports are concatenated rather than shown one scene at a
    time or dropped when there is more than one.
    """
    if not judgements:
        return None
    if len(judgements) == 1:
        return judgements[0]
    merged = JudgementReport()
    for report in judgements:
        merged.assertion_verdicts.extend(report.assertion_verdicts)
        merged.attribute_verdicts.extend(report.attribute_verdicts)
        merged.new_entities.extend(report.new_entities)
        merged.new_assertions.extend(report.new_assertions)
        merged.new_attributes.extend(report.new_attributes)
        merged.rejected.extend(report.rejected)
        merged.coverage_misses.extend(report.coverage_misses)
    return merged


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _resolve_units(
    session: Session, edits: list[UnitEdit]
) -> tuple[list[tuple[ScriptUnit, str]], Script]:
    if not edits:
        raise PreviewRefused("no_change", "No edited lines were sent.")
    units: list[tuple[ScriptUnit, str]] = []
    script: Script | None = None
    for edit in edits:
        try:
            unit = session.get(ScriptUnit, uuid_module.UUID(str(edit.unit_id)))
        except ValueError:
            unit = None
        if unit is None:
            raise PreviewRefused("unknown_unit", "An edited line no longer exists.")
        scene = session.get(Scene, unit.scene_id)
        unit_script = session.get(Script, scene.script_id)
        if script is None:
            script = unit_script
        elif script.id != unit_script.id:
            raise PreviewRefused(
                "cross_script", "One proposal cannot span two scripts."
            )
        units.append((unit, edit.proposed_text))
    return units, script


def _require_baseline(session: Session, script: Script) -> None:
    """Refuse to fake a diff against nothing.

    An empty baseline makes every preview read "no change" or "everything is
    new", both of which look like answers. The graph has to exist first.
    """
    has_graph = session.scalar(
        select(Assertion.id)
        .where(Assertion.script_id == script.id, Assertion.active.is_(True))
        .limit(1)
    )
    if has_graph is None:
        raise PreviewRefused(
            "no_baseline",
            "No graph exists for this script yet, so there is nothing to "
            "judge the edit against. Build the graph first.",
        )


def _find_cached(
    session: Session, script: Script, edits: list[tuple[ScriptUnit, str]]
) -> ChangeSet | None:
    """A pending proposal identical to this one, if one exists.

    Identity is the edited units, their proposed texts, and the base
    versions. A hit means the judgement was already paid for; the stored
    operations, findings, and report are the same answer.
    """
    wanted = {
        (str(unit.id), proposed, unit.current_version) for unit, proposed in edits
    }
    candidates = session.scalars(
        select(ChangeSet)
        .where(
            ChangeSet.script_id == script.id,
            ChangeSet.status == "pending",
            ChangeSet.kind.in_(("edit", "multi_unit_edit")),
            ChangeSet.base_script_version == script.current_version,
        )
        .order_by(ChangeSet.created_at.desc())
    )
    for candidate in candidates:
        stored = {
            (str(link.script_unit_id), link.proposed_text, link.base_unit_version)
            for link in candidate.units
        }
        report = session.scalar(
            select(RippleReport).where(RippleReport.change_set_id == candidate.id)
        )
        if stored == wanted and report is not None:
            return candidate
    return None


def _rebuild(
    session: Session,
    script: Script,
    proposal: ChangeSet,
    edits: list[tuple[ScriptUnit, str]],
    started: float,
) -> PreviewResult:
    """Reassemble a preview from a stored identical proposal, with no call."""
    diff = GraphDiff()
    attribute_changes: list[AttributeChange] = []
    for operation in proposal.operations:
        before = operation.before_json or {}
        after = operation.after_json or {}
        if operation.operation_type == "add_assertion":
            diff.added.append(_edge_from_payload(after))
        elif operation.operation_type == "remove_assertion":
            diff.removed.append(_edge_from_payload(before))
        elif operation.operation_type == "update_assertion":
            diff.changed.append((_edge_from_payload(before), _edge_from_payload(after)))
        elif operation.operation_type == "set_entity_attribute":
            entity_label = after.get("entity_label") or after.get("entity_ref") or ""
            if after.get("entity_id"):
                entity = session.get(Entity, uuid_module.UUID(str(after["entity_id"])))
                if entity is not None:
                    entity_label = entity.canonical_name
            attribute_changes.append(
                AttributeChange(
                    entity_label=entity_label,
                    key=after.get("key", ""),
                    before=(before or {}).get("value"),
                    after=after.get("value"),
                    confidence=after.get("confidence", 0.8),
                )
            )
        elif operation.operation_type == "remove_entity_attribute":
            attribute_changes.append(
                AttributeChange(
                    entity_label=before.get("entity_label", ""),
                    key=before.get("key", ""),
                    before=before.get("value"),
                    after=None,
                    confidence=before.get("confidence", 0.8),
                )
            )
    report = session.scalar(
        select(RippleReport).where(RippleReport.change_set_id == proposal.id)
    )
    findings = list(
        session.scalars(
            select(ContinuityFinding).where(
                ContinuityFinding.change_set_id == proposal.id
            )
        )
    )
    # The stored citations stand in for the in-memory ones the fresh path
    # attaches, so Review works on a rebuilt preview too.
    for finding in findings:
        finding.later_unit_ids = _ordered_unique(
            [
                str(evidence.script_unit_id)
                for evidence in finding.evidence
                if evidence.script_unit_id is not None
            ]
        )
    return PreviewResult(
        change_set=proposal,
        diff=diff,
        attribute_changes=attribute_changes,
        findings=findings,
        summary=report.summary,
        summary_source="model" if report.summary_model_id else "deterministic",
        severity=report.severity or proposal.severity or "none",
        evidence_count=0,
        stages=[
            {
                "name": "Reused the stored preview",
                "seconds": round(time.perf_counter() - started, 2),
            }
        ],
        edits=[
            {
                "unit_id": str(unit.id),
                "accepted_text": unit.current_text,
                "proposed_text": proposed,
                "segments": word_diff(unit.current_text, proposed),
            }
            for unit, proposed in edits
        ],
        cached=True,
    )


def _mentioned_rows(
    session: Session,
    scene_edits: list[tuple[ScriptUnit, str]],
    already: set,
) -> list[Assertion]:
    """Active assertions in the edited scenes about mentioned entities.

    An edit can undo a fact stated on a line it does not touch ("she sets
    the knife down" against a carries edge cited two lines up), so every
    entity the edited lines name, in current or proposed text, brings its
    scene-local assertions to the judgement. The judge holds them unless
    the proposed text explicitly contradicts them, and the validator
    requires the edit to name the entity before a removal is admitted.
    """
    if not scene_edits:
        return []
    scene_ids = {unit.scene_id for unit, _ in scene_edits}
    script_id = session.get(Scene, next(iter(scene_ids))).script_id
    text_words = _significant_words(
        " ".join(f"{unit.current_text} {proposed}" for unit, proposed in scene_edits)
    )
    if not text_words:
        return []
    mentioned = set()
    for entity_id, name in session.execute(
        select(EntityAlias.entity_id, EntityAlias.alias)
        .join(Entity, EntityAlias.entity_id == Entity.id)
        .where(Entity.script_id == script_id)
    ):
        words = _significant_words(name)
        if words and words <= text_words:
            mentioned.add(entity_id)
    for entity in session.execute(
        select(Entity.id, Entity.canonical_name).where(Entity.script_id == script_id)
    ):
        words = _significant_words(entity.canonical_name)
        if words and words <= text_words:
            mentioned.add(entity.id)
    if not mentioned:
        return []
    scene_units = select(ScriptUnit.id).where(ScriptUnit.scene_id.in_(scene_ids))
    query = select(Assertion).where(
        Assertion.active.is_(True),
        Assertion.source_unit_id.in_(scene_units),
        or_(
            Assertion.subject_entity_id.in_(mentioned),
            Assertion.object_entity_id.in_(mentioned),
        ),
    )
    return [row for row in session.scalars(query) if row.id not in already]


def _listed(
    session: Session, scene_edits: list[tuple[ScriptUnit, str]]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """The assertions and attributes the edited units support, as sent,
    widened to the edited scenes' assertions about mentioned entities."""
    unit_ids = [unit.id for unit, _ in scene_edits]
    texts = {str(unit.id): unit.current_text for unit, _ in scene_edits}

    listed_assertions: dict[str, dict[str, Any]] = {}
    rows = list(
        session.scalars(
            select(Assertion).where(
                Assertion.source_unit_id.in_(unit_ids), Assertion.active.is_(True)
            )
        )
    )
    rows.extend(_mentioned_rows(session, scene_edits, {row.id for row in rows}))
    labels = {}
    if rows:
        script_id = rows[0].script_id
        labels = graph_labels(session, script_id)
    alias_names: dict = {}
    alias_entity_ids = {
        endpoint
        for row in rows
        for endpoint in (row.subject_entity_id, row.object_entity_id)
        if endpoint
    }
    if alias_entity_ids:
        for alias in session.scalars(
            select(EntityAlias).where(EntityAlias.entity_id.in_(alias_entity_ids))
        ):
            alias_names.setdefault(alias.entity_id, []).append(alias.alias)
    for row in rows:
        subject = row.subject_entity_id or row.subject_scene_id
        obj = row.object_entity_id or row.object_scene_id
        evidence = ""
        if row.evidence_start is not None and row.evidence_end is not None:
            text = texts.get(str(row.source_unit_id), "")
            evidence = text[row.evidence_start : row.evidence_end]
        listed_assertions[str(row.id)] = {
            "id": str(row.id),
            "subject": labels.get(subject, "?"),
            "predicate": row.predicate,
            "object": labels.get(obj, "?"),
            # Aliases and entity ids feed the removal-visibility guard: an
            # entity is named by any of its names, and a vanished word that
            # is a stored attribute value marks a descriptor change.
            "subject_entity_id": (
                str(row.subject_entity_id) if row.subject_entity_id else None
            ),
            "object_entity_id": (
                str(row.object_entity_id) if row.object_entity_id else None
            ),
            "subject_names": alias_names.get(row.subject_entity_id, []),
            "object_names": alias_names.get(row.object_entity_id, []),
            "source_unit_id": str(row.source_unit_id),
            "evidence": evidence,
            "confidence": row.confidence,
        }

    entity_ids = {
        endpoint
        for row in rows
        for endpoint in (row.subject_entity_id, row.object_entity_id)
        if endpoint
    }
    listed_attributes: dict[str, dict[str, Any]] = {}
    if entity_ids:
        for attribute in session.scalars(
            select(EntityAttribute).where(
                EntityAttribute.entity_id.in_(entity_ids),
                EntityAttribute.active.is_(True),
            )
        ):
            entity = session.get(Entity, attribute.entity_id)
            evidence = ""
            if (
                attribute.source_unit_id is not None
                and str(attribute.source_unit_id) in texts
                and attribute.evidence_start is not None
                and attribute.evidence_end is not None
            ):
                evidence = texts[str(attribute.source_unit_id)][
                    attribute.evidence_start : attribute.evidence_end
                ]
            listed_attributes[str(attribute.id)] = {
                "id": str(attribute.id),
                "entity": entity.canonical_name,
                "entity_id": str(attribute.entity_id),
                "key": attribute.key,
                "value": attribute.value,
                "source_unit_id": (
                    str(attribute.source_unit_id) if attribute.source_unit_id else None
                ),
                "evidence": evidence,
                "confidence": attribute.confidence,
            }
    return listed_assertions, listed_attributes


def _judge_scene(
    session: Session,
    script: Script,
    scene: Scene,
    scene_edits: list[tuple[ScriptUnit, str]],
    listed_assertions: dict[str, dict[str, Any]],
    listed_attributes: dict[str, dict[str, Any]],
    provider: LLMProvider,
    model_id: str,
    audit: list[ModelCall],
) -> tuple[JudgementReport, str]:
    """One judgement call, recorded in the audit table whatever happens.

    Returns the verified judgement and the model that answered: when the
    main model refuses for an availability reason and a fallback is set,
    the fallback answers and the audit shows both attempts.
    """
    edits_payload = [
        {
            "unit_id": str(unit.id),
            "unit_type": unit.unit_type,
            "current_text": unit.current_text,
            "proposed_text": proposed,
        }
        for unit, proposed in scene_edits
    ]
    prompt = build_judge_prompt(
        scene.heading,
        scene.display_scene_number,
        edits_payload,
        list(listed_assertions.values()),
        list(listed_attributes.values()),
    )
    proposed_texts = {str(unit.id): proposed for unit, proposed in scene_edits}
    current_texts = {str(unit.id): unit.current_text for unit, _ in scene_edits}

    try:
        call, result = _generate_verdicts(
            session, script, scene, prompt, provider, model_id, audit
        )
    except PreviewFailed as error:
        _, fallback = get_fallback_model(session)
        if (
            fallback is None
            or fallback == model_id
            or error.code not in AVAILABILITY_CODES
        ):
            raise
        logger.info("judge failing over to %s after %s", fallback, error.code)
        call, result = _generate_verdicts(
            session, script, scene, prompt, provider, fallback, audit
        )

    if result.truncated:
        call.outcome = "truncated"
        session.add(call)
        session.flush()
        raise PreviewFailed(
            "output_truncated",
            _truncation_message(call.model_id, result)
            + " No verdict from a cut-off reply can be trusted, so nothing "
            "was applied. Try again (a retry often completes), shorten the "
            "edit, or pick a model with a larger output limit in Settings.",
        )

    try:
        judgement = validate_judgement(
            result.text,
            listed_assertions,
            listed_attributes,
            proposed_texts,
            current_texts,
        )
    except MalformedResponse as error:
        call.outcome = "malformed"
        call.error_message = str(error)
        session.add(call)
        session.flush()
        raise PreviewFailed("malformed_response", str(error)) from error

    if judgement.coverage_misses:
        call.outcome = "incomplete"
        call.error_message = "No verdict for: " + ", ".join(
            judgement.coverage_misses[:5]
        )
        session.add(call)
        session.flush()
        raise PreviewFailed(
            "incomplete_judgement",
            f"The model's reply skipped {len(judgement.coverage_misses)} "
            "listed item(s), so treating them as unchanged would be a guess. "
            "Run the preview again.",
        )

    call.validation_json = judgement.summary()
    session.add(call)
    session.flush()
    return judgement, call.model_id


def _truncation_message(model_id: str, result: Any) -> str:
    """Say which model stopped, where, and against which requested limit.

    The bare fact ("the reply was cut off") gives the user nothing to act
    on, so the counts are spelled out. On models that reason before
    answering, the hidden reasoning is billed against the same output
    budget, so the visible answer can stop well short of the requested
    cap; a reported reasoning count makes that the stated cause, and an
    unreported one leaves it named as the likely cause rather than
    mis-blaming the model's own limit.
    """
    produced = result.output_tokens or 0
    reasoning = getattr(result, "reasoning_tokens", None) or 0
    reason = result.finish_reason or "length"
    if reasoning:
        where = (
            f"after {produced} answer tokens plus {reasoning} hidden "
            f"reasoning tokens against the {MAX_OUTPUT_TOKENS} requested"
        )
    elif produced:
        where = (
            f"after {produced} of the {MAX_OUTPUT_TOKENS} "
            "output tokens Ripple requested"
        )
    else:
        where = f"before the {MAX_OUTPUT_TOKENS} output tokens Ripple requested"
    message = f"{model_id} stopped mid-reply {where} " f"(finish reason: {reason})."
    if reasoning or produced < MAX_OUTPUT_TOKENS:
        message += (
            " Models that reason before answering bill the hidden "
            "reasoning to the same output budget, so the cap can run out "
            "before the answer finishes."
        )
    return message


def _generate_verdicts(
    session: Session,
    script: Script,
    scene: Scene,
    prompt: str,
    provider: LLMProvider,
    model_id: str,
    audit: list[ModelCall],
    *,
    purpose: str = "judge",
    prompt_version: str = JUDGE_PROMPT_VERSION,
    system: str = JUDGE_SYSTEM,
    schema: dict[str, Any] = JUDGE_SCHEMA,
) -> tuple[ModelCall, Any]:
    """One recorded generation against one model, with the single retry.

    The defaults are the per-scene judgement; the continuity pass runs the
    same machinery under its own purpose, prompt, and schema.
    """
    call = ModelCall(
        script_id=script.id,
        scene_id=scene.id,
        purpose=purpose,
        prompt_version=prompt_version,
        model_id=model_id,
        request_text=prompt,
        outcome="ok",
    )
    audit.append(call)

    # A retried preview must not re-bill a scene already judged. An identical
    # earlier call (same model, same prompt, and the prompt carries the edits
    # and every listed item) is replayed from its recorded reply at no cost.
    # The graph it was judged against is unchanged, because a failed preview
    # persists nothing, and an accepted one changes the units' text and with
    # it the prompt.
    previous = session.scalar(
        select(ModelCall)
        .where(
            ModelCall.purpose == purpose,
            ModelCall.scene_id == scene.id,
            ModelCall.model_id == model_id,
            ModelCall.outcome == "ok",
            ModelCall.request_text == prompt,
            ModelCall.response_text.is_not(None),
        )
        .order_by(ModelCall.created_at.desc())
        .limit(1)
    )
    if previous is not None:
        call.outcome = "cached"
        call.response_text = previous.response_text
        call.input_tokens = 0
        call.output_tokens = 0
        call.duration_ms = 0
        return call, GenerationResult(
            text=previous.response_text,
            model_id=model_id,
            provider=provider.name,
            finish_reason="stop",
        )

    try:
        spend.check_budget(session, call)
    except spend.BudgetExceeded as error:
        raise PreviewRefused(error.code, error.message) from error

    started = time.perf_counter()
    attempts = 0
    while True:
        attempts += 1
        try:
            result = provider.generate(
                model_id,
                prompt,
                system=system,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                json_schema=schema,
                seed=GRAPH_SEED,
            )
            break
        except ProviderError as error:
            if error.code in RETRYABLE_CODES and attempts == 1:
                logger.info("%s retrying after %s", purpose, error.code)
                # The cap may have been crossed since the first check: an
                # earlier scene in this same preview records its tokens
                # between attempts.
                try:
                    spend.check_budget(session, call)
                except spend.BudgetExceeded as budget_error:
                    raise PreviewRefused(
                        budget_error.code, budget_error.message
                    ) from budget_error
                continue
            spend.record_failure(session, call, error, started)
            if error.code == "model_not_available":
                mark_model_unavailable(session, provider.name, model_id)
            model_event(
                purpose=purpose,
                model_id=model_id,
                request=prompt,
                response=None,
                status="failed",
                error=f"{error.code}: {error.message}",
                duration_ms=call.duration_ms,
            )
            raise PreviewFailed(error.code, error.message) from error

    clear_model_unavailable(session, provider.name, model_id)
    spend.note_result(call, result, started)
    model_event(
        purpose=purpose,
        model_id=model_id,
        request=prompt,
        response=result.text,
        result=result,
        status="failed" if result.truncated else "completed",
        duration_ms=call.duration_ms,
    )
    return call, result


def _edge_text(edge: Edge) -> str:
    return f"{edge.display_subject} {edge.predicate} {edge.display_object}".strip()


def _judge_continuity(
    session: Session,
    script: Script,
    scene: Scene,
    live_edits: list[tuple[ScriptUnit, str]],
    diff: GraphDiff,
    packet: EvidencePacket,
    provider: LLMProvider,
    model_id: str,
    audit: list[ModelCall],
) -> tuple[list[ContinuityConflict], str | None]:
    """One continuity judgement per preview, argued from the evidence packet.

    Advisory by design: the deterministic diff and the orphaned-reference
    findings stand on their own, so a failed continuity call degrades the
    preview to those instead of failing it. The failure reaches the page as
    `continuity_error`, and the call is audited like any other.
    """
    if packet.total_items == 0:
        return [], None

    edits_payload = [
        {
            "unit_id": str(unit.id),
            "current_text": unit.current_text,
            "proposed_text": proposed,
        }
        for unit, proposed in live_edits
    ]
    diff_payload = {
        "removed": [_edge_text(edge) for edge in diff.removed],
        "added": [_edge_text(edge) for edge in diff.added],
        "changed": [
            f"{_edge_text(before)} to {_edge_text(after)}"
            for before, after in diff.changed
        ],
    }
    return judge_continuity_payloads(
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


def judge_continuity_payloads(
    session: Session,
    script: Script,
    scene: Scene,
    edits_payload: list[dict],
    diff_payload: dict,
    packet: EvidencePacket,
    provider: LLMProvider,
    model_id: str,
    audit: list[ModelCall],
) -> tuple[list[ContinuityConflict], str | None]:
    """The continuity judgement over already-rendered payloads.

    The preview builds its payloads from live edits and a graph diff; the
    draft report builds them from unit lineage and the cross-draft entity
    delta. Both meet the same prompt, validation, failover, and audit here.
    """
    prompt = build_continuity_prompt(edits_payload, diff_payload, packet)

    try:
        try:
            call, result = _generate_verdicts(
                session,
                script,
                scene,
                prompt,
                provider,
                model_id,
                audit,
                purpose="continuity",
                prompt_version=CONTINUITY_PROMPT_VERSION,
                system=CONTINUITY_SYSTEM,
                schema=CONTINUITY_SCHEMA,
            )
        except PreviewFailed as error:
            _, fallback = get_fallback_model(session)
            if (
                fallback is None
                or fallback == model_id
                or error.code not in AVAILABILITY_CODES
            ):
                raise
            logger.info("continuity failing over to %s after %s", fallback, error.code)
            call, result = _generate_verdicts(
                session,
                script,
                scene,
                prompt,
                provider,
                fallback,
                audit,
                purpose="continuity",
                prompt_version=CONTINUITY_PROMPT_VERSION,
                system=CONTINUITY_SYSTEM,
                schema=CONTINUITY_SCHEMA,
            )
    except (PreviewFailed, PreviewRefused) as error:
        # The audit row (a failure, or a budget refusal) is already recorded.
        logger.info("continuity pass skipped: %s", error.message)
        return [], error.message

    if result.truncated:
        call.outcome = "truncated"
        session.add(call)
        session.flush()
        return [], (
            _truncation_message(call.model_id, result)
            + " Only the deterministic findings are shown."
        )

    try:
        conflicts, validation = validate_continuity(result.text, packet)
    except MalformedResponse as error:
        call.outcome = "malformed"
        call.error_message = str(error)
        session.add(call)
        session.flush()
        return [], (
            "The continuity reply failed validation, so only the "
            "deterministic findings are shown."
        )

    call.validation_json = validation
    session.add(call)
    session.flush()
    return conflicts, None


def _edges_from_verdicts(
    session: Session,
    scene: Scene,
    judgement: JudgementReport,
    listed_assertions: dict[str, dict[str, Any]],
    labels: dict[Any, str],
) -> tuple[list[Edge], list[Edge]]:
    """The accepted and proposed edge sets a verdict list implies."""
    accepted: list[Edge] = []
    proposed: list[Edge] = []

    for verdict in judgement.assertion_verdicts:
        row = _row(session, verdict.assertion_id)
        if row is None:
            continue
        edge = _edge_from_row(session, row, labels)
        accepted.append(edge)
        if verdict.verdict != "removed":
            proposed.append(edge)

    new_entity_types = {entity.local_id: entity for entity in judgement.new_entities}
    for assertion in judgement.new_assertions:
        signature = SIGNATURES.get(assertion.predicate)
        if signature is None:
            continue
        subject = _new_endpoint(
            session,
            scene,
            assertion.subject_kind,
            assertion.subject_local_id,
            new_entity_types,
            signature.subject_types,
        )
        obj = _new_endpoint(
            session,
            scene,
            assertion.object_kind,
            assertion.object_local_id,
            new_entity_types,
            signature.object_types,
        )
        if subject is None or obj is None:
            continue
        subject_ref, subject_label = subject
        object_ref, object_label = obj
        proposed.append(
            Edge(
                subject=subject_ref,
                predicate=assertion.predicate,
                obj=object_ref,
                confidence=assertion.confidence,
                source_unit_id=assertion.source_unit_id,
                display_subject=subject_label,
                display_object=object_label,
                manner=assertion.manner,
            )
        )
    return accepted, proposed


def _attribute_changes(
    session: Session,
    judgement: JudgementReport,
    listed_attributes: dict[str, dict[str, Any]],
    operations: list[dict[str, Any]],
    labels: dict[Any, str],
) -> list[AttributeChange]:
    """Turn attribute verdicts and new attributes into changes and operations."""
    changes: list[AttributeChange] = []
    for verdict in judgement.attribute_verdicts:
        listed = listed_attributes.get(verdict.attribute_id)
        if listed is None or verdict.verdict == "holds":
            continue
        if verdict.verdict == "changed":
            changes.append(
                AttributeChange(
                    entity_label=listed["entity"],
                    key=listed["key"],
                    before=listed["value"],
                    after=verdict.new_value,
                    confidence=verdict.confidence or listed["confidence"],
                )
            )
            operations.append(
                {
                    "operation_type": "set_entity_attribute",
                    "target_type": "entity_attribute",
                    "target_id": verdict.attribute_id,
                    "before_json": {
                        "entity_id": listed["entity_id"],
                        "key": listed["key"],
                        "value": listed["value"],
                        "confidence": listed["confidence"],
                    },
                    "after_json": {
                        "entity_id": listed["entity_id"],
                        "entity_label": listed["entity"],
                        "key": listed["key"],
                        "value": verdict.new_value,
                        "source_unit_id": listed["source_unit_id"],
                        "evidence_start": verdict.evidence_start,
                        "evidence_end": verdict.evidence_end,
                        "confidence": verdict.confidence or listed["confidence"],
                    },
                }
            )
        else:
            changes.append(
                AttributeChange(
                    entity_label=listed["entity"],
                    key=listed["key"],
                    before=listed["value"],
                    after=None,
                    confidence=verdict.confidence or listed["confidence"],
                )
            )
            operations.append(
                {
                    "operation_type": "remove_entity_attribute",
                    "target_type": "entity_attribute",
                    "target_id": verdict.attribute_id,
                    "before_json": {
                        "entity_id": listed["entity_id"],
                        "entity_label": listed["entity"],
                        "key": listed["key"],
                        "value": listed["value"],
                        "source_unit_id": listed["source_unit_id"],
                        "confidence": listed["confidence"],
                    },
                    "after_json": None,
                }
            )

    known_new = {entity.local_id: entity for entity in judgement.new_entities}
    for raw in judgement.new_attributes:
        local = raw["entity_local_id"]
        entity_label = local
        entity_id = None
        entity_type = None
        if raw["known_local"] and local in known_new:
            entity_label = known_new[local].canonical_name
            entity_type = known_new[local].entity_type
        else:
            resolved = _existing_entity(session, labels, local)
            if resolved is None:
                continue
            entity_id, entity_label, entity_type = resolved
        changes.append(
            AttributeChange(
                entity_label=entity_label,
                key=normalize_key(raw["key"]),
                before=None,
                after=raw["value"],
                confidence=raw["confidence"],
            )
        )
        operations.append(
            {
                "operation_type": "set_entity_attribute",
                "target_type": "entity_attribute",
                "target_id": None,
                "before_json": None,
                "after_json": {
                    "entity_id": str(entity_id) if entity_id else None,
                    "entity_ref": entity_label,
                    "entity_type": entity_type,
                    "entity_label": entity_label,
                    "key": normalize_key(raw["key"]),
                    "value": raw["value"],
                    "source_unit_id": raw["source_unit_id"],
                    "evidence_start": raw["evidence_start"],
                    "evidence_end": raw["evidence_end"],
                    "confidence": raw["confidence"],
                },
            }
        )
    return changes


def _existing_entity(
    session: Session, labels: dict[Any, str], reference: str
) -> tuple[Any, str, str] | None:
    """Resolve a judged attribute's entity reference to an existing row."""
    try:
        entity = session.get(Entity, uuid_module.UUID(str(reference)))
        if entity is not None:
            return entity.id, entity.canonical_name, entity.entity_type
    except ValueError:
        pass
    key = normalize(reference)
    for entity_id, label in labels.items():
        if normalize(label) == key:
            entity = session.get(Entity, entity_id)
            if entity is not None:
                return entity.id, entity.canonical_name, entity.entity_type
    return None


def _new_endpoint(
    session: Session,
    scene: Scene,
    kind: str,
    local_id: str,
    new_entities: dict[str, Any],
    allowed_types: frozenset[str] | None,
) -> tuple[EdgeRef, str] | None:
    if kind == "scene":
        # The label the diff rows print. An empty label falls back to the
        # ref, and a scene's ref is its UUID, which is what the overlay
        # would then show.
        return (
            EdgeRef.scene(scene.id),
            f"Sc {scene.label}",
        )
    if local_id in new_entities:
        entity = new_entities[local_id]
        return (
            EdgeRef.entity(entity.canonical_name, entity.entity_type),
            entity.canonical_name,
        )
    # A new assertion may cite an existing entity by name. Matching is
    # constrained to the types the predicate accepts on this side: a name
    # shared across types ("bicycle" the prop, "bicycle" the transportation)
    # must resolve to the one the edge can legally hold.
    query = select(Entity).where(
        Entity.script_id == scene.script_id,
        Entity.normalized_name == normalize(local_id),
    )
    if allowed_types is not None:
        query = query.where(Entity.entity_type.in_(allowed_types))
    existing = session.scalar(query)
    if existing is not None:
        return (
            EdgeRef.entity(existing.canonical_name, existing.entity_type),
            existing.canonical_name,
        )
    return None


def _edge_from_row(session: Session, row: Assertion, labels: dict[Any, str]) -> Edge:
    def _ref(kind: str, entity_id, scene_id) -> EdgeRef:
        if kind == "scene":
            return EdgeRef.scene(scene_id)
        entity = session.get(Entity, entity_id)
        return EdgeRef.entity(entity.canonical_name, entity.entity_type)

    subject = _ref(row.subject_kind, row.subject_entity_id, row.subject_scene_id)
    obj = _ref(row.object_kind, row.object_entity_id, row.object_scene_id)
    return Edge(
        subject=subject,
        predicate=row.predicate,
        obj=obj,
        confidence=row.confidence,
        source_unit_id=str(row.source_unit_id),
        assertion_id=str(row.id),
        display_subject=labels.get(row.subject_entity_id or row.subject_scene_id, ""),
        display_object=labels.get(row.object_entity_id or row.object_scene_id, ""),
        manner=row.manner,
    )


def _edge_from_payload(payload: dict[str, Any]) -> Edge:
    subject = (
        EdgeRef.scene(payload.get("subject_ref"))
        if payload.get("subject_kind") == "scene"
        else EdgeRef.entity(
            payload.get("subject_ref", ""), payload.get("subject_entity_type") or ""
        )
    )
    obj = (
        EdgeRef.scene(payload.get("object_ref"))
        if payload.get("object_kind") == "scene"
        else EdgeRef.entity(
            payload.get("object_ref", ""), payload.get("object_entity_type") or ""
        )
    )
    return Edge(
        subject=subject,
        predicate=payload.get("predicate", ""),
        obj=obj,
        confidence=payload.get("confidence", 0.8),
        source_unit_id=payload.get("source_unit_id"),
        assertion_id=payload.get("assertion_id"),
        display_subject=payload.get("display_subject", ""),
        display_object=payload.get("display_object", ""),
        manner=payload.get("manner"),
    )


def _all_listed_ids(judgements: list[JudgementReport]) -> list[str]:
    return [
        verdict.assertion_id
        for judgement in judgements
        for verdict in judgement.assertion_verdicts
    ]


def _row(session: Session, assertion_id: str) -> Assertion | None:
    try:
        return session.get(Assertion, uuid_module.UUID(str(assertion_id)))
    except ValueError:
        return None


__all__ = [
    "AttributeChange",
    "PreviewFailed",
    "PreviewRefused",
    "PreviewResult",
    "UnitEdit",
    "preview_changes",
    "word_diff",
]
