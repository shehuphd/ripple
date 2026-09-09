# Changelog

All notable changes to Ripple are documented here.

## [1.0.0] - 2026-09-09

### Added

- **Delete all graphs, on the Graphs page.** Every stored graph could be removed at once through `POST /api/graphs/clear`, which no control reached, so a library made stale by a schema or prompt change had to be emptied from a shell. The button is beside the search box: it confirms first, names what goes, and reports the entities and assertions it removed. Scripts, scenes, and lines stay, and Build graph starts each one over.

- **A finished build says how many duplicates it left.** Detection ran only when someone opened the Entities page, so a build that resolved the same thing under two names ("Monitors" beside "Dispatch monitors") said nothing about it. The run reports the count when it ends and the reader shows it, with the review still on the Entities page: merging is a decision a person makes, and this only makes sure the decision is offered. The count is computed once the last scene is done, never mid-build.

- **An appearance records how a cast member is present.** An `appears_in` edge now carries a manner: on stage (physically in the action), referenced (named or spoken of but not present), or depicted (present only inside a photograph, recording, letter, or song). A speaking cue is on stage; the model tags the rest as it reads the scene. The reader and the graph label the referenced and depicted cases, and Ask Ripple can say a character is shown in a photograph rather than a flat "appears" or a false "does not appear". Rebuild a graph to capture the manner on a script read before this.

- **The reader toolbar opens Ask Ripple on its script.** An **Ask Ripple** button beside View graph opens the chat already pointed at the script being read.

- **An ambiguous change request asks before it plans.** "Add a new love interest" decides nothing about how far she reaches, so Ripple now puts that to you: one question with pressable options sized from the script's own scene and act counts, closed by a fixed "Decide for me". A press is the answer and planning continues from it, with the reply box as the write-in. A routine or fully specified change never asks.

- **The library batch-deletes scripts.** A checkbox per row, **Select all** over the rows the search shows, and **Delete selected scripts**, removing the chosen scripts whole behind a confirm. New: `POST /api/scripts/batch/delete`. Every list's batch controls also move beside Select all instead of floating at the foot of the page.

- **Graphs are a list, and one can be deleted without its script.** The sidebar gains **Graphs**: one row per built graph with status, counts, model, and build time. Rows batch-select, and **Delete selected graphs** removes the graph and everything under it while the script stays, ready to rebuild. New: `GET /graphs`, `POST /api/graphs/batch/delete`.

- **A Windows launcher.** `launch.bat` mirrors `launch.command`: stops any running instance, builds or repairs `.venv`, picks a free port from 8420, and opens a browser once the server answers. `--test` runs the suite. Untested on Windows.

- **ARCHITECTURE describes entity resolution.** Its three parts: alias-aware matching during extraction, the duplicate detector with reviewed merges, and rename detection across drafts. All deterministic; an ambiguous identity forks for review instead of merging on a guess.

- **Three lakes, and two rows for how one looks.** **Water** (grey-blue, deep cyan rings, white entity cards) is the default; **Dark** and **Light** are its night and paper versions. **Entity colour** gives each entity a random department colour, or none. Both rows in Settings → Interface → Screensaver.

- **The README opens with the first ten minutes.** Shortcut, lake, open or import a screenplay, add a key, build the graph, change a line, see the ripple. USAGE gained a screensaver section.

### Changed

- **A long scene is read in windows.** One call has a token ceiling and an attention span, and a stage play's act handed over whole came back as a summary of it: 42 assertions off the 1,546 units of Arms and the Man, with long stretches unread. A scene over 120 units is now read in windows of that size, one call each, merged into one answer for the scene; ids the model invents are namespaced per window so two windows cannot collide. The same play reads as 118 entities and 234 assertions. A screenplay scene fits one window and is unchanged.

- **An act splits into the scenes it is played in.** A stage play that prints no numbered scenes gave one scene per act, several hundred units long, and the graph, the reader, continuity findings and the single extraction call a scene gets all worked at that grain. Such an act now cuts at the entrances and exits the text marks, numbered under the act (1.1, 1.2). An act under sixty units is left whole, a part under eight joins the one before it, and a play that prints its own SCENE headings is never recut.

- **The seed reaches the provider on every call.** KeyCall 1.11 passes a seed through to the model, so the pin moves to it and the seed Ripple already sent on extraction and judge calls stops being dropped in transit. Three identical live calls came back identical, where the same call at the provider's default varied read to read. The variance check reports any scene whose extraction count still swings, so a prompt or gate regression surfaces on its own.

- **A rebuild replaces a scene's facts instead of adding to them.** A forced rebuild used to leave the prior read's edges in place and write the new read beside them, so the graph was the union of every rebuild and the assertion count only ever climbed. Each scene now supersedes its own earlier model and pre-pass edges before the fresh read is written, so the graph reflects the latest pass and the count holds steady across rebuilds. Edges you accepted or authored by hand are left untouched, and superseded edges are deactivated, not deleted, so undo and the audit trail still reach them.

- **Ripple runs on a host, not only a desktop.** `PORT` names the exact port and binds every interface, dropping the port scan, the browser, and the reloader; `RIPPLE_HOST` sets the bind address on its own. Both launchers honour them, and a new `.replit` skips the launchers and starts uvicorn on `0.0.0.0` directly. A desktop run sets neither and behaves as it always did: loopback, a scanned port from 8420, a browser, reload on edit.

- **The pre-push hook protects `main`.** It refuses a deletion of `main`, and a push that would drop commits the remote already has, before the docs gates run. GitHub's own protection needs a paid plan on a private repository, so this stands in for it; `--no-verify` goes round it, which makes it a guard against the slip rather than against intent.

- **Ask Ripple opens on a chooser, not the newest script.** Bare `/ask` used to assume the most recently created script, which after any test upload was the wrong one. It now asks which script to work over: every script lists in the sidebar pane with its graph mark and assertion count, and picking one opens its ask surface.

- **A library row says whether its script has a graph.** The glyph beside the title was decoration; it is now the graph mark in accent when a graph exists and the plain script mark when not, each with a tooltip. Two same-titled copies, one analysed and one not, read apart at a glance.

- **A byte-identical re-upload asks before importing twice.** Every upload's SHA-256 is already recorded; a file whose bytes match a script still in the library now comes back as a named duplicate instead of a silent second copy, and a dialog asks whether to import it anyway. Declining imports nothing.

- **The whole library is the import drop target.** The drop card is gone: holding a file over the window raises a "Drop to import" veil naming the formats, and dropping imports it, with **New script** and **Import script** in the toolbar as before. A file whose extension is not importable is refused with a toast, and a valid one shows a progress card, the file's name over a moving bar, that keeps the outcome and warnings after the reload.

- **Changelog entries are capped.** 800 characters at most, bold title plus a few sentences; `tools/check_changelog.py` refuses a longer entry the branch adds, in the pre-push gate. Settings paths use arrows (Settings → Interface → Screensaver).

- **The lake fades in and out.** **Fade** in Settings → Interface → Screensaver: Instant, 0.5s, 1s (the default), or 2s. Reduced motion skips it.

- **The library sorts, and its runtimes carry units.** `2:51` is now `2h 51m`. Column headers sort, a second click reverses, runtime sorts on minutes, and sorting and search run over the rows already in the page.

- **The banned-word gate covers more words, and commit messages.** Shiplock's sweep gains this repo's extra words (`shiplock.toml`), the docs are reworded to match, and the `commit-msg` hook runs the same list.

- **A saved screensaver row stops explaining itself.** The line under the card is silent on a save and speaks only for a refusal or the shortcut capture's guidance.

- **Every setting applies where it is saved, and in whatever else is open.** Saves announce themselves on the page and to other tabs, the Models card swaps its two states in place, and no setting needs a reload.

### Fixed

- **A new element an edit introduces reaches the preview.** The judgement pass verifies stored facts, and asked to also extract what an edit adds it kept missing a new prop ("she sips from her margarita"); because accept marks the scene extracted, the miss lasted until a forced rebuild. A dedicated extraction now runs over the proposed text, keeps only what the graph does not already hold, and folds it into the judgement, so the added element shows in the diff and applies on accept. The pass is advisory: if it cannot run, the judgement's own findings stand. Every extraction call is audited under its own purpose and shows as its own stage in the preview pipeline.

- **A character rename is flagged, even when the model judge misses it.** Renaming a cue (MARGOT to MAGGIE) changes no stored fact, so the judgement pass held every edge and the preview reported no change, while the old name stayed on every other line and the new name never entered the graph. A deterministic pass now compares each edited cue's old and new speaker: a rename raises a continuity finding that names the other lines still using the old name, and reads high when the name survives elsewhere. Rebuild the graph to record the new name as the character.

- **The judgement card says how many the verification dropped, not why, and stops repeating the tally.** Each dropped proposal was translated into a sentence in the pipeline's own vocabulary ("A new fact referred to something the reply never introduced"), which no reader can act on, and beneath a tally that already read "4 held, 0 new" the card added "Every judged assertion held unchanged" and "Verification dropped nothing". The card now shows the verdict rows, the count of what verification dropped, and a link to the trace where the reasons live.

- **A launcher rebuilds a virtual environment whose pip has stopped answering.** An interrupted upgrade, a moved interpreter, or a half-synced folder leaves a `.venv` whose python runs and whose pip does not, and both launchers went on to install into it and failed with a stack trace. They check pip first now and rebuild the environment when it cannot answer, once per run, so a machine that cannot build a working one says so instead of looping.

- **The docs match the code again.** A Shiplock semantic audit against 1.0.0 found 22 disagreements across all five doc surfaces, most of them older than the release: ARCHITECTURE denied that `launch.bat` exists, named a `fixture.py` that does not, counted four adapters where five are registered, and described neither the second provider registry nor Ask Ripple's agent; the README's format list omitted stage plays; MANIFEST counted 22 tables against 25; USAGE listed three run states against four. Each is corrected, and windowed reads gained the tests they lacked.

- **A curly apostrophe no longer makes a second entity.** Name normalization folded accents but left the typographic quotes and dashes alone, so a printed play's “lady’s bedchamber” and the straight-quoted form a person types in the reader were two locations past the unique index, and duplicate detection missed the pair because it compares the same unfolded keys. Those forms fold now, so the pair resolves to one entity as it is written. Entities already stored under the older key are merged from the Entities page.

- **Only a person appears in a scene.** The schema lock took any entity but a location as the subject of appears_in, while the prompt's own rules said cast, so the model followed the table and wrote a photograph "depicted" in a scene where the person in the photograph was the one being depicted. An object's presence is what requires and establishes record, and every object appears_in edge in the corpus duplicated one of those. The subject is cast now, and the extractor and the diff engine both refuse the rest. Rebuild a graph to drop the older edges.

- **A heading that says when is no longer a location.** The heading splitter hands back the whole heading when no INT./EXT. prefix matches, which is right for a slugline and wrong for a stage play's act, so "The sixth of March, 1886" and "In the library after lunch" became locations whose occurs_at edge pointed a scene at itself. A heading segment naming a time, a date, a circumstance, a stage direction, or OMITTED is refused now; a place carrying a clock ("Covent Garden at 11.15 p.m") is kept. Rebuild a graph to remove the entities an earlier read wrote.

- **Every model call sets its own temperature, and grounded queries repeat identically.** Nothing set a temperature, so every call ran at the provider's default (which is high) and the same input could vary run to run. The whole LLM layer now sets one on every call and defaults to the floor. The grounded-query path also pins a seed, which is what makes its output identical read to read: the temperature floor alone does not, checked against the model. Extraction and the judge run at the floor and pass a seed too, which takes effect as soon as their transport carries one. A companion check flags any scene whose extraction count still swings across reads, so a prompt or gate regression surfaces on its own.

- **Tooltips never clip at an edge.** The tooltip was a `::after` element aimed by a growing set of per-location rules; a trigger without one, such as the preview's "Write the explanation" button, grew a centred tooltip that ran past its container and lost its opening words. One floating element now serves every tooltip, positioned from the trigger's box and clamped to the viewport, so the full text shows wherever the trigger is.

- **Ask Ripple scopes a question to what it names.** A question's words were matched as substrings over the graph, so "appear" caught the `appears_in` edge on every cast cue and answered "who is the young woman" with a page of unrelated speaker names as its evidence. Matching now runs on whole words with question stopwords removed, and a question that names an entity is scoped to that entity's own edges. The evidence card prefers substantive lines over bare appearance cues, and an entity the graph holds no facts about returns an honest empty grounding instead of a keyword sweep.

- **In-script search matches across an accepted edit.** A search for a phrase straddling an accepted edit ("young man", where the edit had added "man and his little brother") found nothing, because the accepted words render inside a `<mark>` that splits the line into separate text nodes and the search matched one node at a time. It now searches each line's whole text and highlights the match even where it crosses the edit.

- **The screensaver footer reads "Back to Ripple".** "Exit" on a hosted page read as quitting the app rather than returning to it.

- **The plan card stops leaking its own instructions.** A covered scene the plan left out was added back carrying the model-facing words "Not covered by the plan. Say what changes here, or that nothing does.", counted in the changing tally, and rode into drafting as an instruction. The added row now reads "No change planned.", counts as unchanged, and drafts nothing; the push to decide those scenes goes to the model alone, told to restate the plan if any of them should change.

- **Sortable headers now carry no tooltip.**

- **An unnumbered script's scenes count from 1 in the graph.** A script with no numbered scenes labels them by position (`Sc 1`, `Sc 2`) instead of all `Sc —`. A script with some printed numbers keeps the dash for the unnumbered ones, so a counted label never collides with a printed one.

- **The import-review banner lays out correctly.** Heading over warnings, button top right; its wrapper no longer inherits the centred `grow` row.

- **A changed screensaver setting applies without a reload.** Opening the screensaver re-reads its settings, so a tab open in another window runs on the current values.

### Changed

- **The commit-message guard covers every attribution trailer.** Co-author, generated-by, and tool noreply addresses are all refused. The README states the convention: one imperative line, no body, no trailers; what a change was for goes in this file.

### Added

- **A lake screensaver for the idle tab.** After five idle minutes the tab becomes a lake: stones drop and entities surface on the wavefronts. Ctrl+Opt+Space opens it anywhere; Escape, Space, Enter or the shortcut closes it; a click throws a stone. It reads no script and calls no model, and extraction runs on behind it. Rows in Settings → Interface → Screensaver; reduced motion holds it still.

- **The docs gate runs before a push.** `.githooks/pre-push` runs ruff and the Shiplock gate; `RIPPLE_PREPUSH_TESTS=1` adds the suite and `--no-verify` goes round it. Enable with `git config core.hooksPath .githooks`.

- **The writing shortcuts moved behind a button.** **Shortcuts**, at the foot of the reader's sidebar, opens the reference: element markers on one side, editing keys on the other, the modifier named per platform. **?** opens it outside a text field and Escape closes it. The scene control returns to appearing on hover or keyboard reach.

- **A sound is not a speaker.** BAM, THUNDER, and GUNSHOT type like character cues. Terminal punctuation types them as action at once; the rest are retyped as action when the cue never receives a speech (a non-speech line written under it, or the scene left behind it). A cue the graph cites is untouched, and the reader repaints in place.

- **One marker per element, and the keyboard for the rest.** `@` cue, `"` dialogue, `(` parenthetical, `>` transition, `>>` shot, `!` action, `.` heading, `[[ ]]` note; markers are consumed, and six are Fountain's own. Unmarked lines go by position: dialogue under a cue or parenthetical, action elsewhere. Tab retypes a line, Shift+Tab opens the element list, Shift+arrows step lines, Cmd+Shift+arrows go to the ends, and Cmd+Z with Shift+Cmd+Z undo and redo all of it.

- **A script can be written in Ripple, not only imported.** **New script** starts an empty one: typing a heading starts a scene, every scene ends in a write-here line, and each line formats itself live (guessed type at the line's edge, Tab to correct, Fountain force markers override). With no graph an edited line saves on blur as an accepted change set; with a graph, edits stay drafts for See ripple, and a line sourcing an active fact refuses the direct paths. Backspace on an emptied line deletes it, the title renames in place, and **Export** downloads Fountain that reimports as itself. A written scene is indistinguishable from an imported one downstream.

- **The reader searches the script's words.** A box above the script counts matches, paints them all, and steps through them: Enter next, Shift+Enter previous, wrapping at the last. Cmd/Ctrl+F focuses it and Escape resets it. Painting uses the browser's highlight API, since the lines are editable; editing a line drops the search.

- **Continuity findings reach the line they are about.** A margin glyph marks every cited line, the toolbar chip walks them in script order, and a **Continuity** card lists open findings by scene and line, each a click from its line. **Mark resolved** and **Dismiss** close a finding without touching script or graph. New: `GET /api/scripts/{id}/findings`, `POST /api/findings/{id}/resolve`.

- **Ask Ripple: a conversational agent that works across the script.** A question still answers on the grounded path. A change request opens a conversation: Ripple searches the graph, computes the affected scenes, reads them, states a per-scene plan with its cost (**Go ahead** / **Adjust the plan**), drafts the lines, and runs them through the same judgement and continuity passes as a hand edit. Applying is a Confirm button on the page; the model side has no accept, reject, or undo. After Confirm the reply names the version move, tables what applied, lists open findings, and states the turn's cost, one grouped ledger action.

- **Conversations persist beside questions.** Two searchable panes: conversations left, single questions right. Both replay from storage at no cost, closing a conversation rejects its pending proposal, and a conversation is named by its opening request.

- **A confidence floor gates what Ripple proposes.** A fact judged below the floor (0.7 by default, configurable, off at zero) is withheld from the proposal and named on the card with its score. The text edit stands.

- **The agent is fenced the way the ask path is, and further.** Untrusted text reaches the model between per-turn random sentinels with lookalikes neutralised, tool arguments validate against the conversation's script, drafts pass a minimal-edit guard, the drafter runs toolless behind a schema, tool calls stop at the Settings ceiling, and every call is audited and counted against the budget.

- **Settings holds the Ask Ripple controls, and its sections say what they configure.** The rail reads Models, Spend, and Interface. Interface gains an Ask Ripple section: **Draft around a cut**, **Show the plan before drafting**, **Tool call ceiling per turn**, and **Keep conversations per script**. Each writes on change, an unrecognised value is refused, and an out-of-range ceiling falls back to the default.

- **A graph build runs on the server, so leaving the page no longer stops it.** A worker thread drains the run and the page polls, so a build survives a reload or a closed tab. Cancel still asks twice and stops after the scene in flight. Both build screens report output beside spend ("1,614 assertions extracted · 84,120 tokens · $0.12"). SQLite runs in write-ahead logging so progress reads never block.

- **A current graph offers a rebuild.** Once every scene matches its cached extraction the button reads **Rebuild graph**: it confirms first (every scene re-bills), then forces the run past the cache. Facts already in the graph stay.

- **Graph nodes can be dragged.** Edges redraw live, Shift+arrows nudge the selected node, a drag never counts as a click, positions hold across filter changes, and a reload returns to the computed layout.

- **The reader's scene list searches as you type.** A live filter over headings and scene numbers, with a "nothing matches" line.

- **The graph detail pane names its sections in department colour.** The Attributes and Assertions headings render as chips in the selected entity's hue.

- **A printed play's front matter stops leaking into the scene list.** A contents listing no longer imports as phantom scenes, nor a dramatis personae as cast; both drop with an import warning and the survivors renumber. A cue in Gutenberg's italics underscores ("_Hamlet._") reads as the plain name.

- **Destructive and disabled controls say so the same way everywhere.** Destructive actions share the red treatment and confirm in red; disabled controls carry a tooltip naming why they are off and what enables them.

- **"Needs review" now shows what to review, and a full act imports whole.** The reader opens with a banner naming each stored warning, **Mark reviewed** moves the script out of the review queue, and the library row counts the warnings. The per-scene unit ceiling now holds a full act (a stage play's acts are its scenes, and a full act of Chekhov was being cut at 500 units), and extraction's output ceiling rises with it.

- **The audit pages are sortable tables, and every list chooses its page size.** Traces, Reports, Findings, Entities, and Assertions sort by column, on values rather than cell text (numbers as numbers, severity and status by weight), with `aria-sort`, fixed column widths, a 25/50/100 pager kept per page, and the Traces window widened to 1000 rows.

- **Settings lists what has been billed, action by action.** The Spend tab gains a searchable table grouped the way the calls were made: a build one row, a preview one row, each question its own with the question under it. Grouping goes by script and call proximity, so a build whose run row is gone still reads as one action.

- **A graph build can be cancelled.** Cancel asks twice, stops after the scene in flight, closes the run as cancelled, and drops its pending cache rows so a later build resumes from the finished scenes. A run abandoned by a closed tab or a restart is replaced instead of colliding with the cache's unique key.

- **The reader toolbar reflects whether a graph exists.** Graph is a live link only with a graph to open. Build graph, when nothing is built, is amber with a slow halo pulse (a steady ring under reduced motion) and becomes "Update graph" or disables once one exists. The model-name chip is gone, and the empty script-graph page links to the reader.

- **See ripple waits for a graph to exist.** The button stays off until the script has an assertion to compare against; a seeded or partly built graph enables it.

- **A fuller character name widens the character instead of forking it.** "LUBOV" then "LUBOV ANDREYEVNA" resolves to one character with the other surface kept as an alias. Cast only, whole-word extension only, one candidate only: shared first names stay distinct and an ambiguous bare name forks. The duplicate-review panel stays the net beneath it.

- **Ripple imports stage plays, not only screenplays.** A stage-play format reads acts and scenes ("ACT I", "SCENE II. A platform before the Castle"), the opening setting becomes the heading, an all-caps name ending in a period is the speaker, and bracketed and Enter/Exit lines are directions. The Gutenberg wrapper and front matter drop, and the title reads from the "Title:" line. Detection keys on act and scene headings with no sluglines, so a screenplay still routes to its own parser.

- **The Ask answer states its true grounding and stops calling a sample its citations.** The grounding line carries the true distinct-unit total ("across {total} units") instead of the display cap, and the panel is retitled "Evidence", labelled "sample of 6 of {total}" when capped. The exported Markdown says the same.

- **Scanned PDFs import correctly through OCR.** Three fixes on the Tesseract path: blank lines advance the cursor so blocks separate, a heading or cue is forced onto its own block, and with indentation flattened the OCR path classifies by form (a short all-caps name, then its dialogue). A 7-page scan imports as 28 scenes, 35 speakers, and 42 dialogue lines, marked for review.

- **Extraction drops pronouns, screen text, and stray directions instead of writing them as entities.** A name-quality rule drops resolved pronouns ("He"), group labels ("Two Characters"), lifted screen text, transitions and shots typed as cues, and counted groups in cast ("Two men"), each with the reason recorded; generic roles ("Nurse") and counted props stay. Across the corpus the guards dropped junk and touched no valid name.

- **Ask the graph answers rosters, scene lists, and accent-typed names.** The packet now carries an entities-by-type roster and the full ordered scene list, so enumerations answer completely; question terms and assertion text are diacritic-folded, so "Bela" finds "Béla"; and the no-keyword fallback carries the whole graph up to a cap above the largest demo script.

- **The Ask path calls Google's `google-genai` SDK directly.** Ask uses the official SDK; extraction and judgement stay on KeyCall for schema enforcement and typed errors. The adapter maps onto `GenerationResult`, so the ledger, budget gate, and fallback behaviour are unchanged.

- **The ripple preview shows how it was computed, verdict by verdict.** A card renders the judgement: a tally, one row per changed, removed, or new edge with its verdict, a "verification dropped" section with reasons, and **Open full trace** into the TraceAct viewer. A multi-scene preview combines its judgements.

- **Ask the graph fences untrusted text and caps the question length.** The question and each quoted line reach the model between per-request random sentinel markers (`query.v6`), so a planted line cannot forge the closing marker or claim the trusted region resumed. A question caps at 1024 characters.

- **Ask the graph always makes a live call, and handles mixed questions.** The packet is script facts plus assertions, keyword-scoped or a whole-graph sample, so counting questions get written answers. The prompt treats the question as untrusted: off-scope parts and embedded instructions draw silence rather than an announced refusal, and scoping goes by provenance, so a screenplay about ripples stays answerable. The packet carries scene headings beside numbers, so an unnumbered script answers by heading. The output cap rises 600 → 2048, and prompt versions get a registry with a test failing an unregistered bump.

- **Scripts and history get a pane each, both searchable and resizable.** Scripts left, question history right, each with its own scroll, instant search, "nothing matches" line, collapse, and draggable persisted width. A new question appears at the top without a reload.

- **The ask page is laid out as one centred column.** One 860px axis, the ask box as a single pill, and **Export answer** in the toolbar once there is an answer. The grounding check counts containment in both directions, so "the blue sedan" matches a grounded "Blue sedan".

- **Ask the graph gets a proper empty state, and builds in place.** With no graph the page shows one centred action, **Build graph** (or **Choose a model**), and pressing it runs the build on the page with progress, spend, and pace. Leaving pauses it; returning resumes at no repeated cost.

- **Ask the graph gets its own left pane, exports answers, and checks its own grounding.** A pane of scripts with assertion counts and the chosen script's history; a past question replays its stored answer at zero cost. **Export answer** downloads Markdown. An answer naming a graph entity outside its evidence carries a "names outside grounding" tag.

- **Shiplock guards the docs against the code.** `shiplock.toml` plus deterministic checks in `tests/test_docs.py`: docs present, banned words, version alignment, the module list, the manifest, and README link hygiene. `shiplock check` runs the same gate from the terminal.

- **Lists act on many rows at once, and the Traces page opens the full viewer.** Entities and Assertions gain checkboxes, select-all honouring the search, and a floating batch bar: Merge, Keep separate, and Delete (typed confirm naming the cascade) on Entities; Deactivate and Reactivate on Assertions, with deactivated rows shown muted. Traces keeps no checkboxes and instead gains **Open in TraceAct viewer** over the whole log.

- **Duplicate entities are caught, and the ones that got through can be merged.** Extraction resolves proposed names against aliases, and a shared alias forks rather than guesses. The Entities page leads with suspected duplicate pairs; Merge moves everything onto the more-cited entity, records an accepted `merge_entities` change set with a snapshot, keeps the name as an alias, and bumps the script version; Keep separate stops the suggestion. Colliding attribute values keep the survivor's.

- **An accepted ripple no longer re-bills its scene.** Acceptance stamps each edited scene's new content as extracted, only when the pre-edit content had a completed extraction under the current prompt and model. The judgement also widens to cross-line effects: scene-local assertions of named entities are held unless the proposed text contradicts them.

- **Graph builds cost a fraction of what they did.** Thinking capped at medium, terser output formats (`extract.v5`, `judge.v5`, short ids), a deterministic pre-pass recording cast and location from cues and headings before any call, and escalation to the fallback model on a malformed reply. An evidence span past its line's end drops the span, not the item.

- **A linked draft's reader tints what changed against the predecessor.** Edited lines word-diff against the predecessor line, heading edits tint the header, a wholly new line tints whole, and a new scene is chipped "NEW IN THIS DRAFT". The predecessor baseline wins over the in-draft accepted-edit baseline.

- **Build graph is gated on billable work.** With everything cached the button disables and its tooltip says why; with changed scenes it reads Update graph and counts them, and only those bill. The gate reads the same content hashes the cache keys on.

- **Chargeable actions report their cost in money.** Builds, previews, and the Traces page price every row from the bundled `rates` registry, hidden reasoning billed at the output rate; an unpriced model shows tokens alone and a cached run reports zero.

- **Traces carry full payloads.** Record-by-default: every call's complete prompt, reply, and token usage, plus preview edits, questions and answers, import metadata, and rename payloads. `model_calls.reasoning_tokens` is stored and shown. Traces stay local in `data/traces/`, and credential-shaped values are still redacted.

- **Truncation blames the reasoning budget, not the model's limit.** The message states the reasoning tokens as the cause when the provider reports them, and names the budget as likely otherwise. The judgement output cap rises 4,096 → 8,192.

- **A failed preview says what stopped and offers the trace.** The error names the model, tokens produced against requested, and the finish reason; the banner gains **Open trace** into the TraceAct viewer, passed an absolute path.

- **Renamed characters keep their identity across drafts.** Transferred speaking positions are traced through line lineage: at three or more covering 80% the rename applies on its own (the entity keeps its row, takes the new name, and the old name becomes an alias); between 50% and 80% a `possible_rename` finding offers Confirm and Dismiss. A line still speaking under the old name becomes an open `partial_rename` finding.

- **The cross-draft ripple report.** Entities added and removed with the scenes responsible, moved attribute values, vanished introductions with surviving dependants, and judged continuity conflicts, one call per changed scene. The judged layer is advisory, findings carry Review and Dismiss, and the report refuses while changed scenes are unextracted.

- **Uncertain scene matches ask instead of guessing.** A pair between the thresholds comes up for review with headings, first lines, and similarity; ticked pairs link, unticked stay new.

- **A new draft continues the old one's graph.** A matching title offers "Link as new draft": deterministic alignment, unchanged scenes carry their facts across at zero model cost, changed and new scenes extract, and OMITTED declares a deletion. The predecessor is untouched, and every scene, line, and entity records what it continues.

- **KeyCall 1.8.0.**

- **Scenes can be added and omitted in the reader.** ＋ scene parses like an import and takes a lettered number (12A) with nothing renumbering; with a model selected only the new scene extracts. ⊘ marks OMITTED: facts deactivate as one audited change set, orphaned references become findings, and Restore reverses it all. Both bump the script version.

- **Complete ground truths for the demo corpus.** The three dependencies.md files inventory every scene: 82, 66, and 51 entities and 480, 354, and 315 seeded assertions. A coverage harness cross-checks every mention, and the remaining unlinked ones are each deliberate and documented.

- **Extraction demands completeness (`extract.v4`).** Every named object is a deliverable across all ten entity types, the attributes array is required, and the output cap doubles to 8,192 tokens.

- **A judged continuity pass.** The evidence packet is judged by the model, one call per preview; a conflict survives only with cited evidence ids and a valid severity. Findings persist with citations, replay at no cost, use the fallback model, and respect the budget; a failed call degrades to the deterministic findings with a banner.

- **Every list page searches and paginates.** One toolbar search filtering as you type plus Previous and Next, both display toggles over the rendered rows.

- **A keyboard and screen-reader pass across the app.** CSS tooltips with accessible names, keyboard-reachable graph nodes and rows, a full combobox for graph search, a proper tabs pattern in Settings, live regions for updates, a named confirm dialog, 44px touch targets on coarse pointers, and printed keyboard maps.

- **Findings are actionable everywhere.** Review opens the script and Dismiss records in place, on the findings page and in the preview overlay.

- **An Undo control in the reader.** One level, confirmed first; a refusal disables the control and says why.

- **`MANIFEST.md`**, a per-file map of the repository.

- **A docs-hygiene test.** `tests/test_docs.py` fails on internal references in public docs, relative README links, or a manifest miss.

- **`USAGE.md` is a full manual**, walking the application in order of use, with the refusal and failure code tables.

- **The preview is judged against the stored graph.** The model receives the stored assertions and attributes the edited lines support and answers a verdict per item: `holds`, `changed`, or `removed`, plus new items with cited evidence. Verdicts are verified in code: unlisted and duplicate ids drop, `holds` on deleted evidence downgrades to `removed`, a missing verdict fails the preview, and new items pass extraction's validation.

- **One proposal can span many lines.** One request, one scene per model call, one change set, one decision.

- **The changed words are highlighted.** A word-level diff, computed in code.

- **Attribute changes are their own card.** Entity, key, both values, and confidence; severity floored at medium; applied as `set_entity_attribute` and reversed by undo.

- **An identical preview is not billed twice.** A repeat of a pending proposal rebuilds from storage with no model call and says so.

- **A failed judgement persists nothing.** Provider, truncated, and malformed failures raise, retryable codes retry once, every call still reaches the audit table, and refusals return a code and a next step before provider contact.

- **Extraction reads attributes (`extract.v2`).** Entities carry an `attributes` array citing the unit and span; the validator enforces the fabrication rule, normalizes keys, and never overwrites an active value. Evidence offsets are bounded in the schema after a model free-ran an unbounded integer field.

- **Dead models are marked in the picker.** A `model_not_available` failure disables the model with "unavailable on this key"; a later success clears the mark.

- **The model explanation is opt-in.** `POST /api/changes/{id}/explain` writes prose on request; the always-shown summary is deterministic.

- **A Traces page, fed by every call site.** Every model call writes a `model_calls` row (prompt, reply, tokens, duration, outcome, validation summary), listed newest first with the spend ledger in the header.

- **A token budget with a hard gate.** One cap on total recorded tokens; at the cap every call site refuses before provider contact, audits the refusal, and names the fix. Refusals carry no token counts.

- **A fallback model.** On an availability failure the same call runs once against the fallback, at every call site; both attempts are audited, and a malformed reply never fails over.

- **Settings is tabbed.** API keys, Spend, and Interface; the chosen tab survives a reload.

- **Page errors render in the app.** An in-app view with the library and a back link; API routes keep JSON.

- **Clicking a selected graph node deselects it**, as does Escape.

- **Edges are drawn by family.** Presence solid, handling dotted, `requires` dashed amber, `establishes` a dashed accent over a glow, `interacts_with` a wide band. Directed families animate toward the object and carry a mid-edge arrowhead, and the dash pattern says what the colour says.

- **The graph zooms and pans.** Buttons, wheel zoom under the cursor, drag to pan, and arrow keys with +, -, and 0, on both graph views.

- **A search bar finds a scene or entity and selects it**, then centres and zooms the graph on it.

- **The findings chip opens the findings**, linking to `/findings?script={id}`, and pluralises correctly.

- **The version shows beside the mark**, reading "dev" with no package metadata.

- **Edge labels no longer pile up at hubs.** Each label takes its position from its edge's place in the busier endpoint's fan, with a nudge pass for what still overlaps. Deterministic, and verified overlap-free on all three demo scripts.

- **An unnumbered scene shows an honest placeholder**, `Sc —`, instead of a position that collided with a numbered scene.

- **Selection dims the unrelated graph**, alongside the accent ring and drawn edges, respecting reduced motion.

- **Scripts open on the script.** A Settings choice switches the opening view to the production graph.

- **The demo corpus opens with its graph.** Each script's ground truth seeds at startup from its `dependencies.md` (60+ entities, 260+ assertions, `provenance="system"`, zero model calls), idempotently.

- **The script-level graph is the opening view.** Every scene on a two-row spine in script order, entities in department wedges, filters for department and confidence, edges drawn for the selected node.

- **Entity detail card.** Type, description, aliases, attributes with evidence, assertions, and scenes.

- **Entity attributes.** Evidence-backed key-value facts, one active row per key, model rows required to cite evidence.

- **A model-call audit record.** Every interaction recordable in full as application data, deleted with its script.

- **`ARCHITECTURE.md`.** Structure, a system diagram from upload to acceptance, the data stores and integrations, and a glossary.

- **`Cache-Control: no-store` on every response**, covering the HTML entry point and a restart with unchanged files; the mtime query string still busts on file changes.

- **A failed provider call is now visible in the ripple preview.** `extraction_error` and `synthesis_error` ride the response and render as a banner instead of a confident-looking empty result. Caught live when the selected model had gone dead on the account; `model_not_available` now appends "Pick a different model in Settings."

- **Settings no longer tags the provider "ships with the submission."** With one provider the tag said nothing.

- **A frontend decision log.** Every handler records its decisions in a bounded in-page ring buffer, dumped by `ripple.debug()` and mirrored to `console.debug`. Credentials never reach a record.

- **Confirmations render in-app**, replacing `window.confirm`, with focus trapped and Escape to cancel.

- **`api()` distinguishes an unreachable server from a rejecting one.** "The server is not reachable. Is Ripple still running?"

- **A continuity warning no longer cites itself.** `detect_orphaned_references` now excludes every assertion the diff removes from the survival check, so an edit removing an entity's only introduction and only use stops citing the edited unit as the surviving reference.

- **Provider adapters now run on KeyCall 0.10.0.** One class parametrized per provider replaces three hand-rolled wrappers; credential validation, model listing, and structured output are KeyCall's job, and Gemini is the only provider left.

- **The script is editable in place.** Every line is `contenteditable`; drafts live only in the page, amber-marked and counted, Escape reverts a line. Enter opens the ripple and paste flattens to plain text.

- **Credential checks no longer guess at key formats.** A local check refuses only what cannot be a credential in any format; the provider decides the rest.

- **Collapsible side panes**, persisted per pane; below 900px the sidebar becomes a drawer.

- **Asset cache-busting** via a version derived from the newest file modification time.

- **Responsive breakpoints**, verified by measurement at 1600, 1024, and 480 pixels: no horizontal scrolling at any of them.

- **Graph rendering, 2D.** A deterministic server-side layout (`ripple/graph/layout.py`): focus centred, scenes on a spine, entities in fixed department wedges. Hand-rolled SVG, no graph library.

- **Expanded graph view** at `/graph/{unit}` with department filters, depth, a confidence threshold, a removed-edge overlay, and a detail pane.

- **`tools/seed_graph.py`**, writing the correct graph for scene 14 of the first demo script from its `dependencies.md`, with `provenance="system"`.

- **Analysis and Graph pages**: Ripple reports, Continuity findings, Entities, and Assertions, all linked in the sidebar before they existed.

- **Recently opened** is a working filter. `Script.last_opened_at` is set when the reader displays a script, distinct from `updated_at`.

- **Locked features look locked.** Disabled, greyed, with a banner naming the missing prerequisite and a link to it.

- **Independent pane scrolling** for the scene list, script, requirement pane, and the preview's two columns.

- **The UI rebuilt against the design mockups.** App-window shell, a fixed sidebar with live counts, a toolbar per screen, the script table, the reader with its scene sidebar, and the full-screen ripple preview.

- **Change-set service.** Atomic acceptance with base-version checks, rejection that changes nothing, and latest-only undo through an inverse change set carrying before and after text.

- **Synthesizer.** Explains a diff the engine already computed, given only the diff and findings; severity is computed in code, and no provider means a deterministic sentence.

- **Grounded query.** Answers from accepted assertions only, logging the ids it was allowed to use, and falls back to listing matches with no model.

- **Pages, runtime, and eighths**, calibrated against the demo corpus's rendered PDFs.

- **Web application.** Library with import and deletion, a reader at proper indents, the requirement pane, settings, and the browser-driven extraction loop. `./launch.command` installs, picks a free port, starts, and opens a browser; `--test` runs the suite. First run seeds the demo corpus.

- **Deterministic diff engine.** Edges compare by identity: `(type, normalized name)` endpoints, symmetric predicates ordered canonically, and a removal pairs with an addition only one-to-one per slot.

- **Continuity retrieval.** Deterministic gathering of earlier and later assertions on affected entities and neighbours, ranked and bounded before any prompt.

- **Orphaned-reference detection**, in code with no model: removing the only `establishes` edge for an entity later scenes still use warns whether or not the agent is available.

- **Scene-level graph extraction**, resumable and browser-driven. Scenes claim atomically so two tabs cannot pay for the same scene, and each commits independently.

- **Extraction cache.** Identical input under the same prompt version and model creates no job at all; any edit invalidates through the content hash.

- **Output validation against the predicate signatures**, rendered into the prompt from the same source of truth; a bad assertion drops with a recorded reason and never loses the good ones beside it.

- **Provider settings.** Credential entry, validation against the provider's models endpoint, and model selection. Keys go to the environment and a gitignored 0600 file, never to the database and never back to the browser.

- **Fixture provider** for deterministic tests without a credential; an unrecorded prompt raises.

- **SQLAlchemy models for all 18 tables**, with kinded assertion endpoints and CHECK constraints over native enums for portability.

- **Persistence layer.** `persist_import` plus deletion helpers that return their counts before anything is removed.

- **Foreign keys enforced on SQLite** through a per-connection pragma.

- **LLM provider adapters** behind one contract, model lists read from each provider's own endpoint; only Gemini ships.

- **Title extraction** from the Fountain title page and the Final Draft `TitlePage` element.

- **`script_units.speaker_name`**, so a reload between import and extraction keeps the speaker.

- **Third demo screenplay**, SEVEN MINUTES, targeting graph behaviour: accented names, an omitted scene, an intercut, a state change, and a late-resolving identity.

### Changed

- **The list pages sort, search, and page from their URL.** Sort, needle, page, and size are query parameters, the headers and pager are links, and the server renders only the shown rows: the Assertions page went from 2.7 MB and 2,201 rows to 67 KB in under 0.2 seconds. The batch control reads **Select all on this page**.

- **The ripple card offers to write the explanation.** One click asks the model for prose over the diff and findings and keeps it with the preview. The route existed with nothing calling it.

- **The Entities and library pages read the graph in a handful of queries.** Entities: 2,187 statements and 2.6 seconds down to 53 and under half a second. The library reads across all scripts at once.

- **One clock, one identifier parser, one scene label.** The services share `now()`, `as_uuid()`, and `Scene.label`, and every model call records through the same two ledger helpers.

### Removed

- `GET /api/scripts/{id}/runs`, an extraction-run listing nothing called, and a stylesheet the pages never loaded.

### Fixed

- **An exported script keeps every speech.** The cue opening the next speech overwrote the held one instead of flushing it, so two speeches in a row lost the first. A round-trip test pins it.

- **The element label no longer promises what the save will not deliver.** The reader applies the importer's own cue test, so "BAM!" shows and saves as action alike.

- **The script never scrolls sideways, and an unapplied edit stops narrating itself.** Search-bar tooltips anchor right and long lines wrap in their column; the in-line "edited, not applied" text becomes an amber margin dot with the count in the toolbar; and typing on a graphless script waits on nothing.

- **A camera direction is never a speaker.** The shot prefix is checked before the character cue, so "CLOSE ON THE KEY" stops reaching the graph as cast, and a shot written in Ripple survives a round trip.

- **A play's contents listing no longer opens scenes of its own.** The listing is recognised as a block, opening on "Contents" and ending at the cast list or the play itself.

- **A printed play's speeches import as speeches, not as line fragments.** Gutenberg's hard-wrapped lines join into one unit per paragraph, and a line continuing an open bracket carries the unit on. Mid-sentence units drop from 39–61% to 0–9%.

- **A selected line's caption stays under its own line.** It takes its line's own indent instead of a fixed offset that suited only action lines.

- **Shiplock 0.3 runs two more checks here**: `deps-declared-once` and `test-assertions`, both passing. The pin is `shiplock>=0.3,<1`.

- **Row actions keep their distance**, spaced in both directions so a stacked Dismiss stops crowding Review.

- **`tools/restate_findings.py` brings stored findings up to the current voice.** One audited call each, dry run until `--apply`, `--script` to limit it. The original wording stays on the row, and a truncated reply is refused.

- **A finding says what the script now says.** "Grey walking-costume is now a scarlet riding-habit" instead of an argument that something conflicts (`continuity.v2`); the deterministic orphan message follows the same form.

- **Severity dots draw their colour everywhere**, not only in the toolbar chip.

- **A plan stated on the last allowed tool call is a plan, not a stop.** The stated plan closes the plan stage whichever call states it.

- **A written explanation is no longer cut off mid-sentence.** The cap rises to 2,048 tokens, since hidden reasoning bills against the same ceiling.

- **Archer's Ibsen editions import as plays.** "ACT FIRST." parses, a cue waits across a blank line for its speech, a wrapped "scene." no longer opens a scene, "MRS. ELVSTED." is one name, a multi-line bracket stays a direction, and "THE END" stops speaking.

- **Continuity findings survive their own validation again.** The evidence packet now carries the assertion id the prompt asks the model to cite, so citations stop failing and the planted demo conflicts come back as warnings.

- **The judge reports what an edit adds, a stunt included.** The new-material arrays are required (an empty array is a stated answer), and the prompt extracts added material the same way as replaced.

- **Swapping an object for one of another type reads as a removal and an addition.** The pairing now includes the object's entity type.

- **An added edge names its scene** ("Sc 14") instead of its raw id.

- **Rejecting or undoing a proposal resolves its findings**, stamped with when.

- **A one-word edit can no longer read as "nothing changes".** With the complete ground truths, "Six monitors" to "Twelve monitors" answers with the count change; the summary also states what it judged, and says when the graph is incomplete.

- **Ground-truth seeding could touch a user's upload.** Scripts carry an `origin` column, seeding refuses everything not `bundled`, and existing rows re-mark only on evidence, never a title match.

- **The confidence numbers explain themselves** with hover text, keyboard-reachable and named for screen readers.

- **OCR of a multi-page scan wrote page images into the working directory.** The rasteriser runs one page per pass to stdout, and a regression test asserts the directory stays empty.

- **A removal must be visible in the edit (`judge.v3`).** A removed verdict stands only when a named endpoint is gone from the proposed text, and an attribute changes only through a line visibly carrying its value; downgrades are audited.

- **The launcher finds a working Python instead of trusting PATH's `python3`.** It reuses a valid venv without a system Python and otherwise probes version-named binaries, judging each by running it.

- **Predicates print as words in the UI** ("appears in"); the stored vocabulary is unchanged.

- **The launcher stops a stale server instead of adopting it**, verifies Python 3.11+, rebuilds a broken venv, and takes over a port held by another Ripple instance.

- **The virtual environments and runtime data are excluded from Dropbox sync**, so sync cannot invalidate the venv mid-run or replicate `data/secrets.env` off the machine.

- **Server rejections reach the page as sentences**, assembled from whatever the server sent; error badges print words, and the error page labels its number as an HTTP status.

- **Import warnings survive the library's reload** instead of being wiped after 0.9 seconds.

- **Forgetting a key asks first.**

- **`tools/seed_graph.py` explains an empty database**, exiting with the fix instead of printing success.

- **A selection made before the graph's own layout settled could render without dimming the rest of the graph.** The dim pass is now `draw()`'s completion callback, so it runs against the nodes on the page.

- **Accepting a "changed" assertion applied nothing.** The handler re-activated the row it had just deactivated; every accepted update now writes the new edge, and undo restores the old one.

- **Failed and refused model calls now reach the ledger.** Their audit rows commit before the error propagates instead of rolling back with the request.

- **A judged scene is never re-billed.** A retried preview replays judged scenes from their recorded replies as `cached` zero-token calls, and judge calls link to their own proposal.

- **An empty judge reply no longer passes as "No graph change".** A coverage miss fails the preview with `incomplete_judgement`.

- **A hold cannot survive on deleted text.** Only evidence text still in the proposed line keeps a `holds` verdict alive.

- **Stale proposals are recorded as stale.** The status re-applies in a fresh session after the 409, and a vanished unit records as `failed`.

- **Entities created through accepted changes keep their cased names.** Payloads carry the display name; lookups normalize either way.

- **Screenplay-derived text is escaped before it reaches the page**, through one shared helper across every page that renders it.

- **Extraction run status tells the truth.** Failed scenes mean `partially_ready`, and a fully cached run finishes immediately.

- **Settings validation never touches the process environment.** The candidate key rides the call itself and is discarded.

- **The preview's continuity context is scoped and ordered**: the same script only, anchored on the earliest edited scene in script order.

- **Invented scene numbers are gone everywhere.** One shared label map serves the web layer, the preview, and continuity.

- **New-assertion endpoints resolve by type**, constrained to what the predicate accepts on that side.

- **Evidence offsets are a pair or nothing**, ordered, and an out-of-range confidence is refused instead of clamped.

- **The extraction cache key covers the prompt.** The content hash folds in a fingerprint of the prompt material.

- **Opening a script is a POST, not a side effect of the GET**, so a prefetch cannot reorder Recently opened.

- **Stale links answer honestly.** An unknown script answers 404 instead of falling back silently, and the graph-lock notice and deletion preview scope to their own script.

- **Unexpected server errors answer in the app's own voice**, rendering the in-app error page (JSON on API routes) with details kept to the server log.

- **The graph page stopped burning a core.** The zero-size `draw()` retries are bounded, the expanded view's highlight applies to the clicked node and survives a resize, and the mini graph no longer traps page scrolling.

- **The preview overlay's buttons follow the server's state.** Reject clears the proposal, drafts compare through non-breaking spaces, See ripple disables with no draft, late replies cannot overwrite a newer line's panes, and Ask sends one question at a time.

- **Seeding survives edge input.** Heading-only scenes and empty-name rows are skipped.

- **Undo covers entity operations.** `create_entity` and `update_entity` invert, and a failed acceptance or undo still records its change set and unit.

- **The suite runs without the live credential**, pointing the secret store at an empty file and stubbing the settings and provider tests.

- **Text selection was unreadable**: grey secondary text on a pale blue block.

- **Ask the graph sent a null script id.** The script now comes from the server, which also handles the fallback.

- **Unnumbered scenes were given invented numbers**, listing two scene 9s.

- **Accented character cues were parsed as action.** The cue pattern is Unicode-aware, so `MATÍAS` keeps its dialogue.

## [0.1.0] - 2026-08-01

### Added

- **Import adapters for four formats.** Fountain, Final Draft XML, PDF, and plain text behind one contract; `import_screenplay` never raises, and a bad file returns a rejected result with a stable code.
- **Content-based format detection.** Extensions are hints that can break a tie, never override a content signal.
- **Structural screenplay check.** Scene count, dialogue presence and share, and action length gate acceptance, so prose and invoices are rejected with stated reasons.
- **PDF layout parsing via pdf-inspector.** Local, position-aware, no network calls and no models; scanned PDFs go through `pdftoppm` and `tesseract`, or are rejected with `ocr_unavailable` when either is absent.
- **Hardened XML parsing.** Final Draft files go through `defusedxml`, pinned by billion-laughs and XXE tests.
- **TraceAct 0.12.0 instrumentation** on `script.import`, with redaction presets and tests asserting no screenplay content reaches a trace.
- **Demo corpus.** Three original screenplays, each in four formats, each with a `dependencies.md` of expected entities, assertions, and planted dependency chains.
- **`launch.command`.** Path-safe bootstrap that creates the venv, installs the package, and runs the suite.
