"""The deterministic diff engine.

The engine's job is to be exactly right and never creative, so these tests
attack the cases where a diff could plausibly invent something: symmetric
edges, ambiguous slot pairing, and empty input.
"""

from __future__ import annotations

from ripple.graph.diff import Edge, EdgeRef, diff_edges, to_operations

SCENE = EdgeRef.scene("11111111-1111-1111-1111-111111111111")


def sedan() -> EdgeRef:
    return EdgeRef.entity("Blue sedan", "transportation")


def bicycle() -> EdgeRef:
    return EdgeRef.entity("Picture bicycle", "transportation")


def mara() -> EdgeRef:
    return EdgeRef.entity("Mara", "cast")


def dev() -> EdgeRef:
    return EdgeRef.entity("Dev", "cast")


def edge(subject, predicate, obj, **kwargs) -> Edge:
    return Edge(subject=subject, predicate=predicate, obj=obj, **kwargs)


class TestDegenerateInput:
    def test_two_empty_sets_produce_an_empty_diff(self):
        diff = diff_edges([], [])
        assert diff.is_empty and diff.operation_count == 0

    def test_removing_everything_is_all_removals(self):
        diff = diff_edges([edge(sedan(), "appears_in", SCENE)], [])
        assert len(diff.removed) == 1 and not diff.added and not diff.changed

    def test_adding_to_nothing_is_all_additions(self):
        diff = diff_edges([], [edge(bicycle(), "appears_in", SCENE)])
        assert len(diff.added) == 1 and not diff.removed

    def test_an_identical_set_produces_no_operations(self):
        edges = [
            edge(sedan(), "appears_in", SCENE),
            edge(mara(), "travels_by", sedan()),
        ]
        diff = diff_edges(edges, list(edges))
        assert diff.is_empty
        assert len(diff.unchanged) == 2


class TestNameNormalization:
    def test_a_casing_difference_is_not_a_change(self):
        """THE BLUE SEDAN and the blue sedan are one entity under name normalization."""
        diff = diff_edges(
            [
                edge(
                    EdgeRef.entity("THE BLUE SEDAN", "transportation"),
                    "appears_in",
                    SCENE,
                )
            ],
            [
                edge(
                    EdgeRef.entity("the blue sedan", "transportation"),
                    "appears_in",
                    SCENE,
                )
            ],
        )
        assert diff.is_empty

    def test_the_same_name_in_a_different_type_is_a_different_entity(self):
        diff = diff_edges(
            [edge(EdgeRef.entity("Bicycle", "transportation"), "appears_in", SCENE)],
            [edge(EdgeRef.entity("Bicycle", "prop"), "appears_in", SCENE)],
        )
        assert len(diff.changed) == 0
        assert len(diff.added) == 1 and len(diff.removed) == 1


class TestSymmetricEdges:
    def test_reversing_a_symmetric_edge_is_not_a_change(self):
        """`interacts_with` A-B and B-A are one edge, not a removal plus an add."""
        diff = diff_edges(
            [edge(mara(), "interacts_with", dev())],
            [edge(dev(), "interacts_with", mara())],
        )
        assert diff.is_empty, diff.summary()

    def test_reversing_an_asymmetric_edge_is_a_change(self):
        diff = diff_edges(
            [edge(mara(), "carries", EdgeRef.entity("Torch", "prop"))],
            [edge(EdgeRef.entity("Torch", "prop"), "carries", mara())],
        )
        assert not diff.is_empty


class TestSlotPairing:
    def test_a_swapped_object_reads_as_one_change(self):
        """The mockup shows `Sodium wash -> Practical lamps` as one row."""
        diff = diff_edges(
            [edge(SCENE, "requires", EdgeRef.entity("Sodium wash", "set_design"))],
            [edge(SCENE, "requires", EdgeRef.entity("Practical lamps", "set_design"))],
        )
        assert len(diff.changed) == 1
        before, after = diff.changed[0]
        assert before.obj.ref == "sodium wash"
        assert after.obj.ref == "practical lamps"
        assert not diff.added and not diff.removed

    def test_an_ambiguous_slot_is_not_paired(self):
        """Two out and two in on one slot is a rewrite; guessing pairs invents."""
        diff = diff_edges(
            [
                edge(SCENE, "requires", EdgeRef.entity("Sodium wash", "set_design")),
                edge(SCENE, "requires", EdgeRef.entity("Gel frame", "set_design")),
            ],
            [
                edge(
                    SCENE, "requires", EdgeRef.entity("Practical lamps", "set_design")
                ),
                edge(SCENE, "requires", EdgeRef.entity("Dimmer board", "set_design")),
            ],
        )
        assert diff.changed == []
        assert len(diff.removed) == 2 and len(diff.added) == 2

    def test_different_predicates_never_pair(self):
        diff = diff_edges(
            [edge(mara(), "carries", EdgeRef.entity("Torch", "prop"))],
            [edge(mara(), "uses", EdgeRef.entity("Torch", "prop"))],
        )
        assert diff.changed == []
        assert len(diff.removed) == 1 and len(diff.added) == 1


class TestTheDemoEdit:
    """Scene 14: the blue sedan becomes a bicycle."""

    def _accepted(self):
        return [
            edge(mara(), "travels_by", sedan(), assertion_id="a1", confidence=0.86),
            edge(
                SCENE,
                "requires",
                EdgeRef.entity("Engine idle", "sound"),
                assertion_id="a2",
                confidence=0.74,
            ),
            edge(sedan(), "appears_in", SCENE, assertion_id="a3", confidence=0.9),
            edge(SCENE, "establishes", sedan(), assertion_id="a4", confidence=0.9),
        ]

    def _proposed(self):
        return [
            edge(mara(), "travels_by", bicycle(), confidence=0.88),
            edge(
                SCENE,
                "requires",
                EdgeRef.entity("Chain rattle", "sound"),
                confidence=0.64,
            ),
            edge(bicycle(), "appears_in", SCENE, confidence=0.81),
        ]

    def test_the_diff_groups_slot_swaps_and_splits_the_rest(self):
        """Two changes, one addition, two removals.

        This decomposes the edit differently from the ripple-preview mockup,
        which renders `Mara travels_by Blue sedan -> Picture bicycle` as a
        separate removal and addition. Same information, better grouping: it is
        one production decision about how Mara gets around, and the engine
        pairs it because subject and predicate both survive. The mockup is a
        design sketch; the engine is the source of truth.
        """
        diff = diff_edges(self._accepted(), self._proposed())
        assert diff.summary() == {
            "added": 1,
            "removed": 2,
            "changed": 2,
            "unchanged": 0,
        }
        assert {edge.predicate for edge in diff.removed} == {
            "appears_in",
            "establishes",
        }
        assert diff.added[0].predicate == "appears_in"
        assert {before.predicate for before, _ in diff.changed} == {
            "travels_by",
            "requires",
        }

    def test_the_affected_entities_include_both_vehicles(self):
        diff = diff_edges(self._accepted(), self._proposed())
        affected = diff.affected_entities()
        assert {"blue sedan", "picture bicycle", "mara"} <= affected

    def test_operations_remove_before_they_add(self):
        operations = to_operations(diff_edges(self._accepted(), self._proposed()))
        kinds = [op["operation_type"] for op in operations]
        assert kinds.index("remove_assertion") < kinds.index("add_assertion")
        assert [op["sequence_index"] for op in operations] == list(
            range(len(operations))
        )

    def test_an_addition_carries_no_target_id(self):
        """A proposed edge has no row until acceptance resolves one."""
        operations = to_operations(diff_edges(self._accepted(), self._proposed()))
        for operation in operations:
            if operation["operation_type"] == "add_assertion":
                assert operation["target_id"] is None
                assert operation["before_json"] is None

    def test_a_removal_carries_the_row_it_removes(self):
        operations = to_operations(diff_edges(self._accepted(), self._proposed()))
        removals = [o for o in operations if o["operation_type"] == "remove_assertion"]
        assert removals and all(o["target_id"] for o in removals)


class TestDeterminism:
    def test_input_order_does_not_change_the_result(self):
        accepted = [
            edge(sedan(), "appears_in", SCENE),
            edge(mara(), "travels_by", sedan()),
            edge(SCENE, "establishes", sedan()),
        ]
        proposed = [edge(bicycle(), "appears_in", SCENE)]

        first = diff_edges(accepted, proposed)
        second = diff_edges(list(reversed(accepted)), proposed)
        assert first.summary() == second.summary()
        assert [e.identity for e in first.removed] == [
            e.identity for e in second.removed
        ]

    def test_running_twice_gives_the_same_operations(self):
        accepted = [edge(sedan(), "appears_in", SCENE, assertion_id="a1")]
        proposed = [edge(bicycle(), "appears_in", SCENE)]
        assert to_operations(diff_edges(accepted, proposed)) == to_operations(
            diff_edges(accepted, proposed)
        )


class TestPayloadNames:
    def test_an_entity_payload_carries_the_display_name(self):
        """Regression: payloads used to carry the normalized ref, so an entity
        created on acceptance was named "brass key" instead of "The Brass
        Key". Lookup normalizes either way; creation must not."""
        from ripple.graph.diff import GraphDiff

        edge = Edge(
            subject=EdgeRef.entity("The Brass Key", "prop"),
            predicate="appears_in",
            obj=EdgeRef.scene("00000000-0000-0000-0000-000000000001"),
            source_unit_id="00000000-0000-0000-0000-000000000002",
            display_subject="The Brass Key",
        )
        diff = GraphDiff(added=[edge])
        operations = to_operations(diff)
        assert operations[0]["after_json"]["subject_ref"] == "The Brass Key"
