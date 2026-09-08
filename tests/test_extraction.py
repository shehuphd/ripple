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
from ripple.db.models import (
    Assertion,
    Entity,
    EntityAlias,
    ExtractionRun,
    SceneExtraction,
    Script,
)
from ripple.db.repository import persist_import
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.extraction.prompt import PROMPT_VERSION, build_prompt, input_hash
from ripple.extraction.service import (
    MAX_ATTEMPTS,
    _scene_units,
    cancel_run,
    claim_next_scene,
    extract_scene,
    pending_scene_count,
    progress,
    start_run,
)
from ripple.extraction.validate import MalformedResponse, validate_response
from tests.support.fixture_provider import FixtureProvider

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


def _short_id(session, scene_id, unit_id) -> str:
    """The prompt-facing id of one unit: u1, u2, ... in scene order."""
    units = _units_of(session, scene_id)
    return f"u{[u.id for u in units].index(unit_id) + 1}"


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
                        "id": "e1",
                        "type": "catering",
                        "name": "Sandwiches",
                        "conf": 0.9,
                    }
                ]
            ),
            set(),
        )
        assert report.entities == []
        assert "unknown_entity_type" in report.rejection_codes

    def test_a_pronoun_or_group_name_is_dropped(self):
        """The extractor sometimes resolves a pronoun to a fresh entity, or
        labels a group as one. Neither is a production entity, so both are
        dropped before they reach the graph, while an unnamed role is kept."""
        report = validate_response(
            _reply(
                entities=[
                    {"id": "e1", "type": "cast", "name": "She", "conf": 0.9},
                    {"id": "e2", "type": "cast", "name": "Two Characters", "conf": 0.9},
                    {"id": "e3", "type": "cast", "name": "Nurse", "conf": 0.9},
                ],
                assertions=[
                    {"s": "e3", "p": "appears_in", "o": "scene",
                     "unit": "u1", "conf": 0.9},
                ],
            ),
            {"u1"},
        )
        kept = {e.canonical_name for e in report.entities}
        assert kept == {"Nurse"}, kept
        assert report.rejection_codes.count("pronoun_or_group_name") == 2

    def test_a_line_of_screen_text_is_not_an_entity(self):
        """A line of dialogue or screen text lifted into a name reads as a
        sentence, and is dropped rather than written as a character."""
        report = validate_response(
            _reply(
                entities=[
                    {
                        "id": "e1",
                        "type": "cast",
                        "name": "PERMISSIONS UPDATED. CONTACT YOUR SUPERVISOR",
                        "conf": 0.9,
                    }
                ]
            ),
            set(),
        )
        assert report.entities == []
        assert "sentence_like_name" in report.rejection_codes

    def test_the_group_label_rule_is_scoped_to_cast(self):
        """A count leading a noun ("Two men") is an anonymous group of
        characters and is dropped from cast. The same shape is valid for a
        prop, set dressing, or a location, which carry counts and plural
        nouns ("Six monitors", "14 Stannary Lane"), so those are kept. The
        pronoun and sentence rules still hold for every type."""
        from ripple.extraction.validate import _unusable_name_reason

        assert _unusable_name_reason("Two men", "cast") == "pronoun_or_group_name"
        assert _unusable_name_reason("Six monitors", "set_design") is None
        assert _unusable_name_reason("14 Stannary Lane", "location") is None
        assert _unusable_name_reason("one wall of books", "set_design") is None
        assert _unusable_name_reason("She", "prop") == "pronoun_or_group_name"

    def test_a_cue_convention_is_not_a_character(self):
        """A stage play marks a chorus line with ALL or BOTH and heads its cast
        list DRAMATIS PERSONAE, and a cue naming the whole roster at once is
        that list, not a person. An enumeration needs two commas or a comma
        before a conjunction, so one comma inside a name is left alone."""
        from ripple.extraction.validate import _unusable_name_reason

        for cue in ("ALL", "BOTH", "DRAMATIS PERSONAE", "Omnes",
                    "Lords, Ladies, Officers, Soldiers, and Attendants"):
            assert _unusable_name_reason(cue, "cast") == "pronoun_or_group_name"
        for person in ("HAMLET", "SMITH, JR.", "FIRST SAILOR", "A Priest",
                       "Attendants", "DANES", "English Ambassadors", "nurse"):
            assert _unusable_name_reason(person, "cast") is None

    def test_a_described_set_is_not_a_location(self):
        """A location names a place; a stage-play act opens with prose about the
        set. A finite verb outside a relative clause, or a name too long for a
        slug, marks a description and is dropped. A place-shaped phrase, a
        region-then-spot period, and a naming relative clause are kept, and the
        rule is a location concept only: a prop or set-dressing name may run
        long or read as a phrase."""
        from ripple.extraction.validate import _unusable_name_reason

        for described in (
            "The table has been placed in the middle of the stage",
            "THE Christmas Tree is in the corner by the piano",
            "A room furnished comfortably and tastefully, but not extravagantly",
        ):
            assert _unusable_name_reason(described, "location") == "descriptive_location"
        for place in (
            "Elsinore. A platform before the Castle",
            "A room which is still called the nursery",
            "A drawing-room in Wimpole Street",
            "DISPATCH OFFICE",
        ):
            assert _unusable_name_reason(place, "location") is None
        # The same described sentences are admissible names for other types.
        assert _unusable_name_reason(
            "A room furnished comfortably and tastefully", "set_design"
        ) is None

    def test_a_fabricated_source_unit_is_dropped(self):
        """An evidence pointer to a unit never shown to the model is invented."""
        report = validate_response(
            _reply(
                entities=[
                    {
                        "id": "e1",
                        "type": "prop",
                        "name": "Torch",
                        "conf": 0.9,
                    }
                ],
                assertions=[
                    {
                        "s": "e1",
                        "p": "appears_in",
                        "o": "scene",
                        "unit": "a-unit-that-was-never-shown",
                        "conf": 0.9,
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
                        "id": "e1",
                        "type": "cast",
                        "name": "Mara",
                        "conf": 0.95,
                    }
                ],
                assertions=[
                    {
                        "s": "scene",
                        "p": "requires",
                        "o": "e1",
                        "unit": "u1",
                        "conf": 0.9,
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
                        "id": "e1",
                        "type": "prop",
                        "name": "Torch",
                        "conf": 0.9,
                    }
                ],
                assertions=[
                    {
                        "s": "e1",
                        "p": "appears_in",
                        "o": "scene",
                        "unit": "u1",
                        "conf": 0.4,
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
                        "id": "e1",
                        "type": "prop",
                        "name": "Torch",
                        "conf": 0.9,
                    }
                ],
                assertions=[
                    {
                        "s": "e1",
                        "p": "appears_in",
                        "o": "scene",
                        "unit": "u1",
                        "conf": 0.9,
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
                        "id": "e1",
                        "type": "prop",
                        "name": "Unmentioned",
                        "conf": 0.9,
                    }
                ]
            ),
            {"u1"},
        )
        assert report.entities == []
        assert "unreferenced" in report.rejection_codes

    def test_an_appearance_manner_rides_the_edge(self):
        """A cast member shown only in a photograph is present in a depicted
        manner, and that qualifier survives validation onto the edge."""
        report = validate_response(
            _reply(
                entities=[{"id": "e1", "type": "cast", "name": "Rosa", "conf": 0.9}],
                assertions=[
                    {
                        "s": "e1",
                        "p": "appears_in",
                        "o": "scene",
                        "manner": "depicted",
                        "unit": "u1",
                        "conf": 0.9,
                    }
                ],
            ),
            {"u1"},
        )
        assert [a.manner for a in report.assertions] == ["depicted"]

    def test_an_appearance_without_a_manner_defaults_to_on_stage(self):
        report = validate_response(
            _reply(
                entities=[{"id": "e1", "type": "cast", "name": "Rosa", "conf": 0.9}],
                assertions=[
                    {"s": "e1", "p": "appears_in", "o": "scene",
                     "unit": "u1", "conf": 0.9},
                ],
            ),
            {"u1"},
        )
        assert report.assertions[0].manner == "on_stage"

    def test_an_unknown_manner_falls_back_rather_than_dropping_the_edge(self):
        report = validate_response(
            _reply(
                entities=[{"id": "e1", "type": "cast", "name": "Rosa", "conf": 0.9}],
                assertions=[
                    {"s": "e1", "p": "appears_in", "o": "scene",
                     "manner": "lurking", "unit": "u1", "conf": 0.9},
                ],
            ),
            {"u1"},
        )
        assert report.assertions[0].manner == "on_stage"

    def test_a_manner_on_a_non_appearance_edge_is_discarded(self):
        report = validate_response(
            _reply(
                entities=[
                    {"id": "e1", "type": "cast", "name": "Rosa", "conf": 0.9},
                    {"id": "e2", "type": "prop", "name": "red ledger", "conf": 0.9},
                ],
                assertions=[
                    {"s": "e1", "p": "carries", "o": "e2", "manner": "depicted",
                     "unit": "u1", "conf": 0.9},
                ],
            ),
            {"u1"},
        )
        assert [a.manner for a in report.assertions] == [None]


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

    def test_a_build_over_an_abandoned_run_replaces_its_leftover_jobs(
        self, session, script
    ):
        """A closed tab or a restart leaves the old run's pending rows in
        place; the next build takes them over instead of colliding with the
        unique cache key."""
        abandoned = start_run(session, script.id, MODEL)
        session.commit()
        fresh = start_run(session, script.id, MODEL)
        session.commit()
        assert fresh.total_scenes == len(script.scenes)
        leftover = session.scalar(
            select(func.count())
            .select_from(SceneExtraction)
            .where(SceneExtraction.extraction_run_id == abandoned.id)
        )
        assert leftover == 0

    def test_cancelling_closes_the_run_and_drops_its_pending_jobs(
        self, session, script
    ):
        run = start_run(session, script.id, MODEL)
        job = claim_next_scene(session, run.id)
        job.status = "completed"
        run.completed_scenes = 1
        session.flush()

        cancel_run(session, run.id)

        assert run.status == "cancelled"
        assert run.completed_at is not None
        pending = session.scalar(
            select(func.count())
            .select_from(SceneExtraction)
            .where(
                SceneExtraction.extraction_run_id == run.id,
                SceneExtraction.status == "pending",
            )
        )
        assert pending == 0
        assert session.get(Script, script.id).graph_status == "partially_ready"
        assert claim_next_scene(session, run.id) is None

    def test_a_build_after_a_cancel_resumes_from_the_completed_scenes(
        self, session, script
    ):
        """The pending jobs go with the cancel, so a later run can hold the
        cache key for the scenes still to do, and only those."""
        first = start_run(session, script.id, MODEL)
        job = claim_next_scene(session, first.id)
        job.status = "completed"
        first.completed_scenes = 1
        session.flush()
        cancel_run(session, first.id)

        second = start_run(session, script.id, MODEL)
        fresh = session.scalar(
            select(func.count())
            .select_from(SceneExtraction)
            .where(
                SceneExtraction.extraction_run_id == second.id,
                SceneExtraction.status == "pending",
            )
        )
        assert fresh == len(script.scenes) - 1

    def test_cancelling_a_fresh_run_returns_the_script_to_unanalysed(
        self, session, script
    ):
        run = start_run(session, script.id, MODEL)
        cancel_run(session, run.id)
        assert session.get(Script, script.id).graph_status == "not_analysed"

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
                    "id": "e1",
                    "type": "transportation",
                    "name": "Blue sedan",
                    "aliases": ["the sedan"],
                    "conf": 0.91,
                },
                {
                    "id": "e2",
                    "type": "location",
                    "name": "Loading dock",
                    "conf": 0.9,
                },
            ],
            assertions=[
                {
                    "s": "e1",
                    "p": "appears_in",
                    "o": "scene",
                    "unit": _short_id(session, scene.id, action.id),
                    "start": 0,
                    "end": 10,
                    "conf": 0.88,
                },
                {
                    "s": "scene",
                    "p": "occurs_at",
                    "o": "e2",
                    "unit": _short_id(session, scene.id, action.id),
                    "conf": 0.9,
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
        # The model's two edges plus the pre-pass's occurs_at; the model's
        # location resolves to the same row the pre-pass wrote.
        assert outcome.assertions_written == 3
        assert session.scalar(select(func.count()).select_from(Entity)) == 2
        assert session.scalar(select(func.count()).select_from(Assertion)) == 3
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
                            "id": "e1",
                            "type": "transportation",
                            "name": "THE BLUE SEDAN",
                            "conf": 0.9,
                        }
                    ],
                    assertions=[
                        {
                            "s": "e1",
                            "p": "appears_in",
                            "o": "scene",
                            "unit": _short_id(session, job.scene_id, units[0].id),
                            "conf": 0.9,
                        }
                    ],
                ),
            )
            extract_scene(session, job, provider)

        transportation = session.scalar(
            select(func.count())
            .select_from(Entity)
            .where(Entity.entity_type == "transportation")
        )
        assert transportation == 1

    def test_a_fuller_cast_name_widens_the_existing_character(
        self, session, script
    ):
        """A cue that strictly extends an existing character ("LUBOV" then
        "LUBOV ANDREYEVNA") is one person, so it resolves to that entity and
        the fuller surface becomes an alias rather than a second node."""
        from ripple.extraction.service import _resolve_entity
        from ripple.extraction.validate import ValidatedEntity

        def cast(name):
            return ValidatedEntity(local_id="c", entity_type="cast", canonical_name=name)

        first = _resolve_entity(session, script.id, cast("LUBOV"))
        fuller = _resolve_entity(session, script.id, cast("LUBOV ANDREYEVNA"))
        assert first.id == fuller.id
        aliases = set(
            session.scalars(
                select(EntityAlias.normalized_alias).where(
                    EntityAlias.entity_id == first.id
                )
            )
        )
        assert "lubov andreyevna" in aliases

    def test_a_shorter_cast_name_resolves_to_the_fuller_character(
        self, session, script
    ):
        """The order does not matter: a later bare first name resolves to the
        character already known by the full name."""
        from ripple.extraction.service import _resolve_entity
        from ripple.extraction.validate import ValidatedEntity

        def cast(name):
            return ValidatedEntity(local_id="c", entity_type="cast", canonical_name=name)

        full = _resolve_entity(session, script.id, cast("LUBOV ANDREYEVNA"))
        short = _resolve_entity(session, script.id, cast("LUBOV"))
        assert full.id == short.id

    def test_an_ambiguous_extension_stays_a_separate_character(
        self, session, script
    ):
        """"MARY" extends both "MARY ANNE" and "MARY JANE", so which one it
        belongs to is unknown. It forks rather than guessing."""
        from ripple.extraction.service import _resolve_entity
        from ripple.extraction.validate import ValidatedEntity

        def cast(name):
            return ValidatedEntity(local_id="c", entity_type="cast", canonical_name=name)

        anne = _resolve_entity(session, script.id, cast("MARY ANNE"))
        jane = _resolve_entity(session, script.id, cast("MARY JANE"))
        mary = _resolve_entity(session, script.id, cast("MARY"))
        assert len({anne.id, jane.id, mary.id}) == 3

    def test_a_prop_name_does_not_prefix_merge(self, session, script):
        """The first-name shorthand is a character convention. A prop that
        contains another prop's name ("Blue" inside "Blue key") is not the
        same object, so props never prefix-merge."""
        from ripple.extraction.service import _resolve_entity
        from ripple.extraction.validate import ValidatedEntity

        def prop(name):
            return ValidatedEntity(local_id="p", entity_type="prop", canonical_name=name)

        short = _resolve_entity(session, script.id, prop("Blue"))
        longer = _resolve_entity(session, script.id, prop("Blue key"))
        assert short.id != longer.id

    def test_a_duplicate_edge_from_one_unit_is_skipped_not_fatal(
        self, session, script, tmp_path
    ):
        scene = script.scenes[13]
        units = _units_of(session, scene.id)
        edge = {
            "s": "e1",
            "p": "appears_in",
            "o": "scene",
            "unit": _short_id(session, scene.id, units[0].id),
            "conf": 0.9,
        }
        reply = _reply(
            entities=[
                {
                    "id": "e1",
                    "type": "prop",
                    "name": "Pallet jack",
                    "conf": 0.9,
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
        # One model edge survives the duplicate; the pre-pass wrote the
        # scene's occurs_at beside it.
        assert outcome.assertions_written == 2
        model_edges = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(Assertion.provenance == "model")
        )
        assert model_edges == 1


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

    def test_a_forced_run_reads_every_scene_again(self, session, script, tmp_path):
        """A rebuild exists to pick up changes the cache cannot see: a new
        parser or pre-pass rule produces different rows from identical input,
        and replaying the stored answer would hide that. So a forced run gives
        every scene a job and sends each to the model, where an ordinary
        second run sends none."""
        provider = _provider(tmp_path, _reply())
        first = start_run(session, script.id, MODEL)
        while (job := claim_next_scene(session, first.id)) is not None:
            extract_scene(session, job, provider)
        calls_after_first = len(provider.calls)
        assert calls_after_first > 0

        forced = start_run(session, script.id, MODEL, force=True)
        assert forced.forced is True
        while (job := claim_next_scene(session, forced.id)) is not None:
            extract_scene(session, job, provider)

        assert len(provider.calls) == calls_after_first * 2, "force did not re-read"

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
    """extract.v5: entities carry evidence-backed attributes."""

    def _reply_with_attributes(self, session, scene, attributes) -> str:
        units = _units_of(session, scene.id)
        action = next(u for u in units if u.unit_type == "action")
        return _reply(
            entities=[
                {
                    "id": "e1",
                    "type": "transportation",
                    "name": "Blue sedan",
                    "conf": 0.91,
                    "attrs": [
                        {**attribute, "unit": _short_id(session, scene.id, action.id)}
                        if "unit" not in attribute
                        else attribute
                        for attribute in attributes
                    ],
                }
            ],
            assertions=[
                {
                    "s": "e1",
                    "p": "appears_in",
                    "o": "scene",
                    "unit": _short_id(session, scene.id, action.id),
                    "conf": 0.88,
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
            [{"k": "color", "v": "blue", "conf": 0.9,
              "start": 0, "end": 4}],
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
            session, scene, [{"k": "  Color ", "v": "blue", "conf": 0.9}]
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
            [{"k": "color", "v": "blue", "conf": 0.9,
              "unit": "99999999-9999-9999-9999-999999999999"}],
        )
        outcome = self._extract(session, script, tmp_path, reply)
        assert outcome.attributes_written == 0
        assert self._rows(session) == []

    def test_an_attribute_without_a_value_is_dropped(self, session, script, tmp_path):
        scene = script.scenes[13]
        reply = self._reply_with_attributes(
            session, scene, [{"k": "color", "v": "  ", "conf": 0.9}]
        )
        outcome = self._extract(session, script, tmp_path, reply)
        assert outcome.attributes_written == 0

    def test_a_duplicate_key_keeps_the_first_value(self, session, script, tmp_path):
        scene = script.scenes[13]
        reply = self._reply_with_attributes(
            session,
            scene,
            [
                {"k": "color", "v": "blue", "conf": 0.9},
                {"k": "Color", "v": "grey", "conf": 0.9},
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
            session, scene, [{"k": "color", "v": "grey", "conf": 0.9}]
        )
        outcome = self._extract(session, script, tmp_path, reply)
        assert outcome.attributes_written == 0
        rows = self._rows(session)
        assert len(rows) == 1
        assert rows[0].value == "blue"

    def test_the_schema_and_prompt_version_advertise_v5(self):
        from ripple.extraction.prompt import OUTPUT_SCHEMA

        assert PROMPT_VERSION == "extract.v5"
        entity_schema = OUTPUT_SCHEMA["properties"]["entities"]["items"]
        assert "attrs" in entity_schema["properties"]


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
        # The written model rows carry the model that answered; the
        # pre-pass rows beside them carry none.
        row = session.scalars(
            select(Assertion).where(Assertion.provenance == "model")
        ).first()
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
                    "id": "e1",
                    "type": "transportation",
                    "name": "Blue sedan",
                    "conf": 0.9,
                }
            ],
            assertions=[
                {
                    "s": "e1",
                    "p": "appears_in",
                    "o": "scene",
                    "unit": _short_id(session, scene.id, action.id),
                    "conf": 0.9,
                }
            ],
        )


class TestExtractionEscalation:
    """A reply that fails validation escalates to the fallback model."""

    class _ByModelProvider:
        name = "fixture"
        credential_variable = "FIXTURE_API_KEY"

        def __init__(self, replies, finish_reasons=None):
            self.replies = replies
            self.finish_reasons = finish_reasons or {}
            self.calls_by_model = {}

        def is_configured(self):
            return True

        def list_models(self):
            return []

        def generate(self, model_id, prompt, **kwargs):
            from ripple.llm.base import GenerationResult

            self.calls_by_model[model_id] = (
                self.calls_by_model.get(model_id, 0) + 1
            )
            return GenerationResult(
                text=self.replies[model_id],
                model_id=model_id,
                provider=self.name,
                finish_reason=self.finish_reasons.get(model_id),
            )

    def _job_for(self, session, script, scene):
        run = start_run(session, script.id, MODEL)
        return session.scalar(
            select(SceneExtraction).where(
                SceneExtraction.extraction_run_id == run.id,
                SceneExtraction.scene_id == scene.id,
            )
        )

    def _good_reply(self, session, scene) -> str:
        units = _units_of(session, scene.id)
        action = next(u for u in units if u.unit_type == "action")
        return _reply(
            entities=[
                {
                    "id": "e1",
                    "type": "prop",
                    "name": "Clipboard",
                    "conf": 0.9,
                }
            ],
            assertions=[
                {
                    "s": "e1",
                    "p": "appears_in",
                    "o": "scene",
                    "unit": _short_id(session, scene.id, action.id),
                    "conf": 0.9,
                }
            ],
        )

    def test_a_malformed_reply_escalates_to_the_fallback(self, session, script):
        from ripple.db.models import ModelCall
        from ripple.db.repository import set_fallback_model

        set_fallback_model(session, "fixture", "fixture-mid")
        scene = script.scenes[13]
        provider = self._ByModelProvider(
            {
                MODEL: "this is not json",
                "fixture-mid": self._good_reply(session, scene),
            }
        )
        outcome = extract_scene(
            session, self._job_for(session, script, scene), provider
        )
        assert outcome.status == "completed"
        assert provider.calls_by_model == {MODEL: 1, "fixture-mid": 1}
        outcomes = {
            call.model_id: call.outcome
            for call in session.scalars(select(ModelCall))
        }
        assert outcomes[MODEL] == "malformed"
        assert outcomes["fixture-mid"] == "ok"

    def test_a_truncated_reply_escalates_to_the_fallback(self, session, script):
        from ripple.db.repository import set_fallback_model

        set_fallback_model(session, "fixture", "fixture-mid")
        scene = script.scenes[13]
        provider = self._ByModelProvider(
            {
                MODEL: '{"entities": [',
                "fixture-mid": self._good_reply(session, scene),
            },
            finish_reasons={MODEL: "max_tokens"},
        )
        outcome = extract_scene(
            session, self._job_for(session, script, scene), provider
        )
        assert outcome.status == "completed"
        assert provider.calls_by_model == {MODEL: 1, "fixture-mid": 1}

    def test_an_empty_answer_on_a_scene_with_content_escalates(
        self, session, script
    ):
        from ripple.db.models import ModelCall
        from ripple.db.repository import set_fallback_model

        set_fallback_model(session, "fixture", "fixture-mid")
        scene = script.scenes[13]
        provider = self._ByModelProvider(
            {
                MODEL: _reply(entities=[], assertions=[]),
                "fixture-mid": self._good_reply(session, scene),
            }
        )
        outcome = extract_scene(
            session, self._job_for(session, script, scene), provider
        )
        assert outcome.status == "completed"
        assert outcome.assertions_written == 2
        outcomes = {
            call.model_id: call.outcome
            for call in session.scalars(select(ModelCall))
        }
        assert outcomes[MODEL] == "incomplete"

    def test_no_fallback_keeps_the_malformed_failure(self, session, script):
        scene = script.scenes[13]
        provider = self._ByModelProvider({MODEL: "this is not json"})
        outcome = extract_scene(
            session, self._job_for(session, script, scene), provider
        )
        assert outcome.status in ("pending", "failed")
        assert outcome.error_code == "malformed_response"
        assert list(provider.calls_by_model) == [MODEL]


class TestEvidenceOffsetPairs:
    def _one_assertion(self, **offsets):
        return validate_response(
            _reply(
                entities=[
                    {
                        "id": "e1",
                        "type": "prop",
                        "name": "Brass key",
                        "conf": 0.9,
                    }
                ],
                assertions=[
                    {
                        "s": "e1",
                        "p": "appears_in",
                        "o": "scene",
                        "unit": "u1",
                        "conf": 0.9,
                        **offsets,
                    }
                ],
            ),
            {"u1"},
        )

    def test_a_half_offset_pair_is_dropped_whole(self):
        """Regression: (None, 42) used to survive, and slicing with a None
        side silently read from the start of the unit."""
        report = self._one_assertion(end=42)
        assertion = report.assertions[0]
        assert assertion.evidence_start is None
        assert assertion.evidence_end is None

    def test_a_full_pair_survives(self):
        report = self._one_assertion(start=4, end=13)
        assertion = report.assertions[0]
        assert (assertion.evidence_start, assertion.evidence_end) == (4, 13)

    def test_a_reversed_pair_is_dropped_whole(self):
        report = self._one_assertion(start=13, end=4)
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


class TestPendingSceneCount:
    """The count behind the Build graph gate: billable scenes only."""

    def _complete_all(self, session, script, model=MODEL):
        from datetime import UTC, datetime

        run = ExtractionRun(
            script_id=script.id,
            status="ready",
            prompt_version=PROMPT_VERSION,
            model_id=model,
            total_scenes=len(script.scenes),
            completed_scenes=len(script.scenes),
            started_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        for scene in script.scenes:
            digest = input_hash(scene.heading, _scene_units(session, scene.id))
            session.add(
                SceneExtraction(
                    extraction_run_id=run.id,
                    scene_id=scene.id,
                    status="completed",
                    input_hash=digest,
                    prompt_version=PROMPT_VERSION,
                    model_id=model,
                )
            )
        session.flush()

    def test_an_unbuilt_script_counts_every_scene(self, session, script):
        count = pending_scene_count(session, script.id, MODEL)
        assert count == len(script.scenes)

    def test_a_current_cache_counts_nothing(self, session, script):
        self._complete_all(session, script)
        assert pending_scene_count(session, script.id, MODEL) == 0

    def test_an_edited_scene_counts_again(self, session, script):
        self._complete_all(session, script)
        unit = next(
            unit
            for unit in script.scenes[0].units
            if unit.unit_type != "scene_heading"
        )
        unit.current_text = unit.current_text + " Edited."
        session.flush()
        assert pending_scene_count(session, script.id, MODEL) == 1

    def test_an_omitted_scene_never_counts(self, session, script):
        self._complete_all(session, script)
        scene = script.scenes[0]
        unit = next(
            unit for unit in scene.units if unit.unit_type != "scene_heading"
        )
        unit.current_text = unit.current_text + " Edited."
        scene.omitted = True
        session.flush()
        assert pending_scene_count(session, script.id, MODEL) == 0

    def test_a_different_model_counts_every_scene(self, session, script):
        self._complete_all(session, script)
        assert (
            pending_scene_count(session, script.id, "another-model")
            == len(script.scenes)
        )
