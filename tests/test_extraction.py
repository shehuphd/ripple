"""Scene-level extraction.

Attacks first: malformed replies, fabricated evidence, signature violations,
concurrent claims, and retry limits. A pipeline that writes a graph from a
plausible-looking wrong answer is worse than one that writes nothing, because
every later stage treats the graph as fact.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from ripple.adapters import import_screenplay
from ripple.db.models import Assertion, Entity, EntityAlias, SceneExtraction, Script
from ripple.db.repository import persist_import
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.extraction.prompt import PROMPT_VERSION, build_prompt, input_hash
from ripple.extraction.service import (
    MAX_ATTEMPTS,
    claim_next_scene,
    extract_scene,
    progress,
    start_run,
)
from ripple.extraction.validate import MalformedResponse, validate_response
from ripple.llm.fixture import FixtureProvider

MODEL = "fixture-cheap"


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.rollback()
    instance.close()


@pytest.fixture
def script(session, night_freight_fountain):
    result = import_screenplay(night_freight_fountain, "night-freight.fountain")
    return persist_import(session, result)


def _reply(entities=(), assertions=()) -> str:
    return json.dumps({"entities": list(entities), "assertions": list(assertions)})


def _provider(tmp_path, reply: str) -> FixtureProvider:
    return FixtureProvider(tmp_path / "fixtures", default_reply=reply)


def _units_of(session, scene_id):
    from ripple.db.models import ScriptUnit

    return list(
        session.scalars(
            select(ScriptUnit)
            .where(ScriptUnit.scene_id == scene_id)
            .order_by(ScriptUnit.sequence_index)
        )
    )


class TestOutputValidation:
    def test_a_non_json_reply_is_malformed(self):
        with pytest.raises(MalformedResponse):
            validate_response("I could not find anything.", set())

    def test_a_json_array_is_malformed(self):
        with pytest.raises(MalformedResponse):
            validate_response("[]", set())

    def test_missing_top_level_lists_are_malformed(self):
        with pytest.raises(MalformedResponse):
            validate_response('{"entities": {}}', set())

    def test_an_entity_type_outside_the_lock_is_dropped(self):
        report = validate_response(
            _reply(
                entities=[
                    {
                        "local_id": "e1",
                        "entity_type": "catering",
                        "canonical_name": "Sandwiches",
                        "confidence": 0.9,
                    }
                ]
            ),
            set(),
        )
        assert report.entities == []
        assert "unknown_entity_type" in report.rejection_codes

    def test_a_fabricated_source_unit_is_dropped(self):
        """An evidence pointer to a unit never shown to the model is invented."""
        report = validate_response(
            _reply(
                entities=[
                    {
                        "local_id": "e1",
                        "entity_type": "prop",
                        "canonical_name": "Torch",
                        "confidence": 0.9,
                    }
                ],
                assertions=[
                    {
                        "subject_kind": "entity",
                        "subject_local_id": "e1",
                        "predicate": "appears_in",
                        "object_kind": "scene",
                        "object_local_id": "scene",
                        "source_unit_id": "a-unit-that-was-never-shown",
                        "confidence": 0.9,
                    }
                ],
            ),
            {"real-unit-id"},
        )
        assert report.assertions == []
        assert "unknown_source_unit" in report.rejection_codes

    def test_a_signature_violation_is_dropped(self):
        """`requires` never takes a cast object. Schema Lock v1 section 4."""
        report = validate_response(
            _reply(
                entities=[
                    {
                        "local_id": "e1",
                        "entity_type": "cast",
                        "canonical_name": "Mara",
                        "confidence": 0.95,
                    }
                ],
                assertions=[
                    {
                        "subject_kind": "scene",
                        "subject_local_id": "scene",
                        "predicate": "requires",
                        "object_kind": "entity",
                        "object_local_id": "e1",
                        "source_unit_id": "u1",
                        "confidence": 0.9,
                    }
                ],
            ),
            {"u1"},
        )
        assert report.assertions == []
        assert "signature_mismatch" in report.rejection_codes

    def test_low_confidence_is_discarded_at_the_boundary(self):
        report = validate_response(
            _reply(
                entities=[
                    {
                        "local_id": "e1",
                        "entity_type": "prop",
                        "canonical_name": "Torch",
                        "confidence": 0.9,
                    }
                ],
                assertions=[
                    {
                        "subject_kind": "entity",
                        "subject_local_id": "e1",
                        "predicate": "appears_in",
                        "object_kind": "scene",
                        "object_local_id": "scene",
                        "source_unit_id": "u1",
                        "confidence": 0.4,
                    }
                ],
            ),
            {"u1"},
        )
        assert report.assertions == []
        assert "below_confidence_floor" in report.rejection_codes

    def test_one_bad_assertion_does_not_lose_the_good_ones(self):
        report = validate_response(
            _reply(
                entities=[
                    {
                        "local_id": "e1",
                        "entity_type": "prop",
                        "canonical_name": "Torch",
                        "confidence": 0.9,
                    }
                ],
                assertions=[
                    {
                        "subject_kind": "entity",
                        "subject_local_id": "e1",
                        "predicate": "appears_in",
                        "object_kind": "scene",
                        "object_local_id": "scene",
                        "source_unit_id": "u1",
                        "confidence": 0.9,
                    },
                    "a bare string, not an object",
                ],
            ),
            {"u1"},
        )
        assert len(report.assertions) == 1
        assert "not_an_object" in report.rejection_codes

    def test_an_entity_nothing_cites_is_dropped(self):
        report = validate_response(
            _reply(
                entities=[
                    {
                        "local_id": "e1",
                        "entity_type": "prop",
                        "canonical_name": "Unmentioned",
                        "confidence": 0.9,
                    }
                ]
            ),
            {"u1"},
        )
        assert report.entities == []
        assert "unreferenced" in report.rejection_codes


class TestClaiming:
    def test_a_scene_cannot_be_claimed_twice(self, session, script):
        run = start_run(session, script.id, MODEL)
        first = claim_next_scene(session, run.id)
        second = claim_next_scene(session, run.id)
        assert first is not None and second is not None
        assert first.id != second.id

    def test_claiming_returns_none_when_the_run_is_drained(self, session, script):
        run = start_run(session, script.id, MODEL)
        claimed = 0
        while claim_next_scene(session, run.id) is not None:
            claimed += 1
        assert claimed == run.total_scenes
        assert claim_next_scene(session, run.id) is None

    def test_a_run_creates_one_job_per_scene(self, session, script):
        run = start_run(session, script.id, MODEL)
        count = session.scalar(
            select(func.count())
            .select_from(SceneExtraction)
            .where(SceneExtraction.extraction_run_id == run.id)
        )
        assert count == len(script.scenes) == run.total_scenes
        assert session.get(Script, script.id).graph_status == "analysing"


class TestFailureHandling:
    def test_a_malformed_reply_retries_then_fails(self, session, script, tmp_path):
        provider = _provider(tmp_path, "this is not JSON")
        run = start_run(session, script.id, MODEL)

        job = claim_next_scene(session, run.id)
        outcome = extract_scene(session, job, provider)
        assert outcome.status == "pending"
        assert outcome.error_code == "malformed_response"

        job.status = "running"
        outcome = extract_scene(session, job, provider)
        assert outcome.status == "failed"
        assert job.attempt_count == MAX_ATTEMPTS

    def test_a_provider_error_does_not_raise_to_the_caller(
        self, session, script, tmp_path
    ):
        """The browser's loop must continue to the next scene."""
        provider = FixtureProvider(tmp_path / "empty")  # no default reply
        run = start_run(session, script.id, MODEL)
        job = claim_next_scene(session, run.id)
        outcome = extract_scene(session, job, provider)
        assert outcome.status in {"pending", "failed"}
        assert outcome.error_code == "fixture_missing"

    def test_one_failed_scene_leaves_the_others_extractable(
        self, session, script, tmp_path
    ):
        run = start_run(session, script.id, MODEL)
        bad = _provider(tmp_path / "bad", "not json")
        good = _provider(tmp_path / "good", _reply())

        first = claim_next_scene(session, run.id)
        extract_scene(session, first, bad)
        second = claim_next_scene(session, run.id)
        outcome = extract_scene(session, second, good)
        assert outcome.status == "completed"


class TestWritingTheGraph:
    def _good_reply(self, session, scene) -> str:
        units = _units_of(session, scene.id)
        action = next(u for u in units if u.unit_type == "action")
        return _reply(
            entities=[
                {
                    "local_id": "e1",
                    "entity_type": "transportation",
                    "canonical_name": "Blue sedan",
                    "aliases": ["the sedan"],
                    "confidence": 0.91,
                },
                {
                    "local_id": "e2",
                    "entity_type": "location",
                    "canonical_name": "Loading dock",
                    "confidence": 0.9,
                },
            ],
            assertions=[
                {
                    "subject_kind": "entity",
                    "subject_local_id": "e1",
                    "predicate": "appears_in",
                    "object_kind": "scene",
                    "object_local_id": "scene",
                    "source_unit_id": str(action.id),
                    "evidence_start": 0,
                    "evidence_end": 10,
                    "confidence": 0.88,
                },
                {
                    "subject_kind": "scene",
                    "subject_local_id": "scene",
                    "predicate": "occurs_at",
                    "object_kind": "entity",
                    "object_local_id": "e2",
                    "source_unit_id": str(action.id),
                    "confidence": 0.9,
                },
            ],
        )

    def test_entities_assertions_and_aliases_are_written(
        self, session, script, tmp_path
    ):
        scene = script.scenes[13]
        provider = _provider(tmp_path, self._good_reply(session, scene))
        run = start_run(session, script.id, MODEL)

        job = session.scalar(
            select(SceneExtraction).where(
                SceneExtraction.extraction_run_id == run.id,
                SceneExtraction.scene_id == scene.id,
            )
        )
        outcome = extract_scene(session, job, provider)

        assert outcome.status == "completed"
        assert outcome.entities_written == 2
        assert outcome.assertions_written == 2
        assert session.scalar(select(func.count()).select_from(Entity)) == 2
        assert session.scalar(select(func.count()).select_from(Assertion)) == 2
        aliases = set(session.scalars(select(EntityAlias.normalized_alias)))
        assert {"blue sedan", "sedan"} <= aliases

    def test_the_same_entity_across_scenes_resolves_to_one_row(
        self, session, script, tmp_path
    ):
        """MAYA, Maya, and "the Maya" are one entity. Schema Lock v1 section 5."""
        run = start_run(session, script.id, MODEL)
        for _ in range(2):
            job = claim_next_scene(session, run.id)
            units = _units_of(session, job.scene_id)
            if not units:
                continue
            provider = _provider(
                tmp_path / str(job.id),
                _reply(
                    entities=[
                        {
                            "local_id": "e1",
                            "entity_type": "transportation",
                            "canonical_name": "THE BLUE SEDAN",
                            "confidence": 0.9,
                        }
                    ],
                    assertions=[
                        {
                            "subject_kind": "entity",
                            "subject_local_id": "e1",
                            "predicate": "appears_in",
                            "object_kind": "scene",
                            "object_local_id": "scene",
                            "source_unit_id": str(units[0].id),
                            "confidence": 0.9,
                        }
                    ],
                ),
            )
            extract_scene(session, job, provider)

        assert session.scalar(select(func.count()).select_from(Entity)) == 1

    def test_a_duplicate_edge_from_one_unit_is_skipped_not_fatal(
        self, session, script, tmp_path
    ):
        scene = script.scenes[13]
        units = _units_of(session, scene.id)
        edge = {
            "subject_kind": "entity",
            "subject_local_id": "e1",
            "predicate": "appears_in",
            "object_kind": "scene",
            "object_local_id": "scene",
            "source_unit_id": str(units[0].id),
            "confidence": 0.9,
        }
        reply = _reply(
            entities=[
                {
                    "local_id": "e1",
                    "entity_type": "prop",
                    "canonical_name": "Pallet jack",
                    "confidence": 0.9,
                }
            ],
            assertions=[edge, dict(edge)],
        )
        provider = _provider(tmp_path, reply)
        run = start_run(session, script.id, MODEL)
        job = session.scalar(
            select(SceneExtraction).where(
                SceneExtraction.extraction_run_id == run.id,
                SceneExtraction.scene_id == scene.id,
            )
        )
        outcome = extract_scene(session, job, provider)
        assert outcome.status == "completed"
        assert outcome.assertions_written == 1


class TestResumeAndCache:
    def test_progress_reports_what_is_left(self, session, script, tmp_path):
        provider = _provider(tmp_path, _reply())
        run = start_run(session, script.id, MODEL)

        before = progress(session, run.id)
        assert before.total == run.total_scenes and not before.finished

        while (job := claim_next_scene(session, run.id)) is not None:
            extract_scene(session, job, provider)

        after = progress(session, run.id)
        assert after.finished and after.completed == run.total_scenes
        assert session.get(Script, script.id).graph_status == "ready"

    def test_an_unchanged_scene_is_not_sent_to_the_model_again(
        self, session, script, tmp_path
    ):
        provider = _provider(tmp_path, _reply())
        first = start_run(session, script.id, MODEL)
        while (job := claim_next_scene(session, first.id)) is not None:
            extract_scene(session, job, provider)
        calls_after_first = len(provider.calls)

        second = start_run(session, script.id, MODEL)
        while (job := claim_next_scene(session, second.id)) is not None:
            extract_scene(session, job, provider)

        assert len(provider.calls) == calls_after_first, "cache did not hold"

    def test_editing_a_unit_changes_the_cache_key(self, session, script):
        scene = script.scenes[13]
        units = _units_of(session, scene.id)
        before = input_hash(
            scene.heading, [(str(u.id), u.unit_type, u.current_text) for u in units]
        )
        units[1].current_text = "A bicycle leans against the gate."
        after = input_hash(
            scene.heading, [(str(u.id), u.unit_type, u.current_text) for u in units]
        )
        assert before != after


class TestPrompt:
    def test_the_prompt_carries_unit_ids_for_evidence(self, session, script):
        scene = script.scenes[13]
        units = [
            (str(u.id), u.unit_type, u.current_text)
            for u in _units_of(session, scene.id)
        ]
        prompt = build_prompt(scene.heading, scene.display_scene_number, units)
        assert all(unit_id in prompt for unit_id, _, _ in units)

    def test_the_prompt_states_the_predicate_signatures(self):
        prompt = build_prompt("INT. X - DAY", "1", [("u1", "action", "A chair.")])
        assert "requires: scene -> entity" in prompt
        assert "occurs_at: scene -> entity (location)" in prompt

    def test_the_prompt_version_is_part_of_the_cache_key(self, session, script):
        run = start_run(session, script.id, MODEL)
        job = session.scalar(
            select(SceneExtraction).where(SceneExtraction.extraction_run_id == run.id)
        )
        assert job.prompt_version == PROMPT_VERSION
