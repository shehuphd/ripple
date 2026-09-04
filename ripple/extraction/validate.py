"""Validate what the extraction agent returned.

The model proposes; this module decides what is admissible. Everything it
rejects is dropped with a recorded reason rather than repaired, because a
repair pass is a second inference over an answer already known to be wrong.

An invalid item never fails the whole scene. A scene that produced nine good
assertions and one malformed one should keep the nine.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field

from ripple.db.models import ENTITY_TYPES
from ripple.db.naming import normalize_key
from ripple.extraction.prompt import MINIMUM_CONFIDENCE
from ripple.graph.predicates import SignatureError, validate_edge

# Names that are never a production entity: a pronoun the extractor resolved to
# a fresh entity instead of its antecedent, or a group label standing in for
# one. Unnamed roles ("Nurse", "Woman", "Surgeon") are deliberately absent:
# those are valid characters a screenplay leaves unnamed.
_NON_ENTITY_NAMES = frozenset(
    {
        "he", "she", "it", "they", "him", "her", "them", "we", "us", "you",
        "i", "me", "this", "that", "these", "those", "someone", "somebody",
        "anyone", "anybody", "everyone", "everybody", "nobody", "no one",
        "none", "unknown", "unnamed", "character", "characters",
        "two characters", "a character", "the character", "people",
        "first person", "second person", "third person",
        # Cue conventions: a stage play marks a line spoken in chorus with
        # ALL or BOTH, and heads its cast list DRAMATIS PERSONAE. None of
        # them is a person the production casts.
        "all", "both", "together", "omnes", "dramatis personae",
        "the rest", "others", "the others",
    }
)
# A cue naming several roles at once is the cast list, not a character:
# "Lords, Ladies, Officers, Soldiers, Sailors, Messengers, and Attendants".
# Two commas, or one comma before a conjunction, marks the enumeration. A
# single comma is left alone, since one name can carry it ("SMITH, JR.").
_ROLE_LIST = re.compile(r",[^,]*,|,\s*and\s", re.IGNORECASE)
# A full word, a sentence terminator, then a capitalised next word: a line of
# screen text or dialogue lifted into a name ("PERMISSIONS UPDATED. CONTACT
# YOUR SUPERVISOR"). The four-letter floor spares abbreviations that carry a
# period, like "Dr. Chen", "St. Mary", "INT. WHITE VAN".
_SENTENCE_IN_NAME = re.compile(r"\w{4,}[.!?]\s+[A-Z]")
# A cardinal count or a quantifier leading another word is an anonymous group,
# not a named character: "Two men", "Three cops", "Several workers", "A group
# of guards". A bare number ("Seven") is left alone, since it can be a name,
# and ordinals ("Second Officer", "Third Guard") are left alone, since they
# name distinct roles. Matched case-insensitively on the raw name.
_GROUP_LABEL = re.compile(
    r"^(?:\d+"
    r"|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
    r"|both|several|many|some|few|various|numerous|multiple|countless"
    r"|a\s+(?:couple|few|group|pair|bunch|handful|number)(?:\s+of)?"
    r"|group\s+of|pair\s+of|crowd\s+of)"
    r"\s+\w",
    re.IGNORECASE,
)
# A location names a place; it is not a sentence about the set. A stage play
# opens an act with prose ("The table has been placed in the middle of the
# stage", "The Christmas Tree is in the corner by the piano"), and reading a
# location out of it can lift that whole description. Two marks separate a
# place name from a described set: a finite verb standing outside a relative
# clause (the subject is acting or being, not being named), and a length no
# slug reaches. A verb inside a relative clause is fine, since it modifies the
# place rather than narrating it ("A room which is still called the nursery").
_LOCATION_MAX_CHARS = 48
_PLACE_PREDICATES = frozenset(
    {
        "is", "are", "was", "were", "been", "being",
        "has", "have", "had",
        "stands", "stood", "hangs", "hung", "lies", "sits",
        "leads", "opens",
        "placed", "arranged", "stripped",
    }
)
_RELATIVE_PRONOUNS = frozenset({"which", "that", "who", "whom", "where"})


def _describes_rather_than_names(location: str) -> bool:
    """True when a location reads as a sentence about the set, not a place."""
    words = re.findall(r"[a-z']+", location.lower())
    for index, word in enumerate(words):
        if word in _PLACE_PREDICATES and (
            index == 0 or words[index - 1] not in _RELATIVE_PRONOUNS
        ):
            return True
    return False


def _unusable_name_reason(name: str, entity_type: str = "cast") -> str | None:
    """Why a name cannot be an entity, or None when it is usable.

    The group-label rule ("Two men", "Six ...") is a cast concept only: a
    prop, set dressing, or location legitimately carries a count or a plural
    noun ("Six monitors", "14 Stannary Lane", "one wall of books"), so it is
    applied to cast alone. The described-set rule is a location concept only,
    for the same reason in reverse: a prop or set-dressing name may run long or
    read as a phrase, but a location has to be a place. The pronoun and
    sentence-text rules hold for any type.
    """
    folded = unicodedata.normalize("NFKC", name).casefold().strip()
    if folded in _NON_ENTITY_NAMES:
        return "pronoun_or_group_name"
    if entity_type == "cast" and (
        _GROUP_LABEL.match(name.strip()) or _ROLE_LIST.search(name)
    ):
        return "pronoun_or_group_name"
    # A location's own rule is terminal: it stands in for the generic
    # sentence-in-name check, which a place name ("Elsinore. A platform
    # before the Castle") would trip on its region-then-spot period.
    if entity_type == "location":
        if len(name.strip()) > _LOCATION_MAX_CHARS or _describes_rather_than_names(
            name
        ):
            return "descriptive_location"
        return None
    if _SENTENCE_IN_NAME.search(name):
        return "sentence_like_name"
    return None

logger = logging.getLogger(__name__)

# Below this an assertion is discarded at the
# validation boundary and never written.
DISCARD_BELOW = 0.5


class MalformedResponse(ValueError):
    """The reply was not a JSON object of the expected shape."""


@dataclass
class ValidatedAttribute:
    """One attribute the model proposed on an entity, after checking."""

    key: str
    value: str
    source_unit_id: str
    confidence: float
    evidence_start: int | None = None
    evidence_end: int | None = None


@dataclass
class ValidatedEntity:
    """One entity the model proposed, after checking."""

    local_id: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    description: str | None = None
    confidence: float = 1.0
    attributes: list[ValidatedAttribute] = field(default_factory=list)


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
    text: str,
    valid_unit_ids: set[str],
    scene_local_id: str = "scene",
    provided: dict[str, tuple[str, str]] | None = None,
    unit_texts: dict[str, str] | None = None,
) -> ValidationReport:
    """Check a reply against the schema rules and return what is admissible.

    `valid_unit_ids` is the set of units in the scene being extracted. An
    assertion citing anything else is dropped: an evidence pointer into a unit
    that was not shown to the model is fabricated, whatever else is right
    about the assertion.

    `provided` maps the pre-registered local ids (the deterministic pass's
    cast and location) to (entity_type, name). The model references those
    ids without declaring them; a redeclaration is admitted only to carry
    attrs and aliases, with its type and name pinned to the provided values.

    `unit_texts` maps unit ids to their text. When given, an evidence span
    reaching past its unit's end is dropped to no-span rather than carried:
    a wrong span misquotes the text it cites, and no span is honest.
    """
    payload = parse_response(text)
    report = ValidationReport()
    provided = provided or {}

    entities_raw = payload.get("entities")
    assertions_raw = payload.get("assertions")
    if not isinstance(entities_raw, list) or not isinstance(assertions_raw, list):
        raise MalformedResponse("reply lacks list-valued 'entities' and 'assertions'")

    by_local_id: dict[str, ValidatedEntity] = {}
    for item in entities_raw:
        entity = _validate_entity(item, valid_unit_ids, provided, report, unit_texts)
        if entity is None:
            continue
        if entity.local_id in by_local_id:
            report.reject("entity", "duplicate_local_id")
            continue
        by_local_id[entity.local_id] = entity

    endpoint_types = {
        local_id: entity_type for local_id, (entity_type, _) in provided.items()
    }
    for item in assertions_raw:
        assertion = _validate_assertion(
            item,
            by_local_id,
            endpoint_types,
            valid_unit_ids,
            scene_local_id,
            report,
            unit_texts,
        )
        if assertion is not None:
            report.assertions.append(assertion)

    # An entity nothing points at is not a production requirement, it is a
    # noun the model noticed. Keeping it would inflate the graph with nodes
    # that have no evidence. A provided id is exempt: its row already exists,
    # and its redeclaration is here only for the attrs it carries.
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
        if local_id in referenced or local_id in provided:
            report.entities.append(entity)
        else:
            report.reject("entity", "unreferenced")

    return report


def _validate_entity(
    item: object,
    valid_unit_ids: set[str],
    provided: dict[str, tuple[str, str]],
    report: ValidationReport,
    unit_texts: dict[str, str] | None = None,
) -> ValidatedEntity | None:
    """Check one proposed entity.

    A redeclaration of a provided id keeps the provided type and name
    whatever the model restated: the row already exists, and letting a
    restatement rename it would fork one entity into two.
    """
    if not isinstance(item, dict):
        report.reject("entity", "not_an_object")
        return None

    local_id = item.get("id")
    entity_type = item.get("type")
    name = item.get("name")

    if not isinstance(local_id, str) or not local_id.strip():
        report.reject("entity", "missing_local_id")
        return None
    local_id = local_id.strip()

    if local_id in provided:
        entity_type, name = provided[local_id]
    else:
        if entity_type not in ENTITY_TYPES:
            report.reject("entity", "unknown_entity_type")
            return None
        if not isinstance(name, str) or not name.strip():
            report.reject("entity", "missing_canonical_name")
            return None

    unusable = _unusable_name_reason(name, entity_type)
    if unusable:
        report.reject("entity", unusable)
        return None

    confidence = _confidence(item.get("conf"))
    if confidence is None:
        report.reject("entity", "bad_confidence")
        return None

    aliases = [
        alias.strip()
        for alias in item.get("aliases", []) or []
        if isinstance(alias, str) and alias.strip()
    ]
    description = item.get("desc")
    return ValidatedEntity(
        local_id=local_id,
        entity_type=entity_type,
        canonical_name=name.strip(),
        aliases=aliases,
        description=description.strip() if isinstance(description, str) else None,
        confidence=confidence,
        attributes=_validate_attributes(
            item.get("attrs"), valid_unit_ids, report, unit_texts
        ),
    )


def _validate_attributes(
    raw: object,
    valid_unit_ids: set[str],
    report: ValidationReport,
    unit_texts: dict[str, str] | None = None,
) -> list[ValidatedAttribute]:
    """Check an entity's proposed attributes. One bad row never drops the rest.

    A model-provenance attribute must cite a unit that was shown, the same
    fabrication rule assertions follow. Keys are normalized here so "Color"
    and "color " cannot become two active rows for one fact.
    """
    if not isinstance(raw, list):
        return []
    kept: list[ValidatedAttribute] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            report.reject("attribute", "not_an_object")
            continue
        key = item.get("k")
        value = item.get("v")
        source_unit_id = item.get("unit")
        if not isinstance(key, str) or not key.strip():
            report.reject("attribute", "missing_key")
            continue
        if not isinstance(value, str) or not value.strip():
            report.reject("attribute", "missing_value")
            continue
        if not isinstance(source_unit_id, str) or source_unit_id not in valid_unit_ids:
            report.reject("attribute", "unknown_source_unit")
            continue
        confidence = _confidence(item.get("conf"))
        if confidence is None:
            report.reject("attribute", "bad_confidence")
            continue
        normalized = normalize_key(key)
        if normalized in seen:
            report.reject("attribute", "duplicate_key")
            continue
        seen.add(normalized)
        start, end = _evidence_span(item, source_unit_id, unit_texts)
        kept.append(
            ValidatedAttribute(
                key=normalized,
                value=value.strip(),
                source_unit_id=source_unit_id,
                confidence=confidence,
                evidence_start=start,
                evidence_end=end,
            )
        )
    return kept


def _validate_assertion(
    item: object,
    entities: dict[str, ValidatedEntity],
    provided_types: dict[str, str],
    valid_unit_ids: set[str],
    scene_local_id: str,
    report: ValidationReport,
    unit_texts: dict[str, str] | None = None,
) -> ValidatedAssertion | None:
    """Check one proposed assertion against its predicate signature.

    The format carries no endpoint kinds: the scene local id is the scene,
    and every other id is an entity, declared in this reply or provided.
    """
    if not isinstance(item, dict):
        report.reject("assertion", "not_an_object")
        return None

    predicate = item.get("p")
    subject_local = item.get("s")
    object_local = item.get("o")
    source_unit_id = item.get("unit")

    if not all(
        isinstance(value, str)
        for value in (predicate, subject_local, object_local)
    ):
        report.reject("assertion", "missing_field")
        return None

    subject_kind = "scene" if subject_local == scene_local_id else "entity"
    object_kind = "scene" if object_local == scene_local_id else "entity"

    if not isinstance(source_unit_id, str) or source_unit_id not in valid_unit_ids:
        report.reject("assertion", "unknown_source_unit")
        return None

    confidence = _confidence(item.get("conf"))
    if confidence is None:
        report.reject("assertion", "bad_confidence")
        return None
    if confidence < DISCARD_BELOW:
        report.reject("assertion", "below_confidence_floor")
        return None

    subject_type = _endpoint_type(
        subject_kind, subject_local, entities, provided_types, scene_local_id
    )
    object_type = _endpoint_type(
        object_kind, object_local, entities, provided_types, scene_local_id
    )
    if subject_type is _UNRESOLVED or object_type is _UNRESOLVED:
        report.reject("assertion", "unresolved_endpoint")
        return None

    try:
        validate_edge(predicate, subject_kind, object_kind, subject_type, object_type)
    except SignatureError as error:
        logger.debug("signature rejection: %s", error)
        report.reject("assertion", "signature_mismatch")
        return None

    start, end = _evidence_span(item, source_unit_id, unit_texts)

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


def _evidence_span(
    item: dict,
    source_unit_id: str | None = None,
    unit_texts: dict[str, str] | None = None,
) -> tuple[int | None, int | None]:
    """Both evidence offsets, or neither.

    A half pair cannot address a span, and passing one side through makes
    downstream slicing silently read from the start or to the end of the
    unit. When the cited unit's text is available, a span reaching past its
    end or addressing nothing is dropped the same way: the item survives,
    the misquote does not.
    """
    start, end = item.get("start"), item.get("end")
    if (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 0 <= start <= end
    ):
        if unit_texts is not None and source_unit_id in unit_texts:
            text = unit_texts[source_unit_id]
            if end > len(text) or start == end:
                return None, None
        return start, end
    return None, None


class _Unresolved:
    """Sentinel for an endpoint that names nothing the model declared."""


_UNRESOLVED = _Unresolved()


def _endpoint_type(
    kind: str,
    local_id: str,
    entities: dict[str, ValidatedEntity],
    provided_types: dict[str, str],
    scene_local_id: str,
) -> str | _Unresolved | None:
    """The entity type of one endpoint, None for a scene, sentinel if unknown."""
    if kind == "scene":
        return None if local_id == scene_local_id else _UNRESOLVED
    entity = entities.get(local_id)
    if entity is not None:
        return entity.entity_type
    return provided_types.get(local_id, _UNRESOLVED)


def _confidence(value: object) -> float | None:
    """Coerce a confidence to a float in the admissible range, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not MINIMUM_CONFIDENCE - 0.001 <= number <= 1.0:
        return None
    return number
