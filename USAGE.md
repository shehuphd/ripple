# Ripple: Usage

Ripple treats a screenplay as a production database. You import a script, build a graph of production entities and evidence-backed assertions from it, then edit lines and see the downstream production and continuity impact before anything is applied. This manual walks through the application in the order you'll use it, then covers the import formats, the error codes, and the developer tools.

## Starting Ripple

```bash
./launch.command
```

The launcher stops any Ripple server already running, verifies Python 3.11 or newer, creates or repairs its own virtual environment in `tools/.venv`, installs the package, starts the server on the first free port from 8420, and opens a browser. Run it again at any time; you always get one fresh instance.

```bash
./launch.command --test
```

runs the test suite instead of the server, forwarding any extra arguments to pytest.

| Environment variable | Effect |
|---|---|
| `RIPPLE_PORT` | First port to try instead of 8420. |
| `DATABASE_URL` | PostgreSQL connection string instead of the local SQLite file. |
| `RIPPLE_SECRETS_PATH` | Alternate location for the local credential file. |
| `RIPPLE_TRACING=off` | Disables TraceAct trace writing. |

On first run the library seeds three bundled demo screenplays, each with its ground-truth production graph already built, so every feature is explorable before you configure a model.

## The library

The library lists every imported script with its format, page count, scene count, estimated runtime, and import outcome. The sidebar filters to recently opened scripts and to imports that need review.

**Import** accepts Fountain, Final Draft XML, PDF, and plain text. Detection reads the file's content; the extension only breaks ties between text formats. Every import ends in one of four outcomes:

| Outcome | Meaning |
|---|---|
| `accepted` | Parsed with no findings. |
| `accepted_with_warnings` | Parsed, with non-blocking findings recorded. |
| `needs_review` | Parsed, but structure was inferred rather than read (OCR, no indentation, weak signal). |
| `rejected` | Not importable; the rejection code says why. |

A rejected import changes nothing. Deleting a script first shows a deletion preview counting everything that goes with it: scenes, units, entities, assertions, findings, and traces.

## Settings

Open **Settings** from the sidebar. It has three tabs.

**API keys** holds the provider credential and the model choice. Paste a Google AI Studio key and press Validate; the key is checked against the provider's own models endpoint, never against a guessed format, so any key Google issues works. A valid key is stored in `data/secrets.env` (owner-only file permissions), never in the database and never sent back to the browser. Then pick a **main model** and, optionally, a **fallback model**. When the main model refuses for an availability reason (dead on this key, an outage, a rate limit, a timeout), the same call runs once against the fallback. A model recorded as unavailable on your key is disabled in the picker until a later call succeeds on it.

**Spend** shows the recorded model spend (calls, tokens in, tokens out, by purpose) and takes one number: a cap on total recorded tokens. When the recorded total reaches the cap, every model call refuses before contacting the provider and tells you so. Raising or clearing the cap reopens the gate.

**Interface** chooses what opening a script shows first: the production graph (the default) or the reader.

## Building a graph

Open a script and press **Build graph**. Extraction runs one scene per model call, in the browser's control: each scene commits independently, so a failure late in a run keeps everything before it, and re-running resumes from the unfinished scenes instead of restarting. A scene already extracted from identical input under the same prompt and model is served from the cache with no call and no spend.

A run reports its spend as it goes: the progress counter and the finished label carry the tokens used and, for a model the pricing registry knows, the dollar cost (from the bundled `rates` snapshot). A run served from cache reports nothing spent.

The button is gated on billable work. Once the graph is built and every scene's content matches its cached extraction, it disables, with the reason in its tooltip. When scenes have changed since the last build (an accepted ripple, an added or restored scene, a linked draft, or a switched model), it reads **Update graph** and its tooltip counts the changed scenes; only those are billed, since the rest replay from cache.

A run ends in one of three states:

| Status | Meaning |
|---|---|
| `ready` | Every scene completed. |
| `partially_ready` | Some scenes completed, some failed. Re-run to retry the failures. |
| `failed` | No scene completed. |

Extraction reads entities (cast, props, wardrobe, locations, and the other departments), their aliases, typed attributes (`color: emerald`, cited to the line that states it), and assertions: evidence-backed edges such as MARA `wears` the emerald gown in scene 12. Model output is validated before any row is written; an assertion citing a line the model was never shown, violating a predicate signature, or falling below the confidence floor is dropped with a recorded reason.

## The graph views

Opening a script shows its production graph: every scene on a two-row spine in script order, every entity in a fixed wedge for its department. Filters narrow by department and minimum confidence.

- **Select** a node by clicking it or through the search box; everything unrelated dims, the node's edges draw, and the detail pane fills. Clicking the selected node again, or pressing Escape, deselects.
- **Zoom and pan** with the mouse wheel, dragging, the on-canvas controls, or the keyboard (arrows pan, `+` and `-` zoom, `0` resets) when the canvas has focus.
- **Search** filters live over every node label; picking a result selects it and centres the view on it.

An entity's detail card shows its type, description, aliases, attributes with their evidence lines, every assertion it participates in, and the scenes it spans. The **expanded view** (`Open` from the detail pane) adds depth control, a removed-edge overlay, and per-department filters for one node's neighbourhood.

Edges are drawn by predicate family, so the line style says the same thing the label does: solid for presence, dotted for handling, amber dashes for `requires`, an accent dash for `establishes`, and a wide band for `interacts_with`. The legend on the canvas shows each family.

## Editing and the ripple preview

In the reader, every line is editable in place. Typing marks the line "edited, not applied" in amber; the toolbar counts unapplied edits; Escape reverts a line and **Revert edits** discards them all. Drafts live only in the page. The accepted script does not change until you accept a ripple.

**See ripple** sends every drafted line as one proposal. The engine judges each affected scene in one model call, plus one continuity call per preview: the model is shown the stored assertions and attributes those lines support, each with its evidence, and returns a verdict per item: `holds`, `changed`, or `removed`, plus any new items with cited evidence. Every verdict is verified in code before anything persists; a reply that skips a listed item fails the preview rather than guessing.

While the judgement runs, the overlay shows a waiting interstitial (water, a spreading ripple, "Calculating ripples…") that fades into the results. The preview overlay shows the accepted and proposed text side by side with the changed words highlighted, the graph diff, attribute changes, continuity findings, per-stage timings, and a deterministic summary. **Explain** asks the model for prose on demand; the summary itself never costs a call. After acceptance, the changed words render with a revision tint in the reader, so a skim shows what moved; clicking into a line clears the tint for editing. **Accept** applies the whole proposal atomically, with a base-version check so an edit made against stale text is refused as `stale` rather than applied. **Reject** discards it and changes nothing.

A repeat of a pending proposal with the same lines and texts is rebuilt from the stored result with no model call, and the overlay says so. Undo (one level, latest change set only) restores the previous state through an inverse change set; the undone original is recorded as `reverted`.

The preview refuses, before any model contact, with one of these codes:

| Code | Cause | Fix |
|---|---|---|
| `no_change` | Nothing differs from the accepted text. | Edit a line first. |
| `unknown_unit` | An edited line no longer exists. | Reload the reader. |
| `cross_script` | One proposal spans two scripts. | Propose per script. |
| `no_baseline` | No graph exists for this script. | Build the graph first. |
| `no_model` | No model is selected. | Pick one in Settings. |
| `budget_exceeded` | The token budget is spent. | Raise or clear it in Settings. |

A preview that fails after model contact persists nothing except its audit record:

| Code | Cause |
|---|---|
| `output_truncated` | The model stopped at an output limit. The message names the model, the answer tokens (and any hidden reasoning tokens, which bill against the same output budget) against the tokens Ripple requested, and the finish reason. |
| `malformed_response` | The reply was not valid against the judgement schema. |
| `incomplete_judgement` | The reply skipped listed items; treating them as unchanged would be a guess. |
| `model_not_available` and other provider codes | The provider refused the call; the message carries the provider's reason and a next step. |

Retryable provider errors are retried once, and a retried preview replays already-judged scenes from their recorded replies at no cost.

The preview's footer reports what the run spent: total tokens (answer, hidden reasoning, and input) and, for a model the pricing registry knows, the dollar cost. A cached replay reports zero.

A failed preview's warning banner also carries **Open trace**: it starts (or reuses) the local TraceAct viewer and opens it on that run's trace, map view, filtered to the one trace, so the failing step and its recorded error are one click away.

## Adding and omitting scenes

The reader edits the script's structure as well as its lines.

**Add a scene.** Hover between two scenes (or above the first) and press **＋ scene**. The dialog takes a heading and the scene text; the text is parsed with the same rules an imported file gets, so character cues, parentheticals, and transitions all work. On a numbered script the new scene takes a lettered number per the production convention: after scene 12 comes 12A, and a scene above the first is A1. Every other scene keeps its number. When a model is selected, the new scene is extracted immediately, and only the new scene: nothing else is re-billed. Without a model, the scene inserts and its requirements wait for the next graph build.

**Omit a scene.** Hover a scene heading and press **⊘**. The scene keeps its row and its number and reads OMITTED; its stored facts deactivate as one recorded change, and every entity it introduced that later scenes still use becomes an open continuity finding citing the surviving lines. **Restore**, on the omitted heading, reverses all of it: the facts reactivate and the omission's findings resolve.

Both operations move the script's base version, so a ripple preview drafted against the old structure is refused as stale rather than applied against a script it never saw.

## Importing a new draft

Uploading a file whose title matches a script that already has a graph offers a choice: link it as the next draft, or keep it separate. Nothing links without your answer.

Linking aligns the two drafts in code, with no model call: scenes with identical content match first, then matching locked scene numbers, then an order-preserving text-similarity pass that only accepts strong matches. What happens next depends on how each scene aligned:

| Scene | What happens | Model cost |
|---|---|---|
| Unchanged | Its entities, facts, attributes, and evidence copy across | none |
| Changed | Extracted fresh; untouched lines inside it keep their lineage | one extraction |
| New | Extracted fresh | one extraction |
| Deleted (or replaced by an OMITTED placeholder) | Recorded; nothing carries | none |

With a model selected, the changed scenes extract as soon as the link completes, and the reader opens on the new draft driving that run. The previous draft is untouched, and the new draft's toolbar links back to it ("Draft 2 · open draft 1").

A linked draft's reader also tints what changed against the predecessor: edited words in changed scenes and their headings carry the same revision tint accepted ripples get, and a scene the previous draft did not have is chipped "NEW IN THIS DRAFT" rather than tinted word by word. The previous draft's reader stays unmarked.

**Uncertain matches ask you.** A pair of scenes that look related but not similar enough to link automatically comes up for review before the link runs: both headings, first lines, and the similarity, each with a checkbox. Ticked pairs carry their identity across as rewrites; anything unticked is treated as a new scene and re-read. Cancelling the review cancels the whole link, leaving the upload as a separate script.

**The draft report.** Once the changed scenes are extracted, Ripple compares the drafts and writes a report (on the Reports page, its findings on the Findings page):

- entities the revision added and removed, with the scenes responsible
- attribute values that moved between drafts (a count, a colour, a condition), with before and after
- introductions that vanished while their dependants survive: the previous draft established something, this draft still uses it, and no scene here introduces it
- continuity conflicts, judged one model call per changed scene against the same earlier-and-later evidence the ripple preview uses; this layer is advisory, so a failed call becomes a note in the summary, never a failed report

The report refuses while changed scenes are still unextracted, because a missing extraction would read as missing content. Each finding carries Review (jumps to the surviving lines) and Dismiss, like every other finding.

**Renames.** Before reporting an added and a removed character as two events, the report checks whether they are one: dialogue lines that carried over word-for-word but changed cue name are counted as transferred speaking positions. Strong evidence (three or more transferred positions covering at least 80% of the old speaker's carried lines) applies the rename on its own and the summary reads "Renamed: X to Y"; the old name becomes an alias, so a mention of either name resolves to the same character in every draft. Middling evidence (half or more) becomes a `possible_rename` finding with the counts, and the findings page offers **Confirm rename** to join the identities or Dismiss to keep them separate. After any applied rename, a line still speaking under the old name is filed as a `partial_rename` finding citing the lines and scenes: the rename that missed an instance, reported instead of guessed around.

## Continuity findings

Findings are evidence-backed warnings about a proposal's effect on other scenes, and they come from two places. One is computed with no model at all: removing the only line that establishes an entity while later scenes still reference it. The rest come from a continuity judgement, one model call per preview: the model is handed the edit, the graph changes, and a bounded packet of earlier and later facts about the affected entities, each with the line that states it, and reports the conflicts the edit creates. A reported conflict is kept only when it cites evidence the model was shown, and it carries its own severity. The pass is advisory: when its call fails, the preview stands on the deterministic findings and a banner says so.

Findings appear in the preview overlay, where Review units jumps to the cited lines and Dismiss records a dismissal, and on the **Continuity findings** page, filtered per script. The findings page's Review opens the script scrolled to the cited lines, marked for a moment; evidence citing a scene heading opens on that scene.

## Ask the graph

**Ask the graph** answers natural-language questions from accepted assertions only. Every answer records which assertion ids it was allowed to use, and with no model configured it falls back to listing the matching assertions.

## Reports, entities, assertions, and traces

- **Ripple reports** lists every preview's stored report.
- **Entities** and **Assertions** list the graph row by row, with evidence.
- **Traces** lists every model call, newest first: purpose, model, token counts, duration, and outcome, with the spend ledger in the header. Rows are application data, deleted with their script.

| Trace outcome | Meaning |
|---|---|
| `ok` | The call completed and validated. |
| `cached` | Replayed from an identical earlier call; zero tokens. |
| `truncated` | The reply was cut off. |
| `malformed` | The reply failed validation. |
| `incomplete` | The reply skipped listed items. |
| `provider_error` | The provider refused the call. |
| `budget_refused` | Refused before contact; the budget was spent. No tokens. |

## Import formats

| | Fountain | Final Draft XML | PDF | Plain text |
|---|---|---|---|---|
| Element types | Read from markup | Read from `Paragraph Type` | Inferred from position | Inferred from indentation |
| Scene numbers | `#N#` markers | `Number` attribute | Margin-printed numbers | Trailing `#N#` if present |
| Provenance | Character offsets | Block index | Page, block index, bounding box | Character offsets |
| Notes | Parsed as `note` units | Not present | Not present | Not present |

### PDF

Text-based PDFs are read locally with no network calls and no models. Layout rules measure the page's own left margin, then classify blocks by their offset from it, so a script typeset at any margin parses the same way. Blocks split on vertical space, a page break, or a horizontal shift; the shift rule is what separates a character cue, its parenthetical, and its dialogue, which fall on consecutive lines with only their left edge to tell them apart.

A scanned PDF is rasterised with `pdftoppm` and read with `tesseract`, both as local subprocesses. No bytes leave the machine and nothing is written to disk. If either binary is absent the import is rejected with `ocr_unavailable`, naming what is missing. OCR-derived imports are always `needs_review`.

### Security

Final Draft XML is parsed through `defusedxml`; external entities and entity expansion are refused with `xml_unsafe`. Uploads over 8 MiB are rejected before any parser runs. Scene and unit counts are capped, and truncation is reported as a warning rather than applied silently.

### Rejection codes

| Code | Cause |
|---|---|
| `payload_too_large` | Over the 8 MiB upload ceiling. |
| `undecodable_text` | Not UTF-8, UTF-16, UTF-32, or Windows-1252. |
| `empty_document` | The file contains no text. |
| `unsupported_format` | Not one of the four supported formats. |
| `no_scenes` | Parsed, but no scene headings were found. |
| `not_a_screenplay` | Parsed, but the structure is not a screenplay's. |
| `xml_malformed` | Not well-formed XML. |
| `xml_unsafe` | External entities or entity expansion. |
| `fdx_wrong_root` | XML root is not `<FinalDraft>`. |
| `fdx_no_content` | No `<Content>` element. |
| `pdf_unreadable` | Encrypted or damaged PDF. |
| `pdf_no_text` | No text extracted. |
| `ocr_unavailable` | Scanned PDF with no local OCR toolchain. |
| `ocr_timeout` / `ocr_failed` | OCR ran and did not finish, or failed. |
| `pdf_backend_missing` | `pdf-inspector` not installed. |

## Tracing and debugging

Meaningful actions (import, extraction, preview, accept, undo, query, draft linking, renames) are traced with TraceAct to `data/traces/traces.jsonl`, full payloads included: every model call's complete prompt, reply, and token usage (answer, hidden reasoning, input), every preview's edits, every query's question and answer. The traces are the debugging record; when something fails, its trace says why. They stay on the local machine, out of version control, and credential-shaped values are still caught by value-pattern redaction. The Traces page remains the application's own model-call audit; the reader's page decisions also mirror to the browser console, where the traceact-browser extension can capture them.

In the browser, `ripple.debug()` in the console dumps the frontend decision log (API outcomes, preview results, draft transitions, settings changes), `ripple.debug('preview')` filters it, and `ripple.debug.table()` renders it as a table.

## Developer reference

### Programmatic import

```python
from ripple.adapters import import_screenplay

result = import_screenplay(open("script.fountain", "rb").read(), "script.fountain")
print(result.outcome, result.scene_count, result.unit_count)
for warning in result.warnings:
    print(warning.code, warning.message)
```

`import_screenplay` never raises for bad input; anything unparseable returns a rejected result with a stable code.

### Adding an adapter

Implement the `ImportAdapter` protocol in `ripple/adapters/base.py` and register it in `ADAPTERS`:

```python
class MyAdapter:
    name = "my_format"
    detected_format = DetectedFormat.MY_FORMAT

    def detect(self, payload: SourcePayload) -> float: ...
    def parse(self, payload: SourcePayload) -> tuple[list[ParsedScene], list[ImportWarning]]: ...
```

`parse` raises `ImportRejected(code, message)` for anything unparseable; the entry point converts it to a rejected result.

### Demo corpus

`tools/render_screenplay.py` renders each authored Fountain source to Final Draft XML, PDF, and plain text. The renders diverge from the source in three documented ways: Fountain notes are stripped from every derivative, dual dialogue flattens to sequential dialogue, and shot lines type as action. Each script's `dependencies.md` records the expected extraction output and the expected divergence. `tools/seed_graph.py` writes a script's ground-truth graph by hand; the application runs the same builder at startup.

### Tests

```bash
tools/.venv/bin/python -m pytest
```

Test-order randomisation is enabled. A failure that depends on order is a bug in shared state, not something to pin away.

By [Mo Shehu](https://mohammedshehu.com)
