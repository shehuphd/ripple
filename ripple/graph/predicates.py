"""The predicate signatures: which endpoint kinds and types each accepts.

The database CHECK constraints enforce the structural half of an assertion:
each side is kinded and holds precisely one key. They cannot enforce which
predicate accepts which entity type, because that is a relationship between
three columns and a lookup table. This module is the other half.

Extraction and the diff engine both validate against it, so a model cannot
introduce an edge the schema lock does not define, and a proposed change cannot
either.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeVar

ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "cast",
        "prop",
        "wardrobe",
        "location",
        "set_design",
        "makeup",
        "transportation",
        "vfx",
        "stunt",
        "sound",
    }
)
# Everything a scene can require: a department deliverable, never a person and
# never the place the scene happens at, which is `occurs_at`.
REQUIRABLE = ENTITY_TYPES - {"cast", "location"}
# Everything that can be present in a scene. A location is where the scene is,
# not something appearing in it.
PRESENTABLE = ENTITY_TYPES - {"location"}


class NodeKind(str, Enum):
    ENTITY = "entity"
    SCENE = "scene"


@dataclass(frozen=True)
class Signature:
    """What one predicate accepts on each side."""

    subject_kind: NodeKind
    object_kind: NodeKind
    subject_types: frozenset[str] | None
    object_types: frozenset[str] | None
    symmetric: bool = False
    #: At most one such edge per object per script. Used by `establishes`.
    unique_per_object: bool = False


SIGNATURES: dict[str, Signature] = {
    "appears_in": Signature(NodeKind.ENTITY, NodeKind.SCENE, PRESENTABLE, None),
    "occurs_at": Signature(
        NodeKind.SCENE, NodeKind.ENTITY, None, frozenset({"location"})
    ),
    "wears": Signature(
        NodeKind.ENTITY,
        NodeKind.ENTITY,
        frozenset({"cast"}),
        frozenset({"wardrobe", "makeup"}),
    ),
    "carries": Signature(
        NodeKind.ENTITY,
        NodeKind.ENTITY,
        frozenset({"cast"}),
        frozenset({"prop"}),
    ),
    "uses": Signature(
        NodeKind.ENTITY,
        NodeKind.ENTITY,
        frozenset({"cast"}),
        frozenset({"prop", "set_design", "transportation", "vfx"}),
    ),
    "travels_by": Signature(
        NodeKind.ENTITY,
        NodeKind.ENTITY,
        frozenset({"cast"}),
        frozenset({"transportation"}),
    ),
    "interacts_with": Signature(
        NodeKind.ENTITY,
        NodeKind.ENTITY,
        frozenset({"cast"}),
        frozenset({"cast"}),
        symmetric=True,
    ),
    "requires": Signature(NodeKind.SCENE, NodeKind.ENTITY, None, REQUIRABLE),
    "establishes": Signature(
        NodeKind.SCENE,
        NodeKind.ENTITY,
        None,
        ENTITY_TYPES,
        unique_per_object=True,
    ),
}

PREDICATES: frozenset[str] = frozenset(SIGNATURES)

# Endpoints are UUIDs in the database and strings in a model reply; the
# ordering helper below accepts either and returns what it was given.
Endpoint = TypeVar("Endpoint")


class SignatureError(ValueError):
    """An edge the schema lock does not define."""


def validate_edge(
    predicate: str,
    subject_kind: str,
    object_kind: str,
    subject_type: str | None = None,
    object_type: str | None = None,
) -> None:
    """Raise SignatureError unless this edge matches its predicate's signature.

    Entity types are optional so a scene-side endpoint can pass None. Passing
    None for an entity side skips the type check rather than failing it, which
    lets a caller validate shape before it has resolved an entity.
    """
    signature = SIGNATURES.get(predicate)
    if signature is None:
        raise SignatureError(
            f"{predicate!r} is not one of the nine defined predicates."
        )

    if subject_kind != signature.subject_kind.value:
        raise SignatureError(
            f"{predicate!r} takes a {signature.subject_kind.value} subject, "
            f"got {subject_kind!r}."
        )
    if object_kind != signature.object_kind.value:
        raise SignatureError(
            f"{predicate!r} takes a {signature.object_kind.value} object, "
            f"got {object_kind!r}."
        )

    if (
        signature.subject_types is not None
        and subject_type is not None
        and subject_type not in signature.subject_types
    ):
        raise SignatureError(
            f"{predicate!r} does not accept a {subject_type!r} subject. "
            f"Accepted: {', '.join(sorted(signature.subject_types))}."
        )
    if (
        signature.object_types is not None
        and object_type is not None
        and object_type not in signature.object_types
    ):
        raise SignatureError(
            f"{predicate!r} does not accept a {object_type!r} object. "
            f"Accepted: {', '.join(sorted(signature.object_types))}."
        )


def canonical_endpoints(
    predicate: str, subject: Endpoint, obj: Endpoint
) -> tuple[Endpoint, Endpoint]:
    """Order the endpoints of a symmetric predicate deterministically.

    `interacts_with` is symmetric, so A-with-B and B-with-A are one edge.
    Storing it twice would double every count and make the dedupe key useless.
    The lower identifier is fixed as the subject.

    Endpoints are returned as they were passed, so a caller handing in UUIDs
    gets UUIDs back. Ordering compares their string forms, which is stable for
    both UUID objects and strings.
    """
    signature = SIGNATURES.get(predicate)
    if signature is None or not signature.symmetric:
        return subject, obj
    return (subject, obj) if str(subject) <= str(obj) else (obj, subject)
