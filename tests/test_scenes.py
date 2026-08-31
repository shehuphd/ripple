"""Structural scene edits: insertion, omission, restoration.

Service-level, against seeded ground truth: omission has to move a graph, so
these tests run on a script whose graph exists without model calls.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ripple.adapters import import_screenplay
from ripple.adapters.base import ImportRejected
from ripple.db.models import (
    Assertion,
    ChangeSet,
    ContinuityFinding,
    RippleReport,
    Scene,
    SceneExtraction,
    Script,
    ScriptUnit,
)
from ripple.db.repository import persist_import
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.extraction.service import start_run
from ripple.graph.fixtures import ground_truths, seed_graph
from ripple.services.changeset import InvalidOperation
from ripple.services.scenes import insert_scene, omit_scene, restore_scene

DEMO_SCRIPTS = Path(__file__).resolve().parent.parent / "demo-scripts"


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


@pytest.fixture
def script(session) -> Script:
    path = DEMO_SCRIPTS / "01-night-freight/night-freight.fountain"
    result = import_screenplay(path.read_bytes(), path.name)
    assert result.accepted
    return persist_import(session, result)


@pytest.fixture
def seeded(session, script) -> Script:
    truth = next(t for t in ground_truths(DEMO_SCRIPTS) if "FREIGHT" in t.title)
    seed_graph(session, script, truth)
    return script


def scene_by_number(session, script, number: str) -> Scene:
    return session.scalar(
        select(Scene).where(
            Scene.script_id == script.id, Scene.display_scene_number == number
        )
    )


class TestInsertion:
    def test_a_scene_inserts_after_its_anchor_with_a_lettered_number(
        self, session, script
    ):
        anchor = scene_by_number(session, script, "3")
        before_version = script.current_version
        inserted = insert_scene(
            session,
            script.id,
            heading="INT. DISPATCH OFFICE - LATER",
            body="The monitors flicker.\n\nMARA\nStill here.",
            after_scene_id=anchor.id,
        )
        scene = session.get(Scene, uuid.UUID(inserted.scene_id))
        assert scene.display_scene_number == "3A"
        assert scene.sequence_index == anchor.sequence_index + 1
        assert scene.int_ext == "INT"
        assert script.current_version == before_version + 1

        # Order stays contiguous and unique.
        indexes = list(
            session.scalars(
                select(Scene.sequence_index)
                .where(Scene.script_id == script.id)
                .order_by(Scene.sequence_index)
            )
        )
        assert indexes == list(range(len(indexes)))

    def test_the_body_parses_into_typed_units(self, session, script):
        anchor = scene_by_number(session, script, "3")
        inserted = insert_scene(
            session,
            script.id,
            heading="INT. DISPATCH OFFICE - LATER",
            body="The monitors flicker.\n\nMARA\n(quiet)\nStill here.",
            after_scene_id=anchor.id,
        )
        units = list(
            session.scalars(
                select(ScriptUnit)
                .where(ScriptUnit.scene_id == uuid.UUID(inserted.scene_id))
                .order_by(ScriptUnit.sequence_index)
            )
        )
        types = [unit.unit_type for unit in units]
        assert types == [
            "scene_heading",
            "action",
            "character",
            "parenthetical",
            "dialogue",
        ]
        assert units[2].speaker_name == "MARA"

    def test_inserting_at_the_top_prefixes_a_letter(self, session, script):
        inserted = insert_scene(
            session, script.id, heading="EXT. GATE - NIGHT", body="Rain."
        )
        scene = session.get(Scene, uuid.UUID(inserted.scene_id))
        assert scene.sequence_index == 0
        assert scene.display_scene_number == "A1"

    def test_a_second_heading_in_the_body_is_refused(self, session, script):
        anchor = scene_by_number(session, script, "3")
        with pytest.raises(ImportRejected) as caught:
            insert_scene(
                session,
                script.id,
                heading="INT. DISPATCH OFFICE - LATER",
                body="Text.\n\nINT. ANOTHER PLACE - DAY\n\nMore.",
                after_scene_id=anchor.id,
            )
        assert caught.value.code == "scene_multiple_headings"

    def test_a_lettered_slot_already_taken_advances(self, session, script):
        anchor = scene_by_number(session, script, "3")
        first = insert_scene(
            session, script.id, "INT. A - DAY", "One.", after_scene_id=anchor.id
        )
        second = insert_scene(
            session, script.id, "INT. B - DAY", "Two.", after_scene_id=anchor.id
        )
        assert first.display_number == "3A"
        assert second.display_number == "3B"

    def test_extraction_scoped_to_the_new_scene_bills_nothing_else(
        self, session, seeded
    ):
        anchor = scene_by_number(session, seeded, "3")
        inserted = insert_scene(
            session, seeded.id, "INT. C - DAY", "Dust.", after_scene_id=anchor.id
        )
        run = start_run(
            session, seeded.id, "some-model", scene_ids=[uuid.UUID(inserted.scene_id)]
        )
        jobs = session.scalar(
            select(func.count())
            .select_from(SceneExtraction)
            .where(SceneExtraction.extraction_run_id == run.id)
        )
        assert run.total_scenes == 1
        assert jobs == 1


class TestOmission:
    def test_omission_deactivates_the_scenes_facts_atomically(self, session, seeded):
        scene = scene_by_number(session, seeded, "3")
        active_before = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(Assertion.script_id == seeded.id, Assertion.active.is_(True))
        )
        result = omit_scene(session, scene.id)
        assert session.get(Scene, scene.id).omitted is True
        assert result.assertions_deactivated > 0
        active_after = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(Assertion.script_id == seeded.id, Assertion.active.is_(True))
        )
        assert active_after == active_before - result.assertions_deactivated

        change_set = session.get(ChangeSet, uuid.UUID(result.change_set_id))
        assert change_set.kind == "omit_scene"
        assert change_set.status == "accepted"
        report = session.scalar(
            select(RippleReport).where(RippleReport.change_set_id == change_set.id)
        )
        assert "omitted" in report.summary

    def test_orphaned_dependants_become_open_findings(self, session, seeded):
        # Scene 3 introduces entities the ground truth says later scenes use.
        scene = scene_by_number(session, seeded, "3")
        result = omit_scene(session, scene.id)
        findings = list(
            session.scalars(
                select(ContinuityFinding).where(
                    ContinuityFinding.change_set_id == uuid.UUID(result.change_set_id),
                    ContinuityFinding.status == "open",
                )
            )
        )
        assert findings, "omitting an establishing scene must warn"
        assert result.severity == "high"
        assert all(f.finding_type == "orphaned_reference" for f in findings)
        assert all(f.evidence for f in findings)

    def test_an_omitted_scene_cannot_be_omitted_again(self, session, seeded):
        scene = scene_by_number(session, seeded, "3")
        omit_scene(session, scene.id)
        with pytest.raises(InvalidOperation):
            omit_scene(session, scene.id)

    def test_extraction_skips_omitted_scenes(self, session, seeded):
        scene = scene_by_number(session, seeded, "3")
        omit_scene(session, scene.id)
        run = start_run(session, seeded.id, "some-model")
        total_scenes = session.scalar(
            select(func.count())
            .select_from(Scene)
            .where(Scene.script_id == seeded.id)
        )
        assert run.total_scenes == total_scenes - 1


class TestRestoration:
    def test_restore_reactivates_and_resolves(self, session, seeded):
        scene = scene_by_number(session, seeded, "3")
        active_before = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(Assertion.script_id == seeded.id, Assertion.active.is_(True))
        )
        omission = omit_scene(session, scene.id)
        restore_scene(session, scene.id)

        assert session.get(Scene, scene.id).omitted is False
        active_after = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(Assertion.script_id == seeded.id, Assertion.active.is_(True))
        )
        assert active_after == active_before

        reverted = session.get(ChangeSet, uuid.UUID(omission.change_set_id))
        assert reverted.status == "reverted"
        open_findings = session.scalar(
            select(func.count())
            .select_from(ContinuityFinding)
            .where(
                ContinuityFinding.change_set_id == uuid.UUID(omission.change_set_id),
                ContinuityFinding.status == "open",
            )
        )
        assert open_findings == 0

    def test_restoring_a_live_scene_is_refused(self, session, seeded):
        scene = scene_by_number(session, seeded, "3")
        with pytest.raises(InvalidOperation):
            restore_scene(session, scene.id)
