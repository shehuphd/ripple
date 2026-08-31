# THE UNDERSTUDY: ground truth

Original work written for the Ripple demo corpus. No third-party rights attach; it ships under the repository licence.

41 scene headings across 39 base scenes plus revision inserts 11A and 30A. 9 script pages plus a title page.

Script 01 covers a contemporary thriller with terse action and few departments. This one is deliberately its opposite: period, ensemble, and heavy on the departments 01 barely touches. Between them the two scripts exercise all eight unit types and all ten entity types.

Sections 2a, 2b, and 2c are the complete inventory: every production entity the text names, every stated attribute, and a per-scene assertion table for all 41 numbered scenes. The demo graph seeds from these tables, so an edit to any stated detail in the script must move something in the graph.

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

## 2a. Expected entities

| Canonical name | Type | Aliases in text |
|---|---|---|
| Evelyn Aldridge | `cast` | EVELYN, MISS ALDRIDGE, Miss Evelyn Aldridge |
| Vera Coyne | `cast` | VERA, Vera |
| Holloway | `cast` | HOLLOWAY |
| Desmond | `cast` | DESMOND, Desmond Carr |
| Dresser | `cast` | DRESSER, a dresser |
| Young ASM | `cast` | YOUNG ASM |
| Casting Woman | `cast` | CASTING WOMAN |
| Company | `cast` | COMPANY |
| Stagehand | `cast` | a stagehand |
| Bill-poster | `cast` | a bill-poster |
| Audience | `cast` | house, four hundred people |
| Audition panel | `cast` | three people |
| Emerald gown | `wardrobe` | the gown |
| Robe | `wardrobe` | a robe |
| Desmond's costume | `wardrobe` | in costume |
| Vera's coat | `wardrobe` | her coat |
| Evelyn's coat | `wardrobe` | in a coat |
| Desmond's street clothes | `wardrobe` | his own clothes |
| Buttoned sleeve | `wardrobe` | sleeve |
| Prompt book | `prop` | the book |
| Playbill | `prop` | a playbill |
| Umbrellas | `prop` | umbrella |
| Clipboard | `prop` | clipboard |
| Holloway's watch | `prop` | a watch |
| Telephone | `prop` | phone |
| Gown hanger | `prop` | hanger |
| Notice | `prop` | a notice |
| Paste brush | `prop` | brush |
| Gas footlights | `set_design` | the footlights, footlight, loose hood, the hood, the flame |
| Safety curtain | `set_design` | |
| Painted parlour flat | `set_design` | painted parlour, the parlour, parlour flat |
| Parlour window | `set_design` | window |
| Prompt corner shelf | `set_design` | shelf |
| Mirror bulbs | `set_design` | bulbs |
| Pub tables | `set_design` | tables |
| Pub bench | `set_design` | bench |
| Office desk | `set_design` | desk |
| Office safe | `set_design` | safe |
| Schedule board | `set_design` | a schedule board |
| Office chair | `set_design` | chair |
| Notice board | `set_design` | the board |
| House lights | `set_design` | house lights |
| Hospital beds | `set_design` | beds |
| Hospital chair | `set_design` | chair too small |
| Trestle table | `set_design` | a trestle table |
| Scene dock doors | `set_design` | scene dock |
| Playbill case | `set_design` | glass |
| Burn dressing | `makeup` | the dressing, dressed from wrist to elbow |
| Cold cream | `makeup` | cold cream |
| Burn wound | `makeup` | burned from wrist to elbow |
| Rain | `vfx` | rain on a playbill |
| Stage fire | `vfx` | takes light, smoke |
| Fire tackle | `stunt` | hits her at the waist |
| Albion Theatre | `location` | The Albion Theatre |
| Albion Theatre - Stage | `location` | the stage |
| Albion Theatre - Corridor | `location` | |
| Albion Theatre - Dressing Room One | `location` | |
| Albion Theatre - Dressing Room Two | `location` | |
| Albion Theatre - Green Room | `location` | |
| Albion Theatre - Holloway's Office | `location` | |
| Albion Theatre - Prompt Corner | `location` | |
| Albion Theatre - Auditorium | `location` | |
| Albion Theatre - Wings | `location` | the wings |
| Public House | `location` | |
| Hospital Ward | `location` | |
| Repertory Theatre, Leeds - Audition Room | `location` | Audition Room |

## 2b. Expected attributes

Structured facts the text states about an entity. The
evidence scene is where the stating line lives. One row per
entity and key: where the script changes a value later, the
row records the first stated value, and the later change is
the edit engine's job.

| Entity | Key | Value | Evidence scene |
|---|---|---|---|
| Emerald gown | color | emerald | 2 |
| Emerald gown | construction | beaded to the shoulder | 5 |
| Emerald gown | weight | what a small child weighs | 5 |
| Emerald gown | fastening | hooks up the back | 5 |
| Emerald gown | alteration | let out two inches at the waist | 20 |
| Gas footlights | fuel | gas | 2 |
| Gas footlights | hood | brass | 2 |
| Gas footlights | condition | one hood loose on its bracket | 2 |
| Prompt book | condition | cover scorched along one edge | 37 |
| Playbill | printed text | THE WINTER WIDOW, MISS EVELYN ALDRIDGE | 1 |
| Umbrellas | condition | wet | 1 |
| Painted parlour flat | material | canvas | 30 |
| Parlour window | opens onto | nothing | 2 |
| Mirror bulbs | condition | half of them out | 5 |
| Safety curtain | skirt | weighted | 32 |
| Burn dressing | coverage | wrist to elbow | 33 |
| Burn wound | placement | right forearm, wrist to elbow | 32 |
| Evelyn Aldridge | age | 40s | 5 |
| Vera Coyne | age | 20s | 3 |
| Holloway | age | 40s | 4 |
| Holloway | role | stage manager | 4 |
| Desmond | age | 30s | 9 |
| Desmond | role | leading man | 9 |
| Young ASM | age | 19 | 25 |
| Company | status | dissolved as of Friday | 36 |
| Audience | size | four hundred | 31 |
| Hospital chair | size | too small for him | 33 |
| Albion Theatre | city | Manchester | 1 |
| Albion Theatre - Dressing Room Two | mirror | smaller | 20 |
| Albion Theatre - Dressing Room Two | bulbs | fewer | 20 |

## 2c. Per-scene assertions

One table per numbered scene, in scene order. Speaking-cast `appears_in` edges and heading-derived `occurs_at` edges are seeded automatically, so the tables list only what automation can't derive: silent presences, non-cast presences, department `requires` rows, and the cast relations the text supports. The chains in section 2 already establish their five entities and cover their `appears_in` scenes, so those rows aren't repeated here. Scene 14's montage heading has no location prefix, so its `occurs_at` is written out explicitly.

### Scene 1

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Playbill | `appears_in` | Sc 1 | high |
| Playbill case | `appears_in` | Sc 1 | high |
| Umbrellas | `appears_in` | Sc 1 | high |
| Rain | `appears_in` | Sc 1 | high |
| Sc 1 | `establishes` | Playbill | high |
| Sc 1 | `establishes` | Playbill case | high |
| Sc 1 | `establishes` | Umbrellas | high |
| Sc 1 | `establishes` | Rain | high |
| Sc 1 | `requires` | Playbill | high |
| Sc 1 | `requires` | Playbill case | high |
| Sc 1 | `requires` | Umbrellas | high |
| Sc 1 | `requires` | Rain | high |

### Scene 2

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Evelyn Aldridge | `appears_in` | Sc 2 | medium |
| Painted parlour flat | `appears_in` | Sc 2 | high |
| Parlour window | `appears_in` | Sc 2 | high |
| Sc 2 | `establishes` | Painted parlour flat | high |
| Sc 2 | `establishes` | Parlour window | high |
| Sc 2 | `requires` | Gas footlights | high |
| Sc 2 | `requires` | Emerald gown | high |
| Sc 2 | `requires` | Painted parlour flat | high |
| Sc 2 | `requires` | Parlour window | high |
| Evelyn Aldridge | `wears` | Emerald gown | medium |

### Scene 3

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 3 | high |
| Prompt corner shelf | `appears_in` | Sc 3 | high |
| Sc 3 | `establishes` | Prompt corner shelf | high |
| Sc 3 | `requires` | Prompt book | high |
| Sc 3 | `requires` | Prompt corner shelf | high |
| Vera Coyne | `carries` | Prompt book | high |

### Scene 4

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Clipboard | `appears_in` | Sc 4 | high |
| Holloway's watch | `appears_in` | Sc 4 | high |
| Sc 4 | `establishes` | Clipboard | high |
| Sc 4 | `establishes` | Holloway's watch | high |
| Sc 4 | `requires` | Clipboard | high |
| Sc 4 | `requires` | Holloway's watch | high |
| Holloway | `carries` | Clipboard | high |
| Holloway | `carries` | Holloway's watch | high |
| Holloway | `interacts_with` | Vera Coyne | high |

### Scene 5

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Mirror bulbs | `appears_in` | Sc 5 | high |
| Sc 5 | `establishes` | Mirror bulbs | high |
| Sc 5 | `requires` | Mirror bulbs | high |
| Sc 5 | `requires` | Emerald gown | high |
| Evelyn Aldridge | `wears` | Emerald gown | high |
| Evelyn Aldridge | `interacts_with` | Dresser | high |

### Scene 6

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 6 | high |
| Sc 6 | `requires` | Prompt book | high |
| Vera Coyne | `carries` | Prompt book | high |

### Scene 7

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Evelyn Aldridge | `appears_in` | Sc 7 | high |
| Audience | `appears_in` | Sc 7 | medium |
| Sc 7 | `requires` | Emerald gown | high |
| Evelyn Aldridge | `wears` | Emerald gown | high |

### Scene 8

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 8 | high |
| Sc 8 | `requires` | Prompt book | high |
| Vera Coyne | `carries` | Prompt book | high |

### Scene 9

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Company | `appears_in` | Sc 9 | high |
| Pub tables | `appears_in` | Sc 9 | high |
| Pub bench | `appears_in` | Sc 9 | high |
| Vera's coat | `appears_in` | Sc 9 | high |
| Sc 9 | `establishes` | Pub tables | high |
| Sc 9 | `establishes` | Pub bench | high |
| Sc 9 | `establishes` | Vera's coat | high |
| Sc 9 | `requires` | Pub tables | high |
| Sc 9 | `requires` | Pub bench | high |
| Sc 9 | `requires` | Vera's coat | high |
| Vera Coyne | `wears` | Vera's coat | high |
| Vera Coyne | `interacts_with` | Desmond | high |

### Scene 10

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `interacts_with` | Desmond | high |

### Scene 11

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Office desk | `appears_in` | Sc 11 | high |
| Office safe | `appears_in` | Sc 11 | high |
| Schedule board | `appears_in` | Sc 11 | high |
| Office chair | `appears_in` | Sc 11 | high |
| Prompt book | `appears_in` | Sc 11 | medium |
| Sc 11 | `establishes` | Office desk | high |
| Sc 11 | `establishes` | Office safe | high |
| Sc 11 | `establishes` | Schedule board | high |
| Sc 11 | `establishes` | Office chair | high |
| Sc 11 | `requires` | Office desk | high |
| Sc 11 | `requires` | Office safe | high |
| Sc 11 | `requires` | Schedule board | high |
| Sc 11 | `requires` | Office chair | high |
| Sc 11 | `requires` | Prompt book | medium |
| Vera Coyne | `carries` | Prompt book | medium |
| Holloway | `interacts_with` | Vera Coyne | high |

### Scene 11A

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Office desk | `appears_in` | Sc 11A | high |
| Sc 11A | `requires` | Prompt book | high |
| Sc 11A | `requires` | Office desk | high |
| Vera Coyne | `carries` | Prompt book | high |
| Holloway | `interacts_with` | Vera Coyne | high |

### Scene 12

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 12 | high |

### Scene 13

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Evelyn Aldridge | `appears_in` | Sc 13 | high |
| Parlour window | `appears_in` | Sc 13 | high |
| Sc 13 | `requires` | Emerald gown | high |
| Sc 13 | `requires` | Parlour window | high |
| Evelyn Aldridge | `wears` | Emerald gown | high |

### Scene 14

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Evelyn Aldridge | `appears_in` | Sc 14 | high |
| Vera Coyne | `appears_in` | Sc 14 | high |
| Gown hanger | `appears_in` | Sc 14 | high |
| Gas footlights | `appears_in` | Sc 14 | high |
| Sc 14 | `occurs_at` | Albion Theatre - Stage | medium |
| Sc 14 | `establishes` | Gown hanger | high |
| Sc 14 | `requires` | Emerald gown | high |
| Sc 14 | `requires` | Gown hanger | high |
| Sc 14 | `requires` | Gas footlights | high |

### Scene 15

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Evelyn Aldridge | `appears_in` | Sc 15 | high |
| Robe | `appears_in` | Sc 15 | high |
| Cold cream | `appears_in` | Sc 15 | high |
| Emerald gown | `appears_in` | Sc 15 | medium |
| Sc 15 | `establishes` | Robe | high |
| Sc 15 | `establishes` | Cold cream | high |
| Sc 15 | `requires` | Emerald gown | medium |
| Sc 15 | `requires` | Robe | high |
| Sc 15 | `requires` | Cold cream | high |
| Evelyn Aldridge | `wears` | Robe | high |
| Evelyn Aldridge | `wears` | Cold cream | high |

### Scene 16

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Desmond's costume | `appears_in` | Sc 16 | high |
| Sc 16 | `establishes` | Desmond's costume | high |
| Sc 16 | `requires` | Desmond's costume | high |
| Desmond | `wears` | Desmond's costume | high |
| Desmond | `interacts_with` | Vera Coyne | high |

### Scene 17

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Stagehand | `appears_in` | Sc 17 | high |
| Sc 17 | `requires` | Gas footlights | high |

### Scene 18

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Telephone | `appears_in` | Sc 18 | high |
| Sc 18 | `establishes` | Telephone | high |
| Sc 18 | `requires` | Telephone | high |
| Holloway | `uses` | Telephone | high |

### Scene 19

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 19 | high |
| Company | `appears_in` | Sc 19 | high |
| Notice | `appears_in` | Sc 19 | high |
| Notice board | `appears_in` | Sc 19 | high |
| Sc 19 | `establishes` | Notice | high |
| Sc 19 | `establishes` | Notice board | high |
| Sc 19 | `requires` | Notice | high |
| Sc 19 | `requires` | Notice board | high |

### Scene 20

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 20 | high |
| Mirror bulbs | `appears_in` | Sc 20 | high |
| Sc 20 | `requires` | Emerald gown | high |
| Sc 20 | `requires` | Mirror bulbs | high |

### Scene 21

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 21 | high |
| Painted parlour flat | `appears_in` | Sc 21 | high |
| Sc 21 | `requires` | Emerald gown | high |
| Sc 21 | `requires` | Painted parlour flat | high |
| Vera Coyne | `wears` | Emerald gown | high |

### Scene 22

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Holloway | `appears_in` | Sc 22 | high |
| Clipboard | `appears_in` | Sc 22 | high |
| Sc 22 | `requires` | Clipboard | high |
| Holloway | `carries` | Clipboard | high |

### Scene 23

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 23 | high |
| Desmond | `interacts_with` | Company | high |

### Scene 24

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Evelyn Aldridge | `appears_in` | Sc 24 | high |
| Vera Coyne | `appears_in` | Sc 24 | high |
| Evelyn's coat | `appears_in` | Sc 24 | high |
| Notice board | `appears_in` | Sc 24 | high |
| Notice | `appears_in` | Sc 24 | medium |
| Sc 24 | `establishes` | Evelyn's coat | high |
| Sc 24 | `requires` | Evelyn's coat | high |
| Sc 24 | `requires` | Notice board | high |
| Evelyn Aldridge | `wears` | Evelyn's coat | high |

### Scene 25

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Young ASM | `appears_in` | Sc 25 | high |
| Sc 25 | `requires` | Prompt book | high |
| Young ASM | `carries` | Prompt book | high |

### Scene 26

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 26 | high |
| Audience | `appears_in` | Sc 26 | medium |

### Scene 27

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Holloway | `interacts_with` | Vera Coyne | high |

### Scene 28

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 28 | high |
| Audience | `appears_in` | Sc 28 | medium |
| Sc 28 | `requires` | Emerald gown | high |
| Sc 28 | `requires` | Gas footlights | high |
| Vera Coyne | `wears` | Emerald gown | high |

### Scene 29

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Holloway | `appears_in` | Sc 29 | high |

### Scene 30

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Stage fire | `appears_in` | Sc 30 | high |
| Painted parlour flat | `appears_in` | Sc 30 | high |
| Sc 30 | `establishes` | Stage fire | high |
| Sc 30 | `requires` | Gas footlights | high |
| Sc 30 | `requires` | Painted parlour flat | high |
| Sc 30 | `requires` | Stage fire | high |

### Scene 30A

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 30A | high |
| Desmond | `appears_in` | Sc 30A | high |
| Fire tackle | `appears_in` | Sc 30A | high |
| Stage fire | `appears_in` | Sc 30A | high |
| Sc 30A | `establishes` | Fire tackle | high |
| Sc 30A | `requires` | Emerald gown | high |
| Sc 30A | `requires` | Fire tackle | high |
| Sc 30A | `requires` | Stage fire | high |
| Vera Coyne | `wears` | Emerald gown | high |
| Vera Coyne | `interacts_with` | Desmond | high |

### Scene 31

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Audience | `appears_in` | Sc 31 | high |
| House lights | `appears_in` | Sc 31 | high |
| Stage fire | `appears_in` | Sc 31 | high |
| Sc 31 | `establishes` | House lights | high |
| Sc 31 | `requires` | House lights | high |
| Sc 31 | `requires` | Stage fire | high |

### Scene 32

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 32 | high |
| Desmond | `appears_in` | Sc 32 | high |
| Burn wound | `appears_in` | Sc 32 | high |
| Stage fire | `appears_in` | Sc 32 | medium |
| Sc 32 | `establishes` | Burn wound | high |
| Sc 32 | `requires` | Safety curtain | high |
| Sc 32 | `requires` | Emerald gown | high |
| Sc 32 | `requires` | Burn wound | high |
| Sc 32 | `requires` | Stage fire | medium |
| Desmond | `uses` | Safety curtain | high |
| Vera Coyne | `wears` | Emerald gown | high |
| Vera Coyne | `wears` | Burn wound | high |
| Vera Coyne | `interacts_with` | Desmond | high |

### Scene 33

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Hospital beds | `appears_in` | Sc 33 | high |
| Hospital chair | `appears_in` | Sc 33 | high |
| Desmond's street clothes | `appears_in` | Sc 33 | high |
| Sc 33 | `establishes` | Hospital beds | high |
| Sc 33 | `establishes` | Hospital chair | high |
| Sc 33 | `establishes` | Desmond's street clothes | high |
| Sc 33 | `requires` | Hospital beds | high |
| Sc 33 | `requires` | Hospital chair | high |
| Sc 33 | `requires` | Desmond's street clothes | high |
| Sc 33 | `requires` | Burn dressing | high |
| Desmond | `wears` | Desmond's street clothes | high |
| Vera Coyne | `wears` | Burn dressing | high |
| Desmond | `interacts_with` | Vera Coyne | high |

### Scene 34

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 34 | high |
| Burn dressing | `appears_in` | Sc 34 | medium |
| Sc 34 | `requires` | Burn dressing | medium |

### Scene 35

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 35 | high |
| Scene dock doors | `appears_in` | Sc 35 | high |
| Painted parlour flat | `appears_in` | Sc 35 | high |
| Buttoned sleeve | `appears_in` | Sc 35 | high |
| Sc 35 | `establishes` | Scene dock doors | high |
| Sc 35 | `establishes` | Buttoned sleeve | high |
| Sc 35 | `requires` | Scene dock doors | high |
| Sc 35 | `requires` | Painted parlour flat | high |
| Sc 35 | `requires` | Buttoned sleeve | high |
| Sc 35 | `requires` | Burn dressing | high |
| Vera Coyne | `wears` | Buttoned sleeve | high |
| Vera Coyne | `wears` | Burn dressing | high |

### Scene 36

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Schedule board | `appears_in` | Sc 36 | high |
| Office safe | `appears_in` | Sc 36 | high |
| Sc 36 | `requires` | Schedule board | high |
| Sc 36 | `requires` | Office safe | high |
| Holloway | `interacts_with` | Vera Coyne | high |

### Scene 37

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Vera Coyne | `appears_in` | Sc 37 | high |
| Prompt corner shelf | `appears_in` | Sc 37 | high |
| Sc 37 | `requires` | Prompt book | high |
| Sc 37 | `requires` | Prompt corner shelf | high |
| Vera Coyne | `carries` | Prompt book | high |

### Scene 38

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Bill-poster | `appears_in` | Sc 38 | high |
| Paste brush | `appears_in` | Sc 38 | high |
| Playbill case | `appears_in` | Sc 38 | high |
| Sc 38 | `establishes` | Paste brush | high |
| Sc 38 | `requires` | Paste brush | high |
| Sc 38 | `requires` | Playbill case | high |
| Bill-poster | `uses` | Paste brush | high |

### Scene 39

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Audition panel | `appears_in` | Sc 39 | high |
| Trestle table | `appears_in` | Sc 39 | high |
| Buttoned sleeve | `appears_in` | Sc 39 | high |
| Sc 39 | `establishes` | Trestle table | high |
| Sc 39 | `requires` | Trestle table | high |
| Sc 39 | `requires` | Buttoned sleeve | high |
| Sc 39 | `requires` | Burn dressing | high |
| Vera Coyne | `wears` | Buttoned sleeve | high |
| Vera Coyne | `wears` | Burn dressing | high |
| Vera Coyne | `interacts_with` | Casting Woman | high |

## 3. Entity and alias traps

| Trap | Detail |
|---|---|
| Same person, two names | `EVELYN ALDRIDGE` in action, `MISS ALDRIDGE` in dialogue (scene 5), `Evelyn` throughout, `MISS EVELYN ALDRIDGE` on the playbill in scene 1. One `cast` entity |
| Different people, similar names | `HOLLOWAY` (stage manager) shares no referent with any Aldridge. Alias resolution must not merge on surname proximity |
| A group as cast | `COMPANY` in scene 23 is a cast entity, not four hundred individuals |
| Role-only cues | `DRESSER`, `YOUNG ASM`, `CASTING WOMAN` have no proper names and must still canonicalise once each |
| Fictional play inside the script | `THE WINTER WIDOW` and the character `Aunt Beatrice` (scene 13 dialogue) are not production entities. Extraction must not create them |
| Location repetition | `INT. ALBION THEATRE - STAGE` recurs eleven times with three different times of day. One `location` entity, eleven `occurs_at` assertions from headings, plus an explicit row for the scene 14 montage |

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
