"""TraceAct configuration for Ripple.

PRD section 12 requires traced application actions and forbids recording
screenplay text, prompts, model responses, uploaded bytes, credentials,
authorization headers, and raw filesystem paths.

Two settings carry that requirement. `capture_inputs=False` turns off automatic
argument capture, so a function taking screenplay bytes cannot leak them by
being decorated. Everything recorded is therefore passed explicitly, and the
call sites pass counts, codes, and hashes rather than content.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from traceact import JsonlSink, TraceBudget, TraceConfig, configure

logger = logging.getLogger(__name__)

PROJECT = "ripple"
DEFAULT_TRACE_DIR = Path("data/traces")
# Rotate at 8 MiB so a long extraction run cannot fill a Replit disk.
TRACE_FILE_MAX_BYTES = 8 * 1024 * 1024

# Field-name redaction layered on the always-on baseline. `ai_prompts` and the
# security presets are the ones PRD section 12 names.
REDACTION = ("ai_prompts", "api_keys", "http", "filesystem_paths", "env_vars")

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
            # Never capture arguments automatically: they hold screenplay bytes.
            capture_inputs=False,
            capture_outputs=False,
            redact_by_default=True,
            redact_values=True,
            redaction_presets=list(REDACTION),
        ),
        budget=TraceBudget(
            max_events=200,
            max_steps=100,
            max_depth=10,
            max_payload_bytes=4096,
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
