"""Writing a script inside Ripple: creation, renaming, composition, export.

Service-level. The composition tests run on an imported script for the
guard rails (a cited line refuses direct edits) and on a created-from-
nothing script for the writing path itself.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from ripple.adapters import import_screenplay
from ripple.db.models import ChangeSet, Import, Scene, Script, ScriptUnit
from ripple.db.repository import persist_import
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.graph.fixtures import ground_truths, seed_graph
from ripple.services.authoring import (
    create_script,
    delete_scene,
    delete_unit,
    export_fountain,
    insert_unit,
    rename_script,
    save_unit_text,
    settle_cues,
)
from ripple.services.changeset import InvalidOperation, undo_latest
from ripple.services.scenes import insert_scene

DEMO_SCRIPTS = Path(__file__).resolve().parent.parent / "demo-scripts"


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


@pytest.fixture
def seeded(session) -> Script:
    path = DEMO_SCRIPTS / "01-night-freight/night-freight.fountain"
    result = import_screenplay(path.read_bytes(), path.name)
    assert result.accepted
    script = persist_import(session, result)
    truth = next(t for t in ground_truths(DEMO_SCRIPTS) if "FREIGHT" in t.title)
    seed_graph(session, script, truth)
    return script


@pytest.fixture
def draft(session) -> Script:
    """A script written from nothing, with one scene to compose into."""
    script = create_script(session, "The Understudy Returns")
    insert_scene(session, script.id, "INT. THEATRE - NIGHT", "The stage is dark.")
    return script


def units_of(session, script) -> list[ScriptUnit]:
    scene = session.scalar(select(Scene).where(Scene.script_id == script.id))
    return list(
        session.scalars(
            select(ScriptUnit)
            .where(ScriptUnit.scene_id == scene.id)
            .order_by(ScriptUnit.sequence_index)
        )
    )


class TestCreation:
    def test_a_created_script_is_empty_and_accepted(self, session):
        script = create_script(session, "  Night   Shift  ")
        assert script.title == "Night Shift"
        assert script.import_status == "accepted"
        assert script.graph_status == "not_analysed"
        assert session.scalar(select(Scene).where(Scene.script_id == script.id)) is None
        record = session.scalar(
            select(Import).where(Import.script_id == script.id)
        )
        assert record.adapter_name == "authored"
        assert record.detected_format == "fountain"

    def test_a_blank_title_is_refused(self, session):
        with pytest.raises(InvalidOperation):
            create_script(session, "   ")

    def test_an_overlong_title_is_refused(self, session):
        with pytest.raises(InvalidOperation):
            create_script(session, "x" * 300)

    def test_rename_keeps_the_id(self, session):
        script = create_script(session, "Working Title")
        renamed = rename_script(session, script.id, "Final Title")
        assert renamed.id == script.id
        assert renamed.title == "Final Title"

    def test_rename_refuses_a_blank_title(self, session):
        script = create_script(session, "Working Title")
        with pytest.raises(InvalidOperation):
            rename_script(session, script.id, "")


class TestComposition:
    def test_lines_type_by_their_text(self, session, draft):
        units = units_of(session, draft)
        last = units[-1]
        scene = session.get(Scene, last.scene_id)
        cue = insert_unit(session, scene.id, last.id, "MARLA")
        line = insert_unit(session, scene.id, cue.unit_id, "Places, everyone.")
        aside = insert_unit(session, scene.id, line.unit_id, "(whispering)")
        more = insert_unit(session, scene.id, aside.unit_id, "Not you.")
        act = insert_unit(session, scene.id, more.unit_id, "The curtain rises.")
        cut = insert_unit(session, scene.id, act.unit_id, "SMASH CUT TO:")
        assert cue.unit_type == "character" and cue.speaker_name == "MARLA"
        assert line.unit_type == "dialogue" and line.speaker_name == "MARLA"
        assert aside.unit_type == "parenthetical"
        assert more.unit_type == "dialogue" and more.speaker_name == "MARLA"
        assert act.unit_type == "action" and act.speaker_name is None
        assert cut.unit_type == "transition"

    def test_insertion_renumbers_and_bumps_the_version(self, session, draft):
        units = units_of(session, draft)
        before = session.get(Script, draft.id).current_version
        inserted = insert_unit(session, units[0].scene_id, units[0].id, "A cough.")
        after = units_of(session, draft)
        assert [u.sequence_index for u in after] == list(range(len(after)))
        assert after[1].id == uuid.UUID(inserted.unit_id)
        assert session.get(Script, draft.id).current_version == before + 1

    def test_a_typed_scene_heading_is_refused(self, session, draft):
        units = units_of(session, draft)
        with pytest.raises(InvalidOperation):
            insert_unit(session, units[0].scene_id, units[0].id, "INT. LOBBY - DAY")

    def test_direct_save_is_an_accepted_change_set_with_undo(self, session, draft):
        unit = units_of(session, draft)[-1]
        saved = save_unit_text(session, unit.id, "The stage is bright.")
        assert session.get(ScriptUnit, unit.id).current_text == "The stage is bright."
        change = session.get(ChangeSet, uuid.UUID(saved["change_set_id"]))
        assert change.kind == "direct_save"
        assert change.status == "accepted"
        undo_latest(session, unit.id)
        assert session.get(ScriptUnit, unit.id).current_text == "The stage is dark."

    def test_saving_the_same_text_writes_nothing(self, session, draft):
        unit = units_of(session, draft)[-1]
        saved = save_unit_text(session, unit.id, unit.current_text)
        assert saved["change_set_id"] is None
        assert session.scalar(select(ChangeSet)) is None

    def test_a_cited_line_refuses_direct_save(self, session, seeded):
        unit = session.scalar(
            select(ScriptUnit).where(
                ScriptUnit.current_text.contains("blue sedan")
            )
        )
        with pytest.raises(InvalidOperation, match="See ripple"):
            save_unit_text(session, unit.id, "A red sedan idles.")

    def test_a_cited_line_refuses_deletion(self, session, seeded):
        unit = session.scalar(
            select(ScriptUnit).where(
                ScriptUnit.current_text.contains("blue sedan")
            )
        )
        with pytest.raises(InvalidOperation, match="omit the scene"):
            delete_unit(session, unit.id)

    def test_an_uncited_line_deletes_and_bumps_the_version(self, session, draft):
        unit = units_of(session, draft)[-1]
        before = session.get(Script, draft.id).current_version
        delete_unit(session, unit.id)
        assert session.get(ScriptUnit, unit.id) is None
        assert session.get(Script, draft.id).current_version == before + 1

    def test_a_scene_heading_unit_refuses_deletion(self, session, draft):
        heading = units_of(session, draft)[0]
        assert heading.unit_type == "scene_heading"
        with pytest.raises(InvalidOperation, match=r"[Oo]mit"):
            delete_unit(session, heading.id)


class TestCorrection:
    def test_tab_override_wins_and_finds_the_speaker(self, session, draft):
        """A speech interrupted by an action line: the writer corrects the
        next line to dialogue, and the speaker is the cue above the
        interruption, not the interruption."""
        units = units_of(session, draft)
        scene = session.get(Scene, units[0].scene_id)
        cue = insert_unit(session, scene.id, units[-1].id, "MARLA")
        opening = insert_unit(session, scene.id, cue.unit_id, "How did we get here?")
        beat = insert_unit(
            session, scene.id, opening.unit_id, "She opens the door. A pause."
        )
        assert beat.unit_type == "action"
        reply = insert_unit(
            session, scene.id, beat.unit_id, "And how do we leave?",
            unit_type="dialogue",
        )
        assert reply.unit_type == "dialogue"
        assert reply.speaker_name == "MARLA"

    def test_an_unknown_override_is_refused(self, session, draft):
        unit = units_of(session, draft)[-1]
        with pytest.raises(InvalidOperation):
            insert_unit(
                session, unit.scene_id, unit.id, "whatever", unit_type="scene_heading"
            )

    def test_every_marker_states_its_element(self, session, draft):
        """One marker per element, consumed on save, so the stored words are
        the words alone. Six are Fountain's own; the quote and the doubled
        arrow fill the two elements Fountain leaves to position."""
        units = units_of(session, draft)
        scene = session.get(Scene, units[0].scene_id)
        after = units[-1].id
        written = []
        for text in ('@Mary', '"Get out.', '(quietly)', '> CUT TO:',
                     '>>ANGLE ON THE KEY', '!THE DOOR SLAMS.', '[[check this]]'):
            composed = insert_unit(session, scene.id, after, text)
            written.append((composed.unit_type, composed.text))
            after = composed.unit_id
        assert written == [
            ("character", "MARY"),
            ("dialogue", "Get out."),
            ("parenthetical", "(quietly)"),
            ("transition", "CUT TO:"),
            ("shot", "ANGLE ON THE KEY"),
            ("action", "THE DOOR SLAMS."),
            ("note", "check this"),
        ]

    def test_a_marked_speech_still_belongs_to_its_speaker(self, session, draft):
        """A quote states dialogue; the speaker comes from the cue above,
        across the action line that interrupted the speech."""
        units = units_of(session, draft)
        scene = session.get(Scene, units[0].scene_id)
        cue = insert_unit(session, scene.id, units[-1].id, "MARLA")
        beat = insert_unit(session, scene.id, cue.unit_id, "She turns away.")
        reply = insert_unit(session, scene.id, beat.unit_id, '"And now?')
        assert (reply.unit_type, reply.speaker_name) == ("dialogue", "MARLA")

    def test_a_shot_is_not_read_as_a_speaker(self, session, draft):
        """A shot arrives in capitals like a cue, so the camera words
        separate them: CLOSE ON is a shot, MARLA is a character."""
        units = units_of(session, draft)
        scene = session.get(Scene, units[0].scene_id)
        shot = insert_unit(session, scene.id, units[-1].id, "CLOSE ON THE KEY")
        assert (shot.unit_type, shot.speaker_name) == ("shot", None)

    def test_force_markers_type_the_line(self, session, draft):
        units = units_of(session, draft)
        scene = session.get(Scene, units[0].scene_id)
        cue = insert_unit(session, scene.id, units[-1].id, "@Mrs. Elvsted")
        shout = insert_unit(session, scene.id, cue.unit_id, "!STAY BACK.")
        cut = insert_unit(session, scene.id, shout.unit_id, "> fade out")
        assert cue.unit_type == "character"
        assert cue.speaker_name == "MRS. ELVSTED"
        assert shout.unit_type == "action" and "!" not in "STAY BACK."
        assert cut.unit_type == "transition"


class TestSoundLines:
    """An all-caps line reads like a speaker on the page, so the decision
    waits for the evidence: a cue with nothing speaking under it was a
    sound, a shout, or a line of emphasis, and belongs in action."""

    def _scene(self, session, draft) -> Scene:
        return session.get(Scene, units_of(session, draft)[0].scene_id)

    def test_a_terminated_sound_is_action_at_once(self, session, draft):
        """A name never ends in terminal punctuation, so the shape alone
        settles "BAM!" and "WHAT?" without waiting."""
        scene = self._scene(session, draft)
        after = units_of(session, draft)[-1].id
        for text in ("BAM!", "CRASH!", "WHAT?"):
            composed = insert_unit(session, scene.id, after, text)
            assert composed.unit_type == "action", text
            after = composed.unit_id

    def test_a_bare_sound_word_settles_when_nothing_speaks(self, session, draft):
        """BAM reads as a cue on its own; the action line under it proves
        otherwise, and both end up as action."""
        scene = self._scene(session, draft)
        bam = insert_unit(session, scene.id, units_of(session, draft)[-1].id, "BAM")
        assert bam.unit_type == "character"
        written = insert_unit(
            session, scene.id, bam.unit_id, "!The door flies open."
        )
        assert written.demoted == [bam.unit_id]
        settled = session.get(ScriptUnit, uuid.UUID(bam.unit_id))
        assert (settled.unit_type, settled.speaker_name) == ("action", None)
        assert settled.current_text == "BAM"

    def test_a_cue_with_a_speech_is_left_alone(self, session, draft):
        scene = self._scene(session, draft)
        cue = insert_unit(session, scene.id, units_of(session, draft)[-1].id, "MARY")
        spoke = insert_unit(session, scene.id, cue.unit_id, "Who's there?")
        assert spoke.demoted == []
        assert session.get(ScriptUnit, uuid.UUID(cue.unit_id)).unit_type == "character"

    def test_the_last_cue_waits_until_the_scene_is_left(self, session, draft):
        """The writer is still under it, so a trailing cue stands until the
        scene closes behind them."""
        scene = self._scene(session, draft)
        cue = insert_unit(session, scene.id, units_of(session, draft)[-1].id, "THUNDER")
        assert session.get(ScriptUnit, uuid.UUID(cue.unit_id)).unit_type == "character"
        assert settle_cues(session, scene) == []
        assert settle_cues(session, scene, include_last=True) == [cue.unit_id]
        assert session.get(ScriptUnit, uuid.UUID(cue.unit_id)).unit_type == "action"

    def test_a_cue_the_graph_cites_is_never_retyped(self, session, seeded):
        scene = session.scalar(
            select(Scene).where(Scene.script_id == seeded.id)
        )
        before = [
            u.unit_type
            for u in session.scalars(
                select(ScriptUnit).where(ScriptUnit.scene_id == scene.id)
            )
        ]
        settle_cues(session, scene, include_last=True)
        after = [
            u.unit_type
            for u in session.scalars(
                select(ScriptUnit).where(ScriptUnit.scene_id == scene.id)
            )
        ]
        assert before == after


class TestSceneRemoval:
    def test_a_written_scene_is_removed_whole(self, session, draft):
        """The inverse of writing a heading, which is what undo needs."""
        def script_scenes():
            return session.scalars(
                select(Scene).where(Scene.script_id == draft.id)
            ).all()
        second = insert_scene(
            session, draft.id, "EXT. STREET - NIGHT", "Rain.",
            after_scene_id=script_scenes()[0].id,
        )
        assert len(script_scenes()) == 2
        delete_scene(session, second.scene_id)
        assert len(script_scenes()) == 1
        assert [scene.sequence_index for scene in script_scenes()] == [0]

    def test_a_scene_the_graph_cites_is_kept(self, session, seeded):
        scene = session.scalar(
            select(Scene).where(Scene.script_id == seeded.id)
        )
        with pytest.raises(InvalidOperation, match=r"[Oo]mit"):
            delete_scene(session, scene.id)


class TestExport:
    def test_a_written_script_round_trips_through_fountain(self, session, draft):
        units = units_of(session, draft)
        scene = session.get(Scene, units[0].scene_id)
        cue = insert_unit(session, scene.id, units[-1].id, "MARLA")
        line = insert_unit(session, scene.id, cue.unit_id, "Places, everyone.")
        insert_unit(session, scene.id, line.unit_id, "SMASH CUT TO:")
        text = export_fountain(session, draft.id)
        assert text.startswith("Title: The Understudy Returns")

        result = import_screenplay(text.encode(), "round-trip.fountain")
        assert result.accepted, result.rejection_message
        assert result.scene_count == 1
        typed = [
            (u.unit_type.value, u.text)
            for u in result.scenes[0].units
            if u.unit_type.value != "scene_heading"
        ]
        assert typed == [
            ("action", "The stage is dark."),
            ("character", "MARLA"),
            ("dialogue", "Places, everyone."),
            ("transition", "SMASH CUT TO:"),
        ]

    def test_every_written_element_survives_the_round_trip(self, session, draft):
        """A shot and a note are the two elements Fountain leaves to
        position, so the export has to hand the importer enough to read
        them back as themselves."""
        units = units_of(session, draft)
        scene = session.get(Scene, units[0].scene_id)
        after = units[-1].id
        for text in ('@Mary', '"Get out.', '(quietly)', '>>CLOSE ON THE KEY',
                     '> CUT TO:', '[[check the timing]]'):
            after = insert_unit(session, scene.id, after, text).unit_id

        result = import_screenplay(
            export_fountain(session, draft.id).encode(), "again.fountain"
        )
        assert result.accepted, result.rejection_message
        typed = [
            (u.unit_type.value, u.text)
            for u in result.scenes[0].units
            if u.unit_type.value != "scene_heading"
        ]
        assert typed == [
            ("action", "The stage is dark."),
            ("character", "MARY"),
            ("dialogue", "Get out."),
            ("parenthetical", "(quietly)"),
            ("shot", "CLOSE ON THE KEY"),
            ("transition", "CUT TO:"),
            ("note", "check the timing"),
        ]

    def test_an_imported_script_exports_every_scene(self, session, seeded):
        text = export_fountain(session, seeded.id)
        scenes = session.scalars(
            select(Scene).where(Scene.script_id == seeded.id)
        ).all()
        for scene in scenes:
            assert scene.heading.split(" - ")[0] in text
