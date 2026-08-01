# Changelog

All notable changes to Ripple are documented here.

## [0.1.0] — 2026-08-01

### Added

- **Import adapters for four formats.** Fountain, Final Draft XML, PDF, and plain text behind one contract: detect, extract, parse, validate, return a shared typed result. `import_screenplay` never raises for bad input; a file that cannot become a screenplay returns a rejected result with a stable code.
- **Content-based format detection.** File extensions are hints that can break a tie between two text formats and never override a content signal.
- **Structural screenplay check.** A parsed document is judged on scene count, character-and-dialogue presence, dialogue share, and mean action length before it is accepted, so prose and invoices are rejected with stated reasons.
- **PDF layout parsing via pdf-inspector.** Local classification and position-aware extraction, no network calls and no models. Blocks are classified against the page's own modal left margin and split on vertical gap, page break, or horizontal shift. Scanned PDFs rasterise through `pdftoppm` and read through `tesseract`, both local; when either is absent the import is rejected with `ocr_unavailable`.
- **Hardened XML parsing.** Final Draft files go through `defusedxml`. External entities and entity expansion are refused, pinned by billion-laughs and XXE tests.
- **TraceAct 0.12.0 instrumentation** on the `script.import` workflow, with automatic input capture disabled and the `ai_prompts`, `api_keys`, `http`, `filesystem_paths`, and `env_vars` redaction presets. Tests assert that no screenplay line, character name, or filename appears in any trace.
- **Demo corpus.** Three original screenplays, each authored in Fountain and rendered to Final Draft XML, PDF, and plain text by `tools/render_screenplay.py`. Each ships a `dependencies.md` recording expected entities, assertions, and planted dependency chains.
- **`launch.command`.** Path-safe bootstrap that creates the virtual environment, installs the package, and runs the test suite.
