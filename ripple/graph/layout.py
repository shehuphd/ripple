"""Deterministic 2D layout for a scoped graph view.

This is a fixed layout rather than a
force simulation, for four reasons:

- Edge labels are the content. `Mara travels_by Blue sedan` has to be readable,
  which means horizontal text and edges that do not cross the labels.
- The views are small by design. Architecture section 4 scopes every graph to
  one selection, so the hairball a force layout exists to untangle never forms.
- A force simulation settles differently on each run, so two screenshots of the
  same graph do not match. A deterministic layout is diffable and demoable.
- No dependency, no WebGL, no bundle. It runs on a Replit Starter box.

The arrangement: the focus at the centre, scene nodes on a horizontal spine
either side of it in script order, and entity nodes in fixed angular wedges,
one per department. Department is the strongest grouping a coordinator reads
by, so it gets the strongest spatial signal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Departments in a fixed order, so a wedge belongs to the same department in
# every view of every script. A layout that reshuffles between renders costs
# the viewer their orientation.
DEPARTMENT_ORDER = (
    "cast",
    "transportation",
    "prop",
    "set_design",
    "wardrobe",
    "makeup",
    "sound",
    "vfx",
    "stunt",
    "location",
)

# The horizontal band reserved for the scene spine, in degrees either side of
# horizontal. Entities never enter it, so scene labels stay legible.
SPINE_BAND = 22.0
FOCUS_RADIUS = 0.0
# Ring radii are bounded by the vertical clamp: a node at 0.5 + SECOND_RING
# must still fit inside the margin, or both rings clamp to the same edge and
# the radial separation they exist to provide disappears.
FIRST_RING = 0.25
SECOND_RING = 0.40
SPINE_STEP = 0.30
# From this many entities in one department, alternate rings so neighbours are
# separated radially as well as angularly. Two nodes in adjacent angular slots
# at the same radius overlap once their labels are drawn.
RING_SPLIT_AT = 2


@dataclass(frozen=True)
class Placed:
    """One node with a position, in fractions of the viewport."""

    id: str
    label: str
    kind: str
    entity_type: str | None
    x: float
    y: float
    ring: int
    removed: bool = False


def _sort_key(node: dict[str, Any]) -> tuple:
    """A total order over nodes, so layout does not depend on input order."""
    return (
        0 if node.get("kind") == "scene" else 1,
        (
            DEPARTMENT_ORDER.index(node["entity_type"])
            if node.get("entity_type") in DEPARTMENT_ORDER
            else len(DEPARTMENT_ORDER)
        ),
        str(node.get("label", "")),
        str(node.get("id", "")),
    )


def _scene_order(node: dict[str, Any]) -> tuple:
    """Scene nodes sort by their number, numerically where they have one."""
    label = str(node.get("label", ""))
    digits = "".join(character for character in label if character.isdigit())
    return (int(digits) if digits else 0, label)


def layout(
    nodes: list[dict[str, Any]],
    focus_id: str | None = None,
    aspect: float = 1.6,
) -> list[Placed]:
    """Place nodes in a unit square, deterministically.

    Coordinates are fractions of the viewport, so the caller scales them to
    whatever size it renders at and the layout survives a resize. `aspect`
    widens the horizontal spread to match a landscape canvas.
    """
    if not nodes:
        return []

    ordered = sorted(nodes, key=_sort_key)
    focus = next((n for n in ordered if str(n.get("id")) == str(focus_id)), None)
    placed: list[Placed] = []

    if focus is not None:
        placed.append(
            Placed(
                id=str(focus["id"]),
                label=focus.get("label", ""),
                kind=focus.get("kind", "entity"),
                entity_type=focus.get("entity_type"),
                x=0.5,
                y=0.5,
                ring=0,
                removed=bool(focus.get("removed")),
            )
        )

    rest = [n for n in ordered if n is not focus]
    scenes = sorted((n for n in rest if n.get("kind") == "scene"), key=_scene_order)
    entities = [n for n in rest if n.get("kind") != "scene"]

    placed.extend(_place_spine(scenes, aspect))
    placed.extend(_place_wedges(entities, aspect))
    return placed


def _place_spine(scenes: list[dict[str, Any]], aspect: float) -> list[Placed]:
    """Scenes on a horizontal line, alternating left and right of the focus.

    Alternating rather than filling left-to-right keeps the focus visually
    central even when the scene count is odd.
    """
    placed: list[Placed] = []
    for index, node in enumerate(scenes):
        step = (index // 2) + 1
        direction = -1 if index % 2 == 0 else 1
        offset = min(SPINE_STEP * step, 0.46)
        placed.append(
            Placed(
                id=str(node["id"]),
                label=node.get("label", ""),
                kind="scene",
                entity_type=None,
                x=_clamp(0.5 + direction * offset * (aspect / 1.6)),
                y=0.5,
                ring=step,
                removed=bool(node.get("removed")),
            )
        )
    return placed


def _place_wedges(entities: list[dict[str, Any]], aspect: float) -> list[Placed]:
    """Entities in one angular wedge per department, above and below the spine."""
    by_department: dict[str, list[dict[str, Any]]] = {}
    for node in entities:
        by_department.setdefault(node.get("entity_type") or "other", []).append(node)

    present = [d for d in DEPARTMENT_ORDER if d in by_department]
    present.extend(sorted(d for d in by_department if d not in DEPARTMENT_ORDER))
    if not present:
        return []

    # Two arcs, upper and lower, each running from just past horizontal to just
    # short of it on the other side. Departments alternate between them so a
    # single crowded department does not fill one half.
    upper = (180.0 + SPINE_BAND, 360.0 - SPINE_BAND)
    lower = (SPINE_BAND, 180.0 - SPINE_BAND)
    halves = {0: upper, 1: lower}
    counts = {0: sum(1 for i in range(len(present)) if i % 2 == 0), 1: 0}
    counts[1] = len(present) - counts[0]

    # A wedge is sized by how many entities the department holds, not split
    # evenly. Equal shares crowd a busy department into the same arc as a
    # department holding one node, and its labels then overlap.
    members_in_half = {
        half: sum(
            len(by_department[d])
            for index, d in enumerate(present)
            if index % 2 == half
        )
        or 1
        for half in (0, 1)
    }

    placed: list[Placed] = []
    cursor = {0: halves[0][0], 1: halves[1][0]}
    for index, department in enumerate(present):
        half = index % 2
        start, end = halves[half]
        members = sorted(
            by_department[department],
            key=lambda n: (str(n.get("label", "")), str(n["id"])),
        )
        share = (end - start) * len(members) / members_in_half[half]
        placed.extend(_place_members(members, cursor[half], share, aspect))
        cursor[half] += share
    return placed


def _place_members(
    members: list[dict[str, Any]], wedge_start: float, share: float, aspect: float
) -> list[Placed]:
    """Spread one department's entities across its wedge."""
    placed: list[Placed] = []
    total = len(members)
    for position, node in enumerate(members):
        # Centre a single member in its wedge rather than pinning it to the edge.
        fraction = (position + 1) / (total + 1)
        angle = math.radians(wedge_start + share * fraction)
        ring = 1 if total < RING_SPLIT_AT or position % 2 == 0 else 2
        radius = FIRST_RING if ring == 1 else SECOND_RING
        placed.append(
            Placed(
                id=str(node["id"]),
                label=node.get("label", ""),
                kind="entity",
                entity_type=node.get("entity_type"),
                x=_clamp(0.5 + math.cos(angle) * radius * (aspect / 1.6)),
                y=_clamp(0.5 + math.sin(angle) * radius),
                ring=ring,
                removed=bool(node.get("removed")),
            )
        )
    return placed


def _clamp(value: float, margin: float = 0.09) -> float:
    """Keep a node inside the canvas, with room for its label.

    The margin is generous because a node is positioned by its centre while its
    label extends either side of it. A tighter margin clips the longest names.
    """
    return max(margin, min(1.0 - margin, value))


def script_layout(nodes: list[dict[str, Any]], aspect: float = 2.0) -> list[Placed]:
    """Place a whole script's graph, deterministically.

    Scenes run left to right in script order along the spine, on two staggered
    rows so forty-plus labels do not collide. Entities keep the same
    department wedges as the scoped view, so a department occupies the same
    region of the screen whichever view the user is in.
    """
    if not nodes:
        return []

    ordered = sorted(nodes, key=_sort_key)
    scenes = sorted(
        (n for n in ordered if n.get("kind") == "scene"), key=_scene_order
    )
    entities = [n for n in ordered if n.get("kind") != "scene"]

    placed: list[Placed] = []
    span = max(len(scenes) - 1, 1)
    for index, node in enumerate(scenes):
        placed.append(
            Placed(
                id=str(node["id"]),
                label=node.get("label", ""),
                kind="scene",
                entity_type=None,
                x=_clamp(0.06 + 0.88 * index / span, 0.05),
                # Alternate rows, so adjacent labels stagger instead of touch.
                y=0.47 if index % 2 == 0 else 0.53,
                ring=0,
                removed=bool(node.get("removed")),
            )
        )
    placed.extend(_place_wedges(entities, aspect))
    return placed


__all__ = ["DEPARTMENT_ORDER", "Placed", "layout", "script_layout"]
