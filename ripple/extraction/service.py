"""Resumable, scene-at-a-time graph extraction.

Paid background workers are ruled out, so the browser drives the
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
import time
import uuid as uuid_module
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from traceact import ActionTrace

from ripple.db.models import (
    Assertion,
    Entity,
    EntityAlias,
    EntityAttribute,
    ExtractionRun,
    ModelCall,
    Scene,
    SceneExtraction,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize
from ripple.db.repository import (
    clear_model_unavailable,
    get_fallback_model,
    mark_model_unavailable,
)
from ripple.extraction.prepass import (
    RULE_CONFIDENCE,
    ProvidedEntity,
    provided_for_scene,
)
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
from ripple.llm.base import AVAILABILITY_CODES, LLMProvider, ProviderError
from ripple.services import pricing
from ripple.services.spend import BudgetExceeded, check_budget
from ripple.tracing import ensure_configured, model_event

logger = logging.getLogger(__name__)

# One retry. A second identical call to a model that produced malformed output
# usually produces malformed output again, and each attempt costs money.
MAX_ATTEMPTS = 2
# Capped so one pathological scene cannot consume the run's budget.
MAX_OUTPUT_TOKENS = 8192


@dataclass(frozen=True)
class SceneOutcome:
    """What happened to one scene."""

    scene_id: str
    status: str
    entities_written: int = 0
    assertions_written: int = 0
    attributes_written: int = 0
    rejected: int = 0
    cached: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class RunProgress:
    """Enough for the browser to draw a progress bar and decide to stop.

    `tokens` and `cost` are the run's spend so far, from its audit rows;
    `cost` is None when the model has no rate in the pricing registry.
    """

    run_id: str
    status: str
    total: int
    completed: int
    failed: int
    pending: int
    tokens: int = 0
    cost_usd: float | None = None
    cost: str | None = None

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


def _has_content(units: list[tuple[str, str, str]]) -> bool:
    """Whether the scene has any body text an extraction could read."""
    return any(
        text.strip()
        for _, unit_type, text in units
        if unit_type != "scene_heading"
    )


def pending_scene_count(
    session: Session,
    script_id,
    model_id: str,
    prompt_version: str = PROMPT_VERSION,
) -> int:
    """How many scenes a run started now would bill.

    A scene counts when no completed extraction matches its current content
    under this prompt and model: never extracted, edited since the last
    build, restored with different text, or carried into a draft the cache
    has not seen. Everything else replays from cache at no cost, so this is
    the number the Build graph gate cares about. Omitted scenes are never
    extracted and never count.
    """
    pending = 0
    scenes = session.scalars(
        select(Scene).where(
            Scene.script_id == script_id, Scene.omitted.is_(False)
        )
    )
    for scene in scenes:
        digest = input_hash(scene.heading, _scene_units(session, scene.id))
        done = session.scalar(
            select(SceneExtraction.id).where(
                SceneExtraction.scene_id == scene.id,
                SceneExtraction.input_hash == digest,
                SceneExtraction.prompt_version == prompt_version,
                SceneExtraction.model_id == model_id,
                SceneExtraction.status == "completed",
            )
        )
        if done is None:
            pending += 1
    return pending


def start_run(
    session: Session,
    script_id,
    model_id: str,
    prompt_version: str = PROMPT_VERSION,
    scene_ids: list | None = None,
    force: bool = False,
) -> ExtractionRun:
    """Create a run and a pending job for every scene.

    Content hashes are computed now, so a scene edited after the run starts is
    detected as changed rather than silently extracted from stale text.

    `scene_ids` narrows the run to those scenes, which is how an inserted
    scene is extracted without re-running the rest: on a seeded script the
    other scenes have no cache rows, so a whole-script run would bill all of
    them. Omitted scenes are never extracted.

    `force` ignores the cache and gives every scene a job, so a script whose
    graph is already current is read again. That is what a rebuild is for:
    the parser and the deterministic pre-pass may have changed since the last
    build, and neither is reflected by replaying a stored answer. It bills
    every scene, which is why nothing calls it without the user asking.
    """
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"no script with id {script_id}")

    query = (
        select(Scene)
        .where(Scene.script_id == script_id, Scene.omitted.is_(False))
        .order_by(Scene.sequence_index)
    )
    if scene_ids is not None:
        query = query.where(Scene.id.in_(scene_ids))
    scenes = list(session.scalars(query))

    run = ExtractionRun(
        script_id=script_id,
        status="pending",
        prompt_version=prompt_version,
        model_id=model_id,
        total_scenes=len(scenes),
        forced=force,
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
        if force:
            # The cache key is a unique constraint, so a rebuild cannot add a
            # second job beside the row the cache already holds: the stale row
            # goes first. Nothing has a foreign key to it, and the record of
            # what the run spent lives in the ModelCall rows and the trace
            # files, which a rebuild leaves untouched.
            session.execute(
                delete(SceneExtraction).where(
                    SceneExtraction.scene_id == scene.id,
                    SceneExtraction.input_hash == digest,
                    SceneExtraction.prompt_version == prompt_version,
                    SceneExtraction.model_id == model_id,
                ),
                execution_options={"synchronize_session": False},
            )
        already_done = None if force else session.scalar(
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
        # A run abandoned mid-flight (a closed tab, a server restart) leaves
        # pending or failed rows behind, and the cache key is unique across
        # runs. The leftover goes so this run can hold a job for the scene;
        # its spend record lives in ModelCall and the traces, untouched.
        session.execute(
            delete(SceneExtraction).where(
                SceneExtraction.scene_id == scene.id,
                SceneExtraction.input_hash == digest,
                SceneExtraction.prompt_version == prompt_version,
                SceneExtraction.model_id == model_id,
                SceneExtraction.status != "completed",
            ),
            execution_options={"synchronize_session": False},
        )
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

        # A forced run reads the scene again even when an identical extraction
        # is on file: replaying the stored answer is the one thing a rebuild
        # must not do.
        cached = None if run.forced else _reuse_cached(session, job)
        if cached is not None:
            trace.step("Reused a cached extraction")
            trace.output({"cached": True, "assertions": cached.assertions_written})
            return cached

        units = _scene_units(session, job.scene_id)
        if not units:
            return _complete(session, job, run, ValidationReport(), trace, empty=True)

        # What code can read, code records: the pre-pass derives the speaking
        # cast and the location, and the prompt hands the model their ids and
        # short unit ids instead of UUIDs. Repeating a 36-character UUID on
        # every cited item was a measurable share of every answer's tokens.
        provided = provided_for_scene(scene.heading, units)
        unit_by_short = {
            f"u{index + 1}": unit_id
            for index, (unit_id, _, _) in enumerate(units)
        }
        prompt_units = [
            (short, unit_type, text)
            for short, (_, unit_type, text) in zip(
                unit_by_short, units, strict=True
            )
        ]
        unit_texts = {short: text for short, _, text in prompt_units}
        prompt = build_prompt(
            scene.heading,
            scene.display_scene_number,
            prompt_units,
            provided=[(p.local_id, p.entity_type, p.name) for p in provided],
        )
        job.attempt_count += 1

        # The main model, then the configured fallback. The fallback answers
        # when the main refuses for an availability reason, and also when the
        # main's reply fails validation: a cheap main model with an escalation
        # path costs less over a whole build than running the stronger model
        # everywhere. Each attempt writes its own audit row.
        candidates = [job.model_id]
        _, fallback = get_fallback_model(session)
        if fallback and fallback != job.model_id:
            candidates.append(fallback)

        result = None
        call = None
        report = None
        for candidate in candidates:
            call = ModelCall(
                script_id=run.script_id,
                scene_id=job.scene_id,
                purpose="extract",
                prompt_version=job.prompt_version,
                model_id=candidate,
                request_text=prompt,
                outcome="ok",
            )
            try:
                check_budget(session, call)
            except BudgetExceeded:
                # A spent budget fails the scene outright: retrying cannot
                # help, and the pending run should stop asking rather than
                # loop.
                job.attempt_count = MAX_ATTEMPTS
                return _fail(session, job, run, trace, "budget_exceeded")

            call_started = time.perf_counter()
            try:
                result = provider.generate(
                    candidate,
                    prompt,
                    system=SYSTEM_PROMPT,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    json_schema=OUTPUT_SCHEMA,
                )
            except ProviderError as error:
                if error.code == "model_not_available":
                    mark_model_unavailable(session, provider.name, candidate)
                call.outcome = "provider_error"
                call.error_message = error.message
                call.duration_ms = int(
                    (time.perf_counter() - call_started) * 1000
                )
                session.add(call)
                session.flush()
                more = candidate != candidates[-1]
                if more and error.code in AVAILABILITY_CODES:
                    logger.info(
                        "extraction failing over after %s", error.code
                    )
                    continue
                return _fail(session, job, run, trace, error.code)
            clear_model_unavailable(session, provider.name, candidate)

            call.response_text = result.text
            call.input_tokens = result.input_tokens
            call.output_tokens = result.output_tokens
            call.reasoning_tokens = getattr(result, "reasoning_tokens", None)
            call.duration_ms = int((time.perf_counter() - call_started) * 1000)

            model_event(
                purpose="extract",
                model_id=call.model_id,
                request=prompt,
                response=result.text,
                result=result,
                status="failed" if result.truncated else "completed",
                duration_ms=call.duration_ms,
            )
            escalate = candidate != candidates[-1]
            if result.truncated:
                call.outcome = "truncated"
                session.add(call)
                session.flush()
                if escalate:
                    logger.info("extraction escalating after truncation")
                    continue
                return _fail(session, job, run, trace, "output_truncated")

            try:
                report = validate_response(
                    result.text,
                    set(unit_by_short),
                    provided={
                        p.local_id: (p.entity_type, p.name) for p in provided
                    },
                    unit_texts=unit_texts,
                )
            except MalformedResponse as error:
                logger.info("scene %s: %s", job.scene_id, error)
                call.outcome = "malformed"
                call.error_message = str(error)
                session.add(call)
                session.flush()
                if escalate:
                    logger.info("extraction escalating after a malformed reply")
                    continue
                return _fail(session, job, run, trace, "malformed_response")

            if escalate and not report.entities and _has_content(units):
                # A scene with content and an answer naming nothing in it is
                # a miss the schema cannot catch; the stronger model reads it.
                call.outcome = "incomplete"
                session.add(call)
                session.flush()
                logger.info("extraction escalating after an empty answer")
                continue
            break

        call.validation_json = {
            "entities": len(report.entities),
            "assertions": len(report.assertions),
            "rejected": len(report.rejected),
            "rejection_codes": sorted(set(report.rejection_codes)),
        }
        session.add(call)
        session.flush()

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
        # The database cites units by UUID; the model cited the short ids.
        for assertion in report.assertions:
            assertion.source_unit_id = unit_by_short[assertion.source_unit_id]
        for entity in report.entities:
            for attribute in entity.attributes:
                attribute.source_unit_id = unit_by_short[attribute.source_unit_id]
        return _complete(
            session,
            job,
            run,
            report,
            trace,
            model_used=call.model_id,
            provided=provided,
        )


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
    # Without the roll-up, a run whose last scene is a cache hit keeps its
    # old status forever and the script never leaves "analysing".
    _roll_up(session, run)
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
    model_used: str | None = None,
    provided: list[ProvidedEntity] | None = None,
) -> SceneOutcome:
    """Write the validated graph rows and mark the job completed.

    The pre-pass's cast and location rows are written first, so a model
    reply referencing a provided id resolves against a row that exists.
    """
    resolved: dict[str, Entity] = {}
    written = 0
    for rule_entity in provided or []:
        entity = _resolve_entity(
            session,
            run.script_id,
            ValidatedEntity(
                local_id=rule_entity.local_id,
                entity_type=rule_entity.entity_type,
                canonical_name=rule_entity.name,
                confidence=RULE_CONFIDENCE,
            ),
        )
        resolved[rule_entity.local_id] = entity
        if _write_rule_assertion(session, run, job, rule_entity, entity):
            written += 1

    for proposed in report.entities:
        resolved[proposed.local_id] = _resolve_entity(session, run.script_id, proposed)

    model_used = model_used or job.model_id
    for proposed in report.assertions:
        if _write_assertion(session, run, job, proposed, resolved, model_used):
            written += 1

    attributes_written = 0
    for proposed in report.entities:
        entity = resolved.get(proposed.local_id)
        if entity is None:
            continue
        for attribute in proposed.attributes:
            if _write_attribute(session, job, entity, attribute, model_used):
                attributes_written += 1

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
            "attributes": attributes_written,
            "rejected": len(report.rejected),
        }
    )
    return SceneOutcome(
        scene_id=str(job.scene_id),
        status="completed",
        # Distinct rows, not local ids: the model referencing a provided
        # entity under its own id must not count one row twice.
        entities_written=len({entity.id for entity in resolved.values()}),
        assertions_written=written,
        attributes_written=attributes_written,
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


def _name_extends(shorter: list[str], longer: list[str]) -> bool:
    """True when one word list is a proper prefix of the other, so "lubov" is a
    name-extension of "lubov andreyevna" but "maras" is not of "mara"."""
    return 0 < len(shorter) < len(longer) and longer[: len(shorter)] == shorter


def _name_extension_holder(
    session: Session, script_id, entity_type: str, key: str
) -> Entity | None:
    """The one character whose name strictly extends `key` or is extended by
    it, or None when there is no such character or more than one. Cast only:
    a first-name/full-name shorthand is a character convention, not a prop's."""
    if entity_type != "cast":
        return None
    words = key.split()
    if not words:
        return None
    matches = [
        entity
        for entity in session.scalars(
            select(Entity).where(
                Entity.script_id == script_id,
                Entity.entity_type == "cast",
            )
        )
        if _name_extends(words, other := entity.normalized_name.split())
        or _name_extends(other, words)
    ]
    return matches[0] if len(matches) == 1 else None


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
        # No canonical match; a name already recorded as an alias resolves
        # to its entity ("the sedan" against Blue sedan), so a rephrased
        # extraction extends the entity instead of forking it. Two entities
        # sharing the alias make the identity ambiguous, and an ambiguous
        # match creates a fresh entity rather than guessing.
        holders = list(
            session.scalars(
                select(EntityAlias.entity_id)
                .join(Entity, EntityAlias.entity_id == Entity.id)
                .where(
                    Entity.script_id == script_id,
                    Entity.entity_type == proposed.entity_type,
                    EntityAlias.normalized_alias == key,
                )
                .distinct()
            )
        )
        if len(holders) == 1:
            entity = session.get(Entity, holders[0])
    if entity is None:
        # Still no match. A character cue that strictly extends an existing
        # character's name, or is strictly extended by it ("LUBOV" against
        # "LUBOV ANDREYEVNA"), is the same person under a fuller or shorter
        # surface, so it widens that entity instead of forking. Only when a
        # single character stands in that relation, since two ("MARY" against
        # both "MARY ANNE" and "MARY JANE") is ambiguous; only for cast, since
        # a prop or location does not use first-name shorthand.
        entity = _name_extension_holder(
            session, script_id, proposed.entity_type, key
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

    # Queried, not read from the relationship: aliases added by an earlier
    # resolve in this session (the pre-pass, or another scene) may not be in
    # the loaded collection yet, and a stale set re-inserts a duplicate.
    known = set(
        session.scalars(
            select(EntityAlias.normalized_alias).where(
                EntityAlias.entity_id == entity.id
            )
        )
    )
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


def _write_rule_assertion(
    session: Session,
    run: ExtractionRun,
    job: SceneExtraction,
    rule_entity: ProvidedEntity,
    entity: Entity,
) -> bool:
    """Write one pre-pass edge: appears_in for cast, occurs_at for location.

    Provenance "system" marks the row as rule-derived; no model saw it and
    no model is credited. The dedupe index absorbs a re-extraction's copy.
    """
    scene_first = rule_entity.predicate == "occurs_at"
    assertion = Assertion(
        script_id=run.script_id,
        subject_kind="scene" if scene_first else "entity",
        subject_entity_id=None if scene_first else entity.id,
        subject_scene_id=job.scene_id if scene_first else None,
        predicate=rule_entity.predicate,
        object_kind="entity" if scene_first else "scene",
        object_entity_id=entity.id if scene_first else None,
        object_scene_id=None if scene_first else job.scene_id,
        source_unit_id=uuid_module.UUID(rule_entity.source_unit_id),
        extraction_run_id=run.id,
        evidence_start=rule_entity.evidence_start,
        evidence_end=rule_entity.evidence_end,
        confidence=RULE_CONFIDENCE,
        provenance="system",
        prompt_version=job.prompt_version,
    )
    try:
        with session.begin_nested():
            session.add(assertion)
            session.flush()
    except IntegrityError:
        return False
    return True


def _write_assertion(
    session: Session,
    run: ExtractionRun,
    job: SceneExtraction,
    proposed: ValidatedAssertion,
    resolved: dict[str, Entity],
    model_used: str,
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
        model_id=model_used,
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


def _write_attribute(
    session: Session, job: SceneExtraction, entity, proposed, model_used: str
) -> bool:
    """Write one attribute, unless the key already holds an active value.

    First writer wins across an extraction: an entity's color stated in scene
    3 is not overwritten by scene 40 restating it, and a re-extraction of the
    same scene writes nothing. Changing an active value is the judgement
    engine's job, through an accepted change set, never a side effect of
    extraction.
    """
    existing = session.scalar(
        select(EntityAttribute.id).where(
            EntityAttribute.entity_id == entity.id,
            EntityAttribute.key == proposed.key,
            EntityAttribute.active.is_(True),
        )
    )
    if existing is not None:
        return False

    try:
        source_unit_id = uuid_module.UUID(proposed.source_unit_id)
    except (ValueError, AttributeError):
        return False

    try:
        # A savepoint for the same reason assertions use one: two entities in
        # one reply racing to the same key must not discard the whole scene.
        with session.begin_nested():
            session.add(
                EntityAttribute(
                    entity_id=entity.id,
                    key=proposed.key,
                    value=proposed.value,
                    confidence=proposed.confidence,
                    provenance="model",
                    source_unit_id=source_unit_id,
                    evidence_start=proposed.evidence_start,
                    evidence_end=proposed.evidence_end,
                    prompt_version=job.prompt_version,
                    model_id=model_used,
                )
            )
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
    elif run.failed_scenes:
        # Finished with failures: the graph holds only the completed scenes.
        # "ready" is reserved for a run every scene of which completed.
        run.status = "failed" if not run.completed_scenes else "partially_ready"
        if run.completed_at is None:
            run.completed_at = _now()
    else:
        run.status = "ready"
        if run.completed_at is None:
            run.completed_at = _now()

    script = session.get(Script, run.script_id)
    if script is not None:
        script.graph_status = {
            "ready": "ready",
            "failed": "failed",
            "running": "analysing",
            "partially_ready": "partially_ready",
        }.get(run.status, script.graph_status)


def cancel_run(session: Session, run_id) -> ExtractionRun:
    """Stop a run after the scene in flight.

    The pending jobs are deleted rather than kept: the cache key is unique
    across runs, so a leftover row would collide with the job a later build
    creates for the same scene. Completed scenes keep their rows and their
    facts, so that later build resumes instead of starting over.
    """
    run = session.get(ExtractionRun, run_id)
    if run is None:
        raise ValueError(f"no extraction run with id {run_id}")
    session.execute(
        delete(SceneExtraction).where(
            SceneExtraction.extraction_run_id == run.id,
            SceneExtraction.status == "pending",
        ),
        execution_options={"synchronize_session": False},
    )
    run.status = "cancelled"
    if run.completed_at is None:
        run.completed_at = _now()
    script = session.get(Script, run.script_id)
    if script is not None and script.graph_status == "analysing":
        script.graph_status = (
            "partially_ready" if run.completed_scenes else "not_analysed"
        )
    session.flush()
    logger.info(
        "cancelled run %s with %d of %d scenes done",
        run.id,
        run.completed_scenes,
        run.total_scenes,
    )
    return run


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
    calls = list(
        session.scalars(
            select(ModelCall).where(
                ModelCall.script_id == run.script_id,
                ModelCall.purpose == "extract",
                ModelCall.created_at >= run.started_at,
            )
        )
    )
    tokens = sum(
        (call.input_tokens or 0)
        + (call.output_tokens or 0)
        + (call.reasoning_tokens or 0)
        for call in calls
    )
    cost = pricing.calls_cost_usd(calls)
    return RunProgress(
        run_id=str(run_id),
        status=run.status,
        total=run.total_scenes,
        completed=run.completed_scenes,
        failed=run.failed_scenes,
        pending=outstanding,
        tokens=tokens,
        cost_usd=cost,
        cost=pricing.display(cost),
    )
