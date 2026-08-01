"""The deterministic graph layout.

A layout that moves between renders costs the viewer their orientation and
makes two screenshots of the same graph incomparable, so determinism is the
property under test, not an implementation detail.
"""

from __future__ import annotations

import random

import pytest

from ripple.graph.layout import DEPARTMENT_ORDER, layout


def node(node_id, label, kind="entity", entity_type="prop", **extra):
    return {
        "id": node_id,
        "label": label,
        "kind": kind,
        "entity_type": entity_type if kind == "entity" else None,
        **extra,
    }


def sample():
    return [
        node("f", "unit 111", kind="entity", entity_type=None),
        node("s1", "Sc 14", kind="scene"),
        node("s2", "Sc 22", kind="scene"),
        node("s3", "Sc 31", kind="scene"),
        node("e1", "Mara", entity_type="cast"),
        node("e2", "Dev", entity_type="cast"),
        node("e3", "Blue sedan", entity_type="transportation"),
        node("e4", "Pallet jack", entity_type="prop"),
        node("e5", "Sodium wash", entity_type="set_design"),
        node("e6", "Wool coat", entity_type="wardrobe"),
    ]


class TestDegenerateInput:
    def test_no_nodes_places_nothing(self):
        assert layout([]) == []

    def test_a_lone_focus_sits_in_the_centre(self):
        placed = layout([node("f", "only", entity_type=None)], focus_id="f")
        assert (placed[0].x, placed[0].y) == (0.5, 0.5)

    def test_an_absent_focus_does_not_crash(self):
        placed = layout(sample(), focus_id="not-in-the-set")
        assert len(placed) == len(sample())


class TestDeterminism:
    def test_input_order_does_not_move_anything(self):
        nodes = sample()
        shuffled = nodes[:]
        random.Random(7).shuffle(shuffled)
        first = {p.id: (p.x, p.y) for p in layout(nodes, focus_id="f")}
        second = {p.id: (p.x, p.y) for p in layout(shuffled, focus_id="f")}
        assert first == second

    def test_running_twice_gives_identical_positions(self):
        first = layout(sample(), focus_id="f")
        second = layout(sample(), focus_id="f")
        assert [(p.id, p.x, p.y) for p in first] == [(p.id, p.x, p.y) for p in second]

    def test_a_department_keeps_its_side_across_scripts(self):
        """A wedge belongs to the same department in every view."""
        small = [
            node("f", "u", entity_type=None),
            node("a", "Mara", entity_type="cast"),
        ]
        big = sample()
        cast_small = next(p for p in layout(small, focus_id="f") if p.id == "a")
        cast_big = next(p for p in layout(big, focus_id="f") if p.id == "e1")
        assert (cast_small.y < 0.5) == (cast_big.y < 0.5)


class TestArrangement:
    def test_the_focus_is_centred(self):
        placed = {p.id: p for p in layout(sample(), focus_id="f")}
        assert (placed["f"].x, placed["f"].y) == (0.5, 0.5)

    def test_scenes_sit_on_the_horizontal_spine(self):
        placed = [p for p in layout(sample(), focus_id="f") if p.kind == "scene"]
        assert placed
        assert all(p.y == 0.5 for p in placed)

    def test_scenes_alternate_either_side_of_the_focus(self):
        placed = [p for p in layout(sample(), focus_id="f") if p.kind == "scene"]
        assert any(p.x < 0.5 for p in placed) and any(p.x > 0.5 for p in placed)

    def test_scenes_run_in_script_order_outward(self):
        placed = {
            p.label: p for p in layout(sample(), focus_id="f") if p.kind == "scene"
        }
        assert abs(placed["Sc 14"].x - 0.5) < abs(placed["Sc 31"].x - 0.5)

    def test_entities_stay_clear_of_the_spine(self):
        """Otherwise entity labels overlap the scene labels."""
        entities = [
            p
            for p in layout(sample(), focus_id="f")
            if p.kind == "entity" and p.id != "f"
        ]
        assert entities
        assert all(abs(p.y - 0.5) > 0.05 for p in entities)

    def test_one_department_stays_together(self):
        placed = {p.id: p for p in layout(sample(), focus_id="f")}
        # Both cast members share a wedge, so they are near each other.
        assert abs(placed["e1"].y - placed["e2"].y) < 0.5

    def test_a_crowded_department_uses_two_rings(self):
        crowded = [node("f", "u", entity_type=None)] + [
            node(f"p{i}", f"Prop {i}", entity_type="prop") for i in range(8)
        ]
        rings = {p.ring for p in layout(crowded, focus_id="f") if p.id != "f"}
        assert rings == {1, 2}


class TestBounds:
    @pytest.mark.parametrize("count", [1, 5, 20, 60])
    def test_everything_stays_inside_the_canvas(self, count):
        nodes = [node("f", "u", entity_type=None)] + [
            node(f"n{i}", f"Node {i}", entity_type=DEPARTMENT_ORDER[i % 10])
            for i in range(count)
        ]
        for placed in layout(nodes, focus_id="f"):
            assert 0.0 <= placed.x <= 1.0, placed
            assert 0.0 <= placed.y <= 1.0, placed

    def test_a_wide_aspect_spreads_horizontally_not_vertically(self):
        narrow = {p.id: p for p in layout(sample(), focus_id="f", aspect=1.0)}
        wide = {p.id: p for p in layout(sample(), focus_id="f", aspect=2.4)}
        assert abs(wide["s1"].x - 0.5) > abs(narrow["s1"].x - 0.5)
        assert wide["s1"].y == narrow["s1"].y

    def test_no_two_nodes_land_on_the_same_point(self):
        placed = layout(sample(), focus_id="f")
        points = [(round(p.x, 3), round(p.y, 3)) for p in placed]
        assert len(points) == len(set(points))


class TestSeparation:
    """Nodes that land on top of each other make a graph unreadable."""

    def _closest(self, placed, aspect=1.6):
        pairs = [
            (
                ((a.x - b.x) * aspect) ** 2 + (a.y - b.y) ** 2,
                a.label,
                b.label,
            )
            for i, a in enumerate(placed)
            for b in placed[i + 1 :]
        ]
        return min(pairs) if pairs else (1.0, "", "")

    def test_two_nodes_in_one_department_are_separated(self):
        """Two set_design entities used to clamp onto the same point."""
        nodes = [
            node("f", "Sc 14", kind="scene"),
            node("a", "Sodium wash", entity_type="set_design"),
            node("b", "Dead forklift", entity_type="set_design"),
        ]
        placed = layout(nodes, focus_id="f")
        squared, first, second = self._closest(placed)
        assert squared**0.5 > 0.18, f"{first} and {second} are too close"

    def test_rings_stay_inside_the_clamp(self):
        """A radius the clamp truncates provides no separation at all."""
        crowded = [node("f", "Sc 1", kind="scene")] + [
            node(f"p{i}", f"Prop {i}", entity_type="prop") for i in range(6)
        ]
        placed = [p for p in layout(crowded, focus_id="f") if p.ring == 2]
        assert placed
        assert all(0.09 < p.y < 0.91 for p in placed)

    @pytest.mark.parametrize("count", [4, 9, 16])
    def test_no_pair_collides_at_any_size(self, count):
        nodes = [node("f", "Sc 1", kind="scene")] + [
            node(f"n{i}", f"Node {i}", entity_type=DEPARTMENT_ORDER[i % 10])
            for i in range(count)
        ]
        squared, first, second = self._closest(layout(nodes, focus_id="f"))
        assert squared**0.5 > 0.09, f"{first} and {second} collide"
