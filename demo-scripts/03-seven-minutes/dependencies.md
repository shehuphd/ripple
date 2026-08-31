# SEVEN MINUTES: ground truth

Original work written for the Ripple demo corpus. No third-party rights attach; it ships under the repository licence.

32 scene headings across 30 numbered scenes, with scene 12 omitted and two unnumbered headings inside an intercut. 7 script pages plus a title page.

Scripts 01 and 02 stress the parsers. This one stresses the graph: entities that change state, an identity that resolves late, and a numbering scheme that is not a clean sequence.

## 1. What this script tests that 01 and 02 do not

| Feature | Where | Why it's here |
|---|---|---|
| Non-ASCII names | MATÍAS, BÉLA | Name normalisation applies NFKC; nothing tested it |
| Omitted scene | 12 | Production numbering has holes; `display_scene_number` must not be inferred from position |
| Intercut with inner headings | 8 | Two unnumbered scenes nest inside one numbered sequence |
| Entity that changes state | White van, scenes 2 → 11 → 18 → 29 | Drives `update_assertion`, which no other script exercises |
| Late-resolving identity | THE PASSENGER → Ilona Nagy at 28 | Two cast entities that must merge on evidence, not on name similarity |
| Uppercase action line | `STUNT:` at 9 | An all-caps line at the action margin that is not a character cue |
| Same location, many scenes | Canal path at 13, 15, 18, 20, 29 | Schedule grouping by location |

## 2. Planted dependency chains

| # | Entity | Type | Establishes | Later dependants | Failure shape |
|---|---|---|---|---|---|
| 1 | White van | `transportation` | 2 | 3, 4, 5, 6, 10, 11, 14, 17, 18, 19, 29 | Changes state three times; the graph must track the same entity through damage |
| 2 | Transplant case | `prop` | 1 | 2, 3, 10, 19, 20, 24, 25, 26 | Present in every act; changes custodian only at 26 |
| 3 | Offside wing damage | `makeup` on the vehicle, modelled as `set_design` | 11 | 18, 29 | Pre-existing dent at 2 is overwritten by new damage at 11 |
| 4 | Ilona Nagy | `cast` | 5 (as THE PASSENGER) | 6, 10, 14, 19, 20, 21, 24, 25, 27, 28 | Named only at 28; every earlier assertion must resolve to the same entity |
| 5 | Gas-lit canal path | `location` | 13 | 15, 18, 20, 29 | Five scenes at one location across two time-of-day values |

Chain 1 is the one to demo for `update_assertion`. Scene 2 establishes the van with an old dent. Scene 11 replaces that with new damage. Scene 18 destroys the tyre. Scene 29 shows the wreck. An edit to scene 11 has to change the van's condition without creating a second van entity.

Chain 4 is the extraction trap. `THE PASSENGER` is a cast entity from scene 5. `ILONA` speaks at scene 28. A correct extraction produces one entity with both as aliases; a naive one produces two, and the dialogue at 28 is the only evidence that links them.

## 2a. Expected entities

| Canonical name | Type | Aliases in text |
|---|---|---|
| Matías | `cast` | MATÍAS |
| Béla | `cast` | BÉLA |
| Ilona Nagy | `cast` | THE PASSENGER, ILONA, a woman |
| Dispatch | `cast` | DISPATCH |
| Surgeon | `cast` | SURGEON |
| White van | `transportation` | the van |
| Transplant case | `prop` | the case |
| Offside wing damage | `set_design` | the dent, the offside wing |
| Canal path | `location` | the canal |
| Canal Steps | `location` | |
| Hospital Approach | `location` | |
| Market Street | `location` | |
| River Road | `location` | |
| Underpass | `location` | |
| Ambulance Control Room | `location` | control room |
| Hospital Corridor | `location` | |
| Hospital Entrance | `location` | |
| Transplant Suite | `location` | |
| Hospital Loading Bay | `location` | the loading bay |

## 2b. Expected attributes

| Entity | Key | Value | Evidence scene |
|---|---|---|---|
| White van | color | white | 2 |
| White van | livery | magnetic decal on the door | 2 |
| Canal path | lighting | gas-lit | 13 |

## 3. Known parser behaviour

Two behaviours here are ambiguous rather than wrong, and the tests pin the current choice rather than asserting it is the only defensible one.

**Intercut sub-headings become separate scenes.** Scene 8 is a forced heading (`.INTERCUT - VAN AND CONTROL ROOM`) containing two conventional `INT.` headings with no numbers. The parser makes all three scenes, and the inner two carry `display_scene_number` of `None`. In production these are one numbered scene. Treating them as three keeps every unit inside a scene and keeps the numbered heading addressable, at the cost of two extra scene rows.

**Scene numbers skip 12.** `.OMITTED #12#` parses as a scene with heading `OMITTED` and number `12`, holding no units. This is correct: the number is reserved, so later scenes keep their numbering, and `display_scene_number` is presentation state that must never be recomputed from `sequence_index`.

## 4. Import results across formats

Parsed with the current adapters:

| Format | Outcome | Scenes | Units |
|---|---|---|---|
| `.fountain` | `accepted` | 32 | 142 |
| `.fdx` | `accepted` | 32 | 142 |
| `.pdf` | `accepted_with_warnings` | 31 | 153 |
| `.txt` | `accepted` | 30 | 169 |

The counts diverge by format and that divergence is expected. The PDF loses a heading because the forced `.OMITTED` heading renders as a plain line with no `INT.`/`EXT.` prefix for layout rules to recognise. The plain text loses both intercut sub-headings for the same reason and splits wrapped action into more units. Any test asserting equal unit counts across formats would be wrong.

## 5. Format renders

```bash
tools/.venv/bin/python tools/render_screenplay.py demo-scripts/03-seven-minutes/seven-minutes.fountain
```
