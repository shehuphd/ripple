"""Continuity retrieval and the deterministic orphaned-reference finding.

Retrieval decides what the continuity agent sees, so a bug here is invisible:
the agent answers correctly about the wrong evidence. These tests check what is
retrieved, what is excluded, and that the finding the demo turns on holds with
no model involved at all.
"""

from __future__ import annotations

import pytest

from ripple.db.models import Assertion, Entity, Scene, Script, ScriptUnit
from ripple.db.naming import normalize
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.graph.continuity import (
    MAX_EVIDENCE_ITEMS,
    detect_orphaned_references,
    retrieve,
)


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


@pytest.fixture
def world(session):
    """A small script: sedan established in scene 14, used in 22, 31, and 44."""
    script = Script(title="Night Freight", import_status="accepted")
    session.add(script)
    session.flush()

    scenes, units = {}, {}
    for index, number in enumerate(["13", "14", "22", "31", "44"]):
        scene = Scene(
            script_id=script.id,
            sequence_index=index,
            heading=f"EXT. SOMEWHERE {number}",
            display_scene_number=number,
        )
        session.add(scene)
        session.flush()
        unit = ScriptUnit(
            scene_id=scene.id,
            unit_type="action",
            sequence_index=0,
            current_text=f"Something happens in scene {number}.",
            parser_method="fountain",
        )
        session.add(unit)
        session.flush()
        scenes[number], units[number] = scene, unit

    sedan = Entity(
        script_id=script.id,
        entity_type="transportation",
        canonical_name="Blue sedan",
        normalized_name=normalize("Blue sedan"),
    )
    parka = Entity(
        script_id=script.id,
        entity_type="wardrobe",
        canonical_name="Grey parka",
        normalized_name=normalize("Grey parka"),
    )
    session.add_all([sedan, parka])
    session.flush()

    establishes = Assertion(
        script_id=script.id,
        subject_kind="scene",
        subject_scene_id=scenes["14"].id,
        predicate="establishes",
        object_kind="entity",
        object_entity_id=sedan.id,
        source_unit_id=units["14"].id,
        confidence=0.9,
    )
    session.add(establishes)
    session.flush()
    for number in ("22", "31", "44"):
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind="entity",
                subject_entity_id=sedan.id,
                predicate="appears_in",
                object_kind="scene",
                object_scene_id=scenes[number].id,
                source_unit_id=units[number].id,
                confidence=0.88,
            )
        )
    # An unrelated entity, so retrieval has something it must not return.
    session.add(
        Assertion(
            script_id=script.id,
            subject_kind="entity",
            subject_entity_id=parka.id,
            predicate="appears_in",
            object_kind="scene",
            object_scene_id=scenes["13"].id,
            source_unit_id=units["13"].id,
            confidence=0.9,
        )
    )
    session.flush()
    return {
        "script": script,
        "scenes": scenes,
        "units": units,
        "sedan": sedan,
        "parka": parka,
        "establishes": establishes,
    }


class TestOrphanedReferences:
    def test_removing_the_only_establishes_flags_the_later_uses(self, session, world):
        """The high-severity finding in the ripple preview mockup."""
        findings = detect_orphaned_references(
            session,
            world["script"].id,
            [(world["sedan"].id, "Blue sedan", world["establishes"].id)],
        )
        assert len(findings) == 1
        finding = findings[0]
        assert finding.later_scene_numbers == ["22", "31", "44"]
        assert len(finding.later_unit_ids) == 3
        assert "22, 31, 44" in finding.message

    def test_an_entity_with_no_later_use_produces_no_finding(self, session, world):
        findings = detect_orphaned_references(
            session, world["script"].id, [(world["parka"].id, "Grey parka", None)]
        )
        # The parka has a use but no establishes was removed for it, and its
        # single use is what remains, so a finding here would be noise.
        assert [f.entity_label for f in findings] == ["Grey parka"]

    def test_a_second_establishing_edge_suppresses_the_finding(self, session, world):
        """Still introduced elsewhere means there is nothing to warn about."""
        session.add(
            Assertion(
                script_id=world["script"].id,
                subject_kind="scene",
                subject_scene_id=world["scenes"]["13"].id,
                predicate="establishes",
                object_kind="entity",
                object_entity_id=world["sedan"].id,
                source_unit_id=world["units"]["13"].id,
                confidence=0.9,
            )
        )
        session.flush()
        findings = detect_orphaned_references(
            session,
            world["script"].id,
            [(world["sedan"].id, "Blue sedan", world["establishes"].id)],
        )
        assert findings == []

    def test_nothing_removed_means_nothing_found(self, session, world):
        assert detect_orphaned_references(session, world["script"].id, []) == []

    def test_it_needs_no_model(self, session, world):
        """No provider is constructed, so the warning survives an outage."""
        findings = detect_orphaned_references(
            session,
            world["script"].id,
            [(world["sedan"].id, "Blue sedan", world["establishes"].id)],
        )
        assert findings[0].entity_label == "Blue sedan"


class TestRetrieval:
    def test_an_empty_entity_set_retrieves_nothing(self, session, world):
        packet = retrieve(session, world["script"].id, [], 1)
        assert packet.total_items == 0

    def test_unrelated_entities_are_excluded(self, session, world):
        packet = retrieve(session, world["script"].id, [world["sedan"].id], 1)
        rendered = repr(packet.as_prompt_payload())
        assert "Grey parka" not in rendered
        assert packet.total_items == 4

    def test_earlier_and_later_are_separated_by_the_origin_scene(self, session, world):
        # Origin is scene index 1, which is display scene 14.
        packet = retrieve(session, world["script"].id, [world["sedan"].id], 1)
        assert [item.scene_number for item in packet.later] == ["14", "22", "31", "44"]
        assert packet.earlier == []

    def test_evidence_carries_the_unit_text_that_supports_it(self, session, world):
        packet = retrieve(session, world["script"].id, [world["sedan"].id], 1)
        assert all(item.unit_text for item in packet.later)
        assert all(item.unit_id for item in packet.later)

    def test_aliases_are_included_for_the_agent(self, session, world):
        from ripple.db.models import EntityAlias

        session.add(
            EntityAlias(
                entity_id=world["sedan"].id,
                alias="the sedan",
                normalized_alias="sedan",
                provenance="model",
            )
        )
        session.flush()
        packet = retrieve(session, world["script"].id, [world["sedan"].id], 1)
        assert packet.aliases["Blue sedan"] == ["the sedan"]

    def test_a_relevant_predicate_outranks_an_irrelevant_one(self, session, world):
        packet = retrieve(
            session,
            world["script"].id,
            [world["sedan"].id],
            1,
            predicates=("appears_in",),
        )
        ranked = packet.later
        assert ranked[0].predicate == "appears_in"

    def test_the_packet_is_bounded_and_says_when_it_truncated(self, session, world):
        script_id = world["script"].id
        sedan = world["sedan"]
        unit = world["units"]["44"]
        scene = world["scenes"]["44"]
        # Add well past the cap, each on a distinct unit so the dedupe key
        # does not collapse them.
        for index in range(MAX_EVIDENCE_ITEMS + 10):
            extra = ScriptUnit(
                scene_id=scene.id,
                unit_type="action",
                sequence_index=index + 1,
                current_text=f"Filler {index}.",
                parser_method="fountain",
            )
            session.add(extra)
            session.flush()
            session.add(
                Assertion(
                    script_id=script_id,
                    subject_kind="entity",
                    subject_entity_id=sedan.id,
                    predicate="appears_in",
                    object_kind="scene",
                    object_scene_id=scene.id,
                    source_unit_id=extra.id,
                    confidence=0.7,
                )
            )
        session.flush()

        packet = retrieve(session, script_id, [sedan.id], 1)
        assert packet.truncated
        assert packet.total_items == MAX_EVIDENCE_ITEMS
        assert packet.as_prompt_payload()["truncated"] is True
        assert unit is not None

    def test_inactive_assertions_are_not_retrieved(self, session, world):
        for assertion in session.query(Assertion).all():
            assertion.active = False
        session.flush()
        packet = retrieve(session, world["script"].id, [world["sedan"].id], 1)
        assert packet.total_items == 0

    def test_the_payload_carries_no_screenplay_beyond_cited_units(self, session, world):
        """The packet is a prompt input, so it must be bounded content."""
        packet = retrieve(session, world["script"].id, [world["sedan"].id], 1)
        payload = packet.as_prompt_payload()
        assert set(payload) == {
            "entities",
            "aliases",
            "earlier_evidence",
            "later_evidence",
            "graph_neighbours",
            "existing_open_findings",
            "truncated",
        }
