"""The production extraction prompt and its output schema.

`PROMPT_VERSION` is part of the extraction cache key
`(scene_id, input_hash, prompt_version, model_id)`. Editing anything in this
module without bumping it silently reuses answers produced by the old prompt.
Bumping it invalidates every cached scene and re-spends the credit, so both
mistakes are expensive in opposite directions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ripple.db.models import ENTITY_TYPES, PREDICATES
from ripple.graph.predicates import SIGNATURES

PROMPT_VERSION = "extract.v5"

# Below this the extractor has failed rather than
# hedged, so the value is stated in the prompt as a floor.
MINIMUM_CONFIDENCE = 0.35

# Key names are short because the model writes every one of them once per
# item and the output is billed by the token: over a whole build the key
# names alone were a measurable share of the spend. The endpoint kinds are
# not in the format at all; the literal id "scene" is the scene, and any
# other id is an entity.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["entities", "assertions"],
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type", "name", "conf", "attrs"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string", "enum": list(ENTITY_TYPES)},
                    "name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "desc": {"type": "string"},
                    "conf": {"type": "number"},
                    "attrs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["k", "v", "unit", "conf"],
                            "properties": {
                                "k": {"type": "string"},
                                "v": {"type": "string"},
                                "unit": {"type": "string"},
                                "start": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 100000,
                                },
                                "end": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 100000,
                                },
                                "conf": {"type": "number"},
                            },
                        },
                    },
                },
            },
        },
        "assertions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["s", "p", "o", "unit", "conf"],
                "properties": {
                    "s": {"type": "string"},
                    "p": {"type": "string", "enum": list(PREDICATES)},
                    "o": {"type": "string"},
                    "unit": {"type": "string"},
                    "start": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100000,
                    },
                    "end": {"type": "integer", "minimum": 0, "maximum": 100000},
                    "conf": {"type": "number"},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You extract production requirements from one screenplay scene.

You are reading for a production coordinator. Report what a department has to
supply, cast, dress, build, or capture. Every object the text names is a
deliverable someone has to source; record all of them. Do not summarise the
story, do not infer what happens off screen, and do not invent anything the
text does not state.

Return one JSON object and nothing else. No prose, no code fence.
"""


def _signature_lines() -> str:
    """Render the predicate table as prompt text, from the same source of truth."""
    lines = []
    for name, signature in sorted(SIGNATURES.items()):
        subject = signature.subject_kind.value
        if signature.subject_types is not None:
            subject += f" ({', '.join(sorted(signature.subject_types))})"
        obj = signature.object_kind.value
        if signature.object_types is not None:
            obj += f" ({', '.join(sorted(signature.object_types))})"
        lines.append(f"- {name}: {subject} -> {obj}")
    return "\n".join(lines)


RULES = f"""Entity types, and nothing else:
{", ".join(sorted(ENTITY_TYPES))}

Boundary rules:
- A vehicle a character rides or drives is transportation, never prop.
- A worn object is wardrobe; a carried object is prop. If both apply, use the
  state at first appearance.
- A light source inside the frame is set_design. Equipment outside the frame is
  not an entity.
- location is only ever the object of occurs_at. A place named in dialogue but
  not staged is not an entity.
- A name spoken inside a line of dialogue that belongs to a play, film, or
  story within this screenplay is not a cast entity.

Predicates, and nothing else. Each line gives the permitted subject and object:
{_signature_lines()}

Completeness:
- Record every object a department would have to supply for this scene: every
  prop handled or named, every staged set dressing item (monitors, lamps,
  furniture, signage, machinery), every wardrobe piece, every vehicle, every
  visible makeup element (wounds, scars, blood, dirt), every specifically
  called-out sound, every VFX or stunt beat. Background dressing the text
  names is a set_design entity: six monitors on a wall are a deliverable.
- Record every stated detail about an entity as an attribute: counts and
  quantities ("Six monitors" is quantity "six"), stated conditions ("four of
  them dead" is condition "four dead"), colors, materials, printed text and
  numbers (a seal number, a time written on a lid), damage, markings, and
  stated ages.
- Include the phrasings the text uses for an entity as its aliases, so a
  later mention resolves to the same entity.
- Completeness and fabrication are different failures. List everything the
  text states, and nothing it does not.

Output format:
- Each entity: id (short, yours to choose, e.g. "e1"), type, name, optional
  aliases and desc, conf, and attrs.
- Each assertion: s, p, o, unit, optional start and end, conf. s and o are
  entity ids, or the literal string "scene" for the scene being extracted.
- unit is the bracketed identifier of the line that supports the item, and
  start and end are character offsets into that line's text.

Rules for assertions:
- Every assertion cites the unit that supports it.
- establishes marks the first staged introduction of an entity. Use it only
  when this scene introduces the entity.
- conf is between {MINIMUM_CONFIDENCE} and 1. If you would go lower, omit
  the assertion instead.
- Omit anything you cannot support with text in this scene. Cut speculation,
  never coverage: a complete answer names every stated object.

Rules for attributes:
- An entity's stated descriptors are attributes: "the emerald gown" is a gown
  with k "color" and v "emerald", "a dead forklift" is a forklift with k
  "condition" and v "dead".
- Every entity's attrs array is required. Fill it from the text before
  moving on: "Six monitors, four of them dead" REQUIRES the monitors entity
  to carry quantity "six" and condition "four dead". An entity with a stated
  descriptor and an empty attrs array is a wrong answer; an entity whose
  text states no details carries an empty array.
- Prefer these keys when one fits: color, material, state, condition,
  quantity, size, age, style. Invent a key only when none of them fits.
- Every attribute cites the unit that states it, with start and end offsets
  into that unit's text.
- One value per key per entity. Report what this scene states, not what an
  earlier scene might have said.

Already-recorded entities:
- The scene text may be preceded by a list of already-recorded entities with
  fixed ids. Reference those ids in assertions and do not redeclare them,
  with one exception: to attach attrs or aliases to one, emit an entity
  object with that id; its type and name are fixed and restated values are
  ignored.
- Do not report appears_in for a listed cast member or occurs_at for the
  listed location. Those edges are already recorded.
"""


def render_scene(
    heading: str, display_number: str | None, units: list[tuple[str, str, str]]
) -> str:
    """Render one scene for the prompt.

    `units` is (unit_id, unit_type, text). Unit identifiers are included
    because every assertion has to cite one; without them the model has nothing
    stable to point at and the evidence chain breaks.
    """
    label = f"Scene {display_number}" if display_number else "Scene"
    lines = [f"{label}: {heading}", ""]
    for unit_id, unit_type, text in units:
        lines.append(f"[{unit_id}] ({unit_type}) {text}")
    return "\n".join(lines)


def render_provided(provided: list[tuple[str, str, str]]) -> str:
    """The already-recorded entity list: (local_id, entity_type, name) rows."""
    lines = [
        "Already recorded (reference these ids; see the rules above):"
    ]
    for local_id, entity_type, name in provided:
        lines.append(f"- {local_id} ({entity_type}) {name}")
    return "\n".join(lines)


def build_prompt(
    heading: str,
    display_number: str | None,
    units: list[tuple[str, str, str]],
    provided: list[tuple[str, str, str]] | None = None,
) -> str:
    """The full user prompt for one scene.

    `provided` lists the entities the deterministic pre-pass already wrote,
    so the model references them by id instead of redeclaring them.
    """
    parts = [RULES, "---"]
    if provided:
        parts.append(render_provided(provided))
    parts.append(render_scene(heading, display_number, units))
    return "\n\n".join(parts)


def input_hash(heading: str, units: list[tuple[str, str, str]]) -> str:
    """A content hash for the extraction cache key.

    Covers the heading, every unit's type and text, and the prompt material
    itself. RULES is assembled from the predicate signatures and vocabularies,
    so an edit to those changes every prompt without anyone touching
    PROMPT_VERSION; folding the fingerprint in means such an edit invalidates
    the cache instead of serving answers the new prompt never produced. Unit
    identifiers are excluded: they are stable across edits, and including
    them would tie the cache to row identity rather than to content.
    """
    digest = hashlib.sha256()
    digest.update(prompt_fingerprint().encode("utf-8"))
    digest.update(b"\x00")
    digest.update(heading.encode("utf-8"))
    for _, unit_type, text in units:
        digest.update(b"\x00")
        digest.update(unit_type.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
    return digest.hexdigest()


def prompt_fingerprint() -> str:
    """A short hash of everything the model is shown besides the scene."""
    digest = hashlib.sha256()
    for part in (SYSTEM_PROMPT, RULES, json.dumps(OUTPUT_SCHEMA, sort_keys=True)):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]
