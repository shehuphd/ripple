"""PDF adapter.

Classification and position-aware extraction come from pdf-inspector, which
runs locally with no network calls and no models. It performs no OCR, so a
document it classifies as scanned routes to Tesseract when the binary is
present and is rejected with a stated reason when it is not.

Layout rules classify blocks by their horizontal position relative to the
page's own left margin, so a script typeset at any margin parses the same way.
Ambiguous blocks are marked for the structure repair agent rather than guessed
at; only those are ever sent to a model.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass

from ripple.adapters.base import (
    SCENE_HEADING,
    SCENE_NUMBER,
    SHOT_PREFIX,
    TRANSITION,
    DetectedFormat,
    ImportRejected,
    ImportWarning,
    ParsedScene,
    ParsedUnit,
    ParserMethod,
    SourceAnchor,
    SourcePayload,
    UnitType,
    parse_character_cue,
    split_heading,
)

logger = logging.getLogger(__name__)

# Offsets from the action margin in PostScript points. One inch is 72pt, and a
# standard screenplay puts dialogue 1", parentheticals 1.6", cues 2.2" in.
DIALOGUE_POINTS = 40.0
PARENTHETICAL_POINTS = 80.0
CHARACTER_POINTS = 120.0
# Text items within this many points of each other share a line.
LINE_TOLERANCE = 3.0
# A vertical gap larger than this starts a new block.
BLOCK_GAP = 18.0
# A horizontal shift larger than this also starts a new block. Screenplays set
# a cue, its parenthetical, and its dialogue on consecutive lines with no blank
# line between them, so only the left edge separates three different elements.
COLUMN_SHIFT = 10.0
# Below this classification confidence the import is flagged for review.
MIN_CLASSIFICATION_CONFIDENCE = 0.5

# Production drafts print the scene number in the left margin, and usually the
# right margin too, on the same rendered line as the heading. Extraction reads
# the whole line, so the number arrives glued to the heading text.
MARGIN_SCENE_NUMBER = re.compile(
    r"^(?P<number>[0-9]{1,4}[A-Za-z]{0,2})\s+"
    r"(?P<body>(?:INT|EXT|EST|I/E)\b.*?)"
    r"(?:\s+(?P=number))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Line:
    """One rendered line of text with its position on the page."""

    page: int
    x: float
    y: float
    text: str
    is_bold: bool


class PdfAdapter:
    """Parses a text-layer PDF, or routes a scanned one to OCR."""

    name = "pdf"
    detected_format = DetectedFormat.PDF

    def detect(self, payload: SourcePayload) -> float:
        from ripple.adapters.detect import detect_format

        detected, confidence = detect_format(payload)
        return confidence if detected is DetectedFormat.PDF else 0.0

    def parse(
        self, payload: SourcePayload
    ) -> tuple[list[ParsedScene], list[ImportWarning]]:
        inspector = self._load_inspector()
        warnings: list[ImportWarning] = []

        classification = self._classify_document(inspector, payload)
        method = ParserMethod.PDF_LAYOUT

        if classification.pdf_type != "text_based":
            lines, ocr_warnings = self._ocr(payload, classification)
            warnings.extend(ocr_warnings)
            method = ParserMethod.OCR
        else:
            lines = self._extract_lines(inspector, payload)
            if classification.confidence < MIN_CLASSIFICATION_CONFIDENCE:
                warnings.append(
                    ImportWarning(
                        code="low_classification_confidence",
                        message=f"pdf-inspector classified this as text-based "
                        f"with {classification.confidence:.0%} confidence.",
                    )
                )

        if not lines:
            raise ImportRejected(
                "pdf_no_text",
                "No text was extracted from the PDF. If it is a scan, OCR is "
                "required and unavailable.",
            )

        scenes, block_warnings = self._blocks_to_scenes(lines, method)
        warnings.extend(block_warnings)
        if not scenes:
            raise ImportRejected(
                "no_scenes", "No scene headings were found in the PDF text."
            )
        return scenes, warnings

    @staticmethod
    def _load_inspector():
        """Import pdf-inspector, naming the package if it is absent."""
        try:
            import pdf_inspector
        except ImportError as error:  # pragma: no cover - environment guard
            raise ImportRejected(
                "pdf_backend_missing",
                "PDF import needs the pdf-inspector package. Install it with "
                "'pip install pdf-inspector'.",
            ) from error
        return pdf_inspector

    @staticmethod
    def _classify_document(inspector, payload: SourcePayload):
        """Classify text-based versus scanned, converting failures to rejections."""
        try:
            return inspector.classify_pdf_bytes(payload.data)
        except (ValueError, OSError, RuntimeError) as error:
            raise ImportRejected(
                "pdf_unreadable",
                f"The PDF could not be read: {error}. It may be encrypted or "
                "damaged.",
            ) from error

    @staticmethod
    def _extract_lines(inspector, payload: SourcePayload) -> list[_Line]:
        """Group positioned text items into rendered lines."""
        try:
            items = inspector.extract_text_with_positions_bytes(payload.data)
        except (ValueError, OSError, RuntimeError) as error:
            raise ImportRejected(
                "pdf_unreadable", f"The PDF text layer could not be read: {error}."
            ) from error

        buckets: dict[tuple[int, int], list] = {}
        for item in items:
            if not (item.text or "").strip():
                continue
            key = (item.page, round(item.y / LINE_TOLERANCE))
            buckets.setdefault(key, []).append(item)

        lines: list[_Line] = []
        for (page, _), group in buckets.items():
            group.sort(key=lambda entry: entry.x)
            text = " ".join(entry.text.strip() for entry in group).strip()
            if not text:
                continue
            lines.append(
                _Line(
                    page=page,
                    x=min(entry.x for entry in group),
                    y=group[0].y,
                    text=text,
                    is_bold=any(getattr(entry, "is_bold", False) for entry in group),
                )
            )

        # Reading order: page ascending, then down the page. pdf-inspector
        # reports y increasing upward, so descending y walks top to bottom.
        lines.sort(key=lambda line: (line.page, -line.y, line.x))
        return lines

    def _ocr(
        self, payload: SourcePayload, classification
    ) -> tuple[list[_Line], list[ImportWarning]]:
        """Run local Tesseract, or reject when it is unavailable.

        No bytes leave the application on this path. Tesseract is invoked as a
        subprocess against stdin so nothing is written to disk either.
        """
        # Tesseract reads images, so a PDF has to be rasterised first.
        # pdftoppm ships with poppler; both binaries are needed for this path.
        missing = [
            binary
            for binary in ("pdftoppm", "tesseract")
            if shutil.which(binary) is None
        ]
        if missing:
            raise ImportRejected(
                "ocr_unavailable",
                f"This PDF is a scan ({classification.pages_needing_ocr} of "
                f"{classification.page_count} pages have no text layer) and "
                f"local OCR is unavailable: {', '.join(missing)} not installed. "
                "Install poppler and Tesseract, or upload a PDF with a text "
                "layer, Fountain, Final Draft XML, or plain text.",
            )

        # One page per pass. pdftoppm writes to stdout only when the output
        # root is omitted entirely; any trailing argument, including "-", is a
        # filename prefix, and a prefix means page images dropped into the
        # working directory, which is both a mess and a broken promise about
        # disk. Limiting each pass to a single page keeps stdout a single PNG.
        page_texts: list[str] = []
        try:
            for page_number in range(1, classification.page_count + 1):
                raster = subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        "200",
                        "-gray",
                        "-png",
                        "-f",
                        str(page_number),
                        "-l",
                        str(page_number),
                        "-",
                    ],
                    input=payload.data,
                    capture_output=True,
                    timeout=300,
                    check=True,
                )
                completed = subprocess.run(
                    ["tesseract", "stdin", "stdout", "--psm", "6"],
                    input=raster.stdout,
                    capture_output=True,
                    timeout=300,
                    check=True,
                )
                page_texts.append(completed.stdout.decode("utf-8", "replace"))
        except subprocess.TimeoutExpired as error:
            raise ImportRejected(
                "ocr_timeout", "OCR did not finish within five minutes for a page."
            ) from error
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or b"").decode("utf-8", "replace").strip()
            raise ImportRejected("ocr_failed", f"OCR failed: {detail[:200]}") from error

        lines = []
        index = 0
        for page_number, text in enumerate(page_texts, start=1):
            for line in text.splitlines():
                if not line.strip():
                    continue
                lines.append(
                    _Line(
                        page=page_number,
                        x=float(len(line) - len(line.lstrip())),
                        y=-index,
                        text=line.strip(),
                        is_bold=False,
                    )
                )
                index += 1
        return lines, [
            ImportWarning(
                code="ocr_derived",
                message="Text came from OCR and needs review before the graph "
                "is trusted.",
            )
        ]

    def _blocks_to_scenes(
        self, lines: list[_Line], method: ParserMethod
    ) -> tuple[list[ParsedScene], list[ImportWarning]]:
        """Group lines into blocks and classify each by horizontal position."""
        margin = self._action_margin(lines)
        warnings: list[ImportWarning] = []
        scenes: list[ParsedScene] = []
        scene: ParsedScene | None = None
        previous: UnitType | None = None
        speaker: str | None = None
        ambiguous = 0

        for block_index, block in enumerate(self._group_blocks(lines)):
            text = " ".join(line.text for line in block).strip()
            if not text:
                continue
            text, margin_number = self._strip_margin_number(text)
            left = min(line.x for line in block)
            unit_type, confidence = self._classify_block(text, left, margin, previous)
            if confidence < 0.6:
                ambiguous += 1

            if unit_type is UnitType.SCENE_HEADING:
                scene = self._start_scene(scenes, text, margin_number)
                previous, speaker = unit_type, None
                self._append(
                    scene,
                    unit_type,
                    scene.heading,
                    block,
                    block_index,
                    method,
                    confidence,
                    None,
                )
                continue

            if scene is None:
                scene = ParsedScene(sequence_index=0, heading="")
                scenes.append(scene)
                warnings.append(
                    ImportWarning(
                        code="content_before_first_scene",
                        message="Content appears before the first scene heading "
                        "and was placed in an unheaded opening scene.",
                        scene_index=0,
                    )
                )

            if unit_type is UnitType.CHARACTER:
                parsed = parse_character_cue(text)
                speaker = parsed[0] if parsed else text
            elif unit_type not in (UnitType.DIALOGUE, UnitType.PARENTHETICAL):
                speaker = None

            self._append(
                scene,
                unit_type,
                text,
                block,
                block_index,
                method,
                confidence,
                (
                    speaker
                    if unit_type
                    in (UnitType.CHARACTER, UnitType.DIALOGUE, UnitType.PARENTHETICAL)
                    else None
                ),
            )
            previous = unit_type

        if ambiguous:
            warnings.append(
                ImportWarning(
                    code="ambiguous_blocks",
                    message=f"{ambiguous} block(s) could not be classified by "
                    "layout alone and need the structure repair agent.",
                )
            )
        return scenes, warnings

    @staticmethod
    def _strip_margin_number(text: str) -> tuple[str, str | None]:
        """Separate a margin-printed scene number from its heading.

        Returns the heading alone and the number, or the text unchanged and
        None when the line is not a numbered heading.
        """
        match = MARGIN_SCENE_NUMBER.match(text)
        if not match:
            return text, None
        return match.group("body").strip(), match.group("number")

    @staticmethod
    def _action_margin(lines: list[_Line]) -> float:
        """The page's own left margin, taken as its single most common left edge.

        The mode rather than the minimum: scene numbers, page numbers, and
        revision marks all sit left of the text block, and a minimum would
        follow them out into the margin and shift every offset with it.
        """
        counts = Counter(round(line.x / 5.0) * 5.0 for line in lines)
        return counts.most_common(1)[0][0]

    @staticmethod
    def _group_blocks(lines: list[_Line]) -> list[list[_Line]]:
        """Split lines into blocks on a vertical gap or a page break."""
        blocks: list[list[_Line]] = []
        current: list[_Line] = []
        for line in lines:
            if not current:
                current = [line]
                continue
            last = current[-1]
            new_page = line.page != last.page
            gap = abs(last.y - line.y)
            shifted = abs(line.x - current[0].x) > COLUMN_SHIFT
            if new_page or gap > BLOCK_GAP or shifted:
                blocks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            blocks.append(current)
        return blocks

    @staticmethod
    def _classify_block(
        text: str, left: float, margin: float, previous: UnitType | None
    ) -> tuple[UnitType, float]:
        """Classify a block, returning the type and the rule's confidence."""
        if SCENE_HEADING.match(text):
            return UnitType.SCENE_HEADING, 1.0
        if TRANSITION.match(text):
            return UnitType.TRANSITION, 0.95
        if text.startswith("(") and text.endswith(")"):
            return UnitType.PARENTHETICAL, 0.9

        offset = left - margin
        in_speech = previous in (
            UnitType.CHARACTER,
            UnitType.DIALOGUE,
            UnitType.PARENTHETICAL,
        )

        if offset >= CHARACTER_POINTS and parse_character_cue(text):
            return UnitType.CHARACTER, 0.95
        if offset >= PARENTHETICAL_POINTS and text.startswith("("):
            return UnitType.PARENTHETICAL, 0.9
        if offset >= DIALOGUE_POINTS:
            # Indented but not far enough to be a cue: dialogue when speech is
            # open, otherwise the layout does not say and a model should look.
            return (UnitType.DIALOGUE, 0.9) if in_speech else (UnitType.ACTION, 0.45)
        if SHOT_PREFIX.match(text):
            return UnitType.SHOT, 0.8
        if in_speech and text == text.upper() and parse_character_cue(text):
            return UnitType.CHARACTER, 0.5
        return UnitType.ACTION, 0.9

    @staticmethod
    def _start_scene(
        scenes: list[ParsedScene], text: str, margin_number: str | None = None
    ) -> ParsedScene:
        match = SCENE_NUMBER.search(text)
        heading = SCENE_NUMBER.sub("", text).strip()
        int_ext, _, time_of_day = split_heading(heading)
        scene = ParsedScene(
            sequence_index=len(scenes),
            heading=heading,
            display_scene_number=(match.group("number") if match else margin_number),
            int_ext=int_ext,
            time_of_day=time_of_day,
        )
        scenes.append(scene)
        return scene

    @staticmethod
    def _append(
        scene: ParsedScene,
        unit_type: UnitType,
        text: str,
        block: list[_Line],
        block_index: int,
        method: ParserMethod,
        confidence: float,
        speaker: str | None,
    ) -> None:
        left = min(line.x for line in block)
        top = max(line.y for line in block)
        bottom = min(line.y for line in block)
        scene.units.append(
            ParsedUnit(
                unit_type=unit_type,
                sequence_index=len(scene.units),
                text=text,
                parser_method=method,
                parser_confidence=confidence,
                speaker_name=speaker,
                anchor=SourceAnchor(
                    extraction_method=method.value,
                    page_number=block[0].page,
                    block_index=block_index,
                    bounding_box=(left, bottom, left, top),
                ),
            )
        )
