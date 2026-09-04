"""Per-format parsing against the rendered demo corpus.

Every assertion here runs over files the renderer produced from one authored
Fountain source, so a difference between formats is the adapter's doing.
"""

from __future__ import annotations

import pytest

from ripple.adapters import DetectedFormat, ImportOutcome, UnitType, import_screenplay
from ripple.adapters.base import SourcePayload
from ripple.adapters.detect import detect_format


def _units(result, unit_type: UnitType) -> list:
    return [
        unit
        for scene in result.scenes
        for unit in scene.units
        if unit.unit_type is unit_type
    ]


class TestDetection:
    @pytest.mark.parametrize(
        "suffix, expected",
        [
            ("fountain", DetectedFormat.FOUNTAIN),
            ("fdx", DetectedFormat.FDX),
            ("pdf", DetectedFormat.PDF),
            ("txt", DetectedFormat.PLAIN_TEXT),
        ],
    )
    def test_each_render_is_detected_from_content(self, corpus_stem, suffix, expected):
        from tests.conftest import _read

        data = _read(f"{corpus_stem}.{suffix}")
        # Deliberately misleading name: detection must ignore it.
        payload = SourcePayload(data=data, suggested_name="anonymous.dat")
        detected, confidence = detect_format(payload)
        assert detected is expected
        assert confidence >= 0.5


class TestFountain:
    def test_all_scenes_parse_with_their_numbers(self, night_freight_fountain):
        result = import_screenplay(night_freight_fountain, "night-freight.fountain")
        assert result.accepted
        assert result.scene_count == 44
        numbers = [scene.display_scene_number for scene in result.scenes]
        assert numbers == [str(n) for n in range(1, 45)]

    def test_lettered_revision_scenes_survive(self, understudy_fountain):
        result = import_screenplay(understudy_fountain, "the-understudy.fountain")
        numbers = [scene.display_scene_number for scene in result.scenes]
        assert "11A" in numbers and "30A" in numbers
        assert numbers.index("11A") == numbers.index("11") + 1

    def test_forced_heading_becomes_a_scene(self, understudy_fountain):
        result = import_screenplay(understudy_fountain, "x.fountain")
        headings = [scene.heading for scene in result.scenes]
        assert "MONTAGE - EIGHT PERFORMANCES" in headings

    def test_notes_become_note_units(self, understudy_fountain):
        result = import_screenplay(understudy_fountain, "x.fountain")
        notes = _units(result, UnitType.NOTE)
        assert len(notes) == 1
        assert "burn dressing continuity" in notes[0].text

    def test_dual_dialogue_is_recorded_as_a_warning(self, understudy_fountain):
        result = import_screenplay(understudy_fountain, "x.fountain")
        assert any(w.code == "dual_dialogue" for w in result.warnings)

    def test_character_extensions_do_not_change_the_speaker(
        self, night_freight_fountain
    ):
        result = import_screenplay(night_freight_fountain, "x.fountain")
        speakers = {unit.speaker_name for unit in _units(result, UnitType.CHARACTER)}
        # (O.S.), (V.O.) and (CONT'D) must not create separate speakers.
        assert "MARA" in speakers
        assert not any(name and "(" in name for name in speakers)

    def test_dialogue_carries_the_preceding_speaker(self, night_freight_fountain):
        result = import_screenplay(night_freight_fountain, "x.fountain")
        dialogue = _units(result, UnitType.DIALOGUE)
        assert dialogue
        assert all(unit.speaker_name for unit in dialogue)

    def test_source_offsets_point_at_the_original_text(self, night_freight_fountain):
        result = import_screenplay(night_freight_fountain, "x.fountain")
        source = night_freight_fountain.decode("utf-8")
        scene14 = next(s for s in result.scenes if s.display_scene_number == "14")
        action = next(u for u in scene14.units if u.unit_type is UnitType.ACTION)
        anchor = action.anchor
        assert anchor is not None and anchor.start_offset is not None
        assert source[anchor.start_offset : anchor.end_offset].strip() == action.text

    def test_the_demo_target_line_is_present_and_addressable(
        self, night_freight_fountain
    ):
        """The ripple preview mockup edits this exact unit."""
        result = import_screenplay(night_freight_fountain, "x.fountain")
        scene14 = next(s for s in result.scenes if s.display_scene_number == "14")
        assert scene14.units[1].text.startswith("Rain sheets off the awning.")
        assert "the blue sedan idles by the gate" in scene14.units[1].text


class TestFdx:
    def test_scene_numbers_come_from_the_number_attribute(self, understudy_fdx):
        result = import_screenplay(understudy_fdx, "x.fdx")
        numbers = [scene.display_scene_number for scene in result.scenes]
        assert "11A" in numbers and "30A" in numbers

    def test_paragraph_types_map_to_unit_types(self, fdx_bytes):
        result = import_screenplay(fdx_bytes, "x.fdx")
        present = {unit.unit_type for scene in result.scenes for unit in scene.units}
        assert {
            UnitType.SCENE_HEADING,
            UnitType.ACTION,
            UnitType.CHARACTER,
            UnitType.DIALOGUE,
        } <= present

    def test_block_index_is_recorded_for_provenance(self, fdx_bytes):
        result = import_screenplay(fdx_bytes, "x.fdx")
        anchors = [unit.anchor for scene in result.scenes for unit in scene.units]
        assert all(anchor and anchor.block_index is not None for anchor in anchors)


class TestPlainText:
    def test_indented_text_parses_into_typed_units(self, text_bytes):
        result = import_screenplay(text_bytes, "x.txt")
        assert result.accepted
        present = {unit.unit_type for scene in result.scenes for unit in scene.units}
        assert {
            UnitType.SCENE_HEADING,
            UnitType.ACTION,
            UnitType.CHARACTER,
            UnitType.DIALOGUE,
        } <= present

    def test_flat_text_is_flagged_for_review(self, text_bytes):
        """Strip every indent: structure is now a guess and must be declared."""
        flat = "\n".join(
            line.strip() for line in text_bytes.decode("utf-8").splitlines()
        ).encode()
        result = import_screenplay(flat, "flat.txt")
        assert result.outcome is ImportOutcome.NEEDS_REVIEW
        assert any(w.code == "no_indentation" for w in result.warnings)

    def test_tabs_count_as_indentation(self):
        source = (
            "INT. ROOM - DAY\n\nA chair.\n\n\t\t\tMARA\n\tHello there.\n\n"
            "INT. HALL - DAY\n\nA door.\n\n\t\t\tDEV\n\tAnd you.\n\n"
            "INT. STAIRS - DAY\n\nSteps.\n\n\t\t\tMARA\n\tUp we go.\n"
        )
        result = import_screenplay(source.encode(), "tabs.txt")
        assert result.accepted
        assert _units(result, UnitType.DIALOGUE)


class TestPdf:
    def test_text_layer_pdf_parses(self, pdf_bytes):
        result = import_screenplay(pdf_bytes, "x.pdf")
        assert result.accepted
        assert result.scene_count >= 30

    def test_page_provenance_is_recorded(self, pdf_bytes):
        result = import_screenplay(pdf_bytes, "x.pdf")
        pages = {
            unit.anchor.page_number
            for scene in result.scenes
            for unit in scene.units
            if unit.anchor
        }
        assert pages and all(isinstance(page, int) and page >= 1 for page in pages)
        assert len(pages) > 1

    def test_bounding_boxes_are_recorded(self, pdf_bytes):
        result = import_screenplay(pdf_bytes, "x.pdf")
        boxes = [
            unit.anchor.bounding_box
            for scene in result.scenes
            for unit in scene.units
            if unit.anchor
        ]
        assert boxes and all(box is not None and len(box) == 4 for box in boxes)

    def test_dialogue_is_distinguished_from_action_by_position(self, pdf_bytes):
        result = import_screenplay(pdf_bytes, "x.pdf")
        assert _units(result, UnitType.DIALOGUE)
        assert _units(result, UnitType.CHARACTER)

    def test_ocr_isolates_headings_and_recovers_dialogue(self):
        """OCR flattens indentation and drops the blank line between a slug and
        its action. The adapter isolates the heading and the cue onto their own
        blocks and classifies dialogue by shape, so a clean scan parses into
        numbered scenes with speakers rather than one action blob."""
        from ripple.adapters.base import ParserMethod
        from ripple.adapters.pdf import PdfAdapter, _Line

        def line(text, y):
            return _Line(page=1, x=0.0, y=y, text=text, is_bold=False)

        # Margin numbers on both sides of the slug, no blank between slug and
        # action, a blank (a y-gap) before the cue: the shape OCR produces.
        lines = [
            line("1 INT. LOFT - NIGHT 1", 0),
            line("A steel flask goes into a padded case.", -1),
            line("SURGEON", -21),
            line("Seven minutes of margin. Not eight.", -22),
            line("2 EXT. DOCK - NIGHT 2", -42),
            line("Matias takes the case two-handed.", -43),
        ]
        scenes, _ = PdfAdapter()._blocks_to_scenes(lines, ParserMethod.OCR)
        # Clean headings, with the margin numbers stripped off both sides.
        assert [s.heading for s in scenes] == ["INT. LOFT - NIGHT", "EXT. DOCK - NIGHT"]
        assert scenes[0].display_scene_number == "1"
        types = [u.unit_type for u in scenes[0].units]
        assert UnitType.CHARACTER in types and UnitType.DIALOGUE in types
        cue = next(u for u in scenes[0].units if u.unit_type is UnitType.CHARACTER)
        assert cue.text.strip() == "SURGEON"


_SHAKESPEARE = """The Project Gutenberg eBook of A Test Tragedy

Title: A Test Tragedy

*** START OF THE PROJECT GUTENBERG EBOOK A TEST TRAGEDY ***

Dramatis Personae

  BARNARDO, a sentinel
  HAMLET, Prince of Denmark

ACT I

SCENE I. Elsinore. A platform before the Castle.

Enter BARNARDO and FRANCISCO.

BARNARDO.
Who's there?

FRANCISCO.
Nay, answer me. Stand and unfold yourself.

[_Exit Francisco._]

SCENE II. A room of state in the Castle.

HAMLET.
A little more than kin, and less than kind.

HORATIO.
My lord, I came to see your father's funeral.

ACT II

SCENE I. A room in Polonius's house.

POLONIUS.
Give him this money and these notes, Reynaldo.

HAMLET.
Words, words, words.

[_Exeunt._]

*** END OF THE PROJECT GUTENBERG EBOOK A TEST TRAGEDY ***

Licence boilerplate that must never be parsed as a scene.
"""

_WILDE = """Title: A Test Comedy

FIRST ACT

SCENE

Morning-room in Algernon's flat in Half-Moon Street. The room is furnished.

ALGERNON.
Did you hear what I was playing, Lane?

LANE.
I didn't think it polite to listen, sir.

SECOND ACT

_[SCENE.—A room furnished comfortably and tastefully, but not extravagantly.]_

JACK.
On the contrary, Aunt Augusta, I have now realised.

GWENDOLEN.
I am glad to say I have never seen a spade.

THIRD ACT

SCENE

Morning-room at the Manor House.

CECILY.
They have been eating muffins. That looks like repentance.

ALGERNON.
I am on the verge of a grave decision.
"""


class TestStagePlay:
    """Public-domain plays: act/scene structure, not INT./EXT. sluglines."""

    def test_a_gutenberg_tragedy_is_detected_and_parsed(self):
        result = import_screenplay(_SHAKESPEARE.encode(), "tragedy.txt")
        assert result.accepted
        assert result.detected_format is DetectedFormat.STAGE_PLAY
        assert result.title == "A Test Tragedy"
        assert result.scene_count == 3

    def test_scenes_are_numbered_by_act_and_scene(self):
        result = import_screenplay(_SHAKESPEARE.encode(), "x.txt")
        numbers = [scene.display_scene_number for scene in result.scenes]
        assert numbers == ["1.1", "1.2", "2.1"]

    def test_the_setting_becomes_the_location_heading(self):
        result = import_screenplay(_SHAKESPEARE.encode(), "x.txt")
        assert result.scenes[0].heading == "Elsinore. A platform before the Castle"

    def test_all_caps_cues_name_the_speakers(self):
        result = import_screenplay(_SHAKESPEARE.encode(), "x.txt")
        speakers = {u.speaker_name for u in _units(result, UnitType.CHARACTER)}
        assert {"BARNARDO", "FRANCISCO", "HAMLET", "POLONIUS"} <= speakers

    def test_stage_directions_are_action_not_dialogue(self):
        result = import_screenplay(_SHAKESPEARE.encode(), "x.txt")
        actions = [u.text for u in _units(result, UnitType.ACTION)]
        assert any("Exeunt" in text for text in actions)
        assert any("Exit Francisco" in text for text in actions)

    def test_the_gutenberg_wrapper_and_front_matter_are_dropped(self):
        result = import_screenplay(_SHAKESPEARE.encode(), "x.txt")
        every_text = " ".join(
            u.text for scene in result.scenes for u in scene.units
        )
        assert "Licence boilerplate" not in every_text
        assert "Dramatis Personae" not in every_text

    def test_word_ordinal_acts_and_bare_scenes_parse(self):
        """Wilde names acts in words ('FIRST ACT') and opens with a bare
        'SCENE' whose setting is the prose that follows; Ibsen brackets the
        setting. Both resolve to a clean location heading."""
        result = import_screenplay(_WILDE.encode(), "comedy.txt")
        assert result.accepted
        assert [s.display_scene_number for s in result.scenes] == ["1", "2", "3"]
        assert result.scenes[0].heading.startswith("Morning-room in Algernon's flat")
        # The Ibsen-style bracketed setting loses its brackets and SCENE label.
        assert result.scenes[1].heading == (
            "A room furnished comfortably and tastefully, but not extravagantly"
        )

    def test_inline_cues_split_into_a_speaker_and_a_speech(self):
        """Shaw and Chekhov put the cue and its speech on one line
        ("HIGGINS. Nonsense!", "THE DAUGHTER [chilled] I'm cold."). Each line
        yields a character unit and a dialogue unit; a Title-case setting on the
        same shape is not mistaken for one."""
        play = (
            "Title: Inline\n\nACT I\n\n"
            "A drawing-room in Wimpole Street. Rain outside.\n\n"
            "THE DAUGHTER [in the doorway] I am getting chilled to the bone.\n\n"
            "HIGGINS. Nonsense! You are perfectly warm.\n\n"
            "PICKERING. Right. Shall we go in?\n\n"
            "ACT II\n\n"
            "The same room the next morning.\n\n"
            "HIGGINS. Good morning, Pickering.\n\n"
            "PICKERING. Good morning to you.\n\n"
            "ACT III\n\n"
            "Mrs Higgins's drawing-room that afternoon.\n\n"
            "HIGGINS. Mother, I have made a discovery.\n\n"
            "PICKERING. A remarkable one, indeed.\n"
        )
        result = import_screenplay(play.encode(), "inline.txt")
        assert result.accepted
        speakers = {u.speaker_name for u in _units(result, UnitType.CHARACTER)}
        assert speakers == {"THE DAUGHTER", "HIGGINS", "PICKERING"}
        dialogue = [u.text for u in _units(result, UnitType.DIALOGUE)]
        assert "Nonsense! You are perfectly warm." in dialogue
        # The setting stays the heading, not a speaker.
        assert result.scenes[0].heading == "A drawing-room in Wimpole Street"

    def test_a_long_descriptive_setting_becomes_a_clean_first_sentence(self):
        """Chekhov opens an act with a paragraph of description. The location is
        its first sentence, not a mid-line fragment; a short two-part place
        stays whole (covered by the Shakespeare heading test)."""
        play = (
            "Title: Long Setting\n\nACT I\n\n"
            "A room which is still called the nursery. One of the doors leads "
            "into Anya's room. Dawn, the sun rises during the scene.\n\n"
            "LOPAKHIN. The train's arrived, thank God.\n\n"
            "ACT II\n\nIn a field. An old shrine.\n\n"
            "LOPAKHIN. You must decide.\n\n"
            "ACT III\n\nA drawing-room.\n\n"
            "LOPAKHIN. The estate is sold.\n"
        )
        result = import_screenplay(play.encode(), "long.txt")
        assert result.accepted
        assert result.scenes[0].heading == "A room which is still called the nursery"

    def test_a_screenplay_is_not_misread_as_a_stage_play(self, night_freight_fountain):
        """A real screenplay keeps its slugline format and its own adapter."""
        result = import_screenplay(night_freight_fountain, "night.fountain")
        assert result.detected_format is not DetectedFormat.STAGE_PLAY


class TestCrossFormat:
    """One source, four formats. What must match, and what must not."""

    def test_every_format_accepts_the_same_script(
        self, fountain_bytes, fdx_bytes, pdf_bytes, text_bytes
    ):
        for data, name in (
            (fountain_bytes, "x.fountain"),
            (fdx_bytes, "x.fdx"),
            (pdf_bytes, "x.pdf"),
            (text_bytes, "x.txt"),
        ):
            result = import_screenplay(data, name)
            assert result.accepted, f"{name}: {result.rejection_message}"

    def test_fountain_and_fdx_agree_on_scene_count(self, fountain_bytes, fdx_bytes):
        fountain = import_screenplay(fountain_bytes, "x.fountain")
        fdx = import_screenplay(fdx_bytes, "x.fdx")
        assert fountain.scene_count == fdx.scene_count

    def test_notes_exist_only_in_fountain(self, understudy_fountain, understudy_fdx):
        """A documented divergence: screenplain strips notes from every render.

        Asserting parity here would be wrong. See the corpus dependencies.md.
        """
        fountain = import_screenplay(understudy_fountain, "x.fountain")
        fdx = import_screenplay(understudy_fdx, "x.fdx")
        assert len(_units(fountain, UnitType.NOTE)) == 1
        assert _units(fdx, UnitType.NOTE) == []

    def test_content_hash_is_stable_and_format_specific(self, fountain_bytes):
        first = import_screenplay(fountain_bytes, "a.fountain")
        second = import_screenplay(fountain_bytes, "b.fountain")
        assert first.content_hash == second.content_hash
        assert len(first.content_hash) == 64
