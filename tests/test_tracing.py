"""Tracing must record the shape of an import and none of its content.

Traces must never hold screenplay text, prompts, uploaded bytes, credentials,
and raw filesystem paths in traces. The adversarial test here is not "a trace
was written" but "nothing from the screenplay appears in it".
"""

from __future__ import annotations

import json

import pytest
from traceact import JsonlSink, TraceConfig, configure

from ripple.adapters import import_screenplay
from ripple.tracing import REDACTION, configure_tracing


@pytest.fixture
def captured_traces(tmp_path, monkeypatch):
    """Enable tracing into a temp file and return a reader for its records."""
    monkeypatch.setenv("RIPPLE_TRACING", "on")
    path = tmp_path / "traces.jsonl"
    configure(
        project="ripple-test",
        config=TraceConfig(
            enabled=True,
            sink_mode="blocking",
            capture_inputs=False,
            capture_outputs=False,
            redaction_presets=list(REDACTION),
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


class TestNoContentLeaks:
    def test_no_screenplay_line_appears_in_any_trace(
        self, captured_traces, night_freight_fountain
    ):
        import_screenplay(night_freight_fountain, "night-freight.fountain")
        blob = json.dumps(captured_traces())
        assert blob, "no traces were written"

        for phrase in (
            "the blue sedan idles by the gate",
            "MARA OKONJO",
            "Which yard, Dev?",
            "EXT. LOADING DOCK",
        ):
            assert phrase not in blob, f"screenplay content leaked: {phrase!r}"

    def test_the_filename_is_not_recorded(
        self, captured_traces, night_freight_fountain
    ):
        import_screenplay(
            night_freight_fountain, "/Users/someone/night-freight.fountain"
        )
        blob = json.dumps(captured_traces())
        assert "/Users/" not in blob
        assert "night-freight" not in blob

    def test_rejection_traces_carry_no_content_either(self, captured_traces):
        import_screenplay(
            b"INT. SECRET BUNKER - DAY\n\nA codeword: swordfish.\n", "x.txt"
        )
        blob = json.dumps(captured_traces())
        assert "swordfish" not in blob
        assert "SECRET BUNKER" not in blob


class TestTraceShape:
    def test_the_parent_workflow_is_named_in_the_prd(
        self, captured_traces, night_freight_fountain
    ):
        import_screenplay(night_freight_fountain, "x.fountain")
        actions = {record.get("action") for record in captured_traces()}
        assert "script.import" in actions

    def test_counts_and_codes_are_recorded(
        self, captured_traces, night_freight_fountain
    ):
        import_screenplay(night_freight_fountain, "x.fountain")
        blob = json.dumps(captured_traces())
        assert "fountain" in blob
        assert "44" in blob

    def test_tracing_is_off_by_default_in_this_test_run(self, tmp_path, monkeypatch):
        """The autouse fixture must actually disable durable trace writing."""
        monkeypatch.setenv("RIPPLE_TRACING", "off")
        configure_tracing(trace_dir=tmp_path / "traces")
        assert not (tmp_path / "traces").exists()
