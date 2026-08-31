"""The spend ledger and the budget gate.

When the recorded total reaches the cap, every call site
refuses before contacting the provider, and the refusal itself is recorded.
The ledger aggregates `model_calls`, so these tests write audit rows and
check the arithmetic and the gate against them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from ripple.db.models import ModelCall
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.services.spend import (
    BudgetExceeded,
    check_budget,
    get_budget,
    set_budget,
    summary,
)


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


def record_call(session, purpose="judge", input_tokens=400, output_tokens=100):
    session.add(
        ModelCall(
            purpose=purpose,
            prompt_version="judge.v1",
            model_id="fake",
            request_text="prompt",
            outcome="ok",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    )
    session.flush()


class TestLedger:
    def test_an_empty_ledger_reads_zero(self, session):
        ledger = summary(session)
        assert ledger.calls == 0
        assert ledger.total_tokens == 0

    def test_the_ledger_sums_recorded_tokens_by_purpose(self, session):
        record_call(session, "judge", 400, 100)
        record_call(session, "extract", 900, 200)
        ledger = summary(session)
        assert ledger.calls == 2
        assert ledger.input_tokens == 1300
        assert ledger.output_tokens == 300
        assert ledger.by_purpose == {"judge": 500, "extract": 1100}

    def test_a_call_without_token_counts_still_counts_as_a_call(self, session):
        record_call(session, input_tokens=None, output_tokens=None)
        ledger = summary(session)
        assert ledger.calls == 1
        assert ledger.total_tokens == 0


class TestBudget:
    def test_no_budget_means_no_gate(self, session):
        record_call(session, input_tokens=10**9)
        check_budget(session)  # does not raise

    def test_under_budget_passes(self, session):
        set_budget(session, 1000)
        record_call(session, input_tokens=400, output_tokens=100)
        check_budget(session)

    def test_at_budget_refuses_and_records_the_refusal(self, session):
        set_budget(session, 500)
        record_call(session, input_tokens=400, output_tokens=100)
        refusal = ModelCall(
            purpose="judge",
            prompt_version="judge.v1",
            model_id="fake",
            request_text="the prompt that was not sent",
            outcome="ok",
        )
        with pytest.raises(BudgetExceeded) as caught:
            check_budget(session, refusal)
        assert "Settings" in caught.value.message
        stored = session.scalars(
            select(ModelCall).where(ModelCall.outcome == "budget_refused")
        ).one()
        assert stored.error_message == caught.value.message

    def test_a_refused_call_does_not_inflate_the_spend(self, session):
        """budget_refused rows carry no tokens, so refusals cannot compound."""
        set_budget(session, 500)
        record_call(session, input_tokens=500, output_tokens=0)
        refusal = ModelCall(
            purpose="judge",
            prompt_version="judge.v1",
            model_id="fake",
            request_text="p",
            outcome="ok",
        )
        with pytest.raises(BudgetExceeded):
            check_budget(session, refusal)
        assert summary(session).total_tokens == 500

    def test_clearing_the_budget_reopens_the_gate(self, session):
        set_budget(session, 100)
        record_call(session, input_tokens=400, output_tokens=100)
        with pytest.raises(BudgetExceeded):
            check_budget(session)
        set_budget(session, None)
        assert get_budget(session) is None
        check_budget(session)

    def test_raising_the_budget_reopens_the_gate(self, session):
        set_budget(session, 100)
        record_call(session, input_tokens=400, output_tokens=100)
        with pytest.raises(BudgetExceeded):
            check_budget(session)
        set_budget(session, 10_000)
        check_budget(session)


class TestCallSiteGates:
    def test_a_spent_budget_refuses_the_judge_before_the_provider(self, session):
        from ripple.services.preview import PreviewRefused, preview_changes
        from tests.test_preview import FakeJudge, build_world, edit_for

        world = build_world(session)
        set_budget(session, 100)
        record_call(session, input_tokens=400, output_tokens=100)
        judge = FakeJudge()
        with pytest.raises(PreviewRefused) as caught:
            preview_changes(session, edit_for(world), judge, "fake-judge")
        assert caught.value.code == "budget_exceeded"
        assert judge.calls == 0

    def test_a_spent_budget_fails_an_extraction_scene_without_retrying(
        self, session, night_freight_fountain
    ):
        from ripple.adapters import import_screenplay
        from ripple.db.repository import persist_import
        from ripple.extraction.service import (
            claim_next_scene,
            extract_scene,
            start_run,
        )
        from ripple.llm.fixture import FixtureProvider

        script = persist_import(
            session, import_screenplay(night_freight_fountain, "n.fountain")
        )
        set_budget(session, 100)
        record_call(session, input_tokens=400, output_tokens=100)

        class ExplodingProvider(FixtureProvider):
            def generate(self, *args, **kwargs):
                raise AssertionError("the provider must not be contacted")

        run = start_run(session, script.id, "fixture-cheap")
        job = claim_next_scene(session, run.id)
        outcome = extract_scene(
            session, job, ExplodingProvider(directory="unused")
        )
        assert outcome.status == "failed"
        assert outcome.error_code == "budget_exceeded"

    def test_synthesis_degrades_to_deterministic_when_the_budget_is_spent(
        self, session
    ):
        from ripple.graph.diff import GraphDiff
        from ripple.services.synthesizer import synthesize
        from tests.test_preview import FakeJudge

        set_budget(session, 100)
        record_call(session, input_tokens=400, output_tokens=100)
        synthesis = synthesize(
            GraphDiff(), [], FakeJudge(), "fake-judge", session=session
        )
        assert synthesis.source == "deterministic"
        assert "budget" in (synthesis.error or "").lower()


class TestRefusalsSurviveTheFailure:
    def test_a_preview_budget_refusal_reaches_the_ledger(self, session):
        """Regression: the refusal row was flushed and then rolled back with
        the exception, so billed-but-refused work never reached the ledger."""
        from ripple.services.preview import PreviewRefused, preview_changes
        from tests.test_preview import FakeJudge, build_world, edit_for

        world = build_world(session)
        set_budget(session, 100)
        record_call(session, input_tokens=400, output_tokens=100)
        with pytest.raises(PreviewRefused) as caught:
            preview_changes(session, edit_for(world), FakeJudge(), "fake-judge")
        assert caught.value.code == "budget_exceeded"
        refused = [
            call
            for call in session.scalars(select(ModelCall))
            if call.outcome == "budget_refused"
        ]
        assert len(refused) == 1
        assert refused[0].purpose == "judge"
