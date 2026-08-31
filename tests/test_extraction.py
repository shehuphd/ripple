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
        """`requires` never takes a cast object; the signature bars it."""
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

    def test_two_sessions_cannot_claim_the_same_last_job(
        self, tmp_path, night_freight_fountain
    ):
        """The claim is an atomic UPDATE, so a second session sees nothing
        left once the first has taken the only pending job. Two sessions on
        one file database, because a single in-memory session cannot show a
        cross-connection claim at all."""
        url = f"sqlite+pysqlite:///{tmp_path}/race.db"
        engine = create_db_engine(url)
        create_all(engine)
        factory = session_factory(engine)

        with factory() as setup:
            result = import_screenplay(night_freight_fountain, "nf.fountain")
            script = persist_import(setup, result)
            run = start_run(setup, script.id, MODEL)
            run_id = run.id
            jobs = list(
                setup.scalars(
                    select(SceneExtraction).where(
                        SceneExtraction.extraction_run_id == run.id
                    )
                )
            )
            for job in jobs[1:]:
                job.status = "completed"
            setup.commit()

        first_session, second_session = factory(), factory()
        try:
            first = claim_next_scene(first_session, run_id)
            first_session.commit()
            second = claim_next_scene(second_session, run_id)
            assert first is not None
            assert second is None
        finally:
            first_session.close()
            second_session.close()

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
        """MAYA, Maya, and "the Maya" are one entity under name normalization."""
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


class TestAttributes:
    """extract.v2: entities carry evidence-backed attributes."""

    def _reply_with_attributes(self, session, scene, attributes) -> str:
        units = _units_of(session, scene.id)
        action = next(u for u in units if u.unit_type == "action")
        return _reply(
            entities=[
                {
                    "local_id": "e1",
                    "entity_type": "transportation",
                    "canonical_name": "Blue sedan",
                    "confidence": 0.91,
                    "attributes": [
                        {**attribute, "source_unit_id": str(action.id)}
                        if "source_unit_id" not in attribute
                        else attribute
                        for attribute in attributes
                    ],
                }
            ],
            assertions=[
                {
                    "subject_kind": "entity",
                    "subject_local_id": "e1",
                    "predicate": "appears_in",
                    "object_kind": "scene",
                    "object_local_id": "scene",
                    "source_unit_id": str(action.id),
                    "confidence": 0.88,
                }
            ],
        )

    def _extract(self, session, script, tmp_path, reply):
        scene = script.scenes[13]
        provider = _provider(tmp_path, reply)
        run = start_run(session, script.id, MODEL)
        job = session.scalar(
            select(SceneExtraction).where(
                SceneExtraction.extraction_run_id == run.id,
                SceneExtraction.scene_id == scene.id,
            )
        )
        return extract_scene(session, job, provider)

    def _rows(self, session):
        from ripple.db.models import EntityAttribute

        return list(session.scalars(select(EntityAttribute)))

    def test_a_valid_attribute_is_written_with_its_evidence(
        self, session, script, tmp_path
    ):
        scene = script.scenes[13]
        reply = self._reply_with_attributes(
            session,
            scene,
            [{"key": "color", "value": "blue", "confidence": 0.9,
              "evidence_start": 0, "evidence_end": 4}],
        )
        outcome = self._extract(session, script, tmp_path, reply)
        assert outcome.attributes_written == 1
        row = self._rows(session)[0]
        assert row.key == "color"
        assert row.value == "blue"
        assert row.provenance == "model"
        assert row.source_unit_id is not None
        assert row.active is True

    def test_a_key_is_normalized_before_writing(self, session, script, tmp_path):
        scene = script.scenes[13]
        reply = self._reply_with_attributes(
            session, scene, [{"key": "  Color ", "value": "blue", "confidence": 0.9}]
        )
        self._extract(session, script, tmp_path, reply)
        assert self._rows(session)[0].key == "color"

    def test_an_attribute_citing_an_unshown_unit_is_dropped(
        self, session, script, tmp_path
    ):
        scene = script.scenes[13]
        reply = self._reply_with_attributes(
            session,
            scene,
            [{"key": "color", "value": "blue", "confidence": 0.9,
              "source_unit_id": "99999999-9999-9999-9999-999999999999"}],
        )
        outcome = self._extract(session, script, tmp_path, reply)
        assert outcome.attributes_written == 0
        assert self._rows(session) == []

    def test_an_attribute_without_a_value_is_dropped(self, session, script, tmp_path):
        scene = script.scenes[13]
        reply = self._reply_with_attributes(
            session, scene, [{"key": "color", "value": "  ", "confidence": 0.9}]
        )
        outcome = self._extract(session, script, tmp_path, reply)
        assert outcome.attributes_written == 0

    def test_a_duplicate_key_keeps_the_first_value(self, session, script, tmp_path):
        scene = script.scenes[13]
        reply = self._reply_with_attributes(
            session,
            scene,
            [
                {"key": "color", "value": "blue", "confidence": 0.9},
                {"key": "Color", "value": "grey", "confidence": 0.9},
            ],
        )
        outcome = self._extract(session, script, tmp_path, reply)
        assert outcome.attributes_written == 1
        assert self._rows(session)[0].value == "blue"

    def test_an_active_value_is_not_overwritten_by_extraction(
        self, session, script, tmp_path
    ):
        """Changing a stored value is the judgement engine's job."""
        from ripple.db.models import EntityAttribute
        from ripple.db.naming import normalize

        scene = script.scenes[13]
        units = _units_of(session, scene.id)
        sedan = Entity(
            script_id=script.id,
            entity_type="transportation",
            canonical_name="Blue sedan",
            normalized_name=normalize("Blue sedan"),
        )
        session.add(sedan)
        session.flush()
        session.add(
            EntityAttribute(
                entity_id=sedan.id,
                key="color",
                value="blue",
                confidence=0.9,
                provenance="system",
                source_unit_id=units[0].id,
            )
        )
        session.flush()

        reply = self._reply_with_attributes(
            session, scene, [{"key": "color", "value": "grey", "confidence": 0.9}]
        )
        outcome = self._extract(session, script, tmp_path, reply)
        assert outcome.attributes_written == 0
        rows = self._rows(session)
        assert len(rows) == 1
        assert rows[0].value == "blue"

    def test_the_schema_and_prompt_version_advertise_v2(self):
        from ripple.extraction.prompt import OUTPUT_SCHEMA

        assert PROMPT_VERSION == "extract.v2"
        entity_schema = OUTPUT_SCHEMA["properties"]["entities"]["items"]
        assert "attributes" in entity_schema["properties"]


class TestDeadModelMarking:
    """A live call is the only trustworthy availability signal."""

    class _DeadModelProvider:
        name = "fixture"
        credential_variable = "FIXTURE_API_KEY"

        def is_configured(self):
            return True

        def list_models(self):
            return []

        def generate(self, model_id, prompt, **kwargs):
            from ripple.llm.base import ProviderError

            raise ProviderError("model_not_available", "dead on this account")

    def test_a_dead_model_is_marked_and_a_success_clears_it(
        self, session, script, tmp_path
    ):
        from ripple.db.repository import unavailable_models

        run = start_run(session, script.id, MODEL)
        job = claim_next_scene(session, run.id)
        extract_scene(session, job, self._DeadModelProvider())
        assert MODEL in unavailable_models(session, "fixture")

        # The same model answering later clears the mark.
        job.status = "pending"
        session.flush()
        job = claim_next_scene(session, run.id)
        extract_scene(session, job, _provider(tmp_path, _reply()))
        assert MODEL not in unavailable_models(session, "fixture")

    def test_marking_twice_writes_one_row(self, session):
        from ripple.db.repository import mark_model_unavailable, unavailable_models

        mark_model_unavailable(session, "google", "gemini-2.5-flash")
        mark_model_unavailable(session, "google", "gemini-2.5-flash")
        assert unavailable_models(session, "google") == {"gemini-2.5-flash"}

    def test_marks_are_scoped_to_the_provider(self, session):
        from ripple.db.repository import mark_model_unavailable, unavailable_models

        mark_model_unavailable(session, "google", "gemini-2.5-flash")
        assert unavailable_models(session, "fixture") == set()


class TestExtractionFallback:
    """An unavailable main model fails over to the configured fallback."""

    class _MainDownProvider:
        name = "fixture"
        credential_variable = "FIXTURE_API_KEY"

        def __init__(self, dead, reply):
            self.dead = dead
            self.reply = reply
            self.calls_by_model = {}

        def is_configured(self):
            return True

        def list_models(self):
            return []

        def generate(self, model_id, prompt, **kwargs):
            from ripple.llm.base import GenerationResult, ProviderError

            self.calls_by_model[model_id] = (
                self.calls_by_model.get(model_id, 0) + 1
            )
            if model_id == self.dead:
                raise ProviderError("provider_unavailable", "down right now")
            return GenerationResult(
                text=self.reply, model_id=model_id, provider=self.name
            )

    def test_the_fallback_extracts_when_the_main_model_is_down(
        self, session, script
    ):
        from ripple.db.models import ModelCall
        from ripple.db.repository import set_fallback_model

        set_fallback_model(session, "fixture", "fixture-mid")
        scene = script.scenes[13]
        provider = self._MainDownProvider(
            dead=MODEL, reply=self._reply_for(session, scene)
        )
        run = start_run(session, script.id, MODEL)
        job = session.scalar(
            select(SceneExtraction).where(
                SceneExtraction.extraction_run_id == run.id,
                SceneExtraction.scene_id == scene.id,
            )
        )
        outcome = extract_scene(session, job, provider)
        assert outcome.status == "completed"
        assert provider.calls_by_model == {MODEL: 1, "fixture-mid": 1}

        outcomes = {
            call.model_id: call.outcome
            for call in session.scalars(select(ModelCall))
        }
        assert outcomes[MODEL] == "provider_error"
        assert outcomes["fixture-mid"] == "ok"
        # The written rows carry the model that answered.
        row = session.scalars(select(Assertion)).first()
        assert row.model_id == "fixture-mid"

    def test_no_fallback_keeps_the_failure(self, session, script):
        scene = script.scenes[13]
        provider = self._MainDownProvider(dead=MODEL, reply="{}")
        run = start_run(session, script.id, MODEL)
        job = session.scalar(
            select(SceneExtraction).where(
                SceneExtraction.extraction_run_id == run.id,
                SceneExtraction.scene_id == scene.id,
            )
        )
        outcome = extract_scene(session, job, provider)
        assert outcome.status in ("pending", "failed")
        assert outcome.error_code == "provider_unavailable"
        assert list(provider.calls_by_model) == [MODEL]

    def _reply_for(self, session, scene) -> str:
        units = _units_of(session, scene.id)
        action = next(u for u in units if u.unit_type == "action")
        return _reply(
            entities=[
                {
                    "local_id": "e1",
                    "entity_type": "transportation",
                    "canonical_name": "Blue sedan",
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
                    "source_unit_id": str(action.id),
                    "confidence": 0.9,
                }
            ],
        )


class TestEvidenceOffsetPairs:
    def _one_assertion(self, **offsets):
        return validate_response(
            _reply(
                entities=[
                    {
                        "local_id": "e1",
                        "entity_type": "prop",
                        "canonical_name": "Brass key",
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
                        **offsets,
                    }
                ],
            ),
            {"u1"},
        )

    def test_a_half_offset_pair_is_dropped_whole(self):
        """Regression: (None, 42) used to survive, and slicing with a None
        side silently read from the start of the unit."""
        report = self._one_assertion(evidence_end=42)
        assertion = report.assertions[0]
        assert assertion.evidence_start is None
        assert assertion.evidence_end is None

    def test_a_full_pair_survives(self):
        report = self._one_assertion(evidence_start=4, evidence_end=13)
        assertion = report.assertions[0]
        assert (assertion.evidence_start, assertion.evidence_end) == (4, 13)

    def test_a_reversed_pair_is_dropped_whole(self):
        report = self._one_assertion(evidence_start=13, evidence_end=4)
        assertion = report.assertions[0]
        assert assertion.evidence_start is None
        assert assertion.evidence_end is None


class TestCacheKeyCoversThePrompt:
    def test_editing_the_rules_changes_the_input_hash(self, monkeypatch):
        """Regression: the cache key ignored the prompt material, so editing
        the rules served cached answers the new prompt never produced."""
        from ripple.extraction import prompt as prompt_module

        units = [("u1", "action", "The sedan idles.")]
        before = prompt_module.input_hash("EXT. DOCK - NIGHT", units)
        monkeypatch.setattr(
            prompt_module, "RULES", prompt_module.RULES + "\n- one more rule"
        )
        after = prompt_module.input_hash("EXT. DOCK - NIGHT", units)
        assert before != after


class TestRunStatusTruth:
    def test_a_finished_run_with_failures_is_not_ready(
        self, session, script, tmp_path
    ):
        """Regression: one success used to flip a holed run to ready."""
        run = start_run(session, script.id, MODEL)
        bad = _provider(tmp_path / "bad", "not json")
        good = _provider(tmp_path / "good", _reply())

        first = claim_next_scene(session, run.id)
        extract_scene(session, first, bad)
        first.status = "running"
        extract_scene(session, first, bad)

        while (job := claim_next_scene(session, run.id)) is not None:
            extract_scene(session, job, good)

        assert run.failed_scenes >= 1
        assert run.completed_scenes >= 1
        assert run.status == "partially_ready"
        script_row = session.get(Script, script.id)
        assert script_row.graph_status == "partially_ready"

    def test_a_run_reusing_every_scene_reports_ready_immediately(
        self, session, script, tmp_path
    ):
        """A run whose every scene is served from the cache must finish.

        The dedupe key is unique across runs, so a fully cached second run
        creates no jobs at all; without the roll-up in start_run it would
        stay pending forever with nothing left to claim.
        """
        good = _provider(tmp_path / "good", _reply())
        first = start_run(session, script.id, MODEL)
        while (job := claim_next_scene(session, first.id)) is not None:
            extract_scene(session, job, good)
        assert first.status == "ready"

        second = start_run(session, script.id, MODEL)
        assert claim_next_scene(session, second.id) is None
        assert second.status == "ready"
        assert second.completed_scenes == second.total_scenes
