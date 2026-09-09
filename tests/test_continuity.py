"""Continuity retrieval and the deterministic orphaned-reference finding.

Retrieval decides what the continuity agent sees, so a bug here is invisible:
the agent answers correctly about the wrong evidence. These tests check what is
retrieved, what is excluded, and that the finding the demo turns on holds with
no model involved at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from ripple.db.models import Assertion, Entity, Scene, Script, ScriptUnit
from ripple.db.naming import normalize
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.graph.continuity import (
    MAX_EVIDENCE_ITEMS,
    detect_cast_renames,
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
    forklift = Entity(
        script_id=script.id,
        entity_type="set_design",
        canonical_name="Dead forklift",
        normalized_name=normalize("Dead forklift"),
    )
    session.add_all([sedan, parka, forklift])
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

    # Established and used in the same unit, and nowhere else: an edit that
    # removes both belongs to one proposal, not a later scene.
    forklift_establishes = Assertion(
        script_id=script.id,
        subject_kind="scene",
        subject_scene_id=scenes["14"].id,
        predicate="establishes",
        object_kind="entity",
        object_entity_id=forklift.id,
        source_unit_id=units["14"].id,
        confidence=0.88,
    )
    forklift_appears_in = Assertion(
        script_id=script.id,
        subject_kind="entity",
        subject_entity_id=forklift.id,
        predicate="appears_in",
        object_kind="scene",
        object_scene_id=scenes["14"].id,
        source_unit_id=units["14"].id,
        confidence=0.87,
    )
    session.add_all([forklift_establishes, forklift_appears_in])
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
        "forklift": forklift,
        "forklift_establishes": forklift_establishes,
        "forklift_appears_in": forklift_appears_in,
    }


class TestCastRenames:
    """Renaming a character cue changes no stored fact, so the model judge
    holds every edge and reports nothing. A cue rename is structural, and this
    deterministic pass is what flags it at all."""

    def _play(self, session):
        """Three scenes, two speakers. NADIA speaks in every scene."""
        script = Script(title="Rename Test", import_status="accepted")
        session.add(script)
        session.flush()
        cues = []
        for index in range(3):
            scene = Scene(
                script_id=script.id,
                sequence_index=index,
                heading=f"INT. ROOM {index} - DAY",
                display_scene_number=str(index + 1),
            )
            session.add(scene)
            session.flush()
            cue = ScriptUnit(
                scene_id=scene.id,
                unit_type="character",
                sequence_index=0,
                current_text="NADIA",
                speaker_name="NADIA",
                parser_method="fountain",
            )
            line = ScriptUnit(
                scene_id=scene.id,
                unit_type="dialogue",
                sequence_index=1,
                current_text="A line.",
                speaker_name="NADIA",
                parser_method="fountain",
            )
            session.add_all([cue, line])
            session.flush()
            cues.append(cue)
        return script, cues

    def test_a_renamed_cue_is_flagged_with_the_lines_that_still_use_the_old_name(
        self, session
    ):
        script, cues = self._play(session)
        findings = detect_cast_renames(session, script.id, [(cues[0], "PRIYA")])
        assert len(findings) == 1
        finding = findings[0]
        assert finding.old_label == "NADIA"
        assert finding.new_label == "PRIYA"
        # The two other cues still say NADIA.
        assert len(finding.later_unit_ids) == 2
        assert "renamed PRIYA" in finding.message
        assert "2 other lines" in finding.message

    def test_a_sole_occurrence_rename_names_the_rebuild(self, session):
        script, cues = self._play(session)
        # Delete the other two cues so this is the only line naming NADIA.
        for cue in cues[1:]:
            session.delete(cue)
        session.flush()
        finding = detect_cast_renames(session, script.id, [(cues[0], "PRIYA")])[0]
        assert finding.later_unit_ids == []
        assert "the only line" in finding.message
        assert "Rebuild" in finding.message

    def test_an_unchanged_cue_is_not_a_rename(self, session):
        script, cues = self._play(session)
        assert detect_cast_renames(session, script.id, [(cues[0], "NADIA")]) == []
        # A case or extension change is the same speaker, not a rename.
        assert detect_cast_renames(session, script.id, [(cues[0], "NADIA (V.O.)")]) == []

    def test_a_dialogue_edit_is_never_a_rename(self, session):
        """Only a character cue renames a speaker; editing a line of dialogue
        that happens to name a character does not."""
        script, cues = self._play(session)
        line = session.scalars(
            select(ScriptUnit).where(
                ScriptUnit.scene_id == cues[0].scene_id,
                ScriptUnit.unit_type == "dialogue",
            )
        ).first()
        assert detect_cast_renames(session, script.id, [(line, "PRIYA speaks.")]) == []


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

    def test_the_message_states_what_the_script_now_says(self, session, world):
        """A finding is read in a list and acted on, so it names the thing and
        the new state in one line rather than arguing the contradiction."""
        finding = detect_orphaned_references(
            session,
            world["script"].id,
            [(world["sedan"].id, "Blue sedan", world["establishes"].id)],
        )[0]
        assert finding.message == (
            "Blue sedan is used in scenes 22, 31, 44 but no longer introduced."
        )
        # None of the argued-contradiction phrasing the card used to carry.
        for phrase in ("contradicts", "establishing reference", "later unit(s)"):
            assert phrase not in finding.message
        assert len(finding.message.split()) <= 14

    def test_one_later_scene_reads_in_the_singular(self, session, world):
        finding = detect_orphaned_references(
            session,
            world["script"].id,
            [(world["sedan"].id, "Blue sedan", world["establishes"].id)],
        )[0]
        object.__setattr__(finding, "later_scene_numbers", ["22"])
        assert finding.message == (
            "Blue sedan is used in scene 22 but no longer introduced."
        )

    def test_an_entry_without_an_assertion_id_is_dropped(self, session, world):
        """The contract requires the removed edge's id; None cannot be checked.

        Without the id, the "established elsewhere" exclusion has nothing to
        exclude, so the check would answer from data the caller did not
        state. The entry is dropped rather than half-checked.
        """
        findings = detect_orphaned_references(
            session, world["script"].id, [(world["parka"].id, "Grey parka", None)]
        )
        assert findings == []

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

    def test_a_use_removed_by_the_same_edit_does_not_count_as_surviving(
        self, session, world
    ):
        """An edit removing both an entity's only establishes and its only use
        must not warn about itself: that use is not later, it's the same edit.

        Caught live: a ripple preview removing "Dead forklift" from the one
        unit that both established and used it flagged "Later units still
        reference Dead forklift", citing the very unit being edited. The
        assertion was still `active` in the database, since the proposal isn't
        applied yet, so a check that only excluded removed `establishes`
        edges, not every edge this same diff removes, always found it.
        """
        findings = detect_orphaned_references(
            session,
            world["script"].id,
            [
                (
                    world["forklift"].id,
                    "Dead forklift",
                    world["forklift_establishes"].id,
                )
            ],
            removed_assertion_ids={
                str(world["forklift_establishes"].id),
                str(world["forklift_appears_in"].id),
            },
        )
        assert findings == []

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


class TestOpenFindingsScoping:
    def test_another_scripts_findings_stay_out_of_the_packet(self, session, world):
        """Regression: the open-findings query had no script scoping, so
        another script's warnings entered this script's evidence packet."""
        from ripple.db.models import ChangeSet, ContinuityFinding, Script

        def finding_for(script, message):
            change_set = ChangeSet(
                script_id=script.id,
                kind="edit",
                status="pending",
                base_script_version=1,
            )
            session.add(change_set)
            session.flush()
            session.add(
                ContinuityFinding(
                    change_set_id=change_set.id,
                    finding_type="orphaned_reference",
                    severity="high",
                    message=message,
                    status="open",
                )
            )
            session.flush()

        other = Script(title="The Understudy", import_status="accepted")
        session.add(other)
        session.flush()
        finding_for(world["script"], "sedan finding")
        finding_for(other, "gown finding")

        packet = retrieve(session, world["script"].id, [world["sedan"].id], 1)
        assert "sedan finding" in packet.open_findings
        assert "gown finding" not in packet.open_findings
