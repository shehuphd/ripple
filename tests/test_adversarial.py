"""Hostile and malformed input.

These run before the happy-path suite because they are the tests that find
bugs. Every case asserts a specific rejection code, not merely "it didn't
crash": a parser that returns an empty result for a hostile file is as broken
as one that raises, and only the code distinguishes them.
"""

from __future__ import annotations

import pytest

from ripple.adapters import ImportOutcome, SourcePayload, import_screenplay
from ripple.adapters.base import (
    MAX_UPLOAD_BYTES,
    ImportRejected,
    ParsedUnit,
    ParserMethod,
    UnitType,
    decode_text,
)
from tests.conftest import BILLION_LAUGHS, INVOICE, PROSE, XXE


class TestPayloadGuards:
    def test_non_bytes_payload_raises_type_error(self):
        with pytest.raises(TypeError, match="must be bytes"):
            SourcePayload(data="a string, not bytes")

    def test_oversized_upload_is_rejected_before_parsing(self):
        result = import_screenplay(b"x" * (MAX_UPLOAD_BYTES + 1), "huge.fountain")
        assert result.outcome is ImportOutcome.REJECTED
        assert result.rejection_code == "payload_too_large"

    @pytest.mark.parametrize(
        "data, label",
        [
            (b"", "empty"),
            (b"   \n\n\t  \n", "whitespace only"),
            (b"\x00\x01\x02\x03\xff\xfe\xdd", "binary garbage"),
            (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "png"),
            (b"PK\x03\x04\x14\x00\x00\x00", "zip"),
        ],
    )
    def test_junk_is_rejected(self, data, label):
        result = import_screenplay(data, f"{label}.fountain")
        assert result.outcome is ImportOutcome.REJECTED, label
        assert result.rejection_code is not None
        assert result.rejection_message

    def test_rejection_never_raises_to_the_caller(self):
        """Every failure path returns a result; none propagates an exception."""
        for data in (b"", b"\x00" * 50, b"<broken", b"%PDF-nonsense"):
            result = import_screenplay(data, "x")
            assert result.outcome is ImportOutcome.REJECTED
            assert result.scene_count == 0


class TestEncoding:
    def test_utf16_fountain_decodes(self, night_freight_fountain: bytes):
        utf16 = night_freight_fountain.decode("utf-8").encode("utf-16")
        result = import_screenplay(utf16, "utf16.fountain")
        assert result.accepted
        assert result.scene_count == 44

    def test_cp1252_smart_quotes_survive(self):
        source = (
            "INT. OFFICE – DAY\n\nShe said “don’t” and left.\n\n"
            "MARA\nDon’t.\n\nINT. HALL - DAY\n\nA door.\n\n"
            "MARA\nStop.\n\nINT. STAIRS - DAY\n\nSteps.\n\nMARA\nNo.\n"
        )
        result = import_screenplay(source.encode("cp1252"), "windows.txt")
        assert result.accepted
        assert "’" in "".join(
            unit.text for scene in result.scenes for unit in scene.units
        )

    def test_undecodable_bytes_are_rejected_not_mangled(self):
        # Valid UTF-16 BOM followed by an odd trailing byte.
        with pytest.raises(ImportRejected) as caught:
            decode_text(b"\xff\xfe" + b"a\x00b")
        assert caught.value.code == "undecodable_text"


class TestHostileXml:
    def test_billion_laughs_is_refused(self):
        result = import_screenplay(BILLION_LAUGHS, "bomb.fdx")
        assert result.outcome is ImportOutcome.REJECTED
        assert result.rejection_code == "xml_unsafe"

    def test_external_entity_is_refused(self):
        result = import_screenplay(XXE, "xxe.fdx")
        assert result.outcome is ImportOutcome.REJECTED
        assert result.rejection_code == "xml_unsafe"
        assert "root:" not in (result.rejection_message or "")

    def test_malformed_xml_reports_a_parse_failure(self):
        result = import_screenplay(
            b'<?xml version="1.0"?><FinalDraft><Content><Paragraph>', "torn.fdx"
        )
        assert result.rejection_code == "xml_malformed"

    def test_non_finaldraft_xml_root_is_named_in_the_message(self):
        result = import_screenplay(
            b'<?xml version="1.0"?><OpenDocument><body/></OpenDocument>', "other.xml"
        )
        assert result.outcome is ImportOutcome.REJECTED
        # Detection stops at "XML but not Final Draft" before the adapter runs.
        assert result.rejection_code in {"unsupported_format", "fdx_wrong_root"}

    def test_finaldraft_without_content_is_rejected(self):
        result = import_screenplay(
            b'<?xml version="1.0"?><FinalDraft Version="1"></FinalDraft>', "bare.fdx"
        )
        assert result.rejection_code == "fdx_no_content"

    def test_finaldraft_without_scene_headings_is_rejected(self):
        body = (
            b'<?xml version="1.0"?><FinalDraft><Content>'
            + b'<Paragraph Type="Action"><Text>A room.</Text></Paragraph>' * 5
            + b"</Content></FinalDraft>"
        )
        assert import_screenplay(body, "noscenes.fdx").rejection_code == "no_scenes"


class TestNotAScreenplay:
    def test_prose_is_rejected_with_reasons(self):
        result = import_screenplay(PROSE.encode(), "novel.txt")
        assert result.outcome is ImportOutcome.REJECTED
        assert result.rejection_code in {"no_scenes", "not_a_screenplay"}

    def test_an_invoice_is_rejected(self):
        result = import_screenplay(INVOICE.encode(), "invoice.txt")
        assert result.outcome is ImportOutcome.REJECTED

    def test_scene_headings_with_no_content_are_rejected(self):
        headings = "\n\n".join(f"INT. ROOM {n} - DAY" for n in range(6))
        result = import_screenplay(headings.encode(), "headings.txt")
        assert result.outcome is ImportOutcome.REJECTED
        assert result.rejection_code == "not_a_screenplay"

    def test_two_scenes_is_too_few_to_accept_silently(self):
        source = (
            "INT. ROOM - DAY\n\nA chair.\n\nMARA\nHello.\n\n"
            "INT. HALL - DAY\n\nA door.\n\nDEV\nHello.\n"
        )
        result = import_screenplay(source.encode(), "short.txt")
        # Two scenes with real dialogue is thin, so it must not be a clean accept.
        assert result.outcome is not ImportOutcome.ACCEPTED


class TestWrongExtension:
    """Extensions are hints. Content decides."""

    def test_fountain_named_as_pdf_is_still_fountain(self, night_freight_fountain):
        result = import_screenplay(night_freight_fountain, "mislabelled.pdf")
        assert result.accepted
        assert result.adapter_name == "fountain"

    def test_fdx_named_as_txt_is_still_fdx(self, understudy_fdx: bytes):
        result = import_screenplay(understudy_fdx, "mislabelled.txt")
        assert result.accepted
        assert result.adapter_name == "fdx"

    def test_extension_cannot_promote_junk(self):
        result = import_screenplay(b"\x00\x01\x02" * 100, "screenplay.fountain")
        assert result.outcome is ImportOutcome.REJECTED


class TestHostilePdf:
    def test_truncated_pdf_is_rejected(self):
        result = import_screenplay(
            b"%PDF-1.7\n" + b"\xde\xad\xbe\xef" * 200, "torn.pdf"
        )
        assert result.outcome is ImportOutcome.REJECTED
        assert result.rejection_code in {"pdf_unreadable", "pdf_no_text", "no_scenes"}

    def test_encrypted_pdf_is_rejected_with_a_reason(self, encrypted_pdf: bytes):
        result = import_screenplay(encrypted_pdf, "locked.pdf")
        assert result.outcome is ImportOutcome.REJECTED
        assert result.rejection_message

    def test_image_only_pdf_routes_to_ocr_or_says_why_it_cannot(
        self, image_only_pdf: bytes
    ):
        result = import_screenplay(image_only_pdf, "scan.pdf")
        if result.outcome is ImportOutcome.REJECTED:
            # ocr_failed is the honest outcome on a machine WITH the OCR
            # toolchain: this fixture's image holds no readable text, so
            # tesseract runs and reports failure rather than inventing words.
            assert result.rejection_code in {
                "ocr_unavailable",
                "ocr_failed",
                "pdf_no_text",
            }
            assert "OCR" in (result.rejection_message or "")
        else:
            # OCR present: the import must be flagged, never silently accepted.
            assert result.outcome is ImportOutcome.NEEDS_REVIEW
            assert any(w.code == "ocr_derived" for w in result.warnings)


class TestStructuralEdgeCases:
    def test_content_before_the_first_heading_is_kept_and_flagged(self):
        source = (
            "A cold open with no heading at all.\n\n"
            "INT. ROOM - DAY\n\nA chair.\n\nMARA\nHello.\n\n"
            "INT. HALL - DAY\n\nA door.\n\nDEV\nHello.\n\n"
            "INT. STAIRS - DAY\n\nSteps.\n\nMARA\nUp.\n"
        )
        result = import_screenplay(source.encode(), "coldopen.txt")
        assert result.accepted
        assert any(w.code == "content_before_first_scene" for w in result.warnings)
        assert result.scenes[0].units[0].text.startswith("A cold open")

    def test_title_page_only_is_rejected(self):
        source = "Title: NOTHING\nAuthor: Nobody\nDraft date: 2026-01-01\n"
        result = import_screenplay(source.encode(), "titleonly.fountain")
        assert result.outcome is ImportOutcome.REJECTED

    def test_heading_without_a_time_of_day_does_not_inherit_one(self):
        source = (
            "INT. ROOM - DAY\n\nA chair.\n\nMARA\nOne.\n\n"
            "INT. CELLAR\n\nDark.\n\nMARA\nTwo.\n\n"
            "INT. ATTIC - NIGHT\n\nDust.\n\nMARA\nThree.\n"
        )
        result = import_screenplay(source.encode(), "times.txt")
        assert [scene.time_of_day for scene in result.scenes] == ["DAY", None, "NIGHT"]

    def test_a_location_containing_a_dash_is_not_read_as_a_time(self):
        source = (
            "INT. HOUSE - KITCHEN\n\nA pan.\n\nMARA\nOne.\n\n"
            "INT. HOUSE - HALL\n\nA rug.\n\nMARA\nTwo.\n\n"
            "INT. HOUSE - LOFT\n\nA box.\n\nMARA\nThree.\n"
        )
        result = import_screenplay(source.encode(), "rooms.txt")
        assert all(scene.time_of_day is None for scene in result.scenes)

    def test_parser_confidence_outside_zero_to_one_is_refused(self):
        with pytest.raises(ValueError, match="parser_confidence"):
            ParsedUnit(
                unit_type=UnitType.ACTION,
                sequence_index=0,
                text="x",
                parser_method=ParserMethod.RULE,
                parser_confidence=1.4,
            )

    def test_scene_limit_truncates_and_says_so(self):
        many = "\n\n".join(
            f"INT. ROOM {n} - DAY\n\nA chair.\n\nMARA\nLine {n}." for n in range(450)
        )
        result = import_screenplay(many.encode(), "long.txt")
        assert result.scene_count == 400
        assert any(w.code == "scene_limit" for w in result.warnings)
        assert result.outcome is ImportOutcome.NEEDS_REVIEW


class TestNonAsciiNames:
    """Accented cues are cues. Found by script 03 rendering MATÍAS as action."""

    @pytest.mark.parametrize("name", ["MATÍAS", "BÉLA VARGA", "ILONA", "JOSÉ", "ZOË"])
    def test_an_accented_character_cue_is_recognised(self, name):
        from ripple.adapters.base import parse_character_cue

        parsed = parse_character_cue(name)
        assert parsed is not None, f"{name} was not read as a cue"
        assert parsed[0] == name

    def test_accented_dialogue_parses_in_the_corpus(self):
        from pathlib import Path

        from ripple.adapters import UnitType

        source = Path("demo-scripts/03-seven-minutes/seven-minutes.fountain")
        if not source.exists():
            pytest.skip("corpus missing")
        result = import_screenplay(source.read_bytes(), "seven-minutes.fountain")
        speakers = {
            unit.speaker_name
            for scene in result.scenes
            for unit in scene.units
            if unit.unit_type is UnitType.CHARACTER
        }
        assert "MATÍAS" in speakers
        assert "BÉLA" in speakers

    def test_an_accented_name_still_resolves_to_one_entity(self):
        """NFKC means the composed and decomposed forms are one key."""
        import unicodedata

        from ripple.db.naming import normalize

        composed = "MATÍAS"
        decomposed = unicodedata.normalize("NFD", composed)
        assert composed != decomposed
        assert normalize(composed) == normalize(decomposed)


class TestOcrLeavesNoFiles:
    def test_a_multi_page_scan_writes_nothing_to_the_working_directory(
        self, image_only_pdf: bytes, tmp_path, monkeypatch
    ):
        """pdftoppm's output prefix must never reach the filesystem.

        Any trailing argument to pdftoppm, including "-", is a filename
        prefix, and a prefix drops page images ("--1.png") into the working
        directory instead of writing to stdout.
        """
        monkeypatch.chdir(tmp_path)
        import_screenplay(image_only_pdf, "scan.pdf")
        droppings = list(tmp_path.glob("*.png")) + list(tmp_path.glob("*.ppm"))
        assert droppings == []
