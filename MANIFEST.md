# Manifest

Last updated: 2026-09-06 12:27:45 UTC

Every file in the repository and what it does. Directories the application writes at runtime (`data/`, `.venv`) are gitignored and not listed.

## Root

| File | Purpose |
|---|---|
| `README.md` | What Ripple is, what it does, and how to run it. |
| `USAGE.md` | The full manual: every screen, control, and error code. |
| `ARCHITECTURE.md` | System structure, data stores, integrations, a STRuFO run-through, and the domain glossary. |
| `CHANGELOG.md` | Release history, newest first. |
| `MANIFEST.md` | This file. |
| `LICENSE` | AGPL-3.0. |
| `launch.command` | macOS launcher: stops any running instance, builds or repairs the venv, starts the server, opens a browser. `--test` runs the suite instead. |
| `pyproject.toml` | Package metadata, dependencies, pytest and ruff configuration. |
| `shiplock.toml` | Shiplock docs-vs-code release checks: declared docs, version alignment, architecture and manifest coverage, one declaration per dependency, and an expectation in every test. |
| `.gitignore` | Excludes runtime state, virtual environments, and local tool settings. |
| `.github/dependabot.yml` | Weekly dependency update checks. |

## Application (`ripple/`)

| File | Purpose |
|---|---|
| `ripple/__init__.py` | Package marker and version lookup. |
| `ripple/text.py` | Display formatters the pages and services share (timestamps). |
| `ripple/tracing.py` | TraceAct configuration: no automatic input capture, redaction presets. |

### Import (`ripple/adapters/`)

| File | Purpose |
|---|---|
| `adapters/__init__.py` | The `import_screenplay` entry point: detect, parse, validate, return a typed result. |
| `adapters/base.py` | The adapter contract and shared types: `ParsedScene`, `ParsedUnit`, `ImportResult`, limits. |
| `adapters/detect.py` | Content-based format detection; the filename is only a hint. |
| `adapters/fountain.py` | Fountain parser. |
| `adapters/fdx.py` | Final Draft XML parser, via defusedxml. |
| `adapters/pdf.py` | PDF parser with OCR fallback for scanned documents. |
| `adapters/plaintext.py` | Plain-text screenplay parser. |
| `adapters/stageplay.py` | Stage-play parser: act/scene structure for public-domain plays. |
| `adapters/screenplay_check.py` | Post-parse assessment: is this a screenplay at all? |

### Configuration (`ripple/config/`)

| File | Purpose |
|---|---|
| `config/__init__.py` | Package marker. |
| `config/secrets.py` | Owner-only local credential file (`data/secrets.env`, mode 0600); refuses to write on Replit. |

### Database (`ripple/db/`)

| File | Purpose |
|---|---|
| `db/__init__.py` | Package marker. |
| `db/models.py` | SQLAlchemy models for all 22 tables, with CHECK constraints for every vocabulary. |
| `db/ids.py` | Identifier coercion: strings to UUIDs, shared by routes, payloads, and stored history. |
| `db/naming.py` | Entity name normalization, shared by extraction and the change-set service. |
| `db/repository.py` | Query helpers: persistence, deletion previews, counts, shared graph labels. |
| `db/session.py` | Engine and session setup, SQLite foreign-key and write-ahead pragmas. |

### Extraction (`ripple/extraction/`)

| File | Purpose |
|---|---|
| `extraction/__init__.py` | Package marker. |
| `extraction/prepass.py` | Deterministic pre-pass: cast from cues and the location from the heading, written without a model. |
| `extraction/prompt.py` | Per-scene prompt, `OUTPUT_SCHEMA`, and the cache-key fingerprint. |
| `extraction/service.py` | Resumable per-scene extraction runs with atomic scene claiming. |
| `extraction/validate.py` | Schema and predicate-rule validation of model output before any row is written. |
| `extraction/worker.py` | Background extraction: one thread per run drains the pending scenes so a build outlives the page that started it; the page polls progress instead of posting per scene. |
| `extraction/judge.py` | Code-side verification of preview verdicts: unlisted ids dropped, deleted evidence downgrades a hold, missing verdicts fail the preview. |
| `extraction/continuity_judge.py` | The continuity judgement contract: prompt, schema, and verification, every claim cited to listed evidence. |

### Graph (`ripple/graph/`)

| File | Purpose |
|---|---|
| `graph/__init__.py` | Package marker. |
| `graph/predicates.py` | The predicate vocabulary and signatures, one source of truth. |
| `graph/diff.py` | Deterministic diff of two assertion sets by edge identity. |
| `graph/continuity.py` | Bounded continuity evidence retrieval and the model-free orphaned-reference finding. |
| `graph/alignment.py` | Deterministic scene and unit alignment between drafts. |
| `graph/layout.py` | Deterministic 2D layout: scenes on a spine, entities in department wedges. |
| `graph/fixtures.py` | Judge-visible scene context assembly for the preview prompt. |

### Providers (`ripple/llm/`)

| File | Purpose |
|---|---|
| `llm/__init__.py` | The provider registry: Gemini over KeyCall for extraction and judgement, and the same models over the google-genai SDK for the Ask path. |
| `llm/base.py` | The provider contract and shared types (`ModelInfo`, `GenerationResult`, `ProviderError`). |
| `llm/keycall_provider.py` | The KeyCall-backed adapter extraction and judgement use. |
| `llm/genai_provider.py` | The google-genai SDK adapter the Ask path uses, so a Google SDK generation runs at runtime. |

### Services (`ripple/services/`)

| File | Purpose |
|---|---|
| `services/__init__.py` | Package marker. |
| `services/pricing.py` | Money for the token counts, from the rates registry. |
| `services/duplicates.py` | Suspected duplicate entities: deterministic detection, reviewed merges, keep-separate records. |
| `services/preview.py` | The judgement engine: one proposal across many lines, verdicts validated, calls audited and replayed. |
| `services/changeset.py` | Atomic accept, reject, and latest-only undo of a proposal. |
| `services/draft_report.py` | The cross-draft ripple report: entity deltas, lost introductions, judged conflicts. |
| `services/drafts.py` | Draft linking: alignment, lineage, graph carry-over. |
| `services/renames.py` | Rename detection from transferred speaking positions, auto-apply, confirm, and missed-instance findings. |
| `services/scenes.py` | Scene insertion, omission (OMITTED, reversible), and restoration. |
| `services/spend.py` | Token ledger and the hard budget gate every call site checks. |
| `services/settings.py` | Credential entry and validation, main and fallback model selection. |
| `services/retrieval.py` | The evidence packet one question is answered from: keyword retrieval with alias and diacritic folding, the entity roster, and the ordered scene list. |
| `services/agent.py` | Ask Ripple's agent loop: read-only tools plus a quarantined drafter, sentinel fencing, the minimal-edit guard, the confidence floor, and per-turn ceilings. Accept and undo are not tools. |
| `services/conversations.py` | Ask Ripple threads: stored turns replayed at no cost, thread history for the next turn, the applied summary, and rejection of abandoned proposals. |
| `services/synthesizer.py` | Plain-language ripple explanation and the grounded query answer (script facts + assertions packet, out-of-scope parts declined, answer checked for ungrounded entity names), each with a deterministic no-model fallback. |

### Web (`ripple/web/`)

| File | Purpose |
|---|---|
| `web/app.py` | All FastAPI routes: pages, JSON API, error handling. |
| `web/paging.py` | The list pages' query-string contract: sort, search, page, and size over the built rows. |
| `web/stats.py` | Sidebar counts and page/runtime estimates. |
| `web/templates/base.html` | Shared page shell: sidebar, toolbar, version mark. |
| `web/templates/_nav.html` | Sidebar navigation with live counts. |
| `web/templates/library.html` | Script library and import. |
| `web/templates/reader.html` | Screenplay reader, editing, and the ripple preview overlay. |
| `web/templates/script_graph.html` | The script-level production graph, the opening view. |
| `web/templates/graph.html` | The expanded per-node graph view. |
| `web/templates/ask.html` | Grounded query page, with its own pane of scripts and question history. |
| `web/templates/list.html` | Shared listing page: entities, assertions, reports, findings, traces. Renders one page of rows; headers, pager, and search link into the query string. |
| `web/templates/settings.html` | Tabbed settings: API keys, spend, interface. |
| `web/templates/error.html` | In-app error page for page routes. |
| `web/static/css/ripple-tokens.css` | Design tokens. |
| `web/static/css/ripple-fonts.css` | Font faces. |
| `web/static/css/app.css` | Per-screen styles. |
| `web/static/js/app.js` | Shared helpers: `api()`, escaping, dialogs, the decision log, and the collapse and drag-resize behaviour every pane uses. |
| `web/static/js/library.js` | Library page behaviour. |
| `web/static/js/reader.js` | Reader editing, drafts, and the preview overlay. |
| `web/static/js/graph.js` | Both graph views: rendering, selection, zoom, search. |
| `web/static/js/ask.js` | Grounded query page behaviour: asking, stored replays, Markdown export, the grounding tag. |
| `web/static/js/settings.js` | Settings page behaviour. |
| `web/static/ripple-mark.svg` | The product mark. |

## Tests (`tests/`)

| File | Purpose |
|---|---|
| `tests/__init__.py` | Package marker. |
| `tests/conftest.py` | Shared fixtures, including byte-precise XML attack payloads. |
| `tests/test_adapters.py` | Import pipeline across all four formats. |
| `tests/test_adversarial.py` | Hostile inputs: malformed files, encoding traps, oversized documents. |
| `tests/test_changeset.py` | Accept, reject, undo, and inverse operations. |
| `tests/test_alignment.py` | The draft aligner's passes and refusals. |
| `tests/test_drafts.py` | Draft linking and carry-over on a one-line revision. |
| `tests/test_renames.py` | Rename detection and application across drafts. |
| `tests/test_scenes.py` | Scene insertion, omission, and restoration. |
| `tests/test_continuity.py` | Evidence retrieval and orphaned-reference detection. |
| `tests/test_continuity_judge.py` | Continuity reply verification and prompt assembly. |
| `tests/test_diff.py` | Diff engine identity and grouping rules. |
| `tests/test_docs.py` | Docs hygiene: no internal references in public docs or shipped source, absolute README links, this manifest present, and the shiplock gate. |
| `tests/test_duplicates.py` | Duplicate detection, merge collisions and refusals, alias-aware resolution. |
| `tests/test_extraction.py` | Extraction service, validation, caching, and run status. |
| `tests/test_fixtures.py` | Judge-visible scene context assembly. |
| `tests/test_judge.py` | Verdict verification rules. |
| `tests/test_layout.py` | Layout determinism. |
| `tests/test_llm.py` | Provider contract and the KeyCall adapter. |
| `tests/test_genai_provider.py` | The google-genai adapter: SDK mapping, error codes, and the Ask-path routing. |
| `tests/test_models.py` | Schema constraints and vocabularies. |
| `tests/test_pricing.py` | Cost lookup, reasoning at the output rate, and honest unknowns. |
| `tests/test_prepass.py` | The deterministic pre-pass and the validator's provided-id and span-bound rules. |
| `tests/support/__init__.py` | Marks the test support package. |
| `tests/support/fixture_provider.py` | Deterministic no-network provider the suite runs the pipeline against. |
| `tests/test_preview.py` | The judgement engine end to end against the fixture provider. |
| `tests/test_agent.py` | The agent loop: the tool surface, ceilings, fencing, the plan stage, the draft guard, and the confidence floor. |
| `tests/test_settings.py` | Credential validation and model selection. |
| `tests/test_spend.py` | Budget gate and refusal auditing. |
| `tests/test_tracing.py` | TraceAct configuration and redaction. |
| `tests/test_tools.py` | The commands under `tools/`: which findings a restatement picks, and what it refuses to write. |
| `tests/test_paging.py` | The list pages' query-string contract: defaults left out of links, sort direction per column, page bounds, search over cell text. |
| `tests/test_web.py` | Routes, pages, and API behaviour. |

## Developer tools (`tools/`)

| File | Purpose |
|---|---|
| `tools/render_screenplay.py` | Renders a Fountain source to PDF for calibration. |
| `tools/restate_findings.py` | Rewrites stored continuity messages in the current continuity voice, dry run by default. |
| `tools/seed_graph.py` | Writes a demo script's ground-truth graph by hand. |

## Demo corpus (`demo-scripts/`)

Three screenplays, each in four formats (`.fountain`, `.fdx`, `.pdf`, `.txt`) with a `dependencies.md` recording the entities, aliases, attributes, and assertions a correct extraction must produce:

| Directory | Screenplay |
|---|---|
| `demo-scripts/01-night-freight/` | NIGHT FREIGHT. |
| `demo-scripts/02-the-understudy/` | THE UNDERSTUDY. |
| `demo-scripts/03-seven-minutes/` | SEVEN MINUTES, written to carry parser traps: accented names, an omitted scene, an intercut. |
