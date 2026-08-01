"""The synthesizer and the grounded query, both constrained to retrieved data.

PRD section 8: the synthesizer explains and prioritises a diff that application
code already computed. It may not create operations the diff engine did not
produce, so it never sees the screenplay, only the computed diff and the cited
findings. Whatever it writes, the operations applied on acceptance are the ones
in the change set.

The grounded query has the same shape. It answers from accepted assertions,
with the assertion identifiers it used recorded alongside the answer.

Both degrade rather than fail. With no provider configured, the synthesizer
returns a deterministic sentence assembled from the diff counts, so the preview
is still usable and honestly labelled.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ripple.graph.diff import GraphDiff
from ripple.llm.base import LLMProvider, ProviderError

logger = logging.getLogger(__name__)

SYNTHESIS_PROMPT_VERSION = "synthesize.v1"
QUERY_PROMPT_VERSION = "query.v1"

SYNTHESIS_SYSTEM = """You explain a screenplay change to a production coordinator.

You are given a graph diff that has already been computed, and continuity
findings that have already been determined. Explain what they mean for
production in two or three sentences.

Hard limits:
- Describe only the operations and findings you were given. Do not add, remove,
  or reinterpret any of them.
- Do not speculate about story, character motive, or anything off the page.
- Name departments where the diff makes them obvious.
- No preamble. Start with what changes.
"""

QUERY_SYSTEM = """You answer questions about a film production from graph data.

You are given accepted assertions extracted from a screenplay. Answer only from
them. If the assertions do not answer the question, say so plainly.

Hard limits:
- Never state anything the assertions do not support.
- Cite scenes by their number.
- No preamble, no restating the question.
"""


@dataclass
class Synthesis:
    """A written explanation of a diff, plus how it was produced."""

    summary: str
    severity: str
    model_id: str | None = None
    prompt_version: str = SYNTHESIS_PROMPT_VERSION
    grounded: bool = True
    generated: bool = False

    @property
    def source(self) -> str:
        """Whether a model wrote this or the fallback assembled it."""
        return "model" if self.generated else "deterministic"


@dataclass
class GroundedAnswer:
    """An answer plus the assertions it was allowed to use."""

    answer: str
    cited_assertion_ids: list[str] = field(default_factory=list)
    cited_units: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, str]] = field(default_factory=list)
    model_id: str | None = None
    prompt_version: str = QUERY_PROMPT_VERSION
    generated: bool = False


def severity_for(diff: GraphDiff, findings: list[Any]) -> str:
    """Rank a proposal from what the diff and findings actually contain.

    Determined in code rather than asked of a model: severity drives whether a
    coordinator looks now or later, and it should not vary between two runs
    over identical input.
    """
    if any(getattr(f, "later_unit_ids", None) for f in findings):
        return "high"
    if diff.removed or diff.changed:
        return "medium"
    if diff.added:
        return "low"
    return "none"


def deterministic_summary(diff: GraphDiff, findings: list[Any]) -> str:
    """Assemble a sentence from the diff, with no model involved.

    Used when no provider is configured, and as the fallback when a provider
    call fails. A preview that says nothing is worse than one that says the
    plain arithmetic.
    """
    parts: list[str] = []
    if diff.changed:
        swaps = ", ".join(
            f"{before.display_object or before.obj.label} becomes "
            f"{after.display_object or after.obj.label}"
            for before, after in diff.changed[:3]
        )
        parts.append(f"Changes {len(diff.changed)} requirement(s): {swaps}.")
    if diff.removed:
        parts.append(f"Removes {len(diff.removed)} assertion(s).")
    if diff.added:
        parts.append(f"Adds {len(diff.added)} assertion(s).")
    if not parts:
        parts.append("No graph change.")
    for finding in findings[:2]:
        message = getattr(finding, "message", None)
        if message:
            parts.append(message)
    return " ".join(parts)


def synthesize(
    diff: GraphDiff,
    findings: list[Any],
    provider: LLMProvider | None = None,
    model_id: str | None = None,
) -> Synthesis:
    """Explain a computed diff.

    The model is given the diff and the findings as data. It never sees the
    screenplay, so it cannot describe anything the diff does not contain.
    """
    severity = severity_for(diff, findings)
    fallback = deterministic_summary(diff, findings)

    if provider is None or not model_id:
        return Synthesis(summary=fallback, severity=severity, generated=False)

    payload = {
        "added": [_edge(e) for e in diff.added],
        "removed": [_edge(e) for e in diff.removed],
        "changed": [{"before": _edge(b), "after": _edge(a)} for b, a in diff.changed],
        "findings": [
            {
                "severity": "high",
                "message": getattr(finding, "message", str(finding)),
                "scenes": getattr(finding, "later_scene_numbers", []),
            }
            for finding in findings
        ],
    }

    try:
        result = provider.generate(
            model_id,
            json.dumps(payload, indent=2),
            system=SYNTHESIS_SYSTEM,
            max_output_tokens=400,
        )
    except ProviderError as error:
        logger.info("synthesis failed, using the deterministic summary: %s", error.code)
        return Synthesis(summary=fallback, severity=severity, generated=False)

    text = result.text.strip()
    if not text:
        return Synthesis(summary=fallback, severity=severity, generated=False)
    return Synthesis(
        summary=text,
        severity=severity,
        model_id=result.model_id,
        generated=True,
    )


def _edge(edge: Any) -> dict[str, Any]:
    return {
        "subject": edge.display_subject or edge.subject.label,
        "predicate": edge.predicate,
        "object": edge.display_object or edge.obj.label,
        "confidence": round(edge.confidence, 2),
    }


def answer_question(
    question: str,
    assertions: list[dict[str, Any]],
    provider: LLMProvider | None = None,
    model_id: str | None = None,
) -> GroundedAnswer:
    """Answer a question from accepted assertions only.

    Every assertion handed in is recorded as a citation whether or not the
    model quotes it, because the answer was permitted to use all of them and
    the audit record should say what was available.
    """
    cited_ids = [a["id"] for a in assertions]

    if not assertions:
        return GroundedAnswer(
            answer="Nothing in the accepted graph answers that. "
            "Build the graph, or ask about an entity that has been extracted.",
        )

    if provider is None or not model_id:
        return GroundedAnswer(
            answer=_deterministic_answer(assertions),
            cited_assertion_ids=cited_ids,
        )

    payload = {
        "question": question,
        "assertions": [
            {
                "scene": a.get("scene"),
                "edge": f"{a['subject']} {a['predicate']} {a['object']}",
                "unit_text": a.get("unit_text", ""),
                "confidence": a.get("confidence"),
            }
            for a in assertions
        ],
    }
    try:
        result = provider.generate(
            model_id,
            json.dumps(payload, indent=2),
            system=QUERY_SYSTEM,
            max_output_tokens=600,
        )
    except ProviderError as error:
        logger.info("query failed, using the deterministic answer: %s", error.code)
        return GroundedAnswer(
            answer=_deterministic_answer(assertions), cited_assertion_ids=cited_ids
        )

    return GroundedAnswer(
        answer=result.text.strip() or _deterministic_answer(assertions),
        cited_assertion_ids=cited_ids,
        model_id=result.model_id,
        generated=True,
    )


def _deterministic_answer(assertions: list[dict[str, Any]]) -> str:
    """List the matching assertions when no model is available.

    Less readable than a written answer and equally true, which is the right
    trade when the alternative is nothing.
    """
    scenes: list[str] = []
    for assertion in assertions:
        scene = str(assertion.get("scene") or "")
        if scene and scene not in scenes:
            scenes.append(scene)
    return (
        f"{len(assertions)} accepted assertion(s) match, across "
        f"scene(s) {', '.join(scenes) or 'unknown'}. "
        "Configure a model in Settings for a written answer."
    )


__all__ = [
    "QUERY_PROMPT_VERSION",
    "SYNTHESIS_PROMPT_VERSION",
    "GroundedAnswer",
    "Synthesis",
    "answer_question",
    "deterministic_summary",
    "severity_for",
    "synthesize",
]
