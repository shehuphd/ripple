"""Schema constraints and persistence.

ERD section 13 lists what the schema is incomplete without proving. These tests
attack the constraints first: a schema that accepts a malformed row is worse
than one that rejects a valid one, because the bad row is discovered later, by
something else, as corrupted graph state.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ripple.adapters import import_screenplay
from ripple.db.models import (
    AppConfiguration,
    Assertion,
    ChangeOperation,
    ChangeSet,
    Entity,
    EntityAlias,
    EntityAttribute,
    ModelCall,
    RippleReport,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.naming import normalize, normalize_key
from ripple.db.repository import (
    clear_all_graphs,
    delete_all_scripts,
    delete_script,
    deletion_preview,
    get_active_model,
    persist_import,
    set_active_model,
)
from ripple.db.session import (
    create_all,
    create_db_engine,
    session_factory,
    session_scope,
)


@pytest.fixture
def factory():
    """A fresh in-memory database per test, with foreign keys enforced."""
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    return session_factory(engine)


@pytest.fixture
def session(factory):
    """An open session that rolls back at the end of the test."""
    with session_factory(create_db_engine("sqlite+pysqlite:///:memory:"))() as _:
        pass
    instance = factory()
    yield instance
    instance.rollback()
    instance.close()


def _script(session, title="A Script") -> Script:
    script = Script(title=title, import_status="accepted")
    session.add(script)
    session.flush()
    return script


def _scene(session, script, index=0) -> Scene:
    scene = Scene(script_id=script.id, sequence_index=index, heading=f"INT. {index}")
    session.add(scene)
    session.flush()
    return scene


def _unit(session, scene, index=0) -> ScriptUnit:
    unit = ScriptUnit(
        scene_id=scene.id,
        unit_type="action",
        sequence_index=index,
        current_text="Something happens.",
        parser_method="fountain",
    )
    session.add(unit)
    session.flush()
    return unit


def _entity(session, script, name="Blue sedan", kind="transportation") -> Entity:
    entity = Entity(
        script_id=script.id,
        entity_type=kind,
        canonical_name=name,
        normalized_name=normalize(name),
    )
    session.add(entity)
    session.flush()
    return entity


class TestForeignKeys:
    """SQLite ignores foreign keys unless the pragma is set per connection."""

    def test_orphan_scene_is_rejected(self, session):
        session.add(Scene(script_id=uuid.uuid4(), sequence_index=0, heading="INT. X"))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_orphan_unit_is_rejected(self, session):
        session.add(
            ScriptUnit(
                scene_id=uuid.uuid4(),
                unit_type="action",
                sequence_index=0,
                current_text="x",
                parser_method="rule",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


class TestVocabularies:
    @pytest.mark.parametrize(
        "column, value",
        [
            ("unit_type", "stage_direction"),
            ("parser_method", "telepathy"),
        ],
    )
    def test_a_value_outside_the_schema_lock_is_rejected(self, session, column, value):
        scene = _scene(session, _script(session))
        fields = {
            "scene_id": scene.id,
            "unit_type": "action",
            "sequence_index": 0,
            "current_text": "x",
            "parser_method": "rule",
            column: value,
        }
        session.add(ScriptUnit(**fields))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_an_entity_type_outside_the_lock_is_rejected(self, session):
        script = _script(session)
        session.add(
            Entity(
                script_id=script.id,
                entity_type="catering",
                canonical_name="Sandwiches",
                normalized_name="sandwiches",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_predicate_outside_the_lock_is_rejected(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind="entity",
                subject_entity_id=entity.id,
                predicate="smells_like",
                object_kind="scene",
                object_scene_id=scene.id,
                source_unit_id=unit.id,
                confidence=0.9,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


class TestAppearanceManner:
    def test_an_appears_in_edge_carries_a_manner(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        row = Assertion(
            script_id=script.id,
            subject_kind="entity",
            subject_entity_id=entity.id,
            predicate="appears_in",
            manner="depicted",
            object_kind="scene",
            object_scene_id=scene.id,
            source_unit_id=unit.id,
            confidence=0.9,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        assert row.manner == "depicted"

    def test_an_unknown_manner_is_rejected(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind="entity",
                subject_entity_id=entity.id,
                predicate="appears_in",
                manner="lurking",
                object_kind="scene",
                object_scene_id=scene.id,
                source_unit_id=unit.id,
                confidence=0.9,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_manner_on_a_non_appearance_edge_is_rejected(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind="entity",
                subject_entity_id=entity.id,
                predicate="carries",
                manner="on_stage",
                object_kind="scene",
                object_scene_id=scene.id,
                source_unit_id=unit.id,
                confidence=0.9,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


class TestNumericRanges:
    @pytest.mark.parametrize("confidence", [-0.1, 1.5])
    def test_confidence_outside_zero_to_one_is_rejected(self, session, confidence):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind="entity",
                subject_entity_id=entity.id,
                predicate="appears_in",
                object_kind="scene",
                object_scene_id=scene.id,
                source_unit_id=unit.id,
                confidence=confidence,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_negative_sequence_index_is_rejected(self, session):
        script = _script(session)
        session.add(Scene(script_id=script.id, sequence_index=-1, heading="INT. X"))
        with pytest.raises(IntegrityError):
            session.flush()


class TestAssertionSides:
    """Each assertion side is kinded and holds precisely one key."""

    def test_an_entity_subject_without_an_entity_id_is_rejected(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind="entity",
                subject_scene_id=scene.id,
                predicate="appears_in",
                object_kind="scene",
                object_scene_id=scene.id,
                source_unit_id=unit.id,
                confidence=0.9,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_side_holding_both_keys_is_rejected(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind="entity",
                subject_entity_id=entity.id,
                subject_scene_id=scene.id,
                predicate="appears_in",
                object_kind="scene",
                object_scene_id=scene.id,
                source_unit_id=unit.id,
                confidence=0.9,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_scene_can_be_a_graph_node_on_either_side(self, session):
        """The mockup renders `Sc 14 requires Chain rattle` and the reverse."""
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script, "Chain rattle", "sound")
        session.add_all(
            [
                Assertion(
                    script_id=script.id,
                    subject_kind="scene",
                    subject_scene_id=scene.id,
                    predicate="requires",
                    object_kind="entity",
                    object_entity_id=entity.id,
                    source_unit_id=unit.id,
                    confidence=0.7,
                ),
                Assertion(
                    script_id=script.id,
                    subject_kind="entity",
                    subject_entity_id=entity.id,
                    predicate="appears_in",
                    object_kind="scene",
                    object_scene_id=scene.id,
                    source_unit_id=unit.id,
                    confidence=0.8,
                ),
            ]
        )
        session.flush()
        assert session.scalar(select(func.count()).select_from(Assertion)) == 2


class TestUniqueness:
    def test_two_scenes_cannot_share_an_index_in_one_script(self, session):
        script = _script(session)
        _scene(session, script, 0)
        session.add(Scene(script_id=script.id, sequence_index=0, heading="INT. Y"))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_two_units_cannot_share_an_index_in_one_scene(self, session):
        scene = _scene(session, _script(session))
        _unit(session, scene, 0)
        session.add(
            ScriptUnit(
                scene_id=scene.id,
                unit_type="action",
                sequence_index=0,
                current_text="y",
                parser_method="rule",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_the_same_normalized_name_collides_within_a_type(self, session):
        script = _script(session)
        _entity(session, script, "THE BLUE SEDAN")
        session.add(
            Entity(
                script_id=script.id,
                entity_type="transportation",
                canonical_name="the blue sedan",
                normalized_name=normalize("the blue sedan"),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_the_same_name_in_a_different_type_is_allowed(self, session):
        script = _script(session)
        _entity(session, script, "Bicycle", "transportation")
        _entity(session, script, "Bicycle", "prop")
        assert session.scalar(select(func.count()).select_from(Entity)) == 2

    def test_the_same_name_in_a_different_script_is_allowed(self, session):
        _entity(session, _script(session, "A"), "Blue sedan")
        _entity(session, _script(session, "B"), "Blue sedan")
        assert session.scalar(select(func.count()).select_from(Entity)) == 2

    def test_an_alias_cannot_repeat_within_an_entity(self, session):
        entity = _entity(session, _script(session))
        session.add_all(
            [
                EntityAlias(
                    entity_id=entity.id,
                    alias="the sedan",
                    normalized_alias="the sedan",
                    provenance="model",
                ),
                EntityAlias(
                    entity_id=entity.id,
                    alias="The Sedan",
                    normalized_alias="the sedan",
                    provenance="model",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_change_operations_cannot_share_a_sequence_index(self, session):
        script = _script(session)
        change_set = ChangeSet(script_id=script.id, kind="edit", base_script_version=1)
        session.add(change_set)
        session.flush()
        session.add_all(
            [
                ChangeOperation(
                    change_set_id=change_set.id,
                    sequence_index=0,
                    operation_type="add_assertion",
                    target_type="assertion",
                ),
                ChangeOperation(
                    change_set_id=change_set.id,
                    sequence_index=0,
                    operation_type="remove_assertion",
                    target_type="assertion",
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_change_set_has_at_most_one_ripple_report(self, session):
        script = _script(session)
        change_set = ChangeSet(script_id=script.id, kind="edit", base_script_version=1)
        session.add(change_set)
        session.flush()
        session.add_all(
            [
                RippleReport(
                    change_set_id=change_set.id, summary="one", severity="low"
                ),
                RippleReport(
                    change_set_id=change_set.id, summary="two", severity="low"
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            session.flush()


class TestAssertionDedupe:
    def _edge(self, script, scene, unit, entity, **overrides):
        fields = {
            "script_id": script.id,
            "subject_kind": "entity",
            "subject_entity_id": entity.id,
            "predicate": "appears_in",
            "object_kind": "scene",
            "object_scene_id": scene.id,
            "source_unit_id": unit.id,
            "confidence": 0.9,
        }
        fields.update(overrides)
        return Assertion(**fields)

    def test_re_extraction_cannot_duplicate_an_edge_from_one_unit(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        session.add(self._edge(script, scene, unit, entity))
        session.flush()
        session.add(self._edge(script, scene, unit, entity, confidence=0.5))
        with pytest.raises(IntegrityError):
            session.flush()

    def test_a_deactivated_edge_does_not_block_a_fresh_one(self, session):
        """The dedupe index is partial, so history-preserving deactivation works."""
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        first = self._edge(script, scene, unit, entity)
        session.add(first)
        session.flush()
        first.active = False
        session.flush()
        session.add(self._edge(script, scene, unit, entity, confidence=0.6))
        session.flush()
        assert session.scalar(select(func.count()).select_from(Assertion)) == 2


class TestDeletion:
    def test_deleting_a_script_cannot_touch_another(self, session):
        keep = _script(session, "Keep")
        _scene(session, keep)
        drop = _script(session, "Drop")
        _scene(session, drop)

        delete_script(session, drop.id)
        remaining = list(session.scalars(select(Script)))
        assert [script.title for script in remaining] == ["Keep"]
        assert session.scalar(select(func.count()).select_from(Scene)) == 1

    def test_deletion_preview_counts_before_anything_is_removed(self, session):
        script = _script(session)
        scene = _scene(session, script)
        _unit(session, scene, 0)
        _unit(session, scene, 1)

        counts = deletion_preview(session, script.id)
        assert counts.scenes == 1 and counts.units == 2
        assert session.scalar(select(func.count()).select_from(ScriptUnit)) == 2

    def test_clear_graphs_preserves_scripts_and_units(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script)
        session.add(
            Assertion(
                script_id=script.id,
                subject_kind="entity",
                subject_entity_id=entity.id,
                predicate="appears_in",
                object_kind="scene",
                object_scene_id=scene.id,
                source_unit_id=unit.id,
                confidence=0.9,
            )
        )
        script.graph_status = "ready"
        session.flush()

        counts = clear_all_graphs(session)
        assert counts.assertions == 1 and counts.entities == 1
        assert session.scalar(select(func.count()).select_from(Script)) == 1
        assert session.scalar(select(func.count()).select_from(ScriptUnit)) == 1
        assert session.scalar(select(func.count()).select_from(Assertion)) == 0
        assert session.get(Script, script.id).graph_status == "not_analysed"

    def test_configuration_survives_deleting_every_script(self, session):
        _script(session)
        set_active_model(session, "google", "some-model-id")
        delete_all_scripts(session)
        assert session.scalar(select(func.count()).select_from(Script)) == 0
        assert get_active_model(session) == ("google", "some-model-id")
        assert session.scalar(select(func.count()).select_from(AppConfiguration)) == 1


class TestTransactions:
    def test_a_failure_part_way_through_leaves_nothing_behind(self, factory):
        """session_scope must roll the whole write back, not part of it."""
        with pytest.raises(RuntimeError), session_scope(factory) as session:
            script = _script(session, "Doomed")
            _scene(session, script)
            raise RuntimeError("something failed after the writes")

        with session_scope(factory) as session:
            assert session.scalar(select(func.count()).select_from(Script)) == 0
            assert session.scalar(select(func.count()).select_from(Scene)) == 0


class TestPersistImport:
    def test_a_rejected_import_is_never_persisted(self, session):
        result = import_screenplay(b"not a screenplay at all", "junk.txt")
        with pytest.raises(ValueError, match="rejected"):
            persist_import(session, result)

    def test_every_demo_script_persists_in_every_format(self, session, corpus_stem):
        from tests.conftest import _read

        for suffix in ("fountain", "fdx", "pdf", "txt"):
            result = import_screenplay(_read(f"{corpus_stem}.{suffix}"), f"x.{suffix}")
            script = persist_import(session, result)
            assert len(script.scenes) == result.scene_count
            stored_units = sum(len(scene.units) for scene in script.scenes)
            assert stored_units == result.unit_count

    def test_scene_numbers_and_order_survive_a_round_trip(
        self, session, night_freight_fountain
    ):
        result = import_screenplay(night_freight_fountain, "night-freight.fountain")
        script = persist_import(session, result)
        session.expire_all()

        reloaded = session.get(Script, script.id)
        assert reloaded.title == "NIGHT FREIGHT"
        numbers = [scene.display_scene_number for scene in reloaded.scenes]
        assert numbers == [str(n) for n in range(1, 45)]

    def test_source_provenance_is_stored_for_pdf(self, session, pdf_bytes):
        result = import_screenplay(pdf_bytes, "x.pdf")
        script = persist_import(session, result)
        pages = {
            anchor.source_page_number
            for scene in script.scenes
            for unit in scene.units
            for anchor in unit.anchors
        }
        assert pages and all(page >= 1 for page in pages)

    def test_the_speaker_cue_survives_import(self, session, night_freight_fountain):
        """Extraction resolves this to an entity; losing it before then is a bug."""
        result = import_screenplay(night_freight_fountain, "x.fountain")
        script = persist_import(session, result)
        speakers = {
            unit.speaker_name
            for scene in script.scenes
            for unit in scene.units
            if unit.unit_type == "dialogue"
        }
        assert "MARA" in speakers
        assert None not in speakers


class TestEntityAttributes:
    """Entity attributes are evidence-backed key-value facts."""

    def _attribute(self, session, entity, unit, key="color", value="emerald", **kw):
        attribute = EntityAttribute(
            entity_id=entity.id,
            key=normalize_key(key),
            value=value,
            source_unit_id=unit.id if unit is not None else None,
            confidence=kw.pop("confidence", 0.9),
            provenance=kw.pop("provenance", "model"),
        )
        session.add(attribute)
        session.flush()
        return attribute

    def test_one_active_row_per_entity_and_key(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script, "Gown", "wardrobe")
        self._attribute(session, entity, unit, value="emerald")
        with pytest.raises(IntegrityError):
            self._attribute(session, entity, unit, value="red")

    def test_deactivating_frees_the_key(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script, "Gown", "wardrobe")
        first = self._attribute(session, entity, unit, value="emerald")
        first.active = False
        session.flush()
        second = self._attribute(session, entity, unit, value="red")
        assert second.value == "red"

    def test_two_keys_coexist_on_one_entity(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script, "Gown", "wardrobe")
        self._attribute(session, entity, unit, key="color", value="emerald")
        self._attribute(session, entity, unit, key="material", value="silk")
        assert len(entity.attributes) == 2

    def test_a_model_attribute_without_evidence_is_refused(self, session):
        script = _script(session)
        entity = _entity(session, script, "Gown", "wardrobe")
        with pytest.raises(IntegrityError):
            self._attribute(session, entity, None, provenance="model")

    def test_a_user_attribute_may_omit_evidence(self, session):
        script = _script(session)
        entity = _entity(session, script, "Gown", "wardrobe")
        attribute = self._attribute(session, entity, None, provenance="user")
        assert attribute.source_unit_id is None

    def test_confidence_outside_the_range_is_refused(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script, "Gown", "wardrobe")
        with pytest.raises(IntegrityError):
            self._attribute(session, entity, unit, confidence=1.5)

    def test_deleting_the_entity_deletes_its_attributes(self, session):
        script = _script(session)
        scene = _scene(session, script)
        unit = _unit(session, scene)
        entity = _entity(session, script, "Gown", "wardrobe")
        self._attribute(session, entity, unit)
        session.delete(entity)
        session.flush()
        remaining = session.scalar(
            select(func.count()).select_from(EntityAttribute)
        )
        assert remaining == 0


class TestModelCalls:
    """The per-call audit record."""

    def _call(self, session, script, **kw):
        call = ModelCall(
            script_id=script.id if script is not None else None,
            purpose=kw.pop("purpose", "judge"),
            prompt_version=kw.pop("prompt_version", "judge.v1"),
            model_id=kw.pop("model_id", "gemini-flash-latest"),
            request_text="prompt body",
            response_text=kw.pop("response_text", "{}"),
            outcome=kw.pop("outcome", "ok"),
            **kw,
        )
        session.add(call)
        session.flush()
        return call

    def test_an_unknown_purpose_is_refused(self, session):
        script = _script(session)
        with pytest.raises(IntegrityError):
            self._call(session, script, purpose="summarise")

    def test_an_unknown_outcome_is_refused(self, session):
        script = _script(session)
        with pytest.raises(IntegrityError):
            self._call(session, script, outcome="mystery")

    def test_a_budget_refusal_carries_zero_tokens(self, session):
        script = _script(session)
        call = self._call(
            session,
            script,
            outcome="budget_refused",
            response_text=None,
            input_tokens=0,
            output_tokens=0,
        )
        assert call.response_text is None
        assert (call.input_tokens, call.output_tokens) == (0, 0)

    def test_deleting_the_script_deletes_its_calls(self, session):
        script = _script(session)
        self._call(session, script)
        session.delete(script)
        session.flush()
        assert session.scalar(select(func.count()).select_from(ModelCall)) == 0

    def test_negative_tokens_are_refused(self, session):
        script = _script(session)
        with pytest.raises(IntegrityError):
            self._call(session, script, input_tokens=-1)


class TestSceneLabel:
    """The label a scene prints: its own number, never its position."""

    def test_a_numbered_scene_prints_its_number(self, session):
        script = _script(session)
        scene = _scene(session, script, index=4)
        scene.display_scene_number = "12A"
        assert scene.label == "12A"

    def test_an_unnumbered_scene_prints_a_dash_not_its_index(self, session):
        script = _script(session)
        scene = _scene(session, script, index=4)
        assert scene.display_scene_number is None
        assert scene.label == "—"
        assert "4" not in scene.label


class TestGraphLabels:
    """Scene labels: printed numbers where the document has them, positions
    where it has none, and a visible dash only where the two would mix."""

    @pytest.fixture
    def session(self):
        engine = create_db_engine("sqlite+pysqlite:///:memory:")
        create_all(engine)
        instance = session_factory(engine)()
        yield instance
        instance.close()

    def _script(self, session, numbers):
        from ripple.db.models import Scene, Script
        from ripple.db.repository import graph_labels

        script = Script(title="T", import_status="accepted")
        session.add(script)
        session.flush()
        ids = []
        for index, number in enumerate(numbers):
            scene = Scene(
                script_id=script.id,
                sequence_index=index,
                heading=f"INT. PLACE {index} - DAY",
                display_scene_number=number,
            )
            session.add(scene)
            session.flush()
            ids.append(scene.id)
        return [graph_labels(session, script.id)[one] for one in ids]

    def test_an_unnumbered_script_labels_scenes_by_position(self, session):
        assert self._script(session, [None, None, None]) == [
            "Sc 1", "Sc 2", "Sc 3",
        ]

    def test_a_numbered_script_keeps_the_dash_for_the_odd_one_out(self, session):
        assert self._script(session, ["1", None, "3"]) == ["Sc 1", "Sc —", "Sc 3"]
