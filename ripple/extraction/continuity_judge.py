"""The continuity judgement contract: conflicts argued from listed evidence.

The orphaned-reference check in `ripple/graph/continuity.py` catches the one
continuity problem arithmetic can prove. Everything subtler (a changed driver
against an earlier interaction, wardrobe contradicting an established state)
needs judgement, so the evidence packet that module retrieves is handed to a
model here, once per preview.

The model argues only from what it was given. Application code verifies every
claim before it persists: a finding must cite evidence ids from the packet,
uncited claims are dropped, and an empty list is a valid answer. The pass is
advisory, so a failed call degrades the preview to its deterministic findings
instead of failing it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ripple.extraction.validate import MalformedResponse
from ripple.graph.continuity import EvidencePacket

# Bumped with any wording change: the audit rows record which prompt spoke.
CONTINUITY_PROMPT_VERSION = "continuity.v1"

CONTINUITY_SEVERITIES = ("low", "medium", "high")
MAX_CONTINUITY_FINDINGS = 10

CONTINUITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "maxItems": MAX_CONTINUITY_FINDINGS,
            "items": {
                "type": "object",
                "required": ["message", "severity", "evidence_ids"],
                "properties": {
                    "message": {"type": "string", "maxLength": 500},
                    "severity": {
                        "type": "string",
                        "enum": list(CONTINUITY_SEVERITIES),
                    },
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 64},
                        "maxItems": 8,
                    },
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}

CONTINUITY_SYSTEM = """You check a screenplay edit for continuity conflicts.

You are given the edited lines in their current and proposed form, the graph
changes the edit makes, and evidence: facts from earlier and later scenes
about the entities involved, each with an assertion_id and the line that
states it.

Report each place the proposed text contradicts the evidence: a state, an
ownership, a location, or an interaction that a later scene relies on and
the edit breaks, or an earlier scene establishes differently. For each
conflict, cite the assertion_id values of the evidence that shows it,
verbatim from the lists. Do not report the graph changes themselves; the
diff already shows them. Do not report a conflict the evidence does not
show. An empty findings list is a valid answer.

Severity: high when a cited later scene stops making sense, medium when a
department deliverable or a stated fact goes inconsistent, low for anything
softer.

Return one JSON object and nothing else. No prose, no code fence.
"""


@dataclass(frozen=True)
class ContinuityConflict:
    """One verified model-judged conflict, every citation from the packet."""

    message: str
    severity: str
    evidence_ids: list[str]
    confidence: float | None


def build_continuity_prompt(
    edits: list[dict[str, Any]],
    diff_payload: dict[str, Any],
    packet: EvidencePacket,
) -> str:
    """The full user prompt for one preview's continuity judgement.

    `edits` is [{unit_id, current_text, proposed_text}]. The diff payload and
    the packet are serialised as JSON so the model cites assertion ids back
    rather than paraphrasing.
    """
    payload = {
        "edited_units": edits,
        "graph_changes": diff_payload,
        "evidence": packet.as_prompt_payload(),
    }
    return (
        "Check the proposed edit below against the evidence. Cite "
        "assertion_id values verbatim.\n\n"
        + json.dumps(payload, indent=1, ensure_ascii=False)
    )


def validate_continuity(
    raw_text: str, packet: EvidencePacket
) -> tuple[list[ContinuityConflict], dict[str, int]]:
    """Verify a continuity reply against the packet it was judged from.

    Returns the surviving conflicts and a summary of what was dropped, for
    the audit row. Raises MalformedResponse when the reply is not the
    contract's shape at all.
    """
    try:
        reply = json.loads(raw_text)
    except (TypeError, ValueError) as error:
        raise MalformedResponse(f"reply was not JSON: {error}") from error
    if not isinstance(reply, dict) or not isinstance(reply.get("findings"), list):
        raise MalformedResponse("reply was not an object with a findings list")

    known_ids = {
        item.assertion_id for item in (*packet.earlier, *packet.later)
    }
    kept: list[ContinuityConflict] = []
    seen_messages: set[str] = set()
    dropped_uncited = 0
    dropped_invalid = 0
    for raw in reply["findings"][:MAX_CONTINUITY_FINDINGS]:
        if not isinstance(raw, dict):
            dropped_invalid += 1
            continue
        message = str(raw.get("message") or "").strip()
        severity = raw.get("severity")
        if not message or severity not in CONTINUITY_SEVERITIES:
            dropped_invalid += 1
            continue
        if message.casefold() in seen_messages:
            dropped_invalid += 1
            continue
        cited = raw.get("evidence_ids")
        if not isinstance(cited, list):
            dropped_invalid += 1
            continue
        surviving = [
            str(item) for item in cited if isinstance(item, str) and item in known_ids
        ]
        if not surviving:
            # A claim citing nothing the model was shown is unverifiable.
            dropped_uncited += 1
            continue
        confidence = raw.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None
        elif not 0.0 <= float(confidence) <= 1.0:
            confidence = None
        seen_messages.add(message.casefold())
        kept.append(
            ContinuityConflict(
                message=message,
                severity=severity,
                evidence_ids=surviving,
                confidence=float(confidence) if confidence is not None else None,
            )
        )

    return kept, {
        "reported": len(reply["findings"]),
        "kept": len(kept),
        "dropped_uncited": dropped_uncited,
        "dropped_invalid": dropped_invalid,
    }


__all__ = [
    "CONTINUITY_PROMPT_VERSION",
    "CONTINUITY_SCHEMA",
    "CONTINUITY_SYSTEM",
    "ContinuityConflict",
    "build_continuity_prompt",
    "validate_continuity",
]
