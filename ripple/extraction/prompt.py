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

PROMPT_VERSION = "extract.v1"

# Schema Lock v1 section 6: below this the extractor has failed rather than
# hedged, so the value is stated in the prompt as a floor.
MINIMUM_CONFIDENCE = 0.35

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["entities", "assertions"],
    "properties": {
        "entities": {
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
        "assertions": {
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
                    "evidence_start": {"type": "integer"},
                    "evidence_end": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You extract production requirements from one screenplay scene.

You are reading for a production coordinator. Report what a department has to
supply, cast, dress, build, or capture. Do not summarise the story, do not
infer what happens off screen, and do not invent anything the text does not
state.

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

Rules for assertions:
- Every assertion cites the source_unit_id of the unit that supports it.
- evidence_start and evidence_end are character offsets into that unit's text.
- Use the literal string "scene" as the local_id for the scene being extracted.
- establishes marks the first staged introduction of an entity. Use it only
  when this scene introduces the entity.
- Confidence is between {MINIMUM_CONFIDENCE} and 1. If you would go lower,
  omit the assertion instead.
- Omit anything you cannot support with text in this scene. A short, correct
  answer is worth more than a long, speculative one.
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


def build_prompt(
    heading: str, display_number: str | None, units: list[tuple[str, str, str]]
) -> str:
    """The full user prompt for one scene."""
    return f"{RULES}\n\n---\n\n{render_scene(heading, display_number, units)}"


def input_hash(heading: str, units: list[tuple[str, str, str]]) -> str:
    """A content hash for the extraction cache key.

    Covers the heading and every unit's type and text, so an edit to any unit
    invalidates the scene's cached extraction. Unit identifiers are excluded:
    they are stable across edits, and including them would tie the cache to row
    identity rather than to content.
    """
    digest = hashlib.sha256()
    digest.update(heading.encode("utf-8"))
    for _, unit_type, text in units:
        digest.update(b"\x00")
        digest.update(unit_type.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
    return digest.hexdigest()


def schema_fingerprint() -> str:
    """A short hash of the output schema, for diagnosing a stale cache."""
    return hashlib.sha256(
        json.dumps(OUTPUT_SCHEMA, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
