"""The judgement contract: verdicts on a known subgraph, not blind re-extraction.

The preview used to extract the proposed text from scratch and set-diff the
result against stored assertions, which mixed the edit's effect with the
model's run-to-run variance. Here the model is handed the structure the
database already holds (the edited units, the assertions citing them, the
touched entities' attributes) and returns a verdict per item plus anything
new, schema-constrained. Application code verifies every verdict before the
diff sees it; a fabricated id, a hold on vanished evidence, or an out-of-lock
edge never reaches an operation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ripple.db.models import ENTITY_TYPES, PREDICATES
from ripple.extraction.prompt import MINIMUM_CONFIDENCE, RULES
from ripple.extraction.validate import (
    MalformedResponse,
    ValidatedAssertion,
    ValidatedEntity,
    validate_response,
)

# Bumped with any wording change: the audit rows record which prompt spoke.
JUDGE_PROMPT_VERSION = "judge.v2"

VERDICTS = ("holds", "changed", "removed")

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["assertion_verdicts", "attribute_verdicts"],
    "properties": {
        "assertion_verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["assertion_id", "verdict"],
                "properties": {
                    "assertion_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "evidence_start": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100000,
                    },
                    "evidence_end": {"type": "integer", "minimum": 0, "maximum": 100000},
                    "confidence": {"type": "number"},
                    "note": {"type": "string"},
                },
            },
        },
        "attribute_verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["attribute_id", "verdict"],
                "properties": {
                    "attribute_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "new_value": {"type": "string"},
                    "evidence_start": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100000,
                    },
                    "evidence_end": {"type": "integer", "minimum": 0, "maximum": 100000},
                    "confidence": {"type": "number"},
                },
            },
        },
        "new_entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["local_id", "entity_type", "canonical_name", "confidence"],
                "properties": {
                    "local_id": {"type": "string"},
                    "entity_type": {"type": "string", "enum": list(ENTITY_TYPES)},
                    "canonical_name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "new_assertions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "subject_kind",
                    "subject_local_id",
                    "predicate",
                    "object_kind",
                    "object_local_id",
                    "source_unit_id",
                    "confidence",
                ],
                "properties": {
                    "subject_kind": {"type": "string", "enum": ["entity", "scene"]},
                    "subject_local_id": {"type": "string"},
                    "predicate": {"type": "string", "enum": list(PREDICATES)},
                    "object_kind": {"type": "string", "enum": ["entity", "scene"]},
                    "object_local_id": {"type": "string"},
                    "source_unit_id": {"type": "string"},
                    "evidence_start": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100000,
                    },
                    "evidence_end": {"type": "integer", "minimum": 0, "maximum": 100000},
                    "confidence": {"type": "number"},
                },
            },
        },
        "new_attributes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity_local_id", "key", "value", "confidence"],
                "properties": {
                    "entity_local_id": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "source_unit_id": {"type": "string"},
                    "evidence_start": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100000,
                    },
                    "evidence_end": {"type": "integer", "minimum": 0, "maximum": 100000},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}

JUDGE_SYSTEM = """You judge how a screenplay edit changes known production facts.

You are given a scene, the edited lines in their current and proposed form,
the assertions the current lines support, and the attributes of the entities
involved. Judge each listed item against the proposed text:

- holds: the proposed text still supports it.
- changed: it survives with a different value (attributes) or evidence span.
- removed: the proposed text no longer supports it.

A swapped descriptor is a change, not a removal: an emerald gown becoming a
crimson gown is the color attribute `changed` with new_value "crimson".
Reserve `removed` for facts the proposed text drops with no replacement.

Then report anything the proposed text newly supports, under the same rules
as extraction. Judge only what you were given plus what the proposed text
states. Do not invent items, do not repeat unchanged context back.

Return one JSON object and nothing else. No prose, no code fence.
"""


def build_judge_prompt(
    heading: str,
    display_number: str | None,
    edits: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
    attributes: list[dict[str, Any]],
) -> str:
    """The full user prompt for one scene's judgement.

    `edits` is [{unit_id, unit_type, current_text, proposed_text}].
    `assertions` and `attributes` carry ids and display fields; they are
    serialised as JSON so the model cites ids back rather than paraphrasing.
    """
    label = f"Scene {display_number}" if display_number else "Scene"
    payload = {
        "scene": f"{label}: {heading}",
        "edited_units": [
            {
                "unit_id": edit["unit_id"],
                "unit_type": edit["unit_type"],
                "current_text": edit["current_text"],
                "proposed_text": edit["proposed_text"],
            }
            for edit in edits
        ],
        "assertions_to_judge": assertions,
        "attributes_to_judge": attributes,
    }
    return (
        f"{RULES}\n\n---\n\n"
        "Judge every assertion and attribute below against the proposed text. "
        "Cite ids verbatim. New items follow the extraction rules "
        "above and cite the edited unit that supports them.\n\n"
        f"{json.dumps(payload, indent=1)}"
    )


@dataclass(frozen=True)
class AssertionVerdict:
    assertion_id: str
    verdict: str
    evidence_start: int | None = None
    evidence_end: int | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class AttributeVerdict:
    attribute_id: str
    verdict: str
    new_value: str | None = None
    evidence_start: int | None = None
    evidence_end: int | None = None
    confidence: float | None = None


@dataclass
class JudgementReport:
    """Verified verdicts plus validated new items, ready for the diff."""

    assertion_verdicts: list[AssertionVerdict] = field(default_factory=list)
    attribute_verdicts: list[AttributeVerdict] = field(default_factory=list)
    new_entities: list[ValidatedEntity] = field(default_factory=list)
    new_assertions: list[ValidatedAssertion] = field(default_factory=list)
    new_attributes: list[dict[str, Any]] = field(default_factory=list)
    #: (item id or local id, reason) for everything dropped or rewritten.
    rejected: list[tuple[str, str]] = field(default_factory=list)
    #: Listed items the model returned no verdict for. The preview refuses a
    #: judgement with any of these: a silent miss otherwise reads as "holds".
    coverage_misses: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Counts and reasons, for the model-call audit record."""
        return {
            "assertion_verdicts": len(self.assertion_verdicts),
            "attribute_verdicts": len(self.attribute_verdicts),
            "new_entities": len(self.new_entities),
            "new_assertions": len(self.new_assertions),
            "new_attributes": len(self.new_attributes),
            "rejected": [list(item) for item in self.rejected],
            "coverage_misses": self.coverage_misses,
        }


def validate_judgement(
    raw_text: str,
    listed_assertions: dict[str, dict[str, Any]],
    listed_attributes: dict[str, dict[str, Any]],
    edited_units: dict[str, str],
) -> JudgementReport:
    """Verify a judgement reply. The verification rules, in order.

    `listed_assertions` and `listed_attributes` map id -> the item as sent
    (with its evidence text under "evidence"). `edited_units` maps unit id ->
    proposed text, for the vanished-evidence check. Raises MalformedResponse
    only for a reply that is not a JSON object; one bad verdict never
    discards the rest.
    """
    try:
        reply = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise MalformedResponse(f"reply was not JSON: {error}") from error
    if not isinstance(reply, dict):
        raise MalformedResponse("reply was not a JSON object")

    report = JudgementReport()
    seen_assertions: set[str] = set()
    seen_attributes: set[str] = set()

    for raw in reply.get("assertion_verdicts") or []:
        if not isinstance(raw, dict):
            continue
        verdict_id = str(raw.get("assertion_id", ""))
        verdict = raw.get("verdict")
        if verdict_id not in listed_assertions:
            report.rejected.append((verdict_id, "verdict names an unlisted id"))
            continue
        if verdict not in VERDICTS:
            report.rejected.append((verdict_id, f"unknown verdict {verdict!r}"))
            continue
        if verdict_id in seen_assertions:
            report.rejected.append((verdict_id, "duplicate verdict"))
            continue
        seen_assertions.add(verdict_id)
        # An assertion has no value to change: a changed endpoint or predicate
        # is a removal plus a new assertion, so `changed` here only re-anchors
        # evidence. Anything else the model meant collapses to `holds`.
        if verdict == "holds" and not _evidence_survives(
            listed_assertions[verdict_id], edited_units
        ):
            report.rejected.append(
                (verdict_id, "holds on vanished evidence, downgraded to removed")
            )
            verdict = "removed"
        report.assertion_verdicts.append(
            AssertionVerdict(
                assertion_id=verdict_id,
                verdict=verdict,
                evidence_start=_int_or_none(raw.get("evidence_start")),
                evidence_end=_int_or_none(raw.get("evidence_end")),
                confidence=_confidence(raw.get("confidence")),
            )
        )

    for raw in reply.get("attribute_verdicts") or []:
        if not isinstance(raw, dict):
            continue
        verdict_id = str(raw.get("attribute_id", ""))
        verdict = raw.get("verdict")
        if verdict_id not in listed_attributes:
            report.rejected.append((verdict_id, "verdict names an unlisted id"))
            continue
        if verdict not in VERDICTS:
            report.rejected.append((verdict_id, f"unknown verdict {verdict!r}"))
            continue
        if verdict_id in seen_attributes:
            report.rejected.append((verdict_id, "duplicate verdict"))
            continue
        seen_attributes.add(verdict_id)
        new_value = raw.get("new_value")
        if verdict == "changed" and not (
            isinstance(new_value, str) and new_value.strip()
        ):
            report.rejected.append(
                (verdict_id, "changed without a new value, treated as holds")
            )
            verdict = "holds"
            new_value = None
        report.attribute_verdicts.append(
            AttributeVerdict(
                attribute_id=verdict_id,
                verdict=verdict,
                new_value=new_value if verdict == "changed" else None,
                evidence_start=_int_or_none(raw.get("evidence_start")),
                evidence_end=_int_or_none(raw.get("evidence_end")),
                confidence=_confidence(raw.get("confidence")),
            )
        )

    # A listed item with no verdict is recorded as a coverage miss. The hold
    # rows keep the report total for display; the caller decides whether a
    # miss fails the judgement, and the preview does fail it.
    for missing in listed_assertions.keys() - seen_assertions:
        report.coverage_misses.append(missing)
        report.assertion_verdicts.append(
            AssertionVerdict(assertion_id=missing, verdict="holds")
        )
    for missing in listed_attributes.keys() - seen_attributes:
        report.coverage_misses.append(missing)
        report.attribute_verdicts.append(
            AttributeVerdict(attribute_id=missing, verdict="holds")
        )

    _validate_new_items(reply, edited_units, report)
    return report


def _validate_new_items(
    reply: dict[str, Any], edited_units: dict[str, str], report: JudgementReport
) -> None:
    """New entities and assertions pass the full extraction validation."""
    extraction_shaped = json.dumps(
        {
            "entities": reply.get("new_entities") or [],
            "assertions": reply.get("new_assertions") or [],
        }
    )
    try:
        extracted = validate_response(extraction_shaped, set(edited_units))
    except MalformedResponse as error:
        report.rejected.append(("new_items", str(error)))
        return
    report.new_entities = extracted.entities
    report.new_assertions = extracted.assertions
    report.rejected.extend(extracted.rejected)

    known_locals = {entity.local_id for entity in extracted.entities}
    for raw in reply.get("new_attributes") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key", "")).strip()
        value = str(raw.get("value", "")).strip()
        local = str(raw.get("entity_local_id", ""))
        confidence = _confidence(raw.get("confidence"))
        if not key or not value:
            report.rejected.append((local or "?", "attribute missing key or value"))
            continue
        if confidence is None or confidence < MINIMUM_CONFIDENCE:
            report.rejected.append((local or "?", "attribute below the floor"))
            continue
        source = raw.get("source_unit_id")
        if source is not None and str(source) not in edited_units:
            report.rejected.append((local or "?", "attribute cites an unshown unit"))
            continue
        report.new_attributes.append(
            {
                "entity_local_id": local,
                "known_local": local in known_locals,
                "key": key,
                "value": value,
                "source_unit_id": str(source) if source else None,
                "evidence_start": _int_or_none(raw.get("evidence_start")),
                "evidence_end": _int_or_none(raw.get("evidence_end")),
                "confidence": confidence,
            }
        )


def _evidence_survives(
    listed: dict[str, Any], edited_units: dict[str, str]
) -> bool:
    """Whether a held assertion's evidence text still appears in the edit.

    Only checked when the assertion's evidence lies inside an edited unit and
    the listed item carried its evidence text. Content is what is compared:
    a pair of in-bounds offsets proves nothing about the proposed text, and
    a hold whose supporting words are gone is a removal however the verdict
    is decorated. A model that believes the fact survives under new wording
    answers `changed`, which re-anchors the evidence.
    """
    unit_id = str(listed.get("source_unit_id", ""))
    if unit_id not in edited_units:
        return True
    evidence = listed.get("evidence") or ""
    if not evidence:
        return True
    return evidence.casefold() in edited_units[unit_id].casefold()


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _confidence(value: Any) -> float | None:
    """A confidence in [0, 1], or None for anything else.

    Out-of-range values are refused rather than clamped: clamping would turn
    a nonsense number like 87 into full certainty, and the caller's fallback
    to the stored confidence is the honest answer.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if 0.0 <= number <= 1.0:
            return number
    return None


__all__ = [
    "JUDGE_PROMPT_VERSION",
    "JUDGE_SCHEMA",
    "JUDGE_SYSTEM",
    "AssertionVerdict",
    "AttributeVerdict",
    "JudgementReport",
    "build_judge_prompt",
    "validate_judgement",
]
