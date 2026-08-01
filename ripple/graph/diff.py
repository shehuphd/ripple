"""The deterministic diff engine.

PRD section 8: application code computes the exact graph diff. No model is
involved here and none may be. The synthesizer explains what this produces and
cannot add to it.

The comparison is by edge identity, not by row identity, because a proposed
assertion has no row yet. An edge is identified by its two endpoints and its
predicate, with entity endpoints named by their normalized canonical name so a
proposed entity that does not exist yet still compares against an accepted one
that does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ripple.db.naming import normalize
from ripple.graph.predicates import canonical_endpoints


@dataclass(frozen=True)
class EdgeRef:
    """One endpoint of an edge, named in a way that survives not existing yet."""

    kind: str
    #: A scene UUID for a scene endpoint; a normalized canonical name for an
    #: entity, so a proposed entity compares against an accepted one.
    ref: str
    entity_type: str | None = None

    @classmethod
    def scene(cls, scene_id: Any) -> EdgeRef:
        return cls(kind="scene", ref=str(scene_id))

    @classmethod
    def entity(cls, canonical_name: str, entity_type: str) -> EdgeRef:
        return cls(
            kind="entity", ref=normalize(canonical_name), entity_type=entity_type
        )

    @property
    def key(self) -> str:
        """What makes two endpoints the same endpoint.

        Entity identity is `(type, normalized name)`, matching the database's
        uniqueness rule: a `prop` bicycle and a `transportation` bicycle are
        two entities, and comparing on name alone would silently merge them.
        """
        return f"{self.entity_type}:{self.ref}" if self.kind == "entity" else self.ref

    @property
    def label(self) -> str:
        """A short display label for the diff view."""
        return self.ref


@dataclass(frozen=True)
class Edge:
    """One assertion, as the diff engine sees it."""

    subject: EdgeRef
    predicate: str
    obj: EdgeRef
    confidence: float = 1.0
    source_unit_id: str | None = None
    assertion_id: str | None = None
    display_subject: str = ""
    display_object: str = ""

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        """What makes two edges the same edge."""
        return (
            self.subject.kind,
            self.subject.key,
            self.predicate,
            self.obj.kind,
            self.obj.key,
        )

    @property
    def slot(self) -> tuple[str, str, str, str]:
        """Subject and predicate without the object.

        Two edges sharing a slot but differing in object are a change rather
        than an unrelated removal and addition: `Loading dock requires Sodium
        wash` becoming `Loading dock requires Practical lamps` is one
        production decision, and showing it as two rows loses that.
        """
        return (self.subject.kind, self.subject.key, self.predicate, self.obj.kind)


@dataclass
class GraphDiff:
    """Added, removed, and changed edges between accepted and proposed."""

    added: list[Edge] = field(default_factory=list)
    removed: list[Edge] = field(default_factory=list)
    changed: list[tuple[Edge, Edge]] = field(default_factory=list)
    unchanged: list[Edge] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    @property
    def operation_count(self) -> int:
        """How many graph operations acceptance would apply."""
        return len(self.added) + len(self.removed) + (len(self.changed) * 2)

    def summary(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "removed": len(self.removed),
            "changed": len(self.changed),
            "unchanged": len(self.unchanged),
        }

    def affected_entities(self) -> set[str]:
        """Normalized names of every entity either side of a changed edge.

        Continuity retrieval starts from this set, so an entity touched only as
        the object of a removal still gets swept.
        """
        names: set[str] = set()
        for before, after in self.changed:
            for edge in (before, after):
                names.update(_entity_names(edge))
        for edge in (*self.added, *self.removed):
            names.update(_entity_names(edge))
        return names


def _entity_names(edge: Edge) -> set[str]:
    return {
        endpoint.ref
        for endpoint in (edge.subject, edge.obj)
        if endpoint.kind == "entity"
    }


def normalise_edge(edge: Edge) -> Edge:
    """Put a symmetric edge's endpoints in canonical order.

    Without this, `Mara interacts_with Dev` and `Dev interacts_with Mara` diff
    as one removal and one addition when nothing changed.
    """
    subject, obj = canonical_endpoints(edge.predicate, edge.subject, edge.obj)
    if (subject, obj) == (edge.subject, edge.obj):
        return edge
    return Edge(
        subject=subject,
        predicate=edge.predicate,
        obj=obj,
        confidence=edge.confidence,
        source_unit_id=edge.source_unit_id,
        assertion_id=edge.assertion_id,
        display_subject=edge.display_object,
        display_object=edge.display_subject,
    )


def diff_edges(accepted: list[Edge], proposed: list[Edge]) -> GraphDiff:
    """Compare two sets of edges.

    Deterministic and total: every input edge lands in precisely one of added,
    removed, changed, or unchanged, and running it twice on the same input
    gives the same answer.
    """
    accepted_by_identity = {
        edge.identity: edge for edge in map(normalise_edge, accepted)
    }
    proposed_by_identity = {
        edge.identity: edge for edge in map(normalise_edge, proposed)
    }

    diff = GraphDiff()

    survived = accepted_by_identity.keys() & proposed_by_identity.keys()
    for identity in sorted(survived):
        diff.unchanged.append(accepted_by_identity[identity])

    only_accepted = [
        accepted_by_identity[identity]
        for identity in sorted(accepted_by_identity.keys() - survived)
    ]
    only_proposed = [
        proposed_by_identity[identity]
        for identity in sorted(proposed_by_identity.keys() - survived)
    ]

    # Pair a removal with an addition that shares its slot. Only when the slot
    # holds exactly one of each: two removals and two additions on one slot is
    # a rewrite, and guessing which pairs with which would be invention.
    removals_by_slot: dict[tuple, list[Edge]] = {}
    additions_by_slot: dict[tuple, list[Edge]] = {}
    for edge in only_accepted:
        removals_by_slot.setdefault(edge.slot, []).append(edge)
    for edge in only_proposed:
        additions_by_slot.setdefault(edge.slot, []).append(edge)

    paired_removals: set[tuple] = set()
    paired_additions: set[tuple] = set()
    for slot, removals in removals_by_slot.items():
        additions = additions_by_slot.get(slot, [])
        if len(removals) == 1 and len(additions) == 1:
            diff.changed.append((removals[0], additions[0]))
            paired_removals.add(removals[0].identity)
            paired_additions.add(additions[0].identity)

    diff.removed = [
        edge for edge in only_accepted if edge.identity not in paired_removals
    ]
    diff.added = [
        edge for edge in only_proposed if edge.identity not in paired_additions
    ]
    diff.changed.sort(key=lambda pair: pair[0].identity)
    return diff


def to_operations(diff: GraphDiff) -> list[dict[str, Any]]:
    """Turn a diff into ordered, invertible change operations.

    Removals come before additions so a slot is free before its replacement
    lands, which matters for any predicate the graph constrains to one edge per
    slot. `target_id` is null for a proposed edge that has no row yet; the
    change-set service resolves it inside the acceptance transaction.
    """
    operations: list[dict[str, Any]] = []

    def append(operation_type: str, edge: Edge, before: Edge | None = None) -> None:
        operations.append(
            {
                "sequence_index": len(operations),
                "operation_type": operation_type,
                "target_type": "assertion",
                "target_id": (
                    edge.assertion_id if operation_type != "add_assertion" else None
                ),
                "before_json": (
                    _payload(before or edge)
                    if operation_type != "add_assertion"
                    else None
                ),
                "after_json": (
                    _payload(edge) if operation_type != "remove_assertion" else None
                ),
            }
        )

    for edge in diff.removed:
        append("remove_assertion", edge)
    for before, after in diff.changed:
        operations.append(
            {
                "sequence_index": len(operations),
                "operation_type": "update_assertion",
                "target_type": "assertion",
                "target_id": before.assertion_id,
                "before_json": _payload(before),
                "after_json": _payload(after),
            }
        )
    for edge in diff.added:
        append("add_assertion", edge)

    return operations


def _payload(edge: Edge) -> dict[str, Any]:
    """A snapshot sufficient to validate and invert an operation."""
    return {
        "subject_kind": edge.subject.kind,
        "subject_ref": edge.subject.ref,
        "subject_entity_type": edge.subject.entity_type,
        "predicate": edge.predicate,
        "object_kind": edge.obj.kind,
        "object_ref": edge.obj.ref,
        "object_entity_type": edge.obj.entity_type,
        "confidence": edge.confidence,
        "source_unit_id": edge.source_unit_id,
        "assertion_id": edge.assertion_id,
        "display_subject": edge.display_subject,
        "display_object": edge.display_object,
    }
