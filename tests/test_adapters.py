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
