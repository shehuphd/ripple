"""The judged preview pipeline, end to end against a fake judge.

The fake provider parses the prompt's own JSON payload and answers with
canned verdicts, so these tests exercise the same path a live call takes:
prompt assembly, validation, the audit row, the diff, and persistence.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from ripple.db.models import (
    Assertion,
    ChangeSet,
    Entity,
    EntityAttribute,
    ModelCall,
    RippleReport,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.llm.base import GenerationResult, ProviderError
from ripple.services.changeset import accept, undo_latest
from ripple.services.preview import (
    PreviewFailed,
    PreviewRefused,
    UnitEdit,
    preview_changes,
    word_diff,
)


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


def build_world(session):
    """One scene, one unit, a gown in the graph with color=emerald."""
    script = Script(
        title="The Understudy", import_status="accepted", current_version=1
    )
    session.add(script)
    session.flush()
    scene = Scene(
        script_id=script.id,
        sequence_index=13,
        heading="INT. DRESSING ROOM - NIGHT",
        display_scene_number="14",
    )
    session.add(scene)
    session.flush()
    unit = ScriptUnit(
        scene_id=scene.id,
        unit_type="action",
        sequence_index=0,
        current_text="The emerald gown hangs ready on the rail.",
        parser_method="fountain",
    )
    session.add(unit)
    session.flush()
    gown = Entity(
        script_id=script.id,
        entity_type="wardrobe",
        canonical_name="Emerald gown",
        normalized_name=normalize("Emerald gown"),
    )
    session.add(gown)
    session.flush()
    text = unit.current_text
    assertion = Assertion(
        script_id=script.id,
        subject_kind="entity",
        subject_entity_id=gown.id,
        predicate="appears_in",
        object_kind="scene",
        object_scene_id=scene.id,
        source_unit_id=unit.id,
        confidence=0.9,
        evidence_start=text.index("emerald gown"),
        evidence_end=text.index("emerald gown") + len("emerald gown"),
    )
    session.add(assertion)
    attribute = EntityAttribute(
        entity_id=gown.id,
        key="color",
        value="emerald",
        confidence=0.9,
        provenance="model",
        source_unit_id=unit.id,
        evidence_start=text.index("emerald"),
        evidence_end=text.index("emerald") + len("emerald"),
    )
    session.add(attribute)
    session.flush()
    return {
        "script": script,
        "scene": scene,
        "unit": unit,
        "gown": gown,
        "assertion": assertion,
        "attribute": attribute,
    }


class FakeJudge:
    """A provider whose judge replies come from the prompt it was sent.

    Every listed assertion holds with a fresh evidence span; every listed
    attribute whose value appears verbatim in a proposed text holds, and any
    other listed attribute is answered with `changed` to `self.new_value`.
    A continuity prompt is answered from `self.continuity_findings`, counted
    separately so `calls` keeps meaning judge calls.
    """

    name = "google"
    credential_variable = "GOOGLE_API_KEY"

    def __init__(self, new_value="red", errors=None, attribute_verdict="changed"):
        self.new_value = new_value
        self.errors = list(errors or [])
        self.attribute_verdict = attribute_verdict
        self.calls = 0
        self.continuity_calls = 0
        self.continuity_findings = []

    def is_configured(self):
        return True

    def list_models(self):
        return []

    def continuity_reply(self, model_id):
        self.continuity_calls += 1
        return GenerationResult(
            text=json.dumps({"findings": self.continuity_findings}),
            model_id=model_id,
            provider=self.name,
            input_tokens=200,
            output_tokens=20,
        )

    @staticmethod
    def is_continuity(prompt):
        return prompt.startswith("Check the proposed edit")

    def generate(self, model_id, prompt, **kwargs):
        if self.is_continuity(prompt):
            return self.continuity_reply(model_id)
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        payload = judge_payload(prompt)
        proposed = " ".join(
            edit["proposed_text"] for edit in payload["edited_units"]
        )
        assertion_verdicts = [
            {"assertion_id": item["id"], "verdict": "holds", "confidence": 0.9}
            for item in payload["assertions_to_judge"]
        ]
        attribute_verdicts = []
        for item in payload["attributes_to_judge"]:
            if item["value"] in proposed:
                attribute_verdicts.append(
                    {"attribute_id": item["id"], "verdict": "holds"}
                )
            elif self.attribute_verdict == "removed":
                attribute_verdicts.append(
                    {
                        "attribute_id": item["id"],
                        "verdict": "removed",
                        "confidence": 0.9,
                    }
                )
            else:
                attribute_verdicts.append(
                    {
                        "attribute_id": item["id"],
                        "verdict": "changed",
                        "new_value": self.new_value,
                        "confidence": 0.9,
                    }
                )
        return GenerationResult(
            text=json.dumps(
                {
                    "assertion_verdicts": assertion_verdicts,
                    "attribute_verdicts": attribute_verdicts,
                    "new_entities": [],
                    "new_assertions": [],
                    "new_attributes": [],
                }
            ),
            model_id=model_id,
            provider=self.name,
            input_tokens=500,
            output_tokens=80,
        )


def judge_payload(prompt):
    """The JSON payload at the end of a judge prompt.

    Located from the rules separator rather than a hard-coded serialisation
    prefix, so a formatting change in the prompt builder does not silently
    break every judge fake.
    """
    return json.loads(prompt[prompt.index("{", prompt.rindex("---")):])


def edit_for(world, new_text=None):
    return [
        UnitEdit(
            unit_id=str(world["unit"].id),
            proposed_text=new_text
            or "The red gown hangs ready on the rail.",
        )
    ]


class TestRefusals:
    def test_an_unchanged_text_is_refused_before_any_call(self, session):
        world = build_world(session)
        judge = FakeJudge()
        with pytest.raises(PreviewRefused) as caught:
            preview_changes(
                session,
                edit_for(world, world["unit"].current_text),
                judge,
                "fake-judge",
            )
        assert caught.value.code == "no_change"
        assert judge.calls == 0

    def test_no_model_is_refused_before_any_persistence(self, session):
        world = build_world(session)
        with pytest.raises(PreviewRefused) as caught:
            preview_changes(session, edit_for(world), None, None)
        assert caught.value.code == "no_model"
        assert session.scalar(select(func.count()).select_from(ChangeSet)) == 0

    def test_an_empty_graph_is_refused(self, session):
        world = build_world(session)
        session.delete(world["assertion"])
        session.flush()
        judge = FakeJudge()
        with pytest.raises(PreviewRefused) as caught:
            preview_changes(session, edit_for(world), judge, "fake-judge")
        assert caught.value.code == "no_baseline"
        assert judge.calls == 0

    def test_edits_across_two_scripts_are_refused(self, session):
        world = build_world(session)
        other = build_world(session)
        judge = FakeJudge()
        with pytest.raises(PreviewRefused) as caught:
            preview_changes(
                session,
                edit_for(world) + edit_for(other),
                judge,
                "fake-judge",
            )
        assert caught.value.code == "cross_script"
        assert judge.calls == 0


class TestJudgedPreview:
    def test_a_color_change_shows_as_an_attribute_change(self, session):
        world = build_world(session)
        judge = FakeJudge(new_value="red")
        result = preview_changes(session, edit_for(world), judge, "fake-judge")
        assert judge.calls == 1
        changes = [(c.key, c.before, c.after) for c in result.attribute_changes]
        assert ("color", "emerald", "red") in changes
        assert result.severity in ("medium", "high")
        assert "color" in result.summary

    def test_the_word_diff_marks_the_changed_word(self, session):
        world = build_world(session)
        judge = FakeJudge()
        result = preview_changes(session, edit_for(world), judge, "fake-judge")
        segments = result.edits[0]["segments"]
        assert {"op": "del", "text": "emerald"} in segments
        assert {"op": "ins", "text": "red"} in segments

    def test_the_call_is_audited(self, session):
        world = build_world(session)
        preview_changes(session, edit_for(world), FakeJudge(), "fake-judge")
        call = session.scalars(
            select(ModelCall).where(ModelCall.purpose == "judge")
        ).one()
        assert call.purpose == "judge"
        assert call.outcome == "ok"
        assert call.input_tokens == 500
        assert call.change_set_id is not None

    def test_the_continuity_call_is_audited(self, session):
        world = build_world(session)
        preview_changes(session, edit_for(world), FakeJudge(), "fake-judge")
        call = session.scalars(
            select(ModelCall).where(ModelCall.purpose == "continuity")
        ).one()
        assert call.outcome == "ok"
        assert call.change_set_id is not None
        assert call.validation_json["kept"] == 0

    def test_an_identical_second_preview_is_served_from_the_store(self, session):
        world = build_world(session)
        judge = FakeJudge()
        first = preview_changes(session, edit_for(world), judge, "fake-judge")
        second = preview_changes(session, edit_for(world), judge, "fake-judge")
        assert judge.calls == 1
        assert second.cached is True
        assert second.change_set.id == first.change_set.id
        changes = [(c.key, c.before, c.after) for c in second.attribute_changes]
        assert ("color", "emerald", "red") in changes

    def test_a_cached_preview_keeps_a_removed_attribute(self, session):
        """The rebuild reads remove_entity_attribute operations too."""
        world = build_world(session)
        judge = FakeJudge(attribute_verdict="removed")
        preview_changes(session, edit_for(world), judge, "fake-judge")
        second = preview_changes(session, edit_for(world), judge, "fake-judge")
        assert second.cached is True
        changes = [(c.key, c.before, c.after) for c in second.attribute_changes]
        assert ("color", "emerald", None) in changes

    def test_a_different_edit_is_not_served_from_the_store(self, session):
        world = build_world(session)
        judge = FakeJudge()
        preview_changes(session, edit_for(world), judge, "fake-judge")
        second = preview_changes(
            session,
            edit_for(world, "The grey gown hangs ready on the rail."),
            judge,
            "fake-judge",
        )
        assert judge.calls == 2
        assert second.cached is False

    def test_a_retryable_error_is_retried_once(self, session):
        world = build_world(session)
        judge = FakeJudge(
            errors=[ProviderError("rate_limited", "slow down")]
        )
        result = preview_changes(session, edit_for(world), judge, "fake-judge")
        assert judge.calls == 2
        assert result.change_set is not None


class TestFailure:
    def test_a_failed_call_persists_no_proposal(self, session):
        world = build_world(session)
        judge = FakeJudge(
            errors=[
                ProviderError("model_not_available", "dead model"),
            ]
        )
        with pytest.raises(PreviewFailed) as caught:
            preview_changes(session, edit_for(world), judge, "fake-judge")
        assert caught.value.code == "model_not_available"
        assert session.scalar(select(func.count()).select_from(ChangeSet)) == 0
        assert (
            session.scalar(select(func.count()).select_from(RippleReport)) == 0
        )

    def test_a_failed_call_is_still_audited(self, session):
        world = build_world(session)
        judge = FakeJudge(
            errors=[ProviderError("model_not_available", "dead model")]
        )
        with pytest.raises(PreviewFailed):
            preview_changes(session, edit_for(world), judge, "fake-judge")
        call = session.scalars(select(ModelCall)).one()
        assert call.outcome == "provider_error"
        assert call.change_set_id is None


class TestAcceptRoundTrip:
    def test_accepting_applies_the_attribute_and_undo_restores_it(self, session):
        world = build_world(session)
        result = preview_changes(
            session, edit_for(world), FakeJudge(new_value="red"), "fake-judge"
        )
        accept(session, result.change_set.id)

        def active_color():
            return session.scalar(
                select(EntityAttribute.value).where(
                    EntityAttribute.entity_id == world["gown"].id,
                    EntityAttribute.key == "color",
                    EntityAttribute.active.is_(True),
                )
            )

        assert active_color() == "red"
        assert world["unit"].current_text.startswith("The red gown")

        undo_latest(session, world["unit"].id)
        assert active_color() == "emerald"
        assert world["unit"].current_text.startswith("The emerald gown")


class TestWordDiff:
    def test_a_single_word_swap(self):
        segments = word_diff("the emerald gown", "the red gown")
        assert {"op": "del", "text": "emerald"} in segments
        assert {"op": "ins", "text": "red"} in segments

    def test_identical_texts_have_no_marks(self):
        segments = word_diff("same text", "same text")
        assert all(s["op"] == "equal" for s in segments)

    def test_the_join_is_lossless_on_both_sides(self):
        before = "A grey parka hangs by the dock gate."
        after = "A blue parka hangs by the open dock gate."
        segments = word_diff(before, after)
        rebuilt_before = " ".join(
            s["text"] for s in segments if s["op"] in ("equal", "del")
        )
        rebuilt_after = " ".join(
            s["text"] for s in segments if s["op"] in ("equal", "ins")
        )
        assert rebuilt_before == before
        assert rebuilt_after == after


class TestDeadModelMarking:
    def test_a_dead_model_is_marked_by_a_failed_judgement(self, session):
        from ripple.db.repository import unavailable_models

        world = build_world(session)
        judge = FakeJudge(
            errors=[ProviderError("model_not_available", "dead model")]
        )
        with pytest.raises(PreviewFailed):
            preview_changes(session, edit_for(world), judge, "fake-judge")
        assert "fake-judge" in unavailable_models(session, "google")

    def test_a_successful_judgement_clears_the_mark(self, session):
        from ripple.db.repository import mark_model_unavailable, unavailable_models

        world = build_world(session)
        mark_model_unavailable(session, "google", "fake-judge")
        preview_changes(session, edit_for(world), FakeJudge(), "fake-judge")
        assert "fake-judge" not in unavailable_models(session, "google")

    def test_a_dead_model_is_marked_by_a_failed_synthesis(self, session):
        from ripple.db.repository import unavailable_models
        from ripple.graph.diff import GraphDiff
        from ripple.services.synthesizer import synthesize

        judge = FakeJudge(
            errors=[ProviderError("model_not_available", "dead model")]
        )
        synthesis = synthesize(GraphDiff(), [], judge, "fake-judge", session=session)
        assert not synthesis.generated
        assert "fake-judge" in unavailable_models(session, "google")

    def test_a_successful_synthesis_clears_the_mark(self, session):
        from ripple.db.repository import mark_model_unavailable, unavailable_models
        from ripple.graph.diff import GraphDiff
        from ripple.services.synthesizer import synthesize

        class PlainSpeaker:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def is_configured(self):
                return True

            def list_models(self):
                return []

            def generate(self, model_id, prompt, **kwargs):
                return GenerationResult(
                    text="A summary.",
                    model_id=model_id,
                    provider=self.name,
                    input_tokens=10,
                    output_tokens=5,
                )

        mark_model_unavailable(session, "google", "fake-judge")
        synthesize(GraphDiff(), [], PlainSpeaker(), "fake-judge", session=session)
        assert "fake-judge" not in unavailable_models(session, "google")


class OneDeadModel:
    """A provider where one named model is down and every other one answers."""

    name = "google"
    credential_variable = "GOOGLE_API_KEY"

    def __init__(self, dead, code="provider_unavailable"):
        self.inner = FakeJudge()
        self.dead = dead
        self.code = code
        self.calls_by_model = {}

    def is_configured(self):
        return True

    def list_models(self):
        return []

    def generate(self, model_id, prompt, **kwargs):
        self.calls_by_model[model_id] = self.calls_by_model.get(model_id, 0) + 1
        if model_id == self.dead:
            raise ProviderError(self.code, "down right now")
        return self.inner.generate(model_id, prompt, **kwargs)


class TestFallbackModel:
    """When the main model is unavailable, the configured fallback answers."""

    def _with_fallback(self, session):
        from ripple.db.repository import set_fallback_model

        set_fallback_model(session, "google", "backup-model")

    def test_the_fallback_answers_when_the_main_model_is_down(self, session):
        world = build_world(session)
        self._with_fallback(session)
        provider = OneDeadModel(dead="main-model")
        result = preview_changes(session, edit_for(world), provider, "main-model")
        # The judge call and the continuity call each fail over.
        assert provider.calls_by_model["backup-model"] == 2
        changes = [(c.key, c.before, c.after) for c in result.attribute_changes]
        assert ("color", "emerald", "red") in changes

    def test_both_attempts_are_audited(self, session):
        world = build_world(session)
        self._with_fallback(session)
        preview_changes(
            session, edit_for(world), OneDeadModel(dead="main-model"), "main-model"
        )
        outcomes = {
            call.model_id: call.outcome
            for call in session.scalars(select(ModelCall))
        }
        assert outcomes["main-model"] == "provider_error"
        assert outcomes["backup-model"] == "ok"

    def test_the_report_records_the_answering_model(self, session):
        world = build_world(session)
        self._with_fallback(session)
        result = preview_changes(
            session, edit_for(world), OneDeadModel(dead="main-model"), "main-model"
        )
        report = session.scalar(
            select(RippleReport).where(
                RippleReport.change_set_id == result.change_set.id
            )
        )
        assert report.model_id == "backup-model"

    def test_no_fallback_means_the_failure_stands(self, session):
        world = build_world(session)
        provider = OneDeadModel(dead="main-model")
        with pytest.raises(PreviewFailed):
            preview_changes(session, edit_for(world), provider, "main-model")
        assert "backup-model" not in provider.calls_by_model

    def test_a_malformed_reply_never_fails_over(self, session):
        """The fallback covers availability, not a model answering badly."""
        world = build_world(session)
        self._with_fallback(session)

        class MalformedJudge(FakeJudge):
            def generate(self, model_id, prompt, **kwargs):
                self.calls += 1
                return GenerationResult(
                    text="not json at all",
                    model_id=model_id,
                    provider=self.name,
                )

        provider = MalformedJudge()
        with pytest.raises(PreviewFailed) as caught:
            preview_changes(session, edit_for(world), provider, "main-model")
        assert caught.value.code == "malformed_response"
        assert provider.calls == 1

    def test_a_dead_fallback_fails_the_call(self, session):
        world = build_world(session)
        self._with_fallback(session)

        class AllDead(OneDeadModel):
            def generate(self, model_id, prompt, **kwargs):
                self.calls_by_model[model_id] = (
                    self.calls_by_model.get(model_id, 0) + 1
                )
                raise ProviderError("provider_unavailable", "everything is down")

        provider = AllDead(dead="main-model")
        with pytest.raises(PreviewFailed):
            preview_changes(session, edit_for(world), provider, "main-model")
        assert "backup-model" in provider.calls_by_model


class EmptyVerdictJudge(FakeJudge):
    """Schema-valid reply that judges nothing."""

    def generate(self, model_id, prompt, **kwargs):
        if self.is_continuity(prompt):
            return self.continuity_reply(model_id)
        self.calls += 1
        return GenerationResult(
            text=json.dumps(
                {
                    "assertion_verdicts": [],
                    "attribute_verdicts": [],
                    "new_entities": [],
                    "new_assertions": [],
                    "new_attributes": [],
                }
            ),
            model_id=model_id,
            provider=self.name,
            input_tokens=100,
            output_tokens=10,
        )


class TestIncompleteJudgement:
    def test_an_empty_reply_fails_rather_than_passing_as_no_change(self, session):
        """Regression: missing verdicts used to default to holds, so a reply
        that judged nothing rendered as a green "No graph change"."""
        world = build_world(session)
        judge = EmptyVerdictJudge()
        with pytest.raises(PreviewFailed) as caught:
            preview_changes(session, edit_for(world), judge, "fake-judge")
        assert caught.value.code == "incomplete_judgement"
        call = session.scalars(select(ModelCall)).one()
        assert call.outcome == "incomplete"
        assert session.scalar(select(func.count()).select_from(ChangeSet)) == 0


class TestJudgeReplay:
    def test_a_rerun_replays_the_recorded_judgement_at_no_cost(self, session):
        """A judged scene is never re-billed for the same prompt: the reply
        is replayed from the recorded call."""
        from ripple.services.changeset import reject

        world = build_world(session)
        judge = FakeJudge()
        first = preview_changes(session, edit_for(world), judge, "fake-judge")
        # Rejecting removes the pending proposal from the store, so the next
        # identical preview cannot be served from it and must judge again.
        reject(session, first.change_set.id)

        second = preview_changes(session, edit_for(world), judge, "fake-judge")
        assert judge.calls == 1
        assert second.cached is False
        outcomes = sorted(
            (call.outcome, call.output_tokens or 0)
            for call in session.scalars(select(ModelCall))
        )
        assert ("cached", 0) in outcomes
        changes = [(c.key, c.before, c.after) for c in second.attribute_changes]
        assert ("color", "emerald", "red") in changes


class NewEntityJudge(FakeJudge):
    """Holds everything and adds one cased new prop to the scene."""

    def generate(self, model_id, prompt, **kwargs):
        if self.is_continuity(prompt):
            return self.continuity_reply(model_id)
        self.calls += 1
        payload = judge_payload(prompt)
        unit_id = payload["edited_units"][0]["unit_id"]
        return GenerationResult(
            text=json.dumps(
                {
                    "assertion_verdicts": [
                        {"assertion_id": item["id"], "verdict": "holds"}
                        for item in payload["assertions_to_judge"]
                    ],
                    "attribute_verdicts": [
                        {"attribute_id": item["id"], "verdict": "holds"}
                        for item in payload["attributes_to_judge"]
                    ],
                    "new_entities": [
                        {
                            "id": "e1",
                            "type": "prop",
                            "name": "The Brass Key",
                            "conf": 0.9,
                        }
                    ],
                    "new_assertions": [
                        {
                            "s": "e1",
                            "p": "appears_in",
                            "o": "scene",
                            "unit": unit_id,
                            "conf": 0.9,
                        }
                    ],
                    "new_attributes": [],
                }
            ),
            model_id=model_id,
            provider=self.name,
            input_tokens=500,
            output_tokens=80,
        )


class TestJudgedEntityNaming:
    def test_an_accepted_new_entity_keeps_its_cased_name(self, session):
        """Regression: the diff payload used to carry the normalized ref, so
        the created entity was stored and shown as "the brass key"."""
        world = build_world(session)
        edits = edit_for(
            world, "The emerald gown hangs ready. The Brass Key waits."
        )
        result = preview_changes(session, edits, NewEntityJudge(), "fake-judge")
        accept(session, result.change_set.id)
        created = session.scalar(
            select(Entity).where(
                Entity.script_id == world["script"].id,
                Entity.normalized_name == normalize("The Brass Key"),
            )
        )
        assert created is not None
        assert created.canonical_name == "The Brass Key"


class TestContinuityJudge:
    """The advisory model pass over the evidence packet, one call per preview."""

    def _conflict_for(self, world):
        return {
            "message": "Scene 14 relies on the gown staying emerald.",
            "severity": "medium",
            "evidence_ids": [str(world["assertion"].id)],
            "confidence": 0.8,
        }

    def test_a_cited_conflict_becomes_a_finding(self, session):
        world = build_world(session)
        judge = FakeJudge()
        judge.continuity_findings = [self._conflict_for(world)]
        result = preview_changes(session, edit_for(world), judge, "fake-judge")
        assert judge.continuity_calls == 1
        conflicts = [
            finding
            for finding in result.findings
            if finding.finding_type == "continuity_conflict"
        ]
        assert len(conflicts) == 1
        assert conflicts[0].severity == "medium"
        assert conflicts[0].later_unit_ids == [str(world["unit"].id)]
        assert result.continuity_error is None

    def test_an_uncited_claim_reaches_no_finding(self, session):
        world = build_world(session)
        judge = FakeJudge()
        judge.continuity_findings = [
            {
                "message": "Something feels off.",
                "severity": "high",
                "evidence_ids": ["not-an-id"],
            }
        ]
        result = preview_changes(session, edit_for(world), judge, "fake-judge")
        assert all(
            finding.finding_type != "continuity_conflict"
            for finding in result.findings
        )
        call = session.scalars(
            select(ModelCall).where(ModelCall.purpose == "continuity")
        ).one()
        assert call.validation_json["dropped_uncited"] == 1

    def test_a_failed_continuity_call_degrades_instead_of_failing(self, session):
        world = build_world(session)

        class DeadContinuity(FakeJudge):
            def generate(self, model_id, prompt, **kwargs):
                if self.is_continuity(prompt):
                    raise ProviderError("model_not_available", "gone on this key")
                return super().generate(model_id, prompt, **kwargs)

        result = preview_changes(session, edit_for(world), DeadContinuity(), "fake-judge")
        assert result.change_set is not None
        assert result.continuity_error
        call = session.scalars(
            select(ModelCall).where(ModelCall.purpose == "continuity")
        ).one()
        assert call.outcome == "provider_error"

    def test_a_malformed_continuity_reply_degrades_instead_of_failing(self, session):
        world = build_world(session)

        class GarbledContinuity(FakeJudge):
            def generate(self, model_id, prompt, **kwargs):
                if self.is_continuity(prompt):
                    self.continuity_calls += 1
                    return GenerationResult(
                        text="not json", model_id=model_id, provider=self.name
                    )
                return super().generate(model_id, prompt, **kwargs)

        result = preview_changes(
            session, edit_for(world), GarbledContinuity(), "fake-judge"
        )
        assert result.change_set is not None
        assert result.continuity_error
        call = session.scalars(
            select(ModelCall).where(ModelCall.purpose == "continuity")
        ).one()
        assert call.outcome == "malformed"

    def test_a_rebuilt_preview_keeps_the_conflict_and_its_citations(self, session):
        world = build_world(session)
        judge = FakeJudge()
        judge.continuity_findings = [self._conflict_for(world)]
        preview_changes(session, edit_for(world), judge, "fake-judge")
        second = preview_changes(session, edit_for(world), judge, "fake-judge")
        assert second.cached is True
        assert judge.continuity_calls == 1
        conflicts = [
            finding
            for finding in second.findings
            if finding.finding_type == "continuity_conflict"
        ]
        assert len(conflicts) == 1
        assert conflicts[0].later_unit_ids == [str(world["unit"].id)]


class TestHonestSummary:
    """The summary states what was judged, never a bare "no change"."""

    def test_all_holding_names_the_judged_count(self, session):
        world = build_world(session)
        result = preview_changes(
            session,
            edit_for(world, "The emerald gown hangs ready on the rail again."),
            FakeJudge(),
            "fake-judge",
        )
        assert "All 2 stored fact(s)" in result.summary
        assert result.attribute_changes == []

    def test_an_untracked_line_says_the_graph_is_incomplete(self, session):
        world = build_world(session)
        bare = ScriptUnit(
            scene_id=world["scene"].id,
            unit_type="action",
            sequence_index=1,
            current_text="Six monitors, four of them dead.",
            parser_method="fountain",
        )
        session.add(bare)
        session.flush()
        result = preview_changes(
            session,
            [UnitEdit(unit_id=str(bare.id), proposed_text="Twelve monitors.")],
            FakeJudge(),
            "fake-judge",
        )
        assert "holds nothing extracted" in result.summary


class TestMentionedListing:
    """An edit naming an entity brings its scene-local facts to the judge."""

    def _knife_world(self, session):
        world = build_world(session)
        other = ScriptUnit(
            scene_id=world["scene"].id,
            unit_type="action",
            sequence_index=1,
            current_text="Vera grips the brass knife.",
            parser_method="fountain",
        )
        session.add(other)
        session.flush()
        knife = Entity(
            script_id=world["script"].id,
            entity_type="prop",
            canonical_name="Brass knife",
            normalized_name=normalize("Brass knife"),
        )
        session.add(knife)
        session.flush()
        fact = Assertion(
            script_id=world["script"].id,
            subject_kind="entity",
            subject_entity_id=knife.id,
            predicate="appears_in",
            object_kind="scene",
            object_scene_id=world["scene"].id,
            source_unit_id=other.id,
            confidence=0.9,
        )
        session.add(fact)
        session.flush()
        return world, fact

    def test_a_named_entity_lists_its_untouched_facts(self, session):
        from ripple.services.preview import _listed

        world, fact = self._knife_world(session)
        listed, _ = _listed(
            session,
            [(world["unit"], "The gown falls as the brass knife clatters.")],
        )
        assert str(fact.id) in listed

    def test_an_unmentioned_entity_stays_unlisted(self, session):
        from ripple.services.preview import _listed

        world, fact = self._knife_world(session)
        listed, _ = _listed(
            session,
            [(world["unit"], "The gown falls from the rail.")],
        )
        assert str(fact.id) not in listed
