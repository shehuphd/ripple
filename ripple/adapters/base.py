"""Shared contract every import adapter implements.

One contract covers Fountain, Final Draft XML, PDF, and
plain text: detection, extraction, parsing, validation, and a shared typed
result. Adapters return the types below and never write to the database; the
import service owns persistence.

Enumerated values here mirror the database vocabularies. Changing one means changing the
lock first.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

# An 8 MiB ceiling holds a 200-page scanned PDF and rejects anything that is
# not a screenplay long before it reaches a parser.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SCENES = 400
MAX_UNITS_PER_SCENE = 500


class UnitType(str, Enum):
    """The unit kinds a screenplay decomposes into."""

    SCENE_HEADING = "scene_heading"
    ACTION = "action"
    CHARACTER = "character"
    DIALOGUE = "dialogue"
    PARENTHETICAL = "parenthetical"
    TRANSITION = "transition"
    SHOT = "shot"
    NOTE = "note"


class ParserMethod(str, Enum):
    """How a unit's classification was decided."""

    FOUNTAIN = "fountain"
    FDX = "fdx"
    PDF_LAYOUT = "pdf_layout"
    RULE = "rule"
    OCR = "ocr"
    AGENT_REPAIR = "agent_repair"
    USER_CORRECTED = "user_corrected"


class DetectedFormat(str, Enum):
    FOUNTAIN = "fountain"
    FDX = "fdx"
    PDF = "pdf"
    PLAIN_TEXT = "plain_text"
    STAGE_PLAY = "stage_play"
    UNKNOWN = "unknown"


class ImportOutcome(str, Enum):
    """What an import can conclude."""

    ACCEPTED = "accepted"
    ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class ImportRejected(Exception):
    """Raised when a payload cannot become a screenplay.

    Carries a stable machine-readable code so the UI can explain the refusal
    without parsing prose, and so tests assert on the code rather than wording.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ImportWarning:
    """A non-fatal finding recorded against the import."""

    code: str
    message: str
    scene_index: int | None = None


@dataclass(frozen=True)
class SourceAnchor:
    """Immutable provenance pointing back into the imported document.

    Every field is optional because availability depends on the adapter:
    Fountain and plain text give character offsets, PDF gives page, block
    index, and a bounding box.
    """

    extraction_method: str
    page_number: int | None = None
    block_index: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    bounding_box: tuple[float, float, float, float] | None = None


@dataclass
class ParsedUnit:
    """One structured script unit, before persistence assigns it a UUID."""

    unit_type: UnitType
    sequence_index: int
    text: str
    parser_method: ParserMethod
    parser_confidence: float = 1.0
    speaker_name: str | None = None
    anchor: SourceAnchor | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.parser_confidence <= 1.0:
            raise ValueError(
                f"parser_confidence must be in [0, 1], got {self.parser_confidence}"
            )


@dataclass
class ParsedScene:
    """One scene and its ordered units."""

    sequence_index: int
    heading: str
    units: list[ParsedUnit] = field(default_factory=list)
    display_scene_number: str | None = None
    int_ext: str | None = None
    time_of_day: str | None = None


@dataclass
class ImportResult:
    """What every adapter returns, whatever the input format was."""

    outcome: ImportOutcome
    detected_format: DetectedFormat
    adapter_name: str
    source_name: str
    content_hash: str
    scenes: list[ParsedScene] = field(default_factory=list)
    warnings: list[ImportWarning] = field(default_factory=list)
    # From the document's own title page where the format has one. None for
    # PDF and plain text, where the repository falls back to the filename.
    title: str | None = None
    rejection_code: str | None = None
    rejection_message: str | None = None

    @property
    def scene_count(self) -> int:
        return len(self.scenes)

    @property
    def unit_count(self) -> int:
        return sum(len(scene.units) for scene in self.scenes)

    @property
    def accepted(self) -> bool:
        return self.outcome is not ImportOutcome.REJECTED


@dataclass(frozen=True)
class SourcePayload:
    """Raw upload plus the filename hint, which is a hint and not authority.

    File extensions are hints, not authority. `suggested_name`
    is carried so an adapter can break a tie between two equally plausible
    text formats, never to decide detection on its own.
    """

    data: bytes
    suggested_name: str = "upload"

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError(
                f"payload data must be bytes, got {type(self.data).__name__}"
            )
        if len(self.data) > MAX_UPLOAD_BYTES:
            raise ImportRejected(
                "payload_too_large",
                f"Upload is {len(self.data):,} bytes; the limit is "
                f"{MAX_UPLOAD_BYTES:,} bytes.",
            )

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def extension(self) -> str:
        _, _, suffix = self.suggested_name.rpartition(".")
        return suffix.lower() if suffix != self.suggested_name else ""

    def text(self) -> str:
        """Decode to text, tolerating the encodings screenplay tools emit.

        Raises ImportRejected rather than returning mojibake, because a
        silently mis-decoded screenplay parses into plausible nonsense.
        """
        return decode_text(self.data)


# Byte-order marks, longest first so UTF-32 is not matched as UTF-16.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)


def decode_text(data: bytes) -> str:
    """Decode upload bytes to str, honouring a BOM and falling back to cp1252.

    cp1252 is the fallback rather than latin-1 because Final Draft and Word on
    Windows emit smart quotes and ellipses in that range; latin-1 decodes those
    bytes to control characters without erroring, which hides the problem.
    """
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                # A utf-16-le/be decode leaves U+FEFF in place, and a leading
                # zero-width character defeats every start-of-line rule.
                return data.decode(encoding).lstrip("\ufeff")
            except UnicodeDecodeError as error:
                raise ImportRejected(
                    "undecodable_text",
                    f"File declares a {encoding} byte-order mark but does not "
                    f"decode as {encoding}.",
                ) from error

    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ImportRejected(
        "undecodable_text",
        "File is not valid UTF-8 or Windows-1252 text. If this is a scanned "
        "document, upload it as a PDF.",
    )


@runtime_checkable
class ImportAdapter(Protocol):
    """Contract every format adapter satisfies."""

    name: str
    detected_format: DetectedFormat

    def detect(self, payload: SourcePayload) -> float:
        """Return confidence in [0, 1] that this adapter owns the payload."""

    def parse(self, payload: SourcePayload) -> ImportResult:
        """Parse into scenes and units, or raise ImportRejected."""


# Scene heading grammar, shared by every adapter so the four formats cannot
# disagree about what a heading is.
SCENE_HEADING = re.compile(
    r"^(?P<prefix>INT\.?/EXT\.?|EXT\.?/INT\.?|I/E\.?|INT\.?|EXT\.?|EST\.?)"
    r"(?=[\s.])\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
SCENE_NUMBER = re.compile(r"\s*#(?P<number>[\w.\-]+)#\s*$")
TRANSITION = re.compile(
    r"^(?:[A-Z0-9 '\-]+\s+TO:|FADE (?:IN|OUT)[.:]?|CUT TO BLACK[.:]?|"
    r"SMASH CUT[.:]?|MATCH CUT[.:]?|END(?:\s+OF)?\s+MONTAGE)\s*$"
)
SHOT_PREFIX = re.compile(
    r"^(?:ANGLE ON|CLOSE ON|CLOSE UP|EXTREME CLOSE|WIDE ON|WIDE SHOT|"
    r"POV|INSERT|BACK TO|SERIES OF SHOTS|MONTAGE|INTERCUT|AERIAL|TRACKING)\b",
    re.IGNORECASE,
)
# Stage-play structure, as public-domain plays (Shakespeare, Wilde, Ibsen and
# the like) are distributed by Project Gutenberg: acts and scenes named in
# words or numerals rather than INT./EXT. sluglines. An act is "ACT I", "ACT 1",
# "ACT ONE", or the word-ordinal "FIRST ACT"; a scene is "SCENE", "SCENE II", or
# "SCENE I. <setting>". A scene match counts only when a number, a separator, or
# end-of-line follows the word, so a sentence opening "Scene of ..." is not one.
_ROMAN = r"[IVXLCDM]+"
STAGE_ACT = re.compile(
    r"^(?:ACT\s+(?P<num>" + _ROMAN + r"|\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|"
    r"EIGHT|NINE|TEN)"
    r"|(?P<word>FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|"
    r"TENTH)\s+ACT)"
    r"\b\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
STAGE_SCENE = re.compile(
    r"^SCENE(?:\s+(?P<num>" + _ROMAN + r"|\d+))?\s*(?P<sep>[.\-:—])?\s*"
    r"(?P<rest>.*)$",
    re.IGNORECASE,
)
# Lines that open or close a stage direction rather than a speech.
STAGE_DIRECTION = re.compile(
    r"^_?(?:Enter|Exit|Exeunt|Re-enter|Re-enters|Manet|Manent|Curtain|"
    r"The\s+Curtain|End\s+of)\b",
    re.IGNORECASE,
)


def stage_act_match(line: str) -> re.Match | None:
    """Match an act heading, or None. STAGE_ACT already requires a numeral or a
    word-ordinal, so it does not fire on the word "act" inside a sentence."""
    return STAGE_ACT.match(line.strip())


def stage_scene_match(line: str) -> re.Match | None:
    """Match a scene heading, or None. A bare STAGE_SCENE match is not enough,
    since it captures any line opening with "Scene"; a real heading carries a
    number, a separator, or nothing after the word."""
    match = STAGE_SCENE.match(line.strip())
    if match and (
        match.group("num") or match.group("sep") or not match.group("rest").strip()
    ):
        return match
    return None


# An inline cue puts the speaker and the speech on one line, as Shaw and Chekhov
# do: "HIGGINS. Nonsense!" or "THE DAUGHTER [in the doorway] I'm chilled." The
# speaker is a leading run of all-caps words; the speech begins after a period
# and a space, or at a bracketed direction. A name cannot hold a lowercase
# letter, so a Title-case setting ("Covent Garden at 11 p.m.") is never a cue.
INLINE_CUE = re.compile(
    r"^(?P<name>[A-Z0-9][A-Z0-9 '.\-]*?)"
    r"(?:\.\s+(?=[A-Za-z\"'‘“])|\s*(?=\[))"
    r"(?P<rest>\S.*)$"
)


def inline_cue_split(line: str) -> tuple[str, str] | None:
    """Split "SPEAKER. words" or "SPEAKER [dir] words" into (name, speech), or
    None when the line is not an inline cue."""
    match = INLINE_CUE.match(line.strip())
    if not match:
        return None
    # Project Gutenberg italicises a cue with underscores ("HAMLET._"), and
    # left on the name they fork one character into two entities.
    name = match.group("name").strip().strip("_").rstrip(".").strip("_").strip()
    speech = match.group("rest").strip()
    words = name.split()
    if not (2 <= len(name) <= 35 and 1 <= len(words) <= 5):
        return None
    if "SCENE" in words or "ACT" in words:
        return None
    if not any(character.isalpha() for character in name):
        return None
    return name, speech
# Character cues carry optional extensions and an optional dual-dialogue caret.
#
# The name class is Unicode-aware rather than [A-Z]: MATIAS and MATÍAS are both
# valid cues, and an ASCII-only class silently demotes every accented name to
# an action line. Case is checked separately by parse_character_cue, so a
# lowercase match here is filtered there.
CHARACTER_CUE = re.compile(
    r"^(?P<name>[^\W\d_][\w .'\-#&]*?)"
    r"(?P<extension>(?:\s*\((?:[^)]*)\))*)"
    r"\s*(?P<dual>\^)?\s*$",
    re.UNICODE,
)
# Times of day recognised without a model. Anything else stays None rather
# than being guessed, because a wrong time of day misleads scheduling.
TIMES_OF_DAY = frozenset(
    {
        "DAY",
        "NIGHT",
        "MORNING",
        "AFTERNOON",
        "EVENING",
        "DAWN",
        "DUSK",
        "CONTINUOUS",
        "LATER",
        "MOMENTS LATER",
        "SAME",
        "SAME TIME",
        "MAGIC HOUR",
        "PRE-DAWN",
        "MIDNIGHT",
        "SUNSET",
        "SUNRISE",
    }
)


def split_heading(heading: str) -> tuple[str | None, str | None, str | None]:
    """Split a scene heading into (int_ext, location, time_of_day).

    Returns None for any part the heading does not state. A heading with no
    recognised time of day keeps None rather than borrowing the previous
    scene's, since an inherited value reads as fact downstream.
    """
    text = SCENE_NUMBER.sub("", heading.strip())
    match = SCENE_HEADING.match(text)
    if not match:
        return None, text or None, None

    prefix = match.group("prefix").upper().rstrip(".")
    int_ext = {"I/E": "INT/EXT", "EXT/INT": "INT/EXT"}.get(prefix, prefix)
    rest = match.group("rest").strip()

    # The time of day is the final dash-delimited segment, and only when it is
    # a value we recognise. "INT. HOUSE - KITCHEN" keeps KITCHEN as location.
    location, time_of_day = rest, None
    if " - " in rest or rest.endswith("-"):
        head, _, tail = rest.rpartition(" - ")
        candidate = tail.strip().rstrip(".").upper()
        if candidate in TIMES_OF_DAY:
            location, time_of_day = head.strip(), candidate

    return int_ext, location or None, time_of_day


def parse_character_cue(line: str) -> tuple[str, str | None, bool] | None:
    """Split a character cue into (name, extension, is_dual_dialogue).

    Returns None when the line is not a cue. Extensions such as (V.O.) and
    (CONT'D) are separated rather than stripped, because the import service
    needs the extension for unit text and the bare name for entity resolution.
    """
    stripped = line.strip()
    if not stripped or stripped != stripped.upper():
        return None
    if SCENE_HEADING.match(stripped) or TRANSITION.match(stripped):
        return None

    match = CHARACTER_CUE.match(stripped)
    if not match:
        return None

    # Project Gutenberg italicises a cue with underscores ("HAMLET._"), and
    # left on the name they fork one character into two entities.
    name = match.group("name").strip().strip("_").rstrip(".").strip("_")
    if not name or not any(character.isalpha() for character in name):
        return None

    extension = match.group("extension").strip() or None
    return name, extension, bool(match.group("dual"))
