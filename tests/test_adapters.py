"""Per-format parsing against the rendered demo corpus.

Every assertion here runs over files the renderer produced from one authored
Fountain source, so a difference between formats is the adapter's doing.
"""

from __future__ import annotations

import pytest

from ripple.adapters import DetectedFormat, ImportOutcome, UnitType, import_screenplay
from ripple.adapters.base import SourcePayload, parse_character_cue
from ripple.adapters.detect import detect_format
from ripple.adapters.stageplay import StagePlayAdapter


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

    def test_archer_style_act_headings_parse(self):
        """Archer's Ibsen translations head each act "ACT FIRST." with the
        ordinal after the word and a full stop after that; Hedda Gabler on
        Project Gutenberg was refused as having no scenes."""
        play = (
            "Title: Hedda Test\n\n"
            "ACT FIRST.\n\n"
            "  A spacious, handsome, and tastefully furnished drawing room.\n\n"
            "MISS TESMAN.\n[Stops inside the door.] Why, I don't believe they "
            "are stirring yet!\n\n"
            "BERTA.\nThat's what I said, Miss.\n\n"
            "ACT SECOND.\n\n"
            "  The room at the Tesmans' as in the first Act.\n\n"
            "HEDDA.\nGood afternoon, Judge Brack.\n\n"
            "BRACK.\nMy dear Hedda.\n\n"
            "ACT THIRD.\n\n"
            "  The room at the Tesmans'. The curtains are drawn.\n\n"
            "MRS. ELVSTED.\nNot yet!\n\n"
            "HEDDA.\nGo to sleep.\n\n"
        )
        result = import_screenplay(play.encode(), "hedda.txt")
        assert result.accepted, result.rejection_reason
        assert [s.display_scene_number for s in result.scenes] == ["1", "2", "3"]
        assert result.scenes[0].heading.startswith("A spacious, handsome")
        assert result.scenes[1].heading.startswith("The room at the Tesmans'")

    def test_a_blank_line_between_cue_and_speech_keeps_the_speech(self):
        """Archer's Ibsen sets a blank line between every cue and its speech.
        The speech is dialogue by that speaker, and the blank after the
        speech still closes it so the next cue is read as a cue."""
        play = (
            "Title: Spaced\n\nACT FIRST.\n\n"
            "  A drawing room.\n\n"
            "HEDDA.\n\n"
            "[Holds out her hand.] Good morning, dear Miss Tesman!\n"
            "That is kind of you.\n\n\n"
            "MISS TESMAN.\n\n"
            "Well--has the bride slept well?\n\n"
            "ACT SECOND.\n\n  The same room.\n\n"
            "HEDDA.\n\nOh yes, thanks.\n\n"
            "TESMAN.\n\nWhat a lot of flowers.\n\n"
            "ACT THIRD.\n\n  The same room, curtains drawn.\n\n"
            "BRACK.\n\nMy dear Hedda.\n\n"
        )
        result = import_screenplay(play.encode(), "spaced.txt")
        assert result.accepted, result.rejection_message
        units = [(u.unit_type.value, u.speaker_name) for u in result.scenes[0].units]
        assert units[1:] == [
            ("character", "HEDDA"),
            ("dialogue", "HEDDA"),
            ("character", "MISS TESMAN"),
            ("dialogue", "MISS TESMAN"),
        ]
        # Hedda's speech wraps over two printed lines and is one speech.
        assert result.scenes[0].units[2].text == (
            "[Holds out her hand.] Good morning, dear Miss Tesman! "
            "That is kind of you."
        )
        # The whole play reads as speech, not stage direction.
        types = [u.unit_type.value for scene in result.scenes for u in scene.units]
        assert types.count("dialogue") == 5
        assert types.count("action") == 0

    def test_a_contents_listing_is_skipped_as_a_block(self):
        """A printed play lists its acts and scenes before it prints them,
        and every line of that listing reads as a heading. The listing opens
        on the word Contents and runs while its lines are headings or the
        short all-caps names printed beside them, so an entry never opens a
        scene and never absorbs the play that follows it."""
        play = (
            "Title: Listed\n\n"
            "Contents\n\n"
            "THE PROLOGUE.\n\n"
            "ACT I\n"
            "Scene I. A public place.\n"
            "Scene II. A Street.\n\n"
            "ACT II\n"
            "CHORUS.\n"
            "Scene I. A garden.\n\n"
            "  Dramatis Personae\n\n"
            "ROMEO, son to Montague.\n"
            "JULIET, daughter to Capulet.\n\n"
            "ACT I\n\nSCENE I. A public place.\n\n"
            "ROMEO.\n\nA plague on both your houses.\n\n"
            "ACT II\n\nSCENE I. A garden.\n\n"
            "JULIET.\n\nWherefore art thou.\n\n"
        )
        result = import_screenplay(play.encode(), "listed.txt")
        assert result.accepted, result.rejection_message
        assert [scene.heading for scene in result.scenes] == [
            "A public place",
            "A garden",
        ]
        # The listing's own ACT II never opened a scene holding the CHORUS
        # line under it, and no cast-list name became a speaker.
        speakers = {
            u.speaker_name
            for scene in result.scenes
            for u in scene.units
            if u.unit_type.value == "character"
        }
        assert speakers == {"ROMEO", "JULIET"}
        assert not [w for w in result.warnings if w.code == "contents_entries_dropped"]

    def test_a_wrapped_paragraph_is_one_unit(self):
        """A printed play hard-wraps at about seventy characters, so a speech
        or a direction arrives as several physical lines with no blank
        between them. Those are one paragraph: a unit is what extraction
        reads, what an assertion cites, and what a person edits, so a unit
        ending mid-sentence makes all three worse. The blank line before the
        next cue still ends the paragraph."""
        play = (
            "Title: Wrapped\n\nACT I\n\n"
            "  A drawing room.\n\n"
            "MISS TESMAN.\n\n"
            "Yes, indeed it is. Only think, Berta--some foreign university\n"
            "has made him a doctor--while he has been abroad, you\n"
            "understand. I hadn't heard a word about it.\n\n"
            "[She goes to the glass door, throws it open, and stands\n"
            "looking out at the autumn foliage.]\n\n"
            "BERTA.\n\n"
            "Well well, he's clever enough for anything.\n\n"
            "ACT II\n\n  A garden.\n\nHEDDA.\n\nAnd then?\n\n"
            "ACT III\n\n  A road.\n\nBRACK.\n\nNothing.\n\n"
        )
        result = import_screenplay(play.encode(), "wrapped.txt")
        assert result.accepted, result.rejection_message
        first = result.scenes[0].units
        assert [(u.unit_type.value, u.speaker_name) for u in first] == [
            ("scene_heading", None),
            ("character", "MISS TESMAN"),
            ("dialogue", "MISS TESMAN"),
            ("action", None),
            ("character", "BERTA"),
            ("dialogue", "BERTA"),
        ]
        # Three printed lines, one speech, ending on its own full stop.
        assert first[2].text == (
            "Yes, indeed it is. Only think, Berta--some foreign university "
            "has made him a doctor--while he has been abroad, you "
            "understand. I hadn't heard a word about it."
        )
        # A direction wraps the same way and joins the same way.
        assert first[3].text == (
            "[She goes to the glass door, throws it open, and stands "
            "looking out at the autumn foliage.]"
        )
        # The anchor covers the whole paragraph, not its first line alone.
        anchor = first[2].anchor
        assert play[anchor.start_offset : anchor.end_offset].count("\n") == 3

    def test_a_wrapped_sentence_ending_in_scene_is_not_a_heading(self):
        """A speech that wraps so the word "scene." opens a line is speech.
        Hedda Gabler's third act split in two on such a line."""
        play = (
            "Title: Wrapped\n\nACT I\n\n"
            "  A drawing room.\n\n"
            "BRACK.\nFortunately the police at last appeared on the\n"
            "scene.\n\n"
            "HEDDA.\nAnd then?\n\n"
            "SCENE II.\n\n"
            "  A garden.\n\n"
            "BRACK.\nThen they went home.\n\n"
            "HEDDA.\nAll of them?\n\n"
            "ACT II\n\n  The garden again.\n\n"
            "BRACK.\nEvery one.\n\n"
        )
        result = import_screenplay(play.encode(), "wrapped.txt")
        assert result.accepted, result.rejection_message
        assert [scene.heading for scene in result.scenes] == [
            "A drawing room",
            "A garden",
            "The garden again",
        ]
        first = [
            u.text
            for u in result.scenes[0].units
            if u.unit_type.value == "dialogue"
        ]
        assert "Fortunately the police at last appeared on the scene." in first

    def test_a_title_abbreviation_does_not_end_the_cue(self):
        """"MRS. ELVSTED." is one cue; the Shaw form "MRS. PEARCE. Certainly."
        is that cue and its speech. Hedda Gabler came in with 209 speeches
        by a character called MRS."""
        from ripple.adapters.base import inline_cue_split

        assert inline_cue_split("MRS. ELVSTED.") is None
        assert inline_cue_split("DR. RANK.") is None
        assert inline_cue_split("MRS. PEARCE. Certainly, sir.") == (
            "MRS. PEARCE",
            "Certainly, sir.",
        )
        assert inline_cue_split("HIGGINS. Nonsense!") == ("HIGGINS", "Nonsense!")
        play = (
            "Title: Titles\n\nACT I\n\n  A room.\n\n"
            "MRS. ELVSTED.\n\nNot yet!\n\n"
            "HEDDA.\n\nGo to sleep.\n\n"
            "ACT II\n\n  The same room.\n\n"
            "MRS. PEARCE. Certainly, sir.\n\n"
            "HIGGINS. Nonsense!\n\n"
            "ACT III\n\n  A garden.\n\n"
            "DR. RANK.\n\nGood evening.\n\n"
        )
        result = import_screenplay(play.encode(), "titles.txt")
        assert result.accepted, result.rejection_message
        speakers = [
            u.speaker_name
            for scene in result.scenes
            for u in scene.units
            if u.unit_type.value == "character"
        ]
        assert speakers == ["MRS. ELVSTED", "HEDDA", "MRS. PEARCE", "HIGGINS", "DR. RANK"]
        speeches = [
            u.text
            for scene in result.scenes
            for u in scene.units
            if u.unit_type.value == "dialogue"
        ]
        assert speeches == [
            "Not yet!",
            "Go to sleep.",
            "Certainly, sir.",
            "Nonsense!",
            "Good evening.",
        ]

    def test_a_wrapped_bracketed_direction_stays_a_direction(self):
        """A stage direction that wraps past its opening bracket is still a
        direction on its continuation lines, so a capitalised name opening
        one ("ELVSTED.  HEDDA lies...") is not read as a cue."""
        play = (
            "Title: Wrapped\n\nACT I\n\n  A room.\n\n"
            "TESMAN.\n\nShot herself!\n\n"
            "   [He throws back the curtains and runs in, followed by MRS.\n"
            "       ELVSTED.  HEDDA lies stretched on the sofa, lifeless.\n"
            "       Confusion and cries.  BERTA enters in alarm from the right.]\n\n"
            "BRACK.\n\nGood God!--people don't do such things.\n\n"
            "ACT II\n\n  A garden.\n\n"
            "HEDDA.\n\nAnd then?\n\n"
            "ACT III\n\n  A road.\n\n"
            "BERTA.\n\nNothing.\n\n"
            "THE END\n\n"
        )
        result = import_screenplay(play.encode(), "wrapped.txt")
        assert result.accepted, result.rejection_message
        speakers = {
            u.speaker_name
            for scene in result.scenes
            for u in scene.units
            if u.unit_type.value == "character"
        }
        assert speakers == {"TESMAN", "BRACK", "HEDDA", "BERTA"}
        first = result.scenes[0].units
        directions = [u.text for u in first if u.unit_type.value == "action"]
        # The three printed lines are one direction, so the capitalised name
        # opening a continuation line never stands as a unit of its own.
        assert len(directions) == 1
        assert directions[0].startswith("[He throws back the curtains")
        assert "ELVSTED.  HEDDA lies stretched on the sofa" in directions[0]
        assert directions[0].endswith("BERTA enters in alarm from the right.]")
        # The speech after the direction still belongs to its cue.
        brack = [u for u in first if u.speaker_name == "BRACK"]
        assert [u.unit_type.value for u in brack] == ["character", "dialogue"]

    def test_the_end_closes_the_play(self):
        """Footnotes and a transcriber's note after THE END are not lines of
        the play: "FOOTNOTES." had become a speaker."""
        play = (
            "Title: Ended\n\nACT I\n\n  A room.\n\n"
            "TESMAN.\n\nShot herself!\n\n"
            "ACT II\n\n  A garden.\n\n"
            "HEDDA.\n\nAnd then?\n\n"
            "ACT III\n\n  A road.\n\n"
            "BERTA.\n\nNothing.\n\n"
            "BRACK.\n\nGood God!--people don't do such things.\n\n"
            "THE END\n\n"
            "FOOTNOTES.\n\n(1) Pronounce Reena.\n\n"
            "TRANSCRIBER'S NOTE.\n\nThe text is as printed.\n"
        )
        result = import_screenplay(play.encode(), "ended.txt")
        assert result.accepted, result.rejection_message
        every_text = " ".join(u.text for sc in result.scenes for u in sc.units)
        assert "FOOTNOTES" not in every_text
        assert "Pronounce Reena" not in every_text
        assert "TRANSCRIBER" not in every_text
        assert result.scenes[-1].units[-1].text.startswith("Good God!")

    def test_an_unclosed_bracket_direction_ends_at_the_blank_line(self):
        """Archer's edition never closes a standalone direction's bracket.
        The next paragraph's cue is still a cue."""
        play = (
            "Title: Unclosed\n\nACT I\n\n  A room.\n\n"
            "BERTA.\n\nYes, Miss.\n\n"
            "[She goes to the glass door and throws it open.\n\n"
            "HEDDA.\n\nGood morning.\n\n"
            "ACT II\n\n  A garden.\n\n"
            "HEDDA.\n\nAnd then?\n\n"
            "ACT III\n\n  A road.\n\n"
            "BERTA.\n\nNothing.\n\n"
        )
        result = import_screenplay(play.encode(), "unclosed.txt")
        assert result.accepted, result.rejection_message
        first = result.scenes[0].units
        assert [(u.unit_type.value, u.speaker_name) for u in first[1:]] == [
            ("character", "BERTA"),
            ("dialogue", "BERTA"),
            ("action", None),
            ("character", "HEDDA"),
            ("dialogue", "HEDDA"),
        ]

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


class TestFrenchScenes:
    """An act that prints no numbered scenes is still played in scenes: the
    stage changes when someone comes on or goes off. Left whole, such an act
    is one scene of several hundred units, and the graph, the reader, the
    continuity findings and the one extraction call a scene gets all work at
    the wrong grain."""

    @staticmethod
    def _play(entrances: int, speeches_each: int) -> str:
        """A three-act play whose first act runs long. Detection needs three
        act headings before it reads the text as a play at all."""
        play = ["Title: Split Test\n", "\nACT I\n", "\nA room in the Castle.\n"]
        for index in range(entrances):
            if index:
                play.append(f"\nEnter GUEST {index}.\n")
            for line in range(speeches_each):
                play.append(f"\nHOST.\nLine {index}-{line}.\n")
        for act in ("ACT II", "ACT III"):
            play.append(f"\n{act}\n\nThe same room.\n\nHOST.\nAnd so on.\n")
        return "".join(play)

    def test_an_act_splits_at_its_entrances(self):
        result = import_screenplay(self._play(4, 12).encode(), "play.txt")
        assert result.accepted
        assert [s.display_scene_number for s in result.scenes][:4] == [
            "1.1",
            "1.2",
            "1.3",
            "1.4",
        ]
        # The place does not change when the company does, so every part
        # keeps the act's heading and a location still reads out of it.
        assert {s.heading for s in result.scenes[:4]} == {"A room in the Castle"}
        # The two short acts are untouched.
        assert [s.display_scene_number for s in result.scenes][4:] == ["2", "3"]

    def test_a_short_act_is_left_whole(self):
        """The split buys nothing on a scene the model reads in one call."""
        result = import_screenplay(self._play(3, 3).encode(), "play.txt")
        assert [s.display_scene_number for s in result.scenes] == ["1", "2", "3"]

    def test_a_flurry_of_entrances_makes_no_two_line_scenes(self):
        """An entrance inside a run of them carries on the scene in hand."""
        from ripple.adapters.stageplay import MIN_UNITS_PER_FRENCH_SCENE

        result = import_screenplay(self._play(20, 2).encode(), "play.txt")
        assert result.accepted
        split = [s for s in result.scenes if str(s.display_scene_number).startswith("1")]
        assert len(split) > 1
        assert all(len(scene.units) >= MIN_UNITS_PER_FRENCH_SCENE for scene in split)

    def test_a_declared_scene_is_never_resplit(self):
        """A play that prints SCENE headings has already said where its
        scenes are; an Enter line inside one does not open another."""
        play = (
            "Title: Numbered\n\nACT I\n\nSCENE I. A platform.\n\n"
            + "".join(f"\nGUARD.\nLine {n}.\n" for n in range(30))
            + "\nEnter HAMLET.\n"
            + "".join(f"\nGUARD.\nLine {n}.\n" for n in range(30, 60))
            + "\nSCENE II. A hall.\n\nGUARD.\nDone.\n"
        )
        result = import_screenplay(play.encode(), "play.txt")
        assert result.accepted
        assert [s.display_scene_number for s in result.scenes] == ["1.1", "1.2"]


class TestUnitCap:
    def test_a_full_act_fits_under_the_unit_ceiling(self):
        """A stage play's acts are its scenes; the ceiling is sized for an
        act of Chekhov (1,475 units in the corpus), not a screenplay scene."""
        from ripple.adapters.base import MAX_UNITS_PER_SCENE

        assert MAX_UNITS_PER_SCENE >= 1500


class TestStagePlayFrontMatter:
    """A printed play opens with its contents and its cast list. Neither is a
    scene, and read as one they put phantom scenes in the reader and a roster
    of extras in the graph."""

    PLAY = (
        "ACT I\n"
        " Scene I. A platform before the Castle\n"
        " Scene II. A room of state\n"
        "\n"
        "Dramatis Personae\n"
        "\n"
        "HAMLET, Prince of Denmark\n"
        "Lords, Ladies, Officers, Soldiers, and Attendants\n"
        "\n"
        "ACT I\n"
        "SCENE I. A platform before the Castle.\n"
        "\n"
        "FRANCISCO is on watch.\n"
        "\n"
        "BARNARDO.\n"
        "Who's there?\n"
        "\n"
        "SCENE II. A room of state.\n"
        "\n"
        "The court assembles.\n"
        "\n"
        "CLAUDIUS.\n"
        "Though yet of Hamlet our dear brother's death.\n"
    )

    def _parse(self):
        return StagePlayAdapter().parse(
            SourcePayload(data=self.PLAY.encode(), suggested_name="play.txt")
        )

    def test_contents_headings_are_dropped(self):
        scenes, warnings = self._parse()
        assert len(scenes) == 2
        assert [scene.sequence_index for scene in scenes] == [0, 1]
        assert any(w.code == "contents_entries_dropped" for w in warnings)

    def test_the_cast_list_is_not_read_as_a_scene(self):
        scenes, _ = self._parse()
        text = " ".join(unit.text for scene in scenes for unit in scene.units)
        assert "Dramatis Personae" not in text
        assert "Lords, Ladies" not in text

    def test_an_italicised_cue_keeps_one_name(self):
        assert parse_character_cue("HAMLET._") == ("HAMLET", None, False)
        assert parse_character_cue("HAMLET.") == ("HAMLET", None, False)
