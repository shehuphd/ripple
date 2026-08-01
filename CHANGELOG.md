# Changelog

All notable changes to Ripple are documented here.

## [Unreleased]

### Added

- **Scene-level graph extraction**, resumable and browser-driven. One scene is claimed per request through an atomic `UPDATE ... WHERE status='pending'`, so two tabs polling at once cannot extract and pay for the same scene twice. Each scene commits independently, so a failure late in a run keeps everything before it.
- **Extraction cache.** A scene already extracted from identical input under the same prompt version and model gets no job created at all: no row, no call, no spend. Editing any unit changes the content hash and invalidates it.
- **Output validation against Schema Lock v1.** Predicate signatures live in `ripple/graph/predicates.py` and are rendered into the prompt from the same source of truth. An assertion citing a unit the model was never shown, violating a signature, or falling below the confidence floor is dropped with a recorded reason rather than repaired. One bad assertion never loses the good ones in the same scene.
- **Provider settings.** Per-provider credential entry, validation against the provider's own models endpoint, and model selection. Keys go to the process environment and a gitignored `data/secrets.env` at mode 0600, or to Replit Secrets in production; never to the database and never back to the browser, not even masked. An invalid key is not stored.
- **Fixture provider** for deterministic tests without a credential. Absent from the shipped registry, and an unrecorded prompt raises rather than answering emptily, since an empty extraction is a legitimate outcome.

- **SQLAlchemy models for all 18 tables**, implementing the ERD with the kinded assertion endpoints Schema Lock v1 requires, so a scene can be a graph node. Enumerated values are CHECK constraints rather than native enums, keeping the schema portable between PostgreSQL and SQLite.
- **Persistence layer.** `persist_import` writes a parsed screenplay and its provenance; `delete_script`, `delete_all_scripts`, and `clear_all_graphs` implement ERD section 10 with counts returned before anything is removed.
- **Foreign keys enforced on SQLite** through a per-connection pragma, so local tests prove referential integrity rather than appearing to.
- **LLM provider adapters** for Google Gemini, OpenAI, Anthropic, and DeepSeek behind one contract. Model lists are read from each provider's own endpoint, never hardcoded, and filtered to text generation. Only Gemini ships; the other three are development aids listed for removal in TODO.md.
- **Title extraction** from the Fountain title page and the Final Draft `TitlePage` element.
- **`script_units.speaker_name`**, so a reload between import and extraction does not lose who was speaking.
- **Third demo screenplay**, SEVEN MINUTES, targeting graph behaviour: accented names, an omitted scene, an intercut, an entity that changes state, and an identity that resolves only in the final act.

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
