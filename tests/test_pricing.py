"""Money from the rates registry, correct and honest about the unknown."""

from __future__ import annotations

from ripple.services import pricing


class FakeCall:
    def __init__(self, model_id, tokens_in, tokens_out, reasoning=None):
        self.model_id = model_id
        self.input_tokens = tokens_in
        self.output_tokens = tokens_out
        self.reasoning_tokens = reasoning


class TestCostUsd:
    def test_a_known_google_model_prices_by_the_registry(self):
        # gemini-2.5-flash: $0.30 per input Mtok, $2.50 per output Mtok.
        cost = pricing.cost_usd("google", "gemini-2.5-flash", 1_000_000, 1_000_000)
        assert cost is not None
        assert abs(cost - 2.8) < 1e-9

    def test_reasoning_bills_at_the_output_rate(self):
        base = pricing.cost_usd("google", "gemini-2.5-flash", 0, 100_000)
        with_thinking = pricing.cost_usd(
            "google", "gemini-2.5-flash", 0, 0, reasoning_tokens=100_000
        )
        assert base == with_thinking

    def test_an_unknown_model_has_no_price(self):
        assert pricing.cost_usd("google", "fake-judge", 1000, 1000) is None

    def test_the_latest_alias_is_priced(self):
        # The model Ripple defaults to must resolve in the registry.
        assert pricing.cost_usd("google", "gemini-flash-latest", 1000, 1000)


class TestCallsCost:
    def test_unpriced_calls_are_skipped_not_zeroed(self):
        calls = [
            FakeCall("gemini-2.5-flash", 1_000_000, 0),
            FakeCall("fake-judge", 999, 999),
        ]
        cost = pricing.calls_cost_usd(calls)
        assert cost is not None
        assert abs(cost - 0.3) < 1e-9

    def test_no_priced_call_means_no_total(self):
        assert pricing.calls_cost_usd([FakeCall("fake-judge", 1, 1)]) is None


class TestDisplay:
    def test_formats_scale_with_the_amount(self):
        assert pricing.display(0.31) == "$0.31"
        assert pricing.display(0.0043) == "$0.0043"
        assert pricing.display(0.00003) == "under $0.0001"
        assert pricing.display(0.0) == "$0.00"
        assert pricing.display(None) is None
