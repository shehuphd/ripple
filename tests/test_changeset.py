"""Atomic acceptance, rejection, and undo.

ERD section 13 lists what the schema is incomplete without proving. These are
those tests. The theme is that accepted state either moves completely or not at
all, and that the audit record says which of the two happened.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from ripple.db.models import Assertion, ChangeSet, Entity, Scene, Script, ScriptUnit
from ripple.db.naming import normalize
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.services.changeset import (
    InvalidOperation,
    StaleProposal,
    accept,
    create_proposal,
    reject,
    undo_latest,
)


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


def build_world(session):
    """A one-scene script with a sedan already in the graph."""
    script = Script(title="Night Freight", import_status="accepted", current_version=1)
    session.add(script)
    session.flush()
    scene = Scene(
        script_id=script.id,
        sequence_index=13,
        heading="EXT. LOADING DOCK - NIGHT",
        display_scene_number="14",
    )
    session.add(scene)
    session.flush()
    unit = ScriptUnit(
        scene_id=scene.id,
        unit_type="action",
        sequence_index=0,
        current_text="The blue sedan idles by the gate.",
        parser_method="fountain",
    )
    session.add(unit)
    session.flush()
    sedan = Entity(
        script_id=script.id,
        entity_type="transportation",
        canonical_name="Blue sedan",
        normalized_name=normalize("Blue sedan"),
    )
    session.add(sedan)
    session.flush()
    existing = Assertion(
        script_id=script.id,
        subject_kind="entity",
        subject_entity_id=sedan.id,
        predicate="appears_in",
        object_kind="scene",
        object_scene_id=scene.id,
        source_unit_id=unit.id,
        confidence=0.9,
    )
    session.add(existing)
    session.flush()
    return {
        "script": script,
        "scene": scene,
        "unit": unit,
        "sedan": sedan,
        "existing": existing,
    }


@pytest.fixture
def factory():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    return session_factory(engine)


@pytest.fixture
def world(session):
    return build_world(session)


def add_op(world, name="Picture bicycle", kind="transportation", index=0):
    return {
        "sequence_index": index,
        "operation_type": "add_assertion",
        "target_type": "assertion",
        "target_id": None,
        "before_json": None,
        "after_json": {
            "subject_kind": "entity",
            "subject_ref": name,
            "subject_entity_type": kind,
            "predicate": "appears_in",
            "object_kind": "scene",
            "object_ref": str(world["scene"].id),
            "object_entity_type": None,
            "source_unit_id": str(world["unit"].id),
            "confidence": 0.81,
        },
    }


def remove_op(world, index=0):
    return {
        "sequence_index": index,
        "operation_type": "remove_assertion",
        "target_type": "assertion",
        "target_id": str(world["existing"].id),
        "before_json": {"predicate": "appears_in"},
        "after_json": None,
    }


class TestNothingHappensUntilAccept:
    def test_a_proposal_changes_no_accepted_state(self, session, world):
        create_proposal(session, world["unit"].id, "A bicycle.", [remove_op(world)])
        assert world["existing"].active is True
        assert world["unit"].current_text.startswith("The blue sedan")
        assert world["script"].current_version == 1

    def test_rejecting_changes_nothing(self, session, world):
        proposal = create_proposal(
            session, world["unit"].id, "A bicycle.", [remove_op(world)]
        )
        reject(session, proposal.id, "Not this draft.")
        assert proposal.status == "rejected"
        assert world["existing"].active is True
        assert world["script"].current_version == 1

    def test_a_rejected_proposal_cannot_then_be_accepted(self, session, world):
        proposal = create_proposal(session, world["unit"].id, "x", [remove_op(world)])
        reject(session, proposal.id)
        with pytest.raises(InvalidOperation):
            accept(session, proposal.id)


class TestStaleness:
    def test_a_moved_script_version_is_refused(self, session, world):
        proposal = create_proposal(session, world["unit"].id, "x", [remove_op(world)])
        world["script"].current_version = 2
        session.flush()
        with pytest.raises(StaleProposal):
            accept(session, proposal.id)
        assert proposal.status == "stale"

    def test_a_moved_unit_version_is_refused(self, session, world):
        proposal = create_proposal(session, world["unit"].id, "x", [remove_op(world)])
        world["unit"].current_version = 5
        session.flush()
        with pytest.raises(StaleProposal):
            accept(session, proposal.id)
        assert proposal.status == "stale"

    def test_a_stale_proposal_applied_nothing(self, session, world):
        proposal = create_proposal(
            session, world["unit"].id, "x", [remove_op(world), add_op(world, index=1)]
        )
        world["script"].current_version = 9
        session.flush()
        with pytest.raises(StaleProposal):
            accept(session, proposal.id)
        assert world["existing"].active is True
        assert session.scalar(select(func.count()).select_from(Entity)) == 1


class TestAtomicity:
    def test_an_invalid_operation_rolls_the_whole_set_back(self, factory):
        """`requires` never takes a cast object, so the second op is refused.

        Run through session_scope so the rollback is the real one the
        application performs, rather than an assertion about intent.
        """
        from ripple.db.session import session_scope

        with session_scope(factory) as setup:
            world = build_world(setup)
            ids = {k: v.id for k, v in world.items()}
            bad = add_op(world)
            bad["after_json"].update(
                {
                    "predicate": "requires",
                    "subject_kind": "scene",
                    "subject_ref": str(world["scene"].id),
                    "object_kind": "entity",
                    "object_ref": "Mara",
                    "object_entity_type": "cast",
                }
            )
            proposal = create_proposal(
                setup, world["unit"].id, "x", [remove_op(world), bad]
            )
            proposal_id = proposal.id

        with pytest.raises(InvalidOperation), session_scope(factory) as attempt:
            accept(attempt, proposal_id)

        with session_scope(factory) as after:
            assert after.get(Assertion, ids["existing"]).active is True
            assert after.get(Script, ids["script"]).current_version == 1
            assert after.get(ScriptUnit, ids["unit"]).current_text.startswith(
                "The blue sedan"
            )

    def test_a_payload_missing_a_field_is_refused(self, session, world):
        broken = add_op(world)
        del broken["after_json"]["predicate"]
        proposal = create_proposal(session, world["unit"].id, "x", [broken])
        with pytest.raises(InvalidOperation, match="missing"):
            accept(session, proposal.id)


class TestAcceptance:
    def test_acceptance_applies_text_and_graph_together(self, session, world):
        proposal = create_proposal(
            session,
            world["unit"].id,
            "A bicycle leans against the gate.",
            [remove_op(world), add_op(world, index=1)],
        )
        result = accept(session, proposal.id)

        assert proposal.status == "accepted"
        # Three: the removal, the addition, and the set_unit_text that carries
        # the wording change so undo can invert it.
        assert result.operations_applied == 3
        assert result.entities_created == 1
        assert session.get(Assertion, world["existing"].id).active is False
        assert world["unit"].current_text == "A bicycle leans against the gate."
        assert world["unit"].current_version == 2
        assert world["script"].current_version == 2

    def test_a_removal_deactivates_rather_than_deletes(self, session, world):
        """History has to survive, and undo has to have something to restore."""
        proposal = create_proposal(session, world["unit"].id, "x", [remove_op(world)])
        accept(session, proposal.id)
        assert session.get(Assertion, world["existing"].id) is not None
        assert session.get(Assertion, world["existing"].id).active is False

    def test_a_removal_does_not_delete_the_entity(self, session, world):
        """ERD section 3: an entity outlives any one assertion about it."""
        proposal = create_proposal(session, world["unit"].id, "x", [remove_op(world)])
        accept(session, proposal.id)
        assert session.get(Entity, world["sedan"].id) is not None

    def test_an_added_entity_reuses_an_existing_canonical_match(self, session, world):
        """THE BLUE SEDAN and Blue sedan are one entity, Schema Lock v1 §5."""
        proposal = create_proposal(
            session,
            world["unit"].id,
            "x",
            [add_op(world, name="THE BLUE SEDAN", kind="transportation")],
        )
        accept(session, proposal.id)
        assert session.scalar(select(func.count()).select_from(Entity)) == 1

    def test_adding_an_edge_that_already_exists_is_satisfied_not_duplicated(
        self, session, world
    ):
        """The operation asks for the edge to exist, and it already does."""
        proposal = create_proposal(
            session,
            world["unit"].id,
            "x",
            [add_op(world, name="Blue sedan", kind="transportation")],
        )
        accept(session, proposal.id)
        assert session.scalar(select(func.count()).select_from(Assertion)) == 1


class TestUndo:
    def _accept_an_edit(self, session, world):
        proposal = create_proposal(
            session,
            world["unit"].id,
            "A bicycle leans against the gate.",
            [remove_op(world), add_op(world, index=1)],
        )
        accept(session, proposal.id)
        return proposal

    def test_undo_restores_the_graph_and_the_text(self, session, world):
        self._accept_an_edit(session, world)
        undo_latest(session, world["unit"].id)

        assert session.get(Assertion, world["existing"].id).active is True
        assert world["unit"].current_text == "The blue sedan idles by the gate."

    def test_the_original_becomes_reverted_and_not_rejected(self, session, world):
        """Rejecting is declining to apply; reverting is applying then undoing."""
        original = self._accept_an_edit(session, world)
        undo_latest(session, world["unit"].id)
        assert original.status == "reverted"

    def test_undo_records_an_inverse_pointing_at_the_original(self, session, world):
        original = self._accept_an_edit(session, world)
        undo_latest(session, world["unit"].id)
        inverse = session.scalar(
            select(ChangeSet).where(ChangeSet.reverts_change_set_id == original.id)
        )
        assert inverse is not None
        assert inverse.kind == "undo"
        assert inverse.status == "accepted"

    def test_both_the_change_and_its_inverse_stay_in_the_history(self, session, world):
        self._accept_an_edit(session, world)
        undo_latest(session, world["unit"].id)
        statuses = sorted(session.scalars(select(ChangeSet.status)))
        assert statuses == ["accepted", "reverted"]

    def test_undo_with_nothing_accepted_is_refused(self, session, world):
        with pytest.raises(InvalidOperation, match="No accepted change"):
            undo_latest(session, world["unit"].id)

    def test_undo_takes_the_latest_change_only(self, session, world):
        self._accept_an_edit(session, world)
        second = create_proposal(
            session,
            world["unit"].id,
            "A third version.",
            [add_op(world, name="Chain rattle", kind="sound")],
        )
        accept(session, second.id)

        undo_latest(session, world["unit"].id)
        assert second.status == "reverted"
        assert world["unit"].current_text == "A bicycle leans against the gate."
