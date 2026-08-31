"""Rename detection and application across drafts.

Two stages of evidence: the full rename of a lead on the demo corpus
(strong, applied automatically) and a synthetic four-scene script renamed in
three of them (middling, asked about). Both walk-throughs come from the
drafts design.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from ripple.adapters import import_screenplay
from ripple.db.models import (
    Assertion,
    ContinuityFinding,
    Entity,
    EntityAlias,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize
from ripple.db.repository import persist_import
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.graph.fixtures import ground_truths, seed_graph
from ripple.services.drafts import link_draft
from ripple.services.renames import confirm_rename, detect_renames
from tests.test_drafts import fake_extracted

DEMO_SCRIPTS = Path(__file__).resolve().parent.parent / "demo-scripts"
FOUNTAIN = (DEMO_SCRIPTS / "01-night-freight/night-freight.fountain").read_bytes()

FULL_RENAME = FOUNTAIN.replace(b"MARA", b"NIA").replace(b"Mara", b"Nia")

SMALL = b"""INT. ROOM ONE - DAY

A table.

MARA
One line here.

INT. ROOM TWO - DAY

A chair.

MARA
Two lines here.

INT. ROOM THREE - DAY

A door.

MARA
Three lines here.

INT. ROOM FOUR - DAY

A window.

MARA
Four lines here.
"""

# Renamed in three of the four scenes: enough to suspect, too little to act.
SMALL_PARTIAL = SMALL.replace(b"MARA", b"NIA", 3)


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


def import_script(session, data: bytes, name: str) -> Script:
    result = import_screenplay(data, name)
    assert result.accepted, result.rejection_code
    return persist_import(session, result)


def add_cast(session, script: Script, name: str) -> Entity:
    entity = Entity(
        script_id=script.id,
        entity_type="cast",
        canonical_name=name,
        normalized_name=normalize(name),
    )
    session.add(entity)
    session.flush()
    return entity


def link_and_extract_all(session, new: Script, old: Script):
    link = link_draft(session, new.id, old.id)
    for scene_id in link.to_extract:
        fake_extracted(session, session.get(Scene, uuid.UUID(scene_id)))
    return link


class TestFullRename:
    """Every MARA became NIA: strong positional evidence, applied alone."""

    @pytest.fixture
    def linked(self, session):
        draft_one = import_script(session, FOUNTAIN, "nf.fountain")
        truth = next(t for t in ground_truths(DEMO_SCRIPTS) if "FREIGHT" in t.title)
        seed_graph(session, draft_one, truth)
        draft_two = import_script(session, FULL_RENAME, "nf2.fountain")
        link_and_extract_all(session, draft_two, draft_one)
        # What extraction would have created for the new name.
        nia = add_cast(session, draft_two, "Nia Okonjo")
        return draft_one, draft_two, nia

    def test_detection_counts_the_transferred_speaking_positions(
        self, session, linked
    ):
        draft_one, draft_two, nia = linked
        candidates = detect_renames(session, draft_two, draft_one, [nia])
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.old_name == "Mara Okonjo"
        assert candidate.new_name == "Nia Okonjo"
        assert candidate.transferred >= 3
        assert candidate.share >= 0.8
        assert candidate.automatic
        assert "speaking positions now read" in candidate.message

    def test_the_report_applies_it_and_says_so(self, session, linked):
        from ripple.services.draft_report import build_draft_report

        draft_one, draft_two, _nia = linked
        report = build_draft_report(session, draft_two.id)
        assert report.renames_applied == ["Mara Okonjo to Nia Okonjo"]
        assert "Renamed: Mara Okonjo to Nia Okonjo." in report.summary
        # One identity: the survivor links back to draft 1's Mara, carries
        # the old name as an alias, and Mara is not reported as removed.
        survivor = session.scalar(
            select(Entity).where(
                Entity.script_id == draft_two.id,
                Entity.canonical_name == "Nia Okonjo",
            )
        )
        assert survivor is not None
        predecessor = session.get(Entity, survivor.predecessor_entity_id)
        assert predecessor.canonical_name == "Mara Okonjo"
        assert predecessor.script_id == draft_one.id
        aliases = {
            alias.normalized_alias
            for alias in session.scalars(
                select(EntityAlias).where(EntityAlias.entity_id == survivor.id)
            )
        }
        assert normalize("Mara Okonjo") in aliases
        assert "Mara Okonjo" not in report.entities_removed


class TestSuggestedRename:
    """Three of four scenes renamed: enough to ask, never to act alone."""

    @pytest.fixture
    def linked(self, session):
        draft_one = import_script(session, SMALL, "small.fountain")
        mara = add_cast(session, draft_one, "Mara")
        # A minimal graph: Mara appears in every scene, cited to her line.
        for scene in session.scalars(
            select(Scene).where(Scene.script_id == draft_one.id)
        ):
            unit = session.scalar(
                select(ScriptUnit).where(
                    ScriptUnit.scene_id == scene.id,
                    ScriptUnit.unit_type == "dialogue",
                )
            )
            session.add(
                Assertion(
                    script_id=draft_one.id,
                    subject_kind="entity",
                    subject_entity_id=mara.id,
                    predicate="appears_in",
                    object_kind="scene",
                    object_scene_id=scene.id,
                    source_unit_id=unit.id,
                    confidence=0.9,
                    provenance="system",
                )
            )
        session.flush()
        draft_one.graph_status = "ready"

        draft_two = import_script(session, SMALL_PARTIAL, "small2.fountain")
        link_and_extract_all(session, draft_two, draft_one)
        nia = add_cast(session, draft_two, "Nia")
        return draft_one, draft_two, nia

    def test_middling_evidence_is_filed_for_confirmation(self, session, linked):
        from ripple.services.draft_report import build_draft_report

        _draft_one, draft_two, _nia = linked
        report = build_draft_report(session, draft_two.id)
        assert report.renames_applied == []
        assert report.renames_suggested == 1
        finding = session.scalar(
            select(ContinuityFinding).where(
                ContinuityFinding.finding_type == "possible_rename"
            )
        )
        assert finding is not None
        assert finding.status == "open"
        assert finding.payload_json["old_name"] == "Mara"
        assert finding.payload_json["new_name"] == "Nia"

    def test_confirming_joins_the_identities_and_reports_the_leftover(
        self, session, linked
    ):
        from ripple.services.draft_report import build_draft_report

        draft_one, draft_two, _nia = linked
        build_draft_report(session, draft_two.id)
        finding = session.scalar(
            select(ContinuityFinding).where(
                ContinuityFinding.finding_type == "possible_rename"
            )
        )
        survivor = confirm_rename(session, finding)
        assert finding.status == "resolved"

        # The carried twin is the survivor: it keeps its lineage and takes
        # the new name; the manually added Nia row was absorbed.
        assert survivor.canonical_name == "Nia"
        predecessor = session.get(Entity, survivor.predecessor_entity_id)
        assert predecessor.script_id == draft_one.id
        remaining = session.scalar(
            select(Entity).where(
                Entity.script_id == draft_two.id,
                Entity.entity_type == "cast",
                Entity.id != survivor.id,
            )
        )
        assert remaining is None

        # Scene FOUR still says MARA: the classic missed instance.
        partial = session.scalar(
            select(ContinuityFinding).where(
                ContinuityFinding.finding_type == "partial_rename"
            )
        )
        assert partial is not None
        assert partial.status == "open"
        assert "still Mara" in partial.message or "still MARA" in partial.message
        assert partial.evidence

    def test_dismissing_keeps_them_separate(self, session, linked):
        from ripple.services.draft_report import build_draft_report

        _draft_one, draft_two, _nia = linked
        build_draft_report(session, draft_two.id)
        finding = session.scalar(
            select(ContinuityFinding).where(
                ContinuityFinding.finding_type == "possible_rename"
            )
        )
        finding.status = "dismissed"
        session.flush()
        # Nothing merged: both entities stand.
        cast = list(
            session.scalars(
                select(Entity).where(
                    Entity.script_id == draft_two.id,
                    Entity.entity_type == "cast",
                )
            )
        )
        assert len(cast) == 2
