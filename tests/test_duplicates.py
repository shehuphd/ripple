"""Duplicate-entity detection, reviewed merges, and alias-aware resolution.

Attacks first: merges that would violate the one-active-value-per-key
attribute index, pairs across types and scripts that must refuse, and
ambiguous aliases that must not resolve by guessing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from ripple.db.models import (
    Assertion,
    ChangeSet,
    Entity,
    EntityAlias,
    EntityAttribute,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.services.duplicates import (
    MergeRefused,
    cited_counts,
    detect,
    keep_separate,
    merge,
)


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


def build_world(session):
    """One scene; two parka entities forked by wording, with attributes."""
    script = Script(title="Night Freight", import_status="accepted")
    session.add(script)
    session.flush()
    scene = Scene(
        script_id=script.id, sequence_index=0, heading="INT. OFFICE - NIGHT"
    )
    session.add(scene)
    session.flush()
    unit = ScriptUnit(
        scene_id=scene.id,
        unit_type="action",
        sequence_index=0,
        current_text="Mara's grey parka drips on the floor.",
        parser_method="fountain",
    )
    session.add(unit)
    session.flush()

    def entity(name, entity_type="wardrobe"):
        row = Entity(
            script_id=script.id,
            entity_type=entity_type,
            canonical_name=name,
            normalized_name=normalize(name),
        )
        session.add(row)
        session.flush()
        return row

    def cite(subject, count=1):
        # Each citation gets its own unit: the dedupe key covers endpoints,
        # predicate, and source unit, so one unit cannot cite twice.
        for _ in range(count):
            extra = ScriptUnit(
                scene_id=scene.id,
                unit_type="action",
                sequence_index=session.scalar(
                    select(func.count()).select_from(ScriptUnit)
                ),
                current_text="The parka is mentioned again.",
                parser_method="fountain",
            )
            session.add(extra)
            session.flush()
            session.add(
                Assertion(
                    script_id=script.id,
                    subject_kind="entity",
                    subject_entity_id=subject.id,
                    predicate="appears_in",
                    object_kind="scene",
                    object_scene_id=scene.id,
                    source_unit_id=extra.id,
                    confidence=0.9,
                )
            )
        session.flush()

    parka = entity("Grey parka")
    fork = entity("Parka")
    cite(parka, 3)
    cite(fork, 1)
    return {
        "script": script,
        "scene": scene,
        "unit": unit,
        "parka": parka,
        "fork": fork,
        "entity": entity,
        "cite": cite,
    }


class TestDetection:
    def test_a_contained_name_is_suggested_with_the_cited_survivor(
        self, session
    ):
        world = build_world(session)
        pairs = detect(session, world["script"].id)
        assert len(pairs) == 1
        assert pairs[0].keep.id == world["parka"].id
        assert pairs[0].absorb.id == world["fork"].id
        assert "contains" in pairs[0].reason

    def test_a_name_recorded_as_the_others_alias_is_suggested(self, session):
        world = build_world(session)
        blue = world["entity"]("Blue sedan", "transportation")
        black = world["entity"]("Black sedan", "transportation")
        session.add(
            EntityAlias(
                entity_id=black.id,
                alias="Blue sedan",
                normalized_alias=normalize("Blue sedan"),
            )
        )
        session.flush()
        world["cite"](blue, 2)
        pairs = detect(session, world["script"].id)
        sedans = [p for p in pairs if p.keep.entity_type == "transportation"]
        assert len(sedans) == 1
        assert sedans[0].keep.id == blue.id
        assert "share" in sedans[0].reason

    def test_a_pronoun_alias_offers_no_evidence(self, session):
        world = build_world(session)
        she = world["entity"]("She", "cast")
        mara = world["entity"]("Mara Okonjo", "cast")
        session.add(
            EntityAlias(
                entity_id=mara.id,
                alias="She",
                normalized_alias=normalize("She"),
            )
        )
        session.flush()
        pairs = detect(session, world["script"].id)
        assert all(
            she.id not in (p.keep.id, p.absorb.id) for p in pairs
        )

    def test_different_types_are_never_paired(self, session):
        world = build_world(session)
        world["entity"]("Blue sedan", "transportation")
        world["entity"]("Blue Sedan Interior", "set_design")
        pairs = detect(session, world["script"].id)
        assert all(
            {p.keep.entity_type, p.absorb.entity_type} != {"transportation", "set_design"}
            for p in pairs
        )
        assert len(pairs) == 1  # only the parka pair

    def test_unrelated_names_are_not_paired(self, session):
        world = build_world(session)
        world["entity"]("Clipboard", "prop")
        world["entity"]("Forklift", "prop")
        assert len(detect(session, world["script"].id)) == 1

    def test_a_curly_apostrophe_never_makes_a_second_entity(self, session):
        """A printed play sets every apostrophe curly and a person editing in
        the reader types the straight one, so "A lady's bedchamber" arrived
        beside "lady's bedchamber" as two locations. The typographic forms
        fold, so the pair is one entity rather than a duplicate to review."""
        from ripple.db.naming import normalize

        assert normalize("A lady’s bedchamber") == normalize(
            "lady's bedchamber"
        )
        assert normalize("Friar Lawrence’s Cell") == normalize(
            "friar lawrence's cell"
        )
        # An en-dash name and its hyphen form are also one key.
        assert normalize("Container 4–4–1") == normalize("container 4-4-1")

    def test_a_kept_separate_pair_stops_being_offered(self, session):
        world = build_world(session)
        keep_separate(session, world["parka"].id, world["fork"].id)
        assert detect(session, world["script"].id) == []
        # Recording it twice is a no-op, not an error.
        keep_separate(session, world["fork"].id, world["parka"].id)


class TestMerge:
    def test_facts_aliases_and_name_move_to_the_survivor(self, session):
        world = build_world(session)
        session.add(
            EntityAlias(
                entity_id=world["fork"].id,
                alias="the parka",
                normalized_alias=normalize("the parka"),
            )
        )
        session.flush()
        merge(session, world["parka"].id, world["fork"].id)

        assert session.get(Entity, world["fork"].id) is None
        cited = session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(
                Assertion.active.is_(True),
                Assertion.subject_entity_id == world["parka"].id,
            )
        )
        assert cited == 4
        aliases = set(
            session.scalars(
                select(EntityAlias.normalized_alias).where(
                    EntityAlias.entity_id == world["parka"].id
                )
            )
        )
        # normalize folds the article, so "Parka" and "the parka" are one
        # surface form; the survivor holds it and later mentions resolve.
        assert "parka" in aliases

    def test_a_colliding_attribute_deactivates_instead_of_moving(
        self, session
    ):
        """The survivor's active value wins; two active rows for one key
        would violate the attribute index."""
        world = build_world(session)
        for entity, value in ((world["parka"], "grey"), (world["fork"], "green")):
            session.add(
                EntityAttribute(
                    entity_id=entity.id,
                    key="color",
                    value=value,
                    provenance="user",
                )
            )
        session.flush()
        merge(session, world["parka"].id, world["fork"].id)
        rows = list(
            session.scalars(
                select(EntityAttribute).where(
                    EntityAttribute.entity_id == world["parka"].id,
                    EntityAttribute.key == "color",
                )
            )
        )
        active = [row for row in rows if row.active]
        assert len(active) == 1
        assert active[0].value == "grey"
        assert len(rows) == 2

    def test_the_merge_is_recorded_as_an_accepted_change_set(self, session):
        world = build_world(session)
        before = world["script"].current_version
        change = merge(session, world["parka"].id, world["fork"].id)
        assert change.kind == "merge_entities"
        assert change.status == "accepted"
        record = change.operations[0]
        assert record.before_json["canonical_name"] == "Parka"
        assert record.after_json["surviving_name"] == "Grey parka"
        assert world["script"].current_version == before + 1
        assert session.get(ChangeSet, change.id) is not None

    def test_cross_type_and_cross_script_merges_refuse(self, session):
        world = build_world(session)
        prop = world["entity"]("Parka tag", "prop")
        with pytest.raises(MergeRefused):
            merge(session, world["parka"].id, prop.id)

        other = Script(title="Other", import_status="accepted")
        session.add(other)
        session.flush()
        foreign = Entity(
            script_id=other.id,
            entity_type="wardrobe",
            canonical_name="Parka",
            normalized_name=normalize("Parka"),
        )
        session.add(foreign)
        session.flush()
        with pytest.raises(MergeRefused):
            merge(session, world["parka"].id, foreign.id)

    def test_merging_an_entity_with_itself_refuses(self, session):
        world = build_world(session)
        with pytest.raises(MergeRefused):
            merge(session, world["parka"].id, world["parka"].id)


class TestAliasAwareResolution:
    """Extraction resolves a name to the entity already holding it as an alias."""

    def _resolve(self, session, script_id, name, entity_type="wardrobe"):
        from ripple.extraction.service import _resolve_entity
        from ripple.extraction.validate import ValidatedEntity

        return _resolve_entity(
            session,
            script_id,
            ValidatedEntity(
                local_id="e1",
                entity_type=entity_type,
                canonical_name=name,
            ),
        )

    def test_a_known_alias_resolves_to_its_entity(self, session):
        world = build_world(session)
        session.add(
            EntityAlias(
                entity_id=world["parka"].id,
                alias="soaked coat",
                normalized_alias=normalize("soaked coat"),
            )
        )
        session.flush()
        resolved = self._resolve(session, world["script"].id, "Soaked coat")
        assert resolved.id == world["parka"].id

    def test_an_alias_two_entities_share_creates_a_fresh_entity(self, session):
        world = build_world(session)
        for entity in (world["parka"], world["fork"]):
            session.add(
                EntityAlias(
                    entity_id=entity.id,
                    alias="outer layer",
                    normalized_alias=normalize("outer layer"),
                )
            )
        session.flush()
        resolved = self._resolve(session, world["script"].id, "Outer layer")
        assert resolved.id not in {world["parka"].id, world["fork"].id}

    def test_an_alias_match_respects_the_entity_type(self, session):
        world = build_world(session)
        session.add(
            EntityAlias(
                entity_id=world["parka"].id,
                alias="grey bundle",
                normalized_alias=normalize("grey bundle"),
            )
        )
        session.flush()
        resolved = self._resolve(
            session, world["script"].id, "Grey bundle", entity_type="prop"
        )
        assert resolved.id != world["parka"].id
        assert resolved.entity_type == "prop"


class TestCitedCounts:
    """One grouped query stands in for a per-entity `subject OR object` count."""

    def _per_entity(self, session, entity) -> int:
        from sqlalchemy import or_

        return session.scalar(
            select(func.count())
            .select_from(Assertion)
            .where(
                Assertion.active.is_(True),
                or_(
                    Assertion.subject_entity_id == entity.id,
                    Assertion.object_entity_id == entity.id,
                ),
            )
        )

    def test_the_grouped_count_matches_the_per_entity_count(self, session):
        world = build_world(session)
        counts = cited_counts(session)
        assert counts == {world["parka"].id: 3, world["fork"].id: 1}
        for entity in (world["parka"], world["fork"]):
            assert counts[entity.id] == self._per_entity(session, entity)

    def test_an_entity_cited_on_both_sides_of_one_assertion_counts_once(
        self, session
    ):
        world = build_world(session)
        parka = world["parka"]
        session.add(
            Assertion(
                script_id=world["script"].id,
                subject_kind="entity",
                subject_entity_id=parka.id,
                predicate="interacts_with",
                object_kind="entity",
                object_entity_id=parka.id,
                source_unit_id=world["unit"].id,
                confidence=0.9,
            )
        )
        session.flush()
        assert cited_counts(session)[parka.id] == 4
        assert self._per_entity(session, parka) == 4

    def test_a_deactivated_assertion_is_not_a_citation(self, session):
        world = build_world(session)
        fork = world["fork"]
        row = session.scalar(
            select(Assertion).where(Assertion.subject_entity_id == fork.id)
        )
        row.active = False
        session.flush()
        counts = cited_counts(session)
        assert fork.id not in counts
        assert counts[world["parka"].id] == 3

    def test_the_count_can_be_scoped_to_one_script(self, session):
        world = build_world(session)
        other = Script(title="Elsewhere", import_status="accepted")
        session.add(other)
        session.flush()
        scene = Scene(script_id=other.id, sequence_index=0, heading="EXT. ROAD")
        session.add(scene)
        session.flush()
        unit = ScriptUnit(
            scene_id=scene.id,
            unit_type="action",
            sequence_index=0,
            current_text="A van idles.",
            parser_method="fountain",
        )
        session.add(unit)
        session.flush()
        van = Entity(
            script_id=other.id,
            entity_type="prop",
            canonical_name="Van",
            normalized_name=normalize("Van"),
        )
        session.add(van)
        session.flush()
        session.add(
            Assertion(
                script_id=other.id,
                subject_kind="entity",
                subject_entity_id=van.id,
                predicate="appears_in",
                object_kind="scene",
                object_scene_id=scene.id,
                source_unit_id=unit.id,
                confidence=0.9,
            )
        )
        session.flush()
        everywhere = cited_counts(session)
        assert set(everywhere) == {world["parka"].id, world["fork"].id, van.id}
        scoped = cited_counts(session, other.id)
        assert scoped == {van.id: 1}
