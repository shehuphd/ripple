"""Deterministic pre-pass over a scene: what code can read, code records.

Dialogue cues name the cast speaking in a scene, and the heading names its
location; both were parsed at import time by rules with no model involved.
The pre-pass derives those entities and their baseline edges (appears_in
for each speaking cast member, occurs_at for the location) so the model
never has to restate them: the prompt lists them with fixed ids, the model
references the ids, and its answer spends tokens only on what needs reading
comprehension.

Everything here cites a unit the same way a model assertion does. A cue
edge cites the cue unit; the location edge cites the scene heading unit.
A scene without a heading unit gets no location entity rather than an
edge with invented evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from ripple.adapters.base import parse_character_cue, split_heading
from ripple.db.naming import normalize

#: Confidence for rule-derived rows. Parsing a cue or a heading is not
#: inference, so the value is nominal rather than a probability.
RULE_CONFIDENCE = 1.0


@dataclass(frozen=True)
class ProvidedEntity:
    """One entity the pre-pass supplies, with its baseline edge."""

    local_id: str
    entity_type: str
    name: str
    #: appears_in for cast, occurs_at for the location.
    predicate: str
    #: The unit whose text is the evidence, as its UUID string.
    source_unit_id: str
    evidence_start: int | None
    evidence_end: int | None


def provided_for_scene(
    heading: str, units: list[tuple[str, str, str]]
) -> list[ProvidedEntity]:
    """The cast and location code can derive from one scene.

    `units` is (unit_id, unit_type, text) in scene order. Cast comes from
    character cue units, one entity per distinct name with extensions such
    as (V.O.) stripped; the location comes from the heading via the same
    splitter the importer uses.
    """
    result: list[ProvidedEntity] = []
    seen: set[str] = set()
    for unit_id, unit_type, text in units:
        if unit_type != "character":
            continue
        parsed = parse_character_cue(text)
        if parsed is None:
            continue
        name = parsed[0]
        key = normalize(name)
        if not key or key in seen:
            continue
        seen.add(key)
        found = text.find(name)
        result.append(
            ProvidedEntity(
                local_id=f"c{len(seen)}",
                entity_type="cast",
                name=name,
                predicate="appears_in",
                source_unit_id=unit_id,
                evidence_start=found if found >= 0 else None,
                evidence_end=found + len(name) if found >= 0 else None,
            )
        )

    _, location, _ = split_heading(heading)
    heading_unit = next(
        (
            (unit_id, text)
            for unit_id, unit_type, text in units
            if unit_type == "scene_heading"
        ),
        None,
    )
    if location and heading_unit:
        unit_id, text = heading_unit
        found = text.find(location)
        result.append(
            ProvidedEntity(
                local_id="loc",
                entity_type="location",
                name=location,
                predicate="occurs_at",
                source_unit_id=unit_id,
                evidence_start=found if found >= 0 else None,
                evidence_end=found + len(location) if found >= 0 else None,
            )
        )
    return result
