"""TraceAct configuration for Ripple.

Full-payload tracing, by explicit decision: when something fails at
runtime, the trace answers the question, so nobody has to reconstruct a
cause from partial records. Prompts, model replies, screenplay text, and
token counts are all recorded. Traces live in `data/traces/`, which is
local and gitignored; the one thing still guarded is credential-shaped
values, caught by the value-pattern layer (`redact_values=True`), which
matches key formats rather than field names and leaves ordinary payloads
alone.

KeyCall's own spans pin their redaction on internally and pass no
prompts, so full model payloads come from Ripple's events instead: the
`model_event` helper below records the request, the reply, and the usage
split (answer, reasoning, input) on the active trace at every provider
call site.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from traceact import JsonlSink, TraceBudget, TraceConfig, configure
from traceact.context import get_active_trace

logger = logging.getLogger(__name__)

PROJECT = "ripple"
DEFAULT_TRACE_DIR = Path("data/traces")
# Rotate at 32 MiB: full payloads are bulky, and rotation keeps any one
# file readable; the viewer merges the folder, so nothing is lost.
TRACE_FILE_MAX_BYTES = 32 * 1024 * 1024

_configured = False


def configure_tracing(
    project: str = PROJECT,
    trace_dir: Path | None = None,
    enabled: bool | None = None,
) -> None:
    """Configure TraceAct for this process.

    Call once at application startup. `enabled` defaults to the RIPPLE_TRACING
    environment variable, which tests set to "off" so a test run writes no
    trace files.
    """
    if enabled is None:
        enabled = os.environ.get("RIPPLE_TRACING", "on").lower() not in (
            "off",
            "0",
            "false",
        )

    directory = trace_dir or DEFAULT_TRACE_DIR
    sinks = []
    if enabled:
        directory.mkdir(parents=True, exist_ok=True)
        sinks.append(
            JsonlSink(str(directory / "traces.jsonl"), max_bytes=TRACE_FILE_MAX_BYTES)
        )

    configure(
        project=project,
        config=TraceConfig(
            enabled=enabled,
            sink_mode="blocking" if enabled else "disabled",
            capture_inputs=True,
            capture_outputs=True,
            capture_event_inputs=True,
            # Field-name redaction off: it scrubs any *_tokens field and
            # every payload the debugging depends on. The value-pattern
            # layer stays on to catch credential-shaped strings (sk-…,
            # Bearer …) that could stray into a payload.
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
        sinks=sinks,
    )

    global _configured
    _configured = True
    logger.debug("tracing configured: enabled=%s project=%s", enabled, project)


def ensure_configured() -> None:
    """Configure tracing on first use if the application has not done so.

    Ripple is an application rather than a general-purpose library, so a
    sensible default beats a UserWarning on every trace. An explicit earlier
    call to configure_tracing wins.
    """
    if not _configured:
        configure_tracing()


def model_event(
    *,
    purpose: str,
    model_id: str,
    request: str,
    response: str | None,
    result: Any = None,
    status: str = "completed",
    error: str | None = None,
    duration_ms: float | None = None,
) -> None:
    """Record one model interaction, payloads included, on the active trace.

    KeyCall's spans carry provider and timing but redact themselves, so
    this event is where the request, the reply, and the usage split live.
    A call site with no active trace records nothing rather than raising.
    """
    trace = get_active_trace()
    if trace is None:
        return
    usage = {}
    if result is not None:
        usage = {
            "tokens_in": result.input_tokens,
            "tokens_out": result.output_tokens,
            "tokens_reasoning": getattr(result, "reasoning_tokens", None),
            "finish_reason": result.finish_reason,
        }
    trace.event(
        kind="model",
        operation=purpose,
        target=model_id,
        status=status,
        duration_ms=duration_ms,
        input={"request": request},
        result={"response": response, **usage},
        error=error,
    )
