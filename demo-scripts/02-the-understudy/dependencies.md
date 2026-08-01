# THE UNDERSTUDY: ground truth

Original work written for the Ripple demo corpus. No third-party rights attach; it ships under the repository licence.

41 scene headings across 39 base scenes plus revision inserts 11A and 30A. 9 script pages plus a title page. Written against Schema Lock v1.

Script 01 covers a contemporary thriller with terse action and few departments. This one is deliberately its opposite: period, ensemble, and heavy on the departments 01 barely touches. Between them the two scripts exercise all eight unit types and all ten entity types.

## 1. What this script tests that 01 does not

| Feature | Where | Why it's here |
|---|---|---|
| Lettered scene numbers | 11A, 30A | `scenes.display_scene_number` is a string for precisely this reason, and nothing tested it |
| Forced scene heading | 14, `.MONTAGE - EIGHT PERFORMANCES` | A numbered sequence with no INT/EXT prefix |
| Shot lines | 2 (`ANGLE ON`), 17 (`CLOSE ON`) | The `shot` unit type is unused in 01 |
| Fountain note | 34, `[[...]]` | The `note` unit type is unused in 01 |
| Transitions | 7, 20, 32, 39 | `CUT TO:`, `DISSOLVE TO:`, `FADE TO:`, `FADE OUT.` |
| Dual dialogue | 9, `DESMOND ^` | Overlapping speech, which changes unit ordering |
| Group character cue | 23, `COMPANY` | A cast entity that is a group, not a person |
| Unnamed role cues | 5 `DRESSER`, 25 `YOUNG ASM`, 39 `CASTING WOMAN` | Cast entities with no proper name |
| `stunt`, `vfx`, `makeup` density | 28 to 33 | 01 has one makeup entity and no stunts |

## 2. Planted dependency chains

| # | Entity | Type | Establishes | Later dependants | Failure shape |
|---|---|---|---|---|---|
| 1 | Emerald gown | `wardrobe` | 2 | 5, 7, 13, 14, 20, 21, 28, 30A, 32 | Longest chain in the corpus, and it changes wearer at scene 21 |
| 2 | Prompt book | `prop` | 3 | 6, 8, 11A, 25, 37 | Changes custodian three times, so `carries` churns across subjects |
| 3 | Gas footlights | `set_design` | 2 | 17, 28, 30 | Causal, not presence: removing them removes the fire's mechanism |
| 4 | Burn dressing | `makeup` | 32 | 33, 35, 39 | Establishes late, so continuity retrieval must look forward from a scene near the end |
| 5 | Safety curtain | `set_design` | 32 | 32 only | Single-use, and a deletion candidate for garbage-collection tests |

Chain 2 is the interesting one for the diff engine. The prompt book moves from Vera to Holloway's desk at 11A and to a young ASM at 25, so a single entity carries three different `carries` assertions with different subjects across the script. Editing scene 11A should ripple into 25 and 37 without touching 3, 6, or 8.

Chain 4 inverts the usual retrieval direction. Every chain in script 01 establishes early and depends late. Here the establishing scene is 32 of 39, so a continuity sweep that only looks backwards finds nothing.

## 3. Entity and alias traps

| Trap | Detail |
|---|---|
| Same person, two names | `EVELYN ALDRIDGE` in action, `MISS ALDRIDGE` in dialogue (scene 5), `Evelyn` throughout, `MISS EVELYN ALDRIDGE` on the playbill in scene 1. One `cast` entity |
| Different people, similar names | `HOLLOWAY` (stage manager) shares no referent with any Aldridge. Alias resolution must not merge on surname proximity |
| A group as cast | `COMPANY` in scene 23 is a cast entity, not four hundred individuals |
| Role-only cues | `DRESSER`, `YOUNG ASM`, `CASTING WOMAN` have no proper names and must still canonicalise once each |
| Fictional play inside the script | `THE WINTER WIDOW` and the character `Aunt Beatrice` (scene 13 dialogue) are not production entities. Extraction must not create them |
| Location repetition | `INT. ALBION THEATRE - STAGE` recurs nine times with three different times of day. One `location` entity, nine `occurs_at` assertions |

The fictional-play trap is the most likely to bite. `Aunt Beatrice` appears inside a line of dialogue that is itself a line from the play being performed. A naive extractor creates a `cast` entity for her.

## 4. Known per-format divergences

The four renders come from one Fountain source, so most content is identical. Three things differ by format, and the import tests should assert the difference rather than expect identical unit trees.

| Feature | .fountain | .fdx | .pdf | .txt |
|---|---|---|---|---|
| `[[note]]` in scene 34 | Present | Stripped | Stripped | Stripped |
| Dual dialogue in scene 9 | Marked with `^` | Flattened to sequential dialogue | Flattened | Flattened |
| Shot lines | Distinguishable by convention | Typed `Action` | Visually identical to action | Typed as action |
| Scene numbers | `#N#` markers | `Number` attributes | Not printed | Not printed |

These are screenplain's limits, not defects in the source. The Fountain adapter should produce one `note` unit and correct dual-dialogue ordering; the other three adapters should not, and a test asserting parity across all four formats would be wrong.

Shot lines are the one case where the adapters can do better than the render. `ANGLE ON`, `CLOSE ON`, and similar prefixes are deterministic enough for a layout rule, so classifying them as `shot` rather than `action` should be a rule in the parser, not an agent repair.

## 5. Format renders

Regenerate all three derivatives whenever the Fountain changes:

```bash
tools/.venv/bin/python tools/render_screenplay.py demo-scripts/02-the-understudy/the-understudy.fountain
```
