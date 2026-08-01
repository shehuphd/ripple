"""Resumable, scene-at-a-time graph extraction.

PRD section 10 rules out paid background workers, so the browser drives the
loop: it asks for one scene job, the server does that scene and commits it, and
the browser asks again. A reload resumes from the first incomplete scene
because progress lives in `scene_extractions` rather than in a process.

Three properties this module has to hold:

- **Each scene commits independently.** A failure on scene 30 must not undo
  scenes 1 to 29, so the caller commits per scene rather than per run.
- **A claim is atomic.** Two browser tabs polling at once must not extract the
  same scene twice and pay twice.
- **An unchanged scene is never re-sent.** The cache key is
  `(scene_id, input_hash, prompt_version, model_id)`; a hit copies the previous
  result rather than calling the model.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from traceact import ActionTrace

from ripple.db.models import (
    Assertion,
    Entity,
    EntityAlias,
    ExtractionRun,
    Scene,
    SceneExtraction,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize
from ripple.extraction.prompt import (
    OUTPUT_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    input_hash,
)
from ripple.extraction.validate import (
    MalformedResponse,
    ValidatedAssertion,
    ValidatedEntity,
    ValidationReport,
    validate_response,
)
from ripple.graph.predicates import canonical_endpoints
from ripple.llm.base import LLMProvider, ProviderError
from ripple.tracing import ensure_configured

logger = logging.getLogger(__name__)

# One retry. A second identical call to a model that produced malformed output
# usually produces malformed output again, and each attempt costs money.
MAX_ATTEMPTS = 2
# Capped so one pathological scene cannot consume the run's budget.
MAX_OUTPUT_TOKENS = 4096


@dataclass(frozen=True)
class SceneOutcome:
    """What happened to one scene."""

    scene_id: str
    status: str
    entities_written: int = 0
    assertions_written: int = 0
    rejected: int = 0
    cached: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class RunProgress:
    """Enough for the browser to draw a progress bar and decide to stop."""

    run_id: str
    status: str
    total: int
    completed: int
    failed: int
    pending: int

    @property
    def finished(self) -> bool:
        return self.pending == 0


def _now() -> datetime:
    return datetime.now(UTC)


def _scene_units(session: Session, scene_id) -> list[tuple[str, str, str]]:
    """The scene's units as (id, type, text), in order."""
    rows = session.scalars(
        select(ScriptUnit)
        .where(ScriptUnit.scene_id == scene_id)
        .order_by(ScriptUnit.sequence_index)
    )
    return [(str(unit.id), unit.unit_type, unit.current_text) for unit in rows]


def start_run(
    session: Session, script_id, model_id: str, prompt_version: str = PROMPT_VERSION
) -> ExtractionRun:
    """Create a run and a pending job for every scene.

    Content hashes are computed now, so a scene edited after the run starts is
    detected as changed rather than silently extracted from stale text.
    """
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"no script with id {script_id}")

    scenes = list(
        session.scalars(
            select(Scene)
            .where(Scene.script_id == script_id)
            .order_by(Scene.sequence_index)
        )
    )

    run = ExtractionRun(
        script_id=script_id,
        status="pending",
        prompt_version=prompt_version,
        model_id=model_id,
        total_scenes=len(scenes),
        started_at=_now(),
    )
    session.add(run)
    session.flush()

    reused = 0
    for scene in scenes:
        digest = input_hash(scene.heading, _scene_units(session, scene.id))
        # The extraction cache key is unique across runs, so a scene already
        # extracted from identical input under the same prompt and model gets
        # no job at all. That is the cache: no row, no call, no spend.
        already_done = session.scalar(
            select(SceneExtraction.id).where(
                SceneExtraction.scene_id == scene.id,
                SceneExtraction.input_hash == digest,
                SceneExtraction.prompt_version == prompt_version,
                SceneExtraction.model_id == model_id,
                SceneExtraction.status == "completed",
            )
        )
        if already_done is not None:
            reused += 1
            continue
        session.add(
            SceneExtraction(
                extraction_run_id=run.id,
                scene_id=scene.id,
                status="pending",
                input_hash=digest,
                prompt_version=prompt_version,
                model_id=model_id,
            )
        )

    run.completed_scenes = reused
    script.graph_status = "analysing"
    session.flush()
    _roll_up(session, run)
    session.flush()
    logger.info(
        "started run %s over %d scenes (%d reused from cache)",
        run.id,
        len(scenes),
        reused,
    )
    return run


def claim_next_scene(session: Session, run_id) -> SceneExtraction | None:
    """Atomically take one pending job, or return None when there are none.

    The UPDATE ... WHERE status='pending' is the lock. Two concurrent callers
    issue the same statement; one matches a row and the other matches nothing,
    so a scene cannot be claimed twice and paid for twice. A plain
    SELECT-then-UPDATE would let both read 'pending' before either wrote.
    """
    candidate = session.scalar(
        select(SceneExtraction.id)
        .where(
            SceneExtraction.extraction_run_id == run_id,
            SceneExtraction.status == "pending",
        )
        .order_by(SceneExtraction.id)
        .limit(1)
    )
    if candidate is None:
        return None

    claimed = session.execute(
        update(SceneExtraction)
        .where(SceneExtraction.id == candidate, SceneExtraction.status == "pending")
        .values(status="running", started_at=_now())
        .returning(SceneExtraction.id)
    ).scalar_one_or_none()

    if claimed is None:
        # Another caller took it between the select and the update.
        return None
    session.flush()
    return session.get(SceneExtraction, claimed)


def extract_scene(
    session: Session, job: SceneExtraction, provider: LLMProvider
) -> SceneOutcome:
    """Run one scene through the model and write its graph rows.

    The caller commits. On failure the job is marked and the exception is not
    raised, so the browser's loop continues to the next scene rather than
    stopping the whole run on one bad scene.
    """
    ensure_configured()
    scene = session.get(Scene, job.scene_id)
    run = session.get(ExtractionRun, job.extraction_run_id)

    with ActionTrace.start(action="scene.extract", kind="extract") as trace:
        trace.set_meta("input_hash", job.input_hash)
        trace.input(
            {
                "prompt_version": job.prompt_version,
                "model_id": job.model_id,
                "sequence_index": scene.sequence_index,
            }
        )

        cached = _reuse_cached(session, job)
        if cached is not None:
            trace.step("Reused a cached extraction")
            trace.output({"cached": True, "assertions": cached.assertions_written})
            return cached

        units = _scene_units(session, job.scene_id)
        if not units:
            return _complete(session, job, run, ValidationReport(), trace, empty=True)

        prompt = build_prompt(scene.heading, scene.display_scene_number, units)
        job.attempt_count += 1

        try:
            result = provider.generate(
                job.model_id,
                prompt,
                system=SYSTEM_PROMPT,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                json_schema=OUTPUT_SCHEMA,
            )
        except ProviderError as error:
            return _fail(session, job, run, trace, error.code)

        trace.event(
            kind="model",
            operation="generate",
            target=job.model_id,
            data={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "truncated": result.truncated,
            },
        )
        if result.truncated:
            return _fail(session, job, run, trace, "output_truncated")

        try:
            report = validate_response(
                result.text, {unit_id for unit_id, _, _ in units}
            )
        except MalformedResponse as error:
            logger.info("scene %s: %s", job.scene_id, error)
            return _fail(session, job, run, trace, "malformed_response")

        trace.event(
            kind="validate",
            operation="extraction_output",
            data={
                "entities": len(report.entities),
                "assertions": len(report.assertions),
                "rejected": len(report.rejected),
                "rejection_codes": sorted(set(report.rejection_codes)),
            },
        )
        return _complete(session, job, run, report, trace)


def _reuse_cached(session: Session, job: SceneExtraction) -> SceneOutcome | None:
    """Copy a previous successful extraction of identical input, if there is one.

    The unique constraint on the cache key means at most one completed row can
    match, so this cannot pick between two candidates.
    """
    previous = session.scalar(
        select(SceneExtraction).where(
            SceneExtraction.scene_id == job.scene_id,
            SceneExtraction.input_hash == job.input_hash,
            SceneExtraction.prompt_version == job.prompt_version,
            SceneExtraction.model_id == job.model_id,
            SceneExtraction.status == "completed",
            SceneExtraction.id != job.id,
        )
    )
    if previous is None:
        return None

    existing = list(
        session.scalars(
            select(Assertion).where(
                Assertion.extraction_run_id == previous.extraction_run_id,
                Assertion.active.is_(True),
            )
        )
    )
    scene_assertions = [
        assertion
        for assertion in existing
        if assertion.subject_scene_id == job.scene_id
        or assertion.object_scene_id == job.scene_id
    ]

    job.status = "completed"
    job.completed_at = _now()
    run = session.get(ExtractionRun, job.extraction_run_id)
    run.completed_scenes += 1
    session.flush()
    return SceneOutcome(
        scene_id=str(job.scene_id),
        status="completed",
        assertions_written=len(scene_assertions),
        cached=True,
    )


def _complete(
    session: Session,
    job: SceneExtraction,
    run: ExtractionRun,
    report: ValidationReport,
    trace: ActionTrace,
    empty: bool = False,
) -> SceneOutcome:
    """Write the validated graph rows and mark the job completed."""
    resolved: dict[str, Entity] = {}
    for proposed in report.entities:
        resolved[proposed.local_id] = _resolve_entity(session, run.script_id, proposed)

    written = 0
    for proposed in report.assertions:
        if _write_assertion(session, run, job, proposed, resolved):
            written += 1

    job.status = "completed"
    job.completed_at = _now()
    job.error_code = None
    run.completed_scenes += 1
    _roll_up(session, run)
    session.flush()

    trace.step("Wrote the scene's graph rows")
    trace.output(
        {
            "cached": False,
            "empty": empty,
            "entities": len(resolved),
            "assertions": written,
            "rejected": len(report.rejected),
        }
    )
    return SceneOutcome(
        scene_id=str(job.scene_id),
        status="completed",
        entities_written=len(resolved),
        assertions_written=written,
        rejected=len(report.rejected),
    )


def _fail(
    session: Session,
    job: SceneExtraction,
    run: ExtractionRun,
    trace: ActionTrace,
    error_code: str,
) -> SceneOutcome:
    """Mark a job failed, or return it to pending when a retry remains."""
    retryable = job.attempt_count < MAX_ATTEMPTS
    job.status = "pending" if retryable else "failed"
    job.error_code = error_code
    if not retryable:
        job.completed_at = _now()
        run.failed_scenes += 1
        _roll_up(session, run)
    session.flush()

    trace.step(f"Scene failed: {error_code}")
    trace.output(
        {"status": job.status, "error_code": error_code, "attempts": job.attempt_count}
    )
    return SceneOutcome(
        scene_id=str(job.scene_id), status=job.status, error_code=error_code
    )


def _resolve_entity(session: Session, script_id, proposed: ValidatedEntity) -> Entity:
    """Find or create the canonical entity for a proposed one.

    Matching is on the normalized name within the script and type, so MAYA,
    Maya, and "the Maya" are one entity. Aliases are added rather than
    replaced: a later scene that offers a new surface form should widen the
    entity, not overwrite what an earlier scene learned.
    """
    key = normalize(proposed.canonical_name)
    entity = session.scalar(
        select(Entity).where(
            Entity.script_id == script_id,
            Entity.entity_type == proposed.entity_type,
            Entity.normalized_name == key,
        )
    )
    if entity is None:
        entity = Entity(
            script_id=script_id,
            entity_type=proposed.entity_type,
            canonical_name=proposed.canonical_name,
            normalized_name=key,
            description=proposed.description,
        )
        session.add(entity)
        session.flush()
    elif proposed.description and not entity.description:
        entity.description = proposed.description

    known = {alias.normalized_alias for alias in entity.aliases}
    for alias in [*proposed.aliases, proposed.canonical_name]:
        normalized = normalize(alias)
        if not normalized or normalized in known:
            continue
        session.add(
            EntityAlias(
                entity_id=entity.id,
                alias=alias,
                normalized_alias=normalized,
                provenance="model",
            )
        )
        known.add(normalized)
    session.flush()
    return entity


def _write_assertion(
    session: Session,
    run: ExtractionRun,
    job: SceneExtraction,
    proposed: ValidatedAssertion,
    resolved: dict[str, Entity],
) -> bool:
    """Write one assertion, skipping a duplicate the dedupe key already holds."""
    subject_id = _endpoint_id(
        proposed.subject_kind, proposed.subject_local_id, resolved, job
    )
    object_id = _endpoint_id(
        proposed.object_kind, proposed.object_local_id, resolved, job
    )
    if subject_id is None or object_id is None:
        return False

    subject_id, object_id = canonical_endpoints(
        proposed.predicate, subject_id, object_id
    )

    # The model cites a unit by its string identifier; the column is a UUID.
    try:
        source_unit_id = uuid_module.UUID(proposed.source_unit_id)
    except (ValueError, AttributeError):
        return False

    assertion = Assertion(
        script_id=run.script_id,
        subject_kind=proposed.subject_kind,
        subject_entity_id=subject_id if proposed.subject_kind == "entity" else None,
        subject_scene_id=subject_id if proposed.subject_kind == "scene" else None,
        predicate=proposed.predicate,
        object_kind=proposed.object_kind,
        object_entity_id=object_id if proposed.object_kind == "entity" else None,
        object_scene_id=object_id if proposed.object_kind == "scene" else None,
        source_unit_id=source_unit_id,
        extraction_run_id=run.id,
        evidence_start=proposed.evidence_start,
        evidence_end=proposed.evidence_end,
        confidence=proposed.confidence,
        provenance="model",
        prompt_version=job.prompt_version,
        model_id=job.model_id,
    )
    try:
        # A savepoint, not a rollback: the dedupe index rejecting one duplicate
        # edge must not discard the scene claim and everything else written for
        # this scene. Re-extraction producing the same edge is expected.
        with session.begin_nested():
            session.add(assertion)
            session.flush()
    except IntegrityError:
        return False
    return True


def _endpoint_id(kind: str, local_id: str, resolved: dict[str, Entity], job):
    """Turn a local identifier into a row identifier."""
    if kind == "scene":
        return job.scene_id
    entity = resolved.get(local_id)
    return entity.id if entity else None


def _roll_up(session: Session, run: ExtractionRun) -> None:
    """Set the run status from its scenes' statuses."""
    outstanding = (
        session.scalar(
            select(func.count())
            .select_from(SceneExtraction)
            .where(
                SceneExtraction.extraction_run_id == run.id,
                SceneExtraction.status.in_(("pending", "running")),
            )
        )
        or 0
    )

    if outstanding:
        run.status = "partially_ready" if run.completed_scenes else "running"
    elif run.failed_scenes and not run.completed_scenes:
        run.status = "failed"
    else:
        run.status = "ready"
        run.completed_at = _now()

    script = session.get(Script, run.script_id)
    if script is not None:
        script.graph_status = {
            "ready": "ready",
            "failed": "failed",
            "running": "analysing",
            "partially_ready": "partially_ready",
        }.get(run.status, script.graph_status)


def progress(session: Session, run_id) -> RunProgress:
    """Current run progress, for the browser's loop."""
    run = session.get(ExtractionRun, run_id)
    if run is None:
        raise ValueError(f"no extraction run with id {run_id}")

    outstanding = (
        session.scalar(
            select(func.count())
            .select_from(SceneExtraction)
            .where(
                SceneExtraction.extraction_run_id == run_id,
                SceneExtraction.status.in_(("pending", "running")),
            )
        )
        or 0
    )
    return RunProgress(
        run_id=str(run_id),
        status=run.status,
        total=run.total_scenes,
        completed=run.completed_scenes,
        failed=run.failed_scenes,
        pending=outstanding,
    )
