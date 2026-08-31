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

The complete inventory: every entity the script names, typed with the department that owns it. Aliases list the literal phrasings the text uses, so evidence search can cite the line that names each entity.

| Canonical name | Type | Aliases in text |
|---|---|---|
| Matías | `cast` | MATÍAS, Matías Reyes, Reyes |
| Béla | `cast` | BÉLA, Béla Varga, Varga |
| Ilona Nagy | `cast` | THE PASSENGER, ILONA, a woman, the woman |
| Dispatch | `cast` | DISPATCH |
| Surgeon | `cast` | SURGEON |
| White van | `transportation` | the van |
| Transplant case | `prop` | the case, padded case, the box |
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
| Van | `location` | the cab, that cab |
| Nurse | `cast` | a nurse |
| Trolley team | `cast` | trolley team |
| Recovery truck | `transportation` | a recovery truck |
| Steel flask | `prop` | steel flask, the flask |
| Marker pen | `prop` | marker |
| Headset | `prop` | headset |
| Dispatch log | `prop` | a log, the log |
| Trolley | `prop` | trolley |
| Surgical gloves | `wardrobe` | gloved hand, gloved |
| Wet coat | `wardrobe` | coat |
| Case clip | `set_design` | clips, its clip, the clip |
| Dashboard warning light | `set_design` | dashboard light |
| Dashboard clock | `set_design` | the clock |
| Sodium streetlights | `set_design` | sodium light |
| Strip lights | `set_design` | strip lights |
| Tracking map display | `set_design` | a map, green dot, the dot, a screen |
| Street barriers | `set_design` | barriers |
| Empty crates | `set_design` | crates |
| Bollards | `set_design` | bollards |
| Offside tyre | `set_design` | the tyre, tyre, the wheel |
| Automatic doors | `set_design` | automatic doors |
| Reception desk | `set_design` | the desk |
| Hospital wall clock | `set_design` | the clock over the desk |
| Moving boarding stunt | `stunt` | swings in, passenger door |
| Van slide stunt | `stunt` | slides sideways |
| Rim grind stop | `stunt` | grinds to a stop, drops onto the rim |
| Tyre smoke | `vfx` | starts to smoke, a thin grey line |
| Rim sparks | `vfx` | sparks |
| Radio voice-over | `sound` | V.O. |
| Engine tick | `sound` | engine ticks |

## 2b. Expected attributes

One row per entity and key, holding the first value the text states. Later state changes belong to the edit engine, never to this table.

| Entity | Key | Value | Evidence scene |
|---|---|---|---|
| White van | color | white | 2 |
| White van | livery | magnetic decal on the door | 2 |
| Canal path | lighting | gas-lit | 13 |
| White van | offside wing | pre-existing dent | 2 |
| Matías | age | 30s | 2 |
| Matías | occupation | courier | 2 |
| Ilona Nagy | age | late 40s | 5 |
| Ilona Nagy | bag | none | 5 |
| Ilona Nagy | daughter's list position | two hundred and eleven | 28 |
| Béla | age | 50s | 8 |
| Béla | occupation | dispatcher | 8 |
| Transplant case | lid time | 23:41 | 1 |
| Transplant case | construction | padded | 1 |
| Steel flask | material | steel | 1 |
| Dashboard warning light | color | orange | 3 |
| River Road | lighting | sodium | 4 |
| River Road | surface | wet tarmac | 4 |
| Wet coat | condition | wet | 5 |
| Moving boarding stunt | vehicle speed | thirty miles an hour | 5 |
| Tracking map display | dot color | green | 8 |
| Market Street | surface | wet cobbles | 9 |
| Empty crates | condition | empty | 9 |
| Offside tyre | condition | rubbing on the crumpled wing | 11 |
| Tyre smoke | color | grey | 15 |
| Canal Steps | step count | sixty | 21 |
| Hospital wall clock | displayed time | 23:47 | 26 |
| Dispatch log | recorded time | 23:47 | 30 |

## 2c. Scene-by-scene assertions

One table per numbered scene, in scene order. Scene 12 is omitted in the source, reserves its number, and carries no table. The two unnumbered headings inside the intercut can't be addressed by number, so their content is recorded under Scene 8, the numbered heading that contains them. Speaking-cast appearances and heading-derived `occurs_at` edges are seeded automatically and aren't repeated here; these tables carry silent presences, department requirements, and the cast relations the text supports. The five chain entities in section 2 get their `establishes` and `appears_in` edges from the chains table, so those rows aren't repeated either.

### Scene 1

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 1 | `establishes` | Surgeon | high |
| Sc 1 | `establishes` | Transplant Suite | high |
| Sc 1 | `establishes` | Steel flask | high |
| Sc 1 | `establishes` | Surgical gloves | high |
| Sc 1 | `establishes` | Marker pen | high |
| Steel flask | `appears_in` | Sc 1 | high |
| Surgical gloves | `appears_in` | Sc 1 | high |
| Marker pen | `appears_in` | Sc 1 | high |
| Surgeon | `wears` | Surgical gloves | high |
| Surgeon | `carries` | Steel flask | high |
| Surgeon | `uses` | Marker pen | high |
| Surgeon | `uses` | Transplant case | medium |
| Sc 1 | `requires` | Steel flask | high |
| Sc 1 | `requires` | Transplant case | high |
| Sc 1 | `requires` | Surgical gloves | high |
| Sc 1 | `requires` | Marker pen | high |

### Scene 2

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 2 | `establishes` | Matías | high |
| Sc 2 | `establishes` | Hospital Loading Bay | high |
| Matías | `appears_in` | Sc 2 | high |
| Matías | `carries` | Transplant case | high |
| Sc 2 | `requires` | White van | high |
| Sc 2 | `requires` | Transplant case | high |

### Scene 3

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 3 | `establishes` | Dispatch | high |
| Sc 3 | `establishes` | Van | high |
| Sc 3 | `establishes` | Case clip | high |
| Sc 3 | `establishes` | Dashboard warning light | high |
| Sc 3 | `establishes` | Radio voice-over | high |
| Case clip | `appears_in` | Sc 3 | high |
| Dashboard warning light | `appears_in` | Sc 3 | high |
| Radio voice-over | `appears_in` | Sc 3 | high |
| Matías | `uses` | Case clip | high |
| Matías | `uses` | White van | high |
| Matías | `travels_by` | White van | high |
| Matías | `interacts_with` | Dispatch | high |
| Sc 3 | `requires` | White van | high |
| Sc 3 | `requires` | Transplant case | high |
| Sc 3 | `requires` | Case clip | high |
| Sc 3 | `requires` | Dashboard warning light | high |
| Sc 3 | `requires` | Radio voice-over | high |

### Scene 4

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 4 | `establishes` | River Road | high |
| Sc 4 | `establishes` | Sodium streetlights | high |
| Sodium streetlights | `appears_in` | Sc 4 | high |
| Matías | `appears_in` | Sc 4 | medium |
| Sc 4 | `requires` | White van | high |
| Sc 4 | `requires` | Sodium streetlights | high |

### Scene 5

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 5 | `establishes` | Dashboard clock | high |
| Sc 5 | `establishes` | Wet coat | high |
| Sc 5 | `establishes` | Moving boarding stunt | high |
| Dashboard clock | `appears_in` | Sc 5 | high |
| Wet coat | `appears_in` | Sc 5 | high |
| Moving boarding stunt | `appears_in` | Sc 5 | high |
| Transplant case | `appears_in` | Sc 5 | high |
| Ilona Nagy | `wears` | Wet coat | high |
| Ilona Nagy | `travels_by` | White van | high |
| Matías | `uses` | Dashboard clock | high |
| Matías | `interacts_with` | Ilona Nagy | high |
| Sc 5 | `requires` | Wet coat | high |
| Sc 5 | `requires` | Dashboard clock | high |
| Sc 5 | `requires` | Moving boarding stunt | high |
| Sc 5 | `requires` | White van | high |
| Sc 5 | `requires` | Transplant case | high |

### Scene 6

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Transplant case | `appears_in` | Sc 6 | medium |
| Matías | `interacts_with` | Ilona Nagy | high |
| Sc 6 | `requires` | White van | high |
| Sc 6 | `requires` | Transplant case | high |

### Scene 7

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 7 | `establishes` | Underpass | high |
| Sc 7 | `establishes` | Strip lights | high |
| Strip lights | `appears_in` | Sc 7 | high |
| White van | `appears_in` | Sc 7 | high |
| Matías | `appears_in` | Sc 7 | medium |
| Ilona Nagy | `appears_in` | Sc 7 | medium |
| Sc 7 | `requires` | White van | high |
| Sc 7 | `requires` | Strip lights | high |

### Scene 8

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 8 | `establishes` | Béla | high |
| Sc 8 | `establishes` | Ambulance Control Room | high |
| Sc 8 | `establishes` | Tracking map display | high |
| Béla | `appears_in` | Sc 8 | high |
| Matías | `appears_in` | Sc 8 | high |
| Ilona Nagy | `appears_in` | Sc 8 | high |
| White van | `appears_in` | Sc 8 | high |
| Tracking map display | `appears_in` | Sc 8 | high |
| Radio voice-over | `appears_in` | Sc 8 | medium |
| Béla | `uses` | Tracking map display | high |
| Matías | `interacts_with` | Béla | high |
| Sc 8 | `requires` | White van | high |
| Sc 8 | `requires` | Tracking map display | high |
| Sc 8 | `requires` | Radio voice-over | high |

### Scene 9

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 9 | `establishes` | Market Street | high |
| Sc 9 | `establishes` | Street barriers | high |
| Sc 9 | `establishes` | Van slide stunt | high |
| Sc 9 | `establishes` | Empty crates | high |
| Sc 9 | `establishes` | Offside tyre | high |
| Street barriers | `appears_in` | Sc 9 | high |
| Van slide stunt | `appears_in` | Sc 9 | high |
| Empty crates | `appears_in` | Sc 9 | high |
| Offside tyre | `appears_in` | Sc 9 | high |
| White van | `appears_in` | Sc 9 | high |
| Matías | `appears_in` | Sc 9 | medium |
| Ilona Nagy | `appears_in` | Sc 9 | medium |
| Sc 9 | `requires` | Street barriers | high |
| Sc 9 | `requires` | Van slide stunt | high |
| Sc 9 | `requires` | Empty crates | high |
| Sc 9 | `requires` | White van | high |

### Scene 10

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Case clip | `appears_in` | Sc 10 | high |
| Matías | `uses` | Case clip | high |
| Matías | `carries` | Transplant case | high |
| Matías | `interacts_with` | Ilona Nagy | high |
| Sc 10 | `requires` | Transplant case | high |
| Sc 10 | `requires` | Case clip | high |
| Sc 10 | `requires` | White van | high |

### Scene 11

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Offside tyre | `appears_in` | Sc 11 | high |
| Sc 11 | `requires` | Offside wing damage | high |
| Sc 11 | `requires` | Offside tyre | high |
| Sc 11 | `requires` | White van | high |

### Scene 13

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 13 | `establishes` | Bollards | high |
| Bollards | `appears_in` | Sc 13 | high |
| White van | `appears_in` | Sc 13 | high |
| Matías | `appears_in` | Sc 13 | medium |
| Ilona Nagy | `appears_in` | Sc 13 | medium |
| Sc 13 | `requires` | Bollards | high |
| Sc 13 | `requires` | White van | high |

### Scene 14

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Matías | `interacts_with` | Ilona Nagy | high |
| Sc 14 | `requires` | White van | high |

### Scene 15

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 15 | `establishes` | Tyre smoke | high |
| Tyre smoke | `appears_in` | Sc 15 | high |
| Offside tyre | `appears_in` | Sc 15 | high |
| White van | `appears_in` | Sc 15 | high |
| Sc 15 | `requires` | Tyre smoke | high |
| Sc 15 | `requires` | Offside tyre | high |
| Sc 15 | `requires` | White van | high |

### Scene 16

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 16 | `establishes` | Headset | high |
| Tracking map display | `appears_in` | Sc 16 | high |
| Headset | `appears_in` | Sc 16 | high |
| Béla | `uses` | Tracking map display | high |
| Béla | `uses` | Headset | high |
| Béla | `interacts_with` | Matías | medium |
| Sc 16 | `requires` | Tracking map display | high |
| Sc 16 | `requires` | Headset | high |
| Sc 16 | `requires` | Radio voice-over | medium |

### Scene 17

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Matías | `interacts_with` | Béla | high |
| Sc 17 | `requires` | White van | high |
| Sc 17 | `requires` | Radio voice-over | high |
| Sc 17 | `requires` | Offside tyre | medium |

### Scene 18

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 18 | `establishes` | Rim sparks | high |
| Sc 18 | `establishes` | Rim grind stop | high |
| Rim sparks | `appears_in` | Sc 18 | high |
| Rim grind stop | `appears_in` | Sc 18 | high |
| Offside tyre | `appears_in` | Sc 18 | high |
| Matías | `appears_in` | Sc 18 | medium |
| Ilona Nagy | `appears_in` | Sc 18 | medium |
| Sc 18 | `requires` | Rim sparks | high |
| Sc 18 | `requires` | Rim grind stop | high |
| Sc 18 | `requires` | Offside tyre | high |
| Sc 18 | `requires` | White van | high |

### Scene 19

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 19 | `establishes` | Engine tick | high |
| Engine tick | `appears_in` | Sc 19 | high |
| Case clip | `appears_in` | Sc 19 | high |
| Matías | `uses` | Case clip | high |
| Matías | `carries` | Transplant case | high |
| Matías | `interacts_with` | Ilona Nagy | high |
| Sc 19 | `requires` | Engine tick | high |
| Sc 19 | `requires` | Transplant case | high |
| Sc 19 | `requires` | Case clip | high |
| Sc 19 | `requires` | White van | high |

### Scene 20

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Matías | `appears_in` | Sc 20 | high |
| White van | `appears_in` | Sc 20 | high |
| Matías | `carries` | Transplant case | high |
| Sc 20 | `requires` | Transplant case | high |
| Sc 20 | `requires` | White van | high |

### Scene 21

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 21 | `establishes` | Canal Steps | high |
| Matías | `appears_in` | Sc 21 | high |
| Transplant case | `appears_in` | Sc 21 | medium |
| Matías | `carries` | Transplant case | medium |
| Matías | `interacts_with` | Ilona Nagy | high |
| Sc 21 | `requires` | Transplant case | medium |

### Scene 22

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 22 | `establishes` | Hospital Approach | high |
| Matías | `appears_in` | Sc 22 | high |
| Ilona Nagy | `appears_in` | Sc 22 | high |
| Transplant case | `appears_in` | Sc 22 | medium |
| Matías | `carries` | Transplant case | medium |

### Scene 23

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Tracking map display | `appears_in` | Sc 23 | high |
| Béla | `uses` | Tracking map display | high |
| Sc 23 | `requires` | Tracking map display | high |
| Sc 23 | `requires` | Headset | medium |

### Scene 24

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Matías | `interacts_with` | Ilona Nagy | high |
| Matías | `carries` | Transplant case | high |
| Sc 24 | `requires` | Transplant case | high |

### Scene 25

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Matías | `appears_in` | Sc 25 | high |
| Matías | `interacts_with` | Ilona Nagy | high |
| Matías | `carries` | Transplant case | medium |
| Sc 25 | `requires` | Transplant case | high |

### Scene 26

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 26 | `establishes` | Hospital Entrance | high |
| Sc 26 | `establishes` | Automatic doors | high |
| Sc 26 | `establishes` | Nurse | high |
| Sc 26 | `establishes` | Trolley team | high |
| Sc 26 | `establishes` | Trolley | high |
| Sc 26 | `establishes` | Hospital wall clock | high |
| Sc 26 | `establishes` | Reception desk | high |
| Automatic doors | `appears_in` | Sc 26 | high |
| Nurse | `appears_in` | Sc 26 | high |
| Trolley team | `appears_in` | Sc 26 | high |
| Trolley | `appears_in` | Sc 26 | medium |
| Hospital wall clock | `appears_in` | Sc 26 | high |
| Reception desk | `appears_in` | Sc 26 | high |
| Matías | `appears_in` | Sc 26 | high |
| Ilona Nagy | `appears_in` | Sc 26 | medium |
| Nurse | `carries` | Transplant case | high |
| Nurse | `interacts_with` | Matías | medium |
| Sc 26 | `requires` | Automatic doors | high |
| Sc 26 | `requires` | Trolley | medium |
| Sc 26 | `requires` | Hospital wall clock | high |
| Sc 26 | `requires` | Reception desk | high |
| Sc 26 | `requires` | Transplant case | high |

### Scene 27

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 27 | `establishes` | Hospital Corridor | high |
| Matías | `appears_in` | Sc 27 | high |

### Scene 28

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Matías | `interacts_with` | Ilona Nagy | high |

### Scene 29

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 29 | `establishes` | Recovery truck | high |
| Recovery truck | `appears_in` | Sc 29 | high |
| Offside tyre | `appears_in` | Sc 29 | high |
| Sc 29 | `requires` | Recovery truck | high |
| Sc 29 | `requires` | White van | high |
| Sc 29 | `requires` | Offside tyre | high |
| Sc 29 | `requires` | Offside wing damage | high |

### Scene 30

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 30 | `establishes` | Dispatch log | high |
| Dispatch log | `appears_in` | Sc 30 | high |
| Béla | `appears_in` | Sc 30 | high |
| Béla | `uses` | Dispatch log | high |
| Sc 30 | `requires` | Dispatch log | high |

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
