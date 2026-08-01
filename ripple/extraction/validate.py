"""Validate what the extraction agent returned.

The model proposes; this module decides what is admissible. Everything it
rejects is dropped with a recorded reason rather than repaired, because a
repair pass is a second inference over an answer already known to be wrong, and
Schema Lock v1 section 8 forbids it.

An invalid item never fails the whole scene. A scene that produced nine good
assertions and one malformed one should keep the nine.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ripple.db.models import ENTITY_TYPES
from ripple.extraction.prompt import MINIMUM_CONFIDENCE
from ripple.graph.predicates import SignatureError, validate_edge

logger = logging.getLogger(__name__)

# Schema Lock v1 section 6. Below this an assertion is discarded at the
# validation boundary and never written.
DISCARD_BELOW = 0.5


class MalformedResponse(ValueError):
    """The reply was not a JSON object of the expected shape."""


@dataclass
class ValidatedEntity:
    """One entity the model proposed, after checking."""

    local_id: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    description: str | None = None
    confidence: float = 1.0


@dataclass
class ValidatedAssertion:
    """One assertion the model proposed, after checking."""

    subject_kind: str
    subject_local_id: str
    predicate: str
    object_kind: str
    object_local_id: str
    source_unit_id: str
    confidence: float
    evidence_start: int | None = None
    evidence_end: int | None = None


@dataclass
class ValidationReport:
    """What survived, and why the rest did not."""

    entities: list[ValidatedEntity] = field(default_factory=list)
    assertions: list[ValidatedAssertion] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    def reject(self, what: str, reason: str) -> None:
        """Record a dropped item. Traced as a count, never as content."""
        self.rejected.append((what, reason))

    @property
    def rejection_codes(self) -> list[str]:
        """The reasons, not the kinds: the reason is what identifies a defect."""
        return [reason for _, reason in self.rejected]


def parse_response(text: str) -> dict:
    """Parse the reply, raising MalformedResponse on anything else."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise MalformedResponse(f"reply was not JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise MalformedResponse(
            f"reply was a {type(parsed).__name__}, expected an object"
        )
    return parsed


def validate_response(
    text: str, valid_unit_ids: set[str], scene_local_id: str = "scene"
) -> ValidationReport:
    """Check a reply against Schema Lock v1 and return what is admissible.

    `valid_unit_ids` is the set of units in the scene being extracted. An
    assertion citing anything else is dropped: an evidence pointer into a unit
    that was not shown to the model is fabricated, whatever else is right
    about the assertion.
    """
    payload = parse_response(text)
    report = ValidationReport()

    entities_raw = payload.get("entities")
    assertions_raw = payload.get("assertions")
    if not isinstance(entities_raw, list) or not isinstance(assertions_raw, list):
        raise MalformedResponse("reply lacks list-valued 'entities' and 'assertions'")

    by_local_id: dict[str, ValidatedEntity] = {}
    for item in entities_raw:
        entity = _validate_entity(item, report)
        if entity is None:
            continue
        if entity.local_id in by_local_id:
            report.reject("entity", "duplicate_local_id")
            continue
        by_local_id[entity.local_id] = entity

    for item in assertions_raw:
        assertion = _validate_assertion(
            item, by_local_id, valid_unit_ids, scene_local_id, report
        )
        if assertion is not None:
            report.assertions.append(assertion)

    # An entity nothing points at is not a production requirement, it is a
    # noun the model noticed. Keeping it would inflate the graph with nodes
    # that have no evidence.
    referenced = {
        local_id
        for assertion in report.assertions
        for local_id, kind in (
            (assertion.subject_local_id, assertion.subject_kind),
            (assertion.object_local_id, assertion.object_kind),
        )
        if kind == "entity"
    }
    for local_id, entity in by_local_id.items():
        if local_id in referenced:
            report.entities.append(entity)
        else:
            report.reject("entity", "unreferenced")

    return report


def _validate_entity(item: object, report: ValidationReport) -> ValidatedEntity | None:
    """Check one proposed entity."""
    if not isinstance(item, dict):
        report.reject("entity", "not_an_object")
        return None

    local_id = item.get("local_id")
    entity_type = item.get("entity_type")
    name = item.get("canonical_name")

    if not isinstance(local_id, str) or not local_id.strip():
        report.reject("entity", "missing_local_id")
        return None
    if entity_type not in ENTITY_TYPES:
        report.reject("entity", "unknown_entity_type")
        return None
    if not isinstance(name, str) or not name.strip():
        report.reject("entity", "missing_canonical_name")
        return None

    confidence = _confidence(item.get("confidence"))
    if confidence is None:
        report.reject("entity", "bad_confidence")
        return None

    aliases = [
        alias.strip()
        for alias in item.get("aliases", []) or []
        if isinstance(alias, str) and alias.strip()
    ]
    description = item.get("description")
    return ValidatedEntity(
        local_id=local_id.strip(),
        entity_type=entity_type,
        canonical_name=name.strip(),
        aliases=aliases,
        description=description.strip() if isinstance(description, str) else None,
        confidence=confidence,
    )


def _validate_assertion(
    item: object,
    entities: dict[str, ValidatedEntity],
    valid_unit_ids: set[str],
    scene_local_id: str,
    report: ValidationReport,
) -> ValidatedAssertion | None:
    """Check one proposed assertion against its predicate signature."""
    if not isinstance(item, dict):
        report.reject("assertion", "not_an_object")
        return None

    predicate = item.get("predicate")
    subject_kind = item.get("subject_kind")
    object_kind = item.get("object_kind")
    subject_local = item.get("subject_local_id")
    object_local = item.get("object_local_id")
    source_unit_id = item.get("source_unit_id")

    if not all(
        isinstance(value, str)
        for value in (predicate, subject_kind, object_kind, subject_local, object_local)
    ):
        report.reject("assertion", "missing_field")
        return None

    if not isinstance(source_unit_id, str) or source_unit_id not in valid_unit_ids:
        report.reject("assertion", "unknown_source_unit")
        return None

    confidence = _confidence(item.get("confidence"))
    if confidence is None:
        report.reject("assertion", "bad_confidence")
        return None
    if confidence < DISCARD_BELOW:
        report.reject("assertion", "below_confidence_floor")
        return None

    subject_type = _endpoint_type(subject_kind, subject_local, entities, scene_local_id)
    object_type = _endpoint_type(object_kind, object_local, entities, scene_local_id)
    if subject_type is _UNRESOLVED or object_type is _UNRESOLVED:
        report.reject("assertion", "unresolved_endpoint")
        return None

    try:
        validate_edge(predicate, subject_kind, object_kind, subject_type, object_type)
    except SignatureError as error:
        logger.debug("signature rejection: %s", error)
        report.reject("assertion", "signature_mismatch")
        return None

    start, end = item.get("evidence_start"), item.get("evidence_end")
    if not isinstance(start, int) or start < 0:
        start = None
    if not isinstance(end, int) or (start is not None and end < start):
        end = None

    return ValidatedAssertion(
        subject_kind=subject_kind,
        subject_local_id=subject_local,
        predicate=predicate,
        object_kind=object_kind,
        object_local_id=object_local,
        source_unit_id=source_unit_id,
        confidence=confidence,
        evidence_start=start,
        evidence_end=end,
    )


class _Unresolved:
    """Sentinel for an endpoint that names nothing the model declared."""


_UNRESOLVED = _Unresolved()


def _endpoint_type(
    kind: str,
    local_id: str,
    entities: dict[str, ValidatedEntity],
    scene_local_id: str,
) -> str | None | _Unresolved:
    """The entity type of one endpoint, None for a scene, sentinel if unknown."""
    if kind == "scene":
        return None if local_id == scene_local_id else _UNRESOLVED
    entity = entities.get(local_id)
    return entity.entity_type if entity else _UNRESOLVED


def _confidence(value: object) -> float | None:
    """Coerce a confidence to a float in the admissible range, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not MINIMUM_CONFIDENCE - 0.001 <= number <= 1.0:
        return None
    return number
