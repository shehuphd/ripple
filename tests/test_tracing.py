"""Tracing records full payloads, by explicit decision.

The traces are the debugging record: when a run fails, the answer is read
from them rather than reconstructed. So the adversarial test here is the
inverse of the usual one: the payloads MUST appear. The one thing still
guarded is credential-shaped values, caught by the value-pattern layer.
"""

from __future__ import annotations

import json

import pytest
from traceact import ActionTrace, JsonlSink, TraceBudget, TraceConfig, configure

from ripple.adapters import import_screenplay
from ripple.services.preview import MAX_OUTPUT_TOKENS
from ripple.tracing import model_event


@pytest.fixture
def captured_traces(tmp_path, monkeypatch):
    """Enable full-payload tracing into a temp file, mirroring production."""
    monkeypatch.setenv("RIPPLE_TRACING", "on")
    path = tmp_path / "traces.jsonl"
    configure(
        project="ripple-test",
        config=TraceConfig(
            enabled=True,
            sink_mode="blocking",
            capture_inputs=True,
            capture_outputs=True,
            capture_event_inputs=True,
            redact_by_default=False,
            redact_values=True,
            redaction_presets=[],
        ),
        budget=TraceBudget(
            max_events=1000,
            max_steps=500,
            max_depth=20,
            max_payload_bytes=262_144,
            always_trace_errors=True,
        ),
        sinks=[JsonlSink(str(path))],
    )
    from ripple import tracing

    monkeypatch.setattr(tracing, "_configured", True)

    def read() -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    yield read
    configure(config=TraceConfig(enabled=False, sink_mode="disabled"), sinks=[])


class TestFullPayloads:
    def test_the_import_records_its_source_and_title(
        self, captured_traces, night_freight_fountain
    ):
        import_screenplay(night_freight_fountain, "night-freight.fountain")
        blob = json.dumps(captured_traces())
        assert "night-freight.fountain" in blob
        assert "NIGHT FREIGHT" in blob
        assert "script.import" in blob

    def test_a_model_event_carries_request_reply_and_usage(self, captured_traces):
        from ripple.llm.base import GenerationResult

        with ActionTrace.start(action="test.model", kind="change"):
            model_event(
                purpose="judge",
                model_id="fake-judge",
                request="the full prompt text",
                response='{"verdicts": []}',
                result=GenerationResult(
                    text='{"verdicts": []}',
                    model_id="fake-judge",
                    provider="google",
                    input_tokens=5276,
                    output_tokens=1476,
                    reasoning_tokens=2620,
                    finish_reason="MAX_TOKENS",
                ),
                status="failed",
                duration_ms=1400,
            )
        blob = json.dumps(captured_traces())
        assert "the full prompt text" in blob
        assert '{\\"verdicts\\": []}' in blob or "verdicts" in blob
        assert "5276" in blob
        assert "1476" in blob
        assert "2620" in blob
        assert "MAX_TOKENS" in blob

    def test_token_count_fields_are_not_redacted(self, captured_traces):
        with ActionTrace.start(action="test.counts", kind="change") as trace:
            trace.output({"input_tokens": 111, "output_tokens": 222})
        blob = json.dumps(captured_traces())
        assert "111" in blob
        assert "222" in blob
        assert "[redacted]" not in blob

    def test_credential_shaped_values_still_never_land(self, captured_traces):
        with ActionTrace.start(action="test.leak", kind="change") as trace:
            trace.output({"note": "key sk-abcdefghijklmnop1234 in payload"})
        blob = json.dumps(captured_traces())
        assert "sk-abcdefghijklmnop1234" not in blob

    def test_a_model_event_without_an_active_trace_is_dropped(
        self, captured_traces
    ):
        model_event(
            purpose="judge",
            model_id="m",
            request="r",
            response="x",
        )
        assert all(
            record.get("action") != "test.orphan" for record in captured_traces()
        )


class TestConfiguration:
    def test_the_judgement_output_cap_matches_extraction(self):
        from ripple.extraction.service import MAX_OUTPUT_TOKENS as EXTRACT_CAP

        assert MAX_OUTPUT_TOKENS == EXTRACT_CAP == 8192

    def test_tracing_is_off_by_default_in_this_test_run(self, tmp_path, monkeypatch):
        """The autouse fixture must actually disable durable trace writing."""
        from ripple.tracing import configure_tracing

        monkeypatch.setenv("RIPPLE_TRACING", "off")
        configure_tracing(trace_dir=tmp_path / "traces")
        assert not (tmp_path / "traces").exists()
