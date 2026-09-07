# Ripple

Ripple treats a screenplay as a production database rather than a static document. It imports Fountain, Final Draft XML, PDF, and plain text, or writes one from nothing in the page itself, parses them into structured script units, extracts production entities and evidence-backed assertions into a graph, and shows the downstream production and continuity impact of a proposed edit before anything is applied. A script written in Ripple exports as Fountain that imports back as itself.

The loop is: write or import a script, build its graph, edit a line, see the ripple, decide.

The full manual is [USAGE.md](https://github.com/shehuphd/ripple/blob/main/USAGE.md).

## Requirements

- Python 3.11 or newer
- Optional: `tesseract` and `pdftoppm` (poppler) for OCR of scanned PDFs. Without them, scanned PDFs are rejected with a stated reason rather than silently mis-parsed.

## Install and run

```bash
./launch.command
```

On Windows, run `launch.bat` instead; it does the same things.

This stops any Ripple server already running, creates `.venv`, installs the package, picks a free port, starts the server, and opens a browser. The library seeds the three bundled screenplays on first run, so there is something to read immediately. Run `./launch.command --test` to run the test suite instead.

To build a graph, add a provider key in Settings and choose a model. Nothing else needs a credential: import, the reader, the diff engine, and the deterministic continuity findings all work without one.

Manual equivalent:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/python -m pytest
```

## The first ten minutes

**See what the name means.** With the app open, press `Ctrl` `Opt` `Space`. The tab becomes a lake: stones drop, ripples spread, and as each wavefront passes a point a production entity surfaces there. Settings holds three looks for it: grey-blue water, dark, and warm paper. That is the idea the tool is built on, drawn rather than explained. Escape brings the app back where you left it, down to the scroll position.

**Open a script.** Three screenplays are already in the library, each with its graph built, so there is something to work on before you configure anything. Drop a Fountain, Final Draft, PDF, or plain-text file on the library to import your own, or press **New script** and write one in the page.

**Add a key.** Settings → Models: paste a Google AI Studio key, press Validate, then pick a main model. The key is checked against the provider's own models endpoint and stored in a local owner-only file, never in the database. Everything that does not call a model, importing, reading, writing, exporting, the diff engine, works without this step.

**Build the graph.** Open a script and press **Build graph**. Ripple reads one scene per call and writes what it finds: the cast, props, locations, wardrobe, and vehicles a production has to move, each assertion citing the line it came from. A scene commits on its own, so a build can be stopped, resumed, or left to run while you read.

**Change a line, and see the ripple.** Click into a line and edit it. The line goes amber, and **See ripple** puts the edit through the judgement pipeline before anything is applied: which stored facts still hold, what the graph gains and loses, which attributes changed, and any continuity conflict the change opens with the lines it contradicts. Accept applies it as one change set with undo. Reject leaves the script untouched.

**Read what it found.** The graph views show the production graph around a scene or an entity, **Continuity findings** lists the conflicts still open with a link into the line each is about, and **Ask Ripple** answers questions about the script from the graph, with its evidence, or takes a change request and plans it scene by scene. Every model call, its tokens, and its cost are in Traces and in the billable-actions ledger.

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

## Hooks

The repository carries its own git hooks, so the docs gate travels with it:

```bash
git config core.hooksPath .githooks
```

`pre-push` protects `main` and refuses a push whose docs have drifted from the code. Protection first: no deleting `main`, and no push that would drop commits the remote already has. Then ruff and the Shiplock gate. `RIPPLE_PREPUSH_TESTS=1` runs the suite there too, and `git push --no-verify` goes round all of it, so this guards against the slip rather than against intent. `commit-msg` refuses an attribution trailer: a co-author line, a generated-by line, or a tool's noreply address.

## Commits

One line, imperative, under about 60 characters. No body: the reasoning and the detail go in `CHANGELOG.md`, where they are read. A changelog entry is a bold title and a few sentences, 500 characters at most; `tools/check_changelog.py` refuses a longer one at the push, checking only entries the branch adds. Settings paths are written with arrows (Settings → Interface → Screensaver), so an entry says where the setting lives. No trailers of any kind either, so no co-authors, no attribution, no generated-by lines. The `commit-msg` hook enforces the trailer half of that; the rest is convention.

## Tests

```bash
.venv/bin/python -m pytest
```

Test-order randomisation is enabled. A failure that depends on order is a bug in shared state, not something to pin away.

## Status

Working: import adapters, the demo corpus, persistence, provider settings, scene-level graph extraction with attributes, the judged ripple preview, the deterministic diff engine, continuity retrieval, the synthesizer, the change-set service with undo, Ask Ripple (a chat over one script: grounded question answering with stored replays and a grounding check, and an agent for change requests that plans from graph coverage, drafts behind a minimal-edit guard and a confidence floor, previews through the judgement pipeline, and applies nothing itself — Confirm is a button), duplicate-entity detection and merges, batch actions across the list pages, a model-call audit with a token budget, a billable-actions ledger, and a fallback model, cancellable and resumable graph builds, a web application covering the library, reader, ripple preview, graph views, sortable audit tables, and settings, and a lake screensaver for the idle tab.

Not yet deployed anywhere; local development only.

## Licence

AGPL-3.0. See [LICENSE](https://github.com/shehuphd/ripple/blob/main/LICENSE).

By [Mo Shehu](https://mohammedshehu.com)
