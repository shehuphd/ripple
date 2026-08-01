# Ripple: usage

## The import contract

Every format goes through one contract in `ripple/adapters/base.py`: detect, extract, parse, validate, return a shared typed result. Adapters return data and never write to the database.

```
SourcePayload(data: bytes, suggested_name: str)
        │
        ├─ detect_format() ──► DetectedFormat + confidence
        │
        ├─ adapter.parse() ──► list[ParsedScene], list[ImportWarning]
        │
        ├─ screenplay_check.assess() ──► ScreenplayVerdict
        │
        └─ ImportResult(outcome, scenes, warnings, rejection_code)
```

### Types

| Type | Holds |
|---|---|
| `ParsedScene` | `sequence_index`, `heading`, `display_scene_number`, `int_ext`, `time_of_day`, `units` |
| `ParsedUnit` | `unit_type`, `sequence_index`, `text`, `parser_method`, `parser_confidence`, `speaker_name`, `anchor` |
| `SourceAnchor` | `extraction_method`, and whichever of `page_number`, `block_index`, `start_offset`, `end_offset`, `bounding_box` the adapter can supply |
| `ImportWarning` | `code`, `message`, `scene_index` |

`display_scene_number` is a string because production revisions produce lettered numbers such as `12A`. It is presentation state, never identity.

### Adding an adapter

Implement the `ImportAdapter` protocol and register it in `ADAPTERS`:

```python
class MyAdapter:
    name = "my_format"
    detected_format = DetectedFormat.MY_FORMAT

    def detect(self, payload: SourcePayload) -> float: ...
    def parse(self, payload: SourcePayload) -> tuple[list[ParsedScene], list[ImportWarning]]: ...
```

`parse` raises `ImportRejected(code, message)` for anything unparseable. The entry point converts it to a rejected result; nothing should propagate to the caller.

## Per-format behaviour

| | Fountain | Final Draft XML | PDF | Plain text |
|---|---|---|---|---|
| Element types | Read from markup | Read from `Paragraph Type` | Inferred from position | Inferred from indentation |
| Scene numbers | `#N#` markers | `Number` attribute | Margin-printed numbers | Trailing `#N#` if present |
| Provenance | Character offsets | Block index | Page, block index, bounding box | Character offsets |
| Notes | Parsed as `note` units | Not present | Not present | Not present |
| `parser_method` | `fountain` | `fdx` | `pdf_layout` or `ocr` | `rule` |

### PDF

`pdf-inspector` classifies text-based versus scanned and returns position-aware text items. It runs locally with no network calls and no models, and performs no OCR.

Layout rules measure the page's own left margin as the modal left edge, then classify blocks by their offset from it, so a script typeset at any margin parses the same way. Blocks split on a vertical gap, a page break, or a horizontal shift; the last matters because a character cue, its parenthetical, and its dialogue sit on consecutive lines with only their left edge to separate them.

A block the layout rules cannot place gets a `parser_confidence` below 0.6 and an `ambiguous_blocks` warning. Only those blocks go to the structure repair agent.

### Scanned PDFs

A scanned PDF is rasterised with `pdftoppm` and read with `tesseract`, both as local subprocesses over stdin. No bytes leave the application and nothing is written to disk. If either binary is absent, the import is rejected with `ocr_unavailable` naming what is missing. OCR-derived imports are always `needs_review`.

### Security

Final Draft XML is parsed through `defusedxml`. External entities and entity expansion are refused with `xml_unsafe`, which the test suite pins with a billion-laughs payload and an XXE payload.

Uploads over 8 MiB are rejected before any parser runs. Scene and unit counts are capped, and truncation is reported as a warning rather than applied silently.

## Rejection codes

| Code | Cause |
|---|---|
| `payload_too_large` | Over the 8 MiB upload ceiling |
| `undecodable_text` | Not UTF-8, UTF-16, UTF-32, or Windows-1252 |
| `unsupported_format` | Not one of the four supported formats |
| `no_scenes` | Parsed, but no scene headings were found |
| `not_a_screenplay` | Parsed, but the structure is not a screenplay's |
| `xml_malformed` | Not well-formed XML |
| `xml_unsafe` | External entities or entity expansion |
| `fdx_wrong_root` | XML root is not `<FinalDraft>` |
| `fdx_no_content` | No `<Content>` element |
| `pdf_unreadable` | Encrypted or damaged PDF |
| `pdf_no_text` | No text extracted |
| `ocr_unavailable` | Scanned PDF with no local OCR toolchain |
| `ocr_timeout` / `ocr_failed` | OCR ran and did not finish or failed |
| `pdf_backend_missing` | `pdf-inspector` not installed |

## Demo corpus

`tools/render_screenplay.py` renders one authored Fountain file to Final Draft XML, PDF, and plain text. It reinjects `#N#` scene numbers into the FDX as `Number` attributes, which screenplain drops, and fails loudly when the heading count and the number count disagree.

The renders diverge from the source in three documented ways: Fountain notes are stripped from every derivative, dual dialogue flattens to sequential dialogue, and shot lines type as action. A test asserting parity across all four formats would be wrong. Each script's `dependencies.md` records the expected divergence.

Built by Mo Shehu — mohammedshehu.com
