# NIGHT FREIGHT: ground truth

Original work written for the Ripple demo corpus. No third-party rights attach; it ships under the repository licence.

44 scenes, 10 script pages plus a title page. Written against Schema Lock v1. This file is the extraction oracle: the entities and assertions below are what a correct run must produce, so parser and extraction regressions have something to fail against.

## 1. Planted dependency chains

Each chain has an establishing scene and later scenes that depend on it. Editing the establishing scene is what produces a ripple.

| # | Entity | Type | Establishes | Later dependants | What breaks |
|---|---|---|---|---|---|
| 1 | Blue sedan | `transportation` | 14 | 15, 16, 22, 23, 25, 31, 34, 44 | Removing it from 14 leaves Mara driving a car the script never introduced |
| 2 | Grey parka | `wardrobe` | 3 | 9, 13, 18, 22, 37, 41 | Costume continuity across the whole script, including the 41 concealment beat |
| 3 | Dead forklift | `set_design` | 14 | 20, 41, 44 | Mara hides behind it in 41 and emerges from behind it in 44 |
| 4 | Scar on Dev's hand | `makeup` | 8 | 26, 44 | The final beat turns on Mara recognising the hand; a 36-scene span |
| 5 | Sodium wash | `set_design` | 1 | 14 | Lighting package for the dock; the mockup's changed assertion |

Chain 1 is the demo edit. Chain 4 is the longest span and the hardest continuity retrieval. Chain 3 crosses a scene boundary in the opposite direction, since 41 depends on a prop established 27 scenes earlier and used again 3 scenes later.

## 2. Expected entities

| Canonical name | Type | Normalized key | Aliases in text |
|---|---|---|---|
| Mara Okonjo | `cast` | `mara okonjo` | MARA, Mara |
| Dev Ramirez | `cast` | `dev ramirez` | DEV, Dev |
| Callie Voss | `cast` | `callie voss` | CALLIE |
| Tomas Adeyemi | `cast` | `tomas adeyemi` | TOMAS |
| Cashier | `cast` | `cashier` | CASHIER |
| Blue sedan | `transportation` | `blue sedan` | the sedan, the car |
| Plateless trailer | `transportation` | `plateless trailer` | a trailer with no plates |
| Grey parka | `wardrobe` | `grey parka` | the parka |
| Scar on Dev's hand | `makeup` | `scar on dev's hand` | the scar, his hand |
| Dead forklift | `set_design` | `dead forklift` | the forklift |
| Sodium wash | `set_design` | `sodium wash` | sodium lamp |
| Pallet jack | `prop` | `pallet jack` | two pallet jacks |
| Seal stock box | `prop` | `seal stock box` | seal stock, the box |
| Manifest | `prop` | `manifest` | clipboard, gate log |
| Container 4-4-1 | `prop` | `container 4-4-1` | the four-forties |
| Loading dock | `location` | `loading dock` | the dock |
| Dispatch office | `location` | `dispatch office` | the office |
| Container stack | `location` | `container stack` | the stack |
| Records room | `location` | `records room` | |
| Depot yard | `location` | `depot yard` | the yard |

Cast count is deliberately low so a full-script extraction stays inside the cost cap while still producing a graph dense enough to render.

## 3. Expected assertions for scene 14

Scene 14 is the demo target. A correct extraction produces at least these, following the predicate signatures in Schema Lock v1 §4.

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 14 | `occurs_at` | Loading dock | high |
| Sc 14 | `establishes` | Blue sedan | high |
| Sc 14 | `establishes` | Dead forklift | high |
| Blue sedan | `appears_in` | Sc 14 | high |
| Dead forklift | `appears_in` | Sc 14 | high |
| Pallet jack | `appears_in` | Sc 14 | high |
| Mara Okonjo | `appears_in` | Sc 14 | high |
| Mara Okonjo | `travels_by` | Blue sedan | high |
| Sc 14 | `requires` | Sodium wash | medium |
| Sc 14 | `requires` | Pallet jack | high |
| Sc 14 | `requires` | Dead forklift | high |

Scene 14 holds no `wears` assertion. The grey parka is present on Mara but the scene text doesn't restate it, which is correct behaviour: the costume dependency lives on chain 2 and is retrieved through continuity, not re-extracted per scene.

## 4. The demo edit

Target unit: the action block in scene 14 beginning "Rain sheets off the awning."

| | Text |
|---|---|
| Accepted | Rain sheets off the awning. Two pallet jacks, one dead forklift, the blue sedan idles by the gate, wipers going. |
| Proposed | Rain sheets off the awning. Two pallet jacks, one dead forklift, a bicycle leans against the gate, chain still ticking. |

Expected deterministic diff:

- Removed: `Mara Okonjo travels_by Blue sedan`, `Sc 14 requires Engine idle`
- Added: `Mara Okonjo travels_by Picture bicycle`, `Sc 14 requires Chain rattle`, `Picture bicycle appears_in Sc 14`
- Changed: `Loading dock requires Sodium wash → Practical lamps` if the lighting line is edited in the same pass

Expected continuity findings:

1. **High.** Removing the sole `establishes` edge for Blue sedan while `appears_in` edges survive in scenes 22, 31, and 44. Cites three units.
2. **Medium.** Transport loses its only picture vehicle at the dock, so scene 14 drops off the transport schedule.

This reproduces the ripple preview mockup at `internal/design/screenshots/03-ripple-preview.png`. Keep the two in sync: if the script changes, the screenshot is stale.

## 5. Adversarial value

What this script is built to break:

- **Duplicate seal numbers** in scenes 10 and 11 give two entities with near-identical descriptions and distinct identity. Alias resolution must keep them apart.
- **`(O.S.)` and `(V.O.)` speaker suffixes** in scenes 5, 42, and the radio scenes must normalize away before entity resolution, per Schema Lock v1 §5.
- **`(CONT'D)` on a repeated character cue** in scenes 28 and 42 must not create a second cast entity.
- **CONTINUOUS and LATER time-of-day values** in headings are not `DAY` or `NIGHT`. The scene parser must accept them without falling back to the repair agent.
- **Dev's scar** is introduced in an action block, referenced obliquely in dialogue in scene 26 ("Yeah."), and referenced visually in scene 44. Only the first and third are extractable. The middle one should not be invented.

## 6. Format renders

One authored source, four import formats. Regenerate all three derivatives when the Fountain changes.

| File | Produced by |
|---|---|
| `night-freight.fountain` | Authored, canonical |
| `night-freight.fdx` | Fountain to Final Draft export |
| `night-freight.pdf` | Fountain to PDF render, text layer intact |
| `night-freight.txt` | Plain text dump, formatting stripped |
