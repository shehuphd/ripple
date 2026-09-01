"""Adapter registry and the single import entry point.

Callers use `import_screenplay`. It detects the format, dispatches to the
adapter that claims it, runs the screenplay check, and returns an ImportResult
whatever happens. No exception from a parser reaches the caller: a failed
import is a rejected result with a stable code, because the UI has to explain
every refusal and a traceback explains nothing.
"""

from __future__ import annotations

import logging

from traceact import ActionTrace

from ripple.adapters.base import (
    MAX_SCENES,
    MAX_UNITS_PER_SCENE,
    DetectedFormat,
    ImportAdapter,
    ImportOutcome,
    ImportRejected,
    ImportResult,
    ImportWarning,
    ParsedScene,
    ParsedUnit,
    ParserMethod,
    SourceAnchor,
    SourcePayload,
    UnitType,
)
from ripple.adapters.detect import detect_format
from ripple.adapters.fdx import FdxAdapter
from ripple.adapters.fountain import FountainAdapter
from ripple.adapters.pdf import PdfAdapter
from ripple.adapters.plaintext import PlainTextAdapter
from ripple.adapters.screenplay_check import assess, verdict_warnings
from ripple.tracing import ensure_configured

logger = logging.getLogger(__name__)

ADAPTERS: dict[DetectedFormat, ImportAdapter] = {
    DetectedFormat.FOUNTAIN: FountainAdapter(),
    DetectedFormat.FDX: FdxAdapter(),
    DetectedFormat.PDF: PdfAdapter(),
    DetectedFormat.PLAIN_TEXT: PlainTextAdapter(),
}

__all__ = [
    "ADAPTERS",
    "DetectedFormat",
    "ImportOutcome",
    "ImportRejected",
    "ImportResult",
    "ImportWarning",
    "ParsedScene",
    "ParsedUnit",
    "ParserMethod",
    "SourceAnchor",
    "SourcePayload",
    "UnitType",
    "import_screenplay",
]


def import_screenplay(data: bytes, source_name: str = "upload") -> ImportResult:
    """Detect, parse, and validate an uploaded screenplay.

    Never raises for bad input. A payload that cannot become a screenplay
    returns an ImportResult with outcome REJECTED and a rejection_code the UI
    can map to an explanation.
    """
    ensure_configured()
    with ActionTrace.start(action="script.import", kind="import") as trace:
        result = _import(data, source_name, trace)
        trace.output(
            {
                "title": result.title,
                "outcome": result.outcome.value,
                "detected_format": result.detected_format.value,
                "adapter": result.adapter_name,
                "scene_count": result.scene_count,
                "unit_count": result.unit_count,
                "warning_codes": [warning.code for warning in result.warnings],
                "rejection_code": result.rejection_code,
            }
        )
        return result


def _import(data: bytes, source_name: str, trace: ActionTrace) -> ImportResult:
    """Run the import pipeline, recording each stage on the open trace.

    The trace records the filename, title, counts, codes, hashes, and
    formats; the raw uploaded bytes stay out only for bulk, since the
    content hash identifies them.
    """
    try:
        payload = SourcePayload(data=data, suggested_name=source_name)
    except ImportRejected as rejection:
        return _rejected(rejection, source_name, "", DetectedFormat.UNKNOWN, "none")

    trace.input(
        {
            "source_name": source_name,
            "byte_length": len(data),
            "source_extension": payload.extension,
        }
    )
    trace.set_meta("content_hash", payload.content_hash)

    detected, confidence = detect_format(payload)
    trace.step(f"Detected {detected.value}")
    trace.event(
        kind="parse",
        operation="detect",
        target=detected.value,
        data={"confidence": round(confidence, 2)},
    )
    adapter = ADAPTERS.get(detected)
    if adapter is None:
        return _rejected(
            ImportRejected(
                "unsupported_format",
                "The file is not Fountain, Final Draft XML, PDF, or plain text.",
            ),
            source_name,
            payload.content_hash,
            detected,
            "none",
        )

    try:
        scenes, warnings = adapter.parse(payload)
        trace.step(f"Parsed {len(scenes)} scene(s)")
        trace.event(
            kind="parse",
            operation="parse",
            target=adapter.name,
            data={
                "scene_count": len(scenes),
                "unit_count": sum(len(scene.units) for scene in scenes),
                "warning_codes": [warning.code for warning in warnings],
            },
        )
    except ImportRejected as rejection:
        trace.step(f"Rejected: {rejection.code}")
        return _rejected(
            rejection, source_name, payload.content_hash, detected, adapter.name
        )

    # Adapters open an unheaded scene to hold content that precedes the first
    # heading. A document where every scene is unheaded has no headings at all.
    if not any(scene.heading for scene in scenes):
        return _rejected(
            ImportRejected(
                "no_scenes",
                "No scene headings were found. A screenplay needs lines "
                "beginning INT., EXT., or EST.",
            ),
            source_name,
            payload.content_hash,
            detected,
            adapter.name,
        )

    warnings = list(warnings)
    warnings.extend(_enforce_limits(scenes))

    if confidence < 0.5:
        warnings.append(
            ImportWarning(
                code="low_format_confidence",
                message=f"Format detected as {detected.value} with "
                f"{confidence:.0%} confidence.",
            )
        )

    verdict = assess(scenes)
    trace.event(
        kind="validate",
        operation="screenplay_check",
        data={
            "is_screenplay": verdict.is_screenplay,
            "confidence": verdict.confidence,
            "reason_count": len(verdict.reasons),
        },
    )
    if not verdict.is_screenplay:
        return _rejected(
            ImportRejected(
                "not_a_screenplay",
                "The file parsed but does not look like a screenplay. "
                + " ".join(verdict.reasons),
            ),
            source_name,
            payload.content_hash,
            detected,
            adapter.name,
        )
    warnings.extend(verdict_warnings(verdict))

    title_reader = getattr(adapter, "title", None)
    title = title_reader(payload) if callable(title_reader) else None

    return ImportResult(
        outcome=_outcome(warnings, verdict.needs_review),
        detected_format=detected,
        adapter_name=adapter.name,
        source_name=source_name,
        content_hash=payload.content_hash,
        scenes=scenes,
        warnings=warnings,
        title=title,
    )


def _enforce_limits(scenes: list[ParsedScene]) -> list[ImportWarning]:
    """Truncate pathological documents, saying so rather than silently capping."""
    warnings: list[ImportWarning] = []
    if len(scenes) > MAX_SCENES:
        warnings.append(
            ImportWarning(
                code="scene_limit",
                message=f"{len(scenes)} scenes found; only the first "
                f"{MAX_SCENES} were imported.",
            )
        )
        del scenes[MAX_SCENES:]

    for scene in scenes:
        if len(scene.units) > MAX_UNITS_PER_SCENE:
            warnings.append(
                ImportWarning(
                    code="unit_limit",
                    message=f"Scene {scene.sequence_index} has "
                    f"{len(scene.units)} units; only the first "
                    f"{MAX_UNITS_PER_SCENE} were imported.",
                    scene_index=scene.sequence_index,
                )
            )
            del scene.units[MAX_UNITS_PER_SCENE:]
    return warnings


def _outcome(warnings: list[ImportWarning], needs_review: bool) -> ImportOutcome:
    """Map warnings to an import outcome.

    Anything OCR-derived or structurally uncertain is needs_review, so a
    human sees it before the graph is built on it.
    """
    review_codes = {
        "ocr_derived",
        "no_indentation",
        "weak_screenplay_signal",
        "low_format_confidence",
        "scene_limit",
        "unit_limit",
    }
    if needs_review or any(warning.code in review_codes for warning in warnings):
        return ImportOutcome.NEEDS_REVIEW
    if warnings:
        return ImportOutcome.ACCEPTED_WITH_WARNINGS
    return ImportOutcome.ACCEPTED


def _rejected(
    rejection: ImportRejected,
    source_name: str,
    content_hash: str,
    detected: DetectedFormat,
    adapter_name: str,
) -> ImportResult:
    logger.info("import rejected: %s (%s)", rejection.code, source_name)
    return ImportResult(
        outcome=ImportOutcome.REJECTED,
        detected_format=detected,
        adapter_name=adapter_name,
        source_name=source_name,
        content_hash=content_hash,
        rejection_code=rejection.code,
        rejection_message=rejection.message,
    )
