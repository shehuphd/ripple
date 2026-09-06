# Ripple

Ripple treats a screenplay as a production database rather than a static document. It imports Fountain, Final Draft XML, PDF, and plain text, parses them into structured script units, extracts production entities and evidence-backed assertions into a graph, and shows the downstream production and continuity impact of a proposed edit before anything is applied.

The loop is: select a unit, inspect its graph, edit it, see the ripple, decide.

The full manual is [USAGE.md](https://github.com/shehuphd/ripple/blob/main/USAGE.md).

## Requirements

- Python 3.11 or newer
- Optional: `tesseract` and `pdftoppm` (poppler) for OCR of scanned PDFs. Without them, scanned PDFs are rejected with a stated reason rather than silently mis-parsed.

## Install and run

```bash
./launch.command
```

This stops any Ripple server already running, creates `.venv`, installs the package, picks a free port, starts the server, and opens a browser. The library seeds the three bundled screenplays on first run, so there is something to read immediately. Run `./launch.command --test` to run the test suite instead.

To build a graph, add a provider key in Settings and choose a model. Nothing else needs a credential: import, the reader, the diff engine, and the deterministic continuity findings all work without one.

Manual equivalent:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest
```

## Importing a screenplay

```python
from ripple.adapters import import_screenplay

result = import_screenplay(open("script.fountain", "rb").read(), "script.fountain")
print(result.outcome, result.scene_count, result.unit_count)
for warning in result.warnings:
    print(warning.code, warning.message)
```

`import_screenplay` never raises for bad input. A file that cannot become a screenplay returns an `ImportResult` with `outcome` `REJECTED` and a `rejection_code`.

| Outcome | Meaning |
|---|---|
| `accepted` | Parsed with no findings |
| `accepted_with_warnings` | Parsed, with non-blocking findings recorded |
| `needs_review` | Parsed, but structure was inferred rather than read (OCR, no indentation, weak signal) |
| `rejected` | Not importable; `rejection_code` says why |

Format detection reads the bytes. A file extension is a hint that can break a tie between two text formats and never overrides a content signal.

## Demo corpus

`demo-scripts/` holds three original screenplays written for this project, each authored in Fountain and rendered to Final Draft XML, PDF, and plain text by one tool:

```bash
.venv/bin/python tools/render_screenplay.py
```

Each script ships a `dependencies.md` recording the entities, assertions, and planted dependency chains a correct extraction must produce.

## Tracing

Meaningful actions are traced with [TraceAct](https://github.com/traceact/traceact), full payloads included: every model call records its complete prompt, reply, and token usage (answer, hidden reasoning, input) on the trace, so a failed run is diagnosed by reading its trace rather than reconstructing a cause. Traces are written to `data/traces/`, which stays on the local machine and out of version control. Credential-shaped values are still caught by TraceAct's value-pattern redaction.

Set `RIPPLE_TRACING=off` to disable trace writing.

## Tests

```bash
.venv/bin/python -m pytest
```

Test-order randomisation is enabled. A failure that depends on order is a bug in shared state, not something to pin away.

## Status

Working: import adapters, the demo corpus, persistence, provider settings, scene-level graph extraction with attributes, the judged ripple preview, the deterministic diff engine, continuity retrieval, the synthesizer, the change-set service with undo, Ask Ripple (a chat over one script: grounded question answering with stored replays and a grounding check, and an agent for change requests that plans from graph coverage, drafts behind a minimal-edit guard and a confidence floor, previews through the judgement pipeline, and applies nothing itself — Confirm is a button), duplicate-entity detection and merges, batch actions across the list pages, a model-call audit with a token budget, a billable-actions ledger, and a fallback model, cancellable and resumable graph builds, and a web application covering the library, reader, ripple preview, graph views, sortable audit tables, and settings.

Not yet deployed anywhere; local development only.

## Licence

AGPL-3.0. See [LICENSE](https://github.com/shehuphd/ripple/blob/main/LICENSE).

By [Mo Shehu](https://mohammedshehu.com)
