"""Linking a new draft and carrying the graph forward.

Runs on NIGHT FREIGHT with its seeded ground truth as draft 1, and a
one-line revision of the same file as draft 2: the classic small rewrite.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ripple.adapters import import_screenplay
from ripple.db.models import (
    Assertion,
    Entity,
    EntityAttribute,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.repository import persist_import
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.graph.fixtures import ground_truths, seed_graph
from ripple.services.drafts import LinkRefused, draft_candidates, link_draft

DEMO_SCRIPTS = Path(__file__).resolve().parent.parent / "demo-scripts"
FOUNTAIN = (DEMO_SCRIPTS / "01-night-freight/night-freight.fountain").read_bytes()


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


@pytest.fixture
def draft_one(session) -> Script:
    result = import_screenplay(FOUNTAIN, "night-freight.fountain")
    script = persist_import(session, result)
    truth = next(t for t in ground_truths(DEMO_SCRIPTS) if "FREIGHT" in t.title)
    seed_graph(session, script, truth)
    return script


def import_revision(session, data: bytes) -> Script:
    result = import_screenplay(data, "night-freight-d2.fountain")
    assert result.accepted, result.rejection_code
    return persist_import(session, result)


REVISED = FOUNTAIN.replace(
    b"Six monitors, four of them dead", b"Twelve monitors, four of them dead"
)


class TestLinking:
    def test_a_one_line_revision_carries_everything_but_one_scene(
        self, session, draft_one
    ):
        draft_two = import_revision(session, REVISED)
        link = link_draft(session, draft_two.id, draft_one.id)

        assert link.draft_number == 2
        assert link.modified == 1
        assert link.inserted == 0
        assert link.deleted == 0
        total_scenes = session.scalar(
            select(func.count())
            .select_from(Scene)
            .where(Scene.script_id == draft_one.id)
        )
        assert link.unchanged == total_scenes - 1
        assert len(link.to_extract) == 1
        assert link.graph_status == "partially_ready"
        assert draft_two.predecessor_script_id == draft_one.id

        # The one scene left to extract is the revised one.
        pending = session.get(Scene, uuid.UUID(link.to_extract[0]))
        assert pending.display_scene_number == "3"
        assert pending.lineage_kind == "modified"

    def test_carried_facts_point_at_the_new_drafts_rows(self, session, draft_one):
        draft_two = import_revision(session, REVISED)
        link = link_draft(session, draft_two.id, draft_one.id)
        assert link.assertions_carried > 300
        assert link.entities_carried > 50
        assert link.attributes_carried > 20

        new_unit_ids = set(
            session.scalars(
                select(ScriptUnit.id)
                .join(Scene)
                .where(Scene.script_id == draft_two.id)
            )
        )
        carried = list(
            session.scalars(
                select(Assertion).where(Assertion.script_id == draft_two.id)
            )
        )
        assert carried
        assert all(row.source_unit_id in new_unit_ids for row in carried)
        assert all(row.active for row in carried)

        # Entity lineage: the carried Mara links to draft 1's Mara.
        mara_two = session.scalar(
            select(Entity).where(
                Entity.script_id == draft_two.id,
                Entity.canonical_name == "Mara Okonjo",
            )
        )
        assert mara_two is not None
        assert (
            session.get(Entity, mara_two.predecessor_entity_id).script_id
            == draft_one.id
        )

    def test_the_modified_scene_carries_no_facts_but_links_its_lines(
        self, session, draft_one
    ):
        draft_two = import_revision(session, REVISED)
        link = link_draft(session, draft_two.id, draft_one.id)
        modified = session.get(Scene, uuid.UUID(link.to_extract[0]))

        unit_ids = list(
            session.scalars(
                select(ScriptUnit.id).where(ScriptUnit.scene_id == modified.id)
            )
        )
        facts = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(Assertion.source_unit_id.in_(unit_ids))
        )
        assert facts == 0

        units = list(
            session.scalars(
                select(ScriptUnit)
                .where(ScriptUnit.scene_id == modified.id)
                .order_by(ScriptUnit.sequence_index)
            )
        )
        edited = [u for u in units if "Twelve monitors" in u.current_text]
        untouched = [
            u
            for u in units
            if "Twelve monitors" not in u.current_text
            and u.unit_type != "scene_heading"
        ]
        assert edited and edited[0].predecessor_unit_id is None
        assert untouched
        assert all(u.predecessor_unit_id is not None for u in untouched)

    def test_attribute_evidence_survives_the_carry(self, session, draft_one):
        draft_two = import_revision(session, REVISED)
        link_draft(session, draft_two.id, draft_one.id)
        # Cited in scene 1, which carried; the parka's own attributes are
        # cited in scene 3, the modified scene, so they deliberately do not
        # carry and re-extraction of that scene is what restores them.
        stacked = session.scalar(
            select(EntityAttribute)
            .join(Entity, EntityAttribute.entity_id == Entity.id)
            .where(
                Entity.script_id == draft_two.id,
                Entity.canonical_name == "Stacked containers",
                EntityAttribute.key == "stack_height",
            )
        )
        assert stacked is not None
        unit = session.get(ScriptUnit, stacked.source_unit_id)
        assert stacked.value.casefold() in unit.current_text.casefold()
        parka_attrs = session.scalar(
            select(func.count())
            .select_from(EntityAttribute)
            .join(Entity, EntityAttribute.entity_id == Entity.id)
            .where(
                Entity.script_id == draft_two.id,
                Entity.canonical_name == "Grey parka",
            )
        )
        assert parka_attrs == 0


class TestRefusals:
    def test_a_second_link_is_refused(self, session, draft_one):
        draft_two = import_revision(session, REVISED)
        link_draft(session, draft_two.id, draft_one.id)
        with pytest.raises(LinkRefused, match="already linked"):
            link_draft(session, draft_two.id, draft_one.id)

    def test_a_script_with_a_graph_cannot_become_a_draft(self, session, draft_one):
        other = import_revision(session, REVISED)
        truth = next(t for t in ground_truths(DEMO_SCRIPTS) if "FREIGHT" in t.title)
        seed_graph(session, other, truth)
        with pytest.raises(LinkRefused, match="already has a graph"):
            link_draft(session, other.id, draft_one.id)

    def test_a_loop_is_refused(self, session, draft_one):
        draft_two = import_revision(session, REVISED)
        link_draft(session, draft_two.id, draft_one.id)
        # draft_one linking back under draft_two would make a loop.
        with pytest.raises(LinkRefused, match="loop"):
            link_draft(session, draft_one.id, draft_two.id)


class TestCandidates:
    def test_the_chain_head_is_offered_and_superseded_drafts_are_not(
        self, session, draft_one
    ):
        draft_two = import_revision(session, REVISED)
        link_draft(session, draft_two.id, draft_one.id)
        draft_two.graph_status = "ready"
        session.flush()

        upload = import_revision(session, REVISED)
        offered = draft_candidates(session, upload)
        assert [c.id for c in offered] == [draft_two.id]

    def test_a_different_title_is_never_offered(self, session, draft_one):
        result = import_screenplay(
            (DEMO_SCRIPTS / "02-the-understudy/the-understudy.fountain").read_bytes(),
            "the-understudy.fountain",
        )
        upload = persist_import(session, result)
        assert draft_candidates(session, upload) == []


def fake_extracted(session, scene) -> None:
    """Mark a scene extracted without a model, for report-guard tests."""
    from datetime import UTC, datetime

    from ripple.db.models import ExtractionRun, SceneExtraction

    run = ExtractionRun(
        script_id=scene.script_id,
        status="ready",
        prompt_version="extract.v4",
        model_id="test-model",
        total_scenes=1,
        completed_scenes=1,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(run)
    session.flush()
    session.add(
        SceneExtraction(
            extraction_run_id=run.id,
            scene_id=scene.id,
            status="completed",
            input_hash="t" * 64,
            prompt_version="extract.v4",
            model_id="test-model",
        )
    )
    session.flush()


class TestDraftReport:
    def test_the_report_refuses_while_changed_scenes_await_extraction(
        self, session, draft_one
    ):
        from ripple.services.draft_report import ReportRefused, build_draft_report

        draft_two = import_revision(session, REVISED)
        link_draft(session, draft_two.id, draft_one.id)
        with pytest.raises(ReportRefused, match="not extracted yet"):
            build_draft_report(session, draft_two.id)

    def test_an_identical_draft_reports_no_change_at_low_severity(
        self, session, draft_one
    ):
        from ripple.db.models import RippleReport as ReportRow
        from ripple.services.draft_report import build_draft_report

        draft_two = import_revision(session, FOUNTAIN)
        link_draft(session, draft_two.id, draft_one.id)
        report = build_draft_report(session, draft_two.id)
        assert report.severity == "low"
        assert report.entities_added == []
        assert report.entities_removed == []
        assert report.attribute_changes == []
        assert report.lost_introductions == []
        assert "unchanged" in report.summary
        row = session.scalar(
            select(ReportRow).where(
                ReportRow.change_set_id == uuid.UUID(report.change_set_id)
            )
        )
        assert row is not None

        again = build_draft_report(session, draft_two.id)
        assert again.already_existed
        assert again.change_set_id == report.change_set_id

    def test_a_rewritten_scene_surfaces_losses_and_changes(
        self, session, draft_one
    ):
        """Scene 3 rewritten: its extraction (simulated) restates the
        monitors with a new count but establishes nothing, so the report
        has to say what moved and what lost its introduction."""
        from ripple.db.models import ContinuityFinding, EntityAttribute
        from ripple.services.draft_report import build_draft_report

        draft_two = import_revision(session, REVISED)
        link = link_draft(session, draft_two.id, draft_one.id)
        rewritten = session.get(Scene, uuid.UUID(link.to_extract[0]))
        fake_extracted(session, rewritten)

        monitors = session.scalar(
            select(Entity).where(
                Entity.script_id == draft_two.id,
                Entity.canonical_name == "Dispatch monitors",
            )
        )
        assert monitors is not None, "carried via scenes 20 and 27"
        unit = session.scalar(
            select(ScriptUnit)
            .where(ScriptUnit.scene_id == rewritten.id)
            .order_by(ScriptUnit.sequence_index)
        )
        session.add(
            EntityAttribute(
                entity_id=monitors.id,
                key="count",
                value="twelve",
                source_unit_id=unit.id,
                confidence=0.9,
                provenance="model",
            )
        )
        session.flush()

        report = build_draft_report(session, draft_two.id)
        assert report.severity == "high"
        assert {
            "entity": "Dispatch monitors",
            "key": "count",
            "before": "six",
            "after": "twelve",
        } in report.attribute_changes
        # Entities introduced in the rewritten scene and still used later
        # lost their introduction; the monitors are the canonical case.
        assert any("Dispatch monitors" in m for m in report.lost_introductions)

        findings = list(
            session.scalars(
                select(ContinuityFinding).where(
                    ContinuityFinding.change_set_id
                    == uuid.UUID(report.change_set_id)
                )
            )
        )
        kinds = {f.finding_type for f in findings}
        assert "lost_introduction" in kinds
        lost = next(
            f for f in findings if f.finding_type == "lost_introduction"
        )
        assert lost.evidence, "a lost introduction cites the surviving uses"
        assert "No model is selected" in " ".join(report.continuity_notes)

    def test_the_judged_layer_runs_once_per_changed_scene(
        self, session, draft_one
    ):
        from ripple.db.models import ModelCall
        from ripple.services.draft_report import build_draft_report
        from tests.test_preview import FakeJudge

        draft_two = import_revision(session, REVISED)
        link = link_draft(session, draft_two.id, draft_one.id)
        rewritten = session.get(Scene, uuid.UUID(link.to_extract[0]))
        fake_extracted(session, rewritten)

        judge = FakeJudge()
        report = build_draft_report(
            session, draft_two.id, provider=judge, model_id="fake-judge"
        )
        assert judge.continuity_calls == 1
        assert report.conflicts == 0
        assert report.continuity_notes == []
        # The pass's audit rows hang on the report's change set.
        audited = session.scalar(
            select(func.count())
            .select_from(ModelCall)
            .where(
                ModelCall.change_set_id == uuid.UUID(report.change_set_id),
                ModelCall.purpose == "continuity",
            )
        )
        assert audited == 1
