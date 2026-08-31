"""Ground-truth parsing and graph seeding.

The bundled corpus must open with a full graph and zero model calls, so the
dependencies.md files are load-bearing: a parse failure here is a script that
opens empty. These tests parse the shipped files themselves, not synthetic stand-ins.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from ripple.adapters import import_screenplay
from ripple.db.models import Assertion, Entity, EntityAttribute, ExtractionRun, Script
from ripple.db.repository import persist_import
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.graph.fixtures import (
    ground_truths,
    parse_ground_truth,
    seed_demo_graphs,
    seed_graph,
)
from ripple.graph.predicates import validate_edge

DEMO_SCRIPTS = Path(__file__).resolve().parent.parent / "demo-scripts"


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


def _import(session, folder: str, filename: str) -> Script:
    path = DEMO_SCRIPTS / folder / filename
    result = import_screenplay(path.read_bytes(), path.name)
    assert result.accepted, result.rejection_code
    return persist_import(session, result)


class TestParsing:
    """Every shipped ground-truth file parses into a usable oracle."""

    def test_all_three_files_parse_with_titles(self):
        truths = ground_truths(DEMO_SCRIPTS)
        assert [t.title for t in truths] == [
            "NIGHT FREIGHT",
            "THE UNDERSTUDY",
            "SEVEN MINUTES",
        ]

    def test_every_script_has_entities_chains_and_attributes(self):
        for truth in ground_truths(DEMO_SCRIPTS):
            assert truth.entities, truth.title
            assert len(truth.chains) == 5, truth.title
            assert truth.attributes, truth.title

    def test_entity_types_are_all_in_the_schema_vocabulary(self):
        from ripple.db.models import ENTITY_TYPES

        for truth in ground_truths(DEMO_SCRIPTS):
            for name, entity_type, _ in truth.entities:
                assert entity_type in ENTITY_TYPES, f"{truth.title}: {name}"
            for chain in truth.chains:
                assert chain.entity_type in ENTITY_TYPES, chain.entity

    def test_night_freight_carries_the_scene_14_oracle(self):
        text = (DEMO_SCRIPTS / "01-night-freight" / "dependencies.md").read_text()
        truth = parse_ground_truth(text)
        fourteen = [row for row in truth.scene_assertions if row[0] == "14"]
        assert len(fourteen) >= 11

    def test_a_prose_type_cell_reads_the_stated_modelling(self):
        """"`makeup` on the vehicle, modelled as `set_design`" is set_design."""
        text = (DEMO_SCRIPTS / "03-seven-minutes" / "dependencies.md").read_text()
        truth = parse_ground_truth(text)
        damage = next(c for c in truth.chains if c.entity == "Offside wing damage")
        assert damage.entity_type == "set_design"

    def test_lettered_scene_numbers_survive(self):
        text = (DEMO_SCRIPTS / "02-the-understudy" / "dependencies.md").read_text()
        truth = parse_ground_truth(text)
        gown = next(c for c in truth.chains if c.entity == "Emerald gown")
        assert "30A" in gown.dependants


class TestSeeding:
    def test_seeding_builds_a_dense_valid_graph(self, session):
        script = _import(session, "01-night-freight", "night-freight.fountain")
        truth = ground_truths(DEMO_SCRIPTS)[0]
        counts = seed_graph(session, script, truth)

        assert counts.entities >= 15
        assert counts.assertions >= 50
        assert counts.attributes >= 3
        assert script.graph_status == "ready"

        # Every written edge satisfies its predicate signature.
        for assertion in session.scalars(
            select(Assertion).where(Assertion.script_id == script.id)
        ):
            subject_type = (
                session.get(Entity, assertion.subject_entity_id).entity_type
                if assertion.subject_entity_id
                else None
            )
            object_type = (
                session.get(Entity, assertion.object_entity_id).entity_type
                if assertion.object_entity_id
                else None
            )
            validate_edge(
                assertion.predicate,
                assertion.subject_kind,
                assertion.object_kind,
                subject_type,
                object_type,
            )

    def test_the_demo_chain_is_present(self, session):
        """Blue sedan: established in 14, appearing in the dependant scenes."""
        script = _import(session, "01-night-freight", "night-freight.fountain")
        seed_graph(session, script, ground_truths(DEMO_SCRIPTS)[0])
        sedan = session.scalar(
            select(Entity).where(
                Entity.script_id == script.id, Entity.normalized_name == "blue sedan"
            )
        )
        establishes = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(
                Assertion.predicate == "establishes",
                Assertion.object_entity_id == sedan.id,
                Assertion.active.is_(True),
            )
        )
        appearances = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(
                Assertion.predicate == "appears_in",
                Assertion.subject_entity_id == sedan.id,
                Assertion.active.is_(True),
            )
        )
        assert establishes == 1
        assert appearances >= 8

    def test_attributes_carry_evidence(self, session):
        script = _import(session, "02-the-understudy", "the-understudy.fountain")
        truth = next(t for t in ground_truths(DEMO_SCRIPTS) if "UNDER" in t.title)
        seed_graph(session, script, truth)
        gown_color = session.scalar(
            select(EntityAttribute)
            .join(Entity)
            .where(Entity.script_id == script.id, EntityAttribute.key == "color")
        )
        assert gown_color.value == "emerald"
        assert gown_color.source_unit_id is not None
        assert gown_color.provenance == "system"

    def test_seeding_twice_writes_nothing_new(self, session):
        script = _import(session, "03-seven-minutes", "seven-minutes.fountain")
        truth = next(t for t in ground_truths(DEMO_SCRIPTS) if "SEVEN" in t.title)
        first = seed_graph(session, script, truth)
        again = seed_graph(session, script, truth)
        assert first.assertions > 0
        assert again.assertions == 0
        assert again.entities == 0
        assert again.attributes == 0

    def test_startup_seeding_skips_ready_scripts(self, session):
        script = _import(session, "01-night-freight", "night-freight.fountain")
        script.origin = "bundled"
        assert seed_demo_graphs(session, DEMO_SCRIPTS) == 1
        assert seed_demo_graphs(session, DEMO_SCRIPTS) == 0
        assert script.graph_status == "ready"

    def test_a_cleared_graph_reseeds_on_the_next_start(self, session):
        script = _import(session, "01-night-freight", "night-freight.fountain")
        script.origin = "bundled"
        seed_demo_graphs(session, DEMO_SCRIPTS)
        script.graph_status = "not_analysed"
        session.flush()
        assert seed_demo_graphs(session, DEMO_SCRIPTS) == 1


class TestOrigin:
    """Seeding trusts the origin marker, never a title match."""

    def test_an_upload_sharing_a_demo_title_is_never_seeded(self, session):
        script = _import(session, "01-night-freight", "night-freight.fountain")
        assert script.origin == "upload"
        assert seed_demo_graphs(session, DEMO_SCRIPTS) == 0
        assert script.graph_status == "not_analysed"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Assertion)
                .where(Assertion.script_id == script.id)
            )
            == 0
        )

    def test_a_pre_origin_demo_script_is_re_marked_and_reseeds(self, session):
        # A database seeded before the origin column existed: system-provenance
        # graph, no extraction run, origin stamped "upload" by the migration.
        script = _import(session, "01-night-freight", "night-freight.fountain")
        truth = next(t for t in ground_truths(DEMO_SCRIPTS) if "FREIGHT" in t.title)
        seed_graph(session, script, truth)
        assert script.origin == "upload"
        script.graph_status = "not_analysed"
        session.flush()
        assert seed_demo_graphs(session, DEMO_SCRIPTS) == 1
        assert script.origin == "bundled"

    def test_an_upload_with_extraction_history_is_not_re_marked(self, session):
        script = _import(session, "01-night-freight", "night-freight.fountain")
        session.add(
            ExtractionRun(
                script_id=script.id,
                status="partially_ready",
                prompt_version="extract.v4",
                model_id="gemini-flash-latest",
            )
        )
        session.flush()
        assert seed_demo_graphs(session, DEMO_SCRIPTS) == 0
        assert script.origin == "upload"
