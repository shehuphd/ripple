# NIGHT FREIGHT: ground truth

Original work written for the Ripple demo corpus. No third-party rights attach; it ships under the repository licence.

44 scenes, 10 script pages plus a title page. This file is the extraction oracle: the entities and assertions below are what a correct run must produce, so parser and extraction regressions have something to fail against. It also seeds the demo graph, so anything missing here is invisible to the edit-impact preview.

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
| Voice on Radio | `cast` | `voice on radio` | VOICE ON RADIO, Seventeen |
| Two men | `cast` | `two men` | two of them, the one on the left |
| Blue sedan | `transportation` | `blue sedan` | the sedan, the car, windscreen |
| Plateless trailer | `transportation` | `plateless trailer` | a trailer with no plates |
| Twelve-metre trailer | `transportation` | `twelve-metre trailer` | the trailer, the cab |
| Passing car | `transportation` | `passing car` | headlights |
| Grey parka | `wardrobe` | `grey parka` | the parka |
| Dev's jacket | `wardrobe` | `dev's jacket` | his jacket, hood up, hood down |
| Scar on Dev's hand | `makeup` | `scar on dev's hand` | the scar, his hand |
| Grease on Dev's hand | `makeup` | `grease on dev's hand` | wet and black |
| Dead forklift | `set_design` | `dead forklift` | the forklift |
| Sodium wash | `set_design` | `sodium wash` | sodium lamp |
| Strip lighting | `set_design` | `strip lighting` | |
| Dispatch monitors | `set_design` | `dispatch monitors` | monitors, six monitors |
| Dispatch desk | `set_design` | `dispatch desk` | the desk |
| Clipboard nail | `set_design` | `clipboard nail` | a nail |
| Weighbridge display | `set_design` | `weighbridge display` | the display |
| Weighbridge plate | `set_design` | `weighbridge plate` | the plate, the seam |
| Vending machine | `set_design` | `vending machine` | vending machine light |
| Gate log hook | `set_design` | `gate log hook` | the hook |
| Container door | `set_design` | `container door` | a container door |
| Dock awning | `set_design` | `dock awning` | the awning |
| Dock steps | `set_design` | `dock steps` | the steps |
| Kitchen table | `set_design` | `kitchen table` | the kitchen table |
| Dispatch window | `set_design` | `dispatch window` | the window |
| Box files | `set_design` | `box files` | box file, the shelf |
| Canopy fluorescents | `set_design` | `canopy fluorescents` | fluorescent, the canopy |
| Forecourt cameras | `set_design` | `forecourt cameras` | four cameras, cameras |
| Callie's office door | `set_design` | `callie's office door` | the door |
| Flat door | `set_design` | `flat door` | the door, back of the door |
| Dock ramp | `set_design` | `dock ramp` | the ramp |
| Gate barrier | `set_design` | `gate barrier` | the barrier |
| Chain-link fence | `set_design` | `chain-link fence` | the fence |
| Stacked containers | `set_design` | `stacked containers` | containers |
| Pallet jack | `prop` | `pallet jack` | two pallet jacks |
| Seal stock box | `prop` | `seal stock box` | seal stock, the box, seal box |
| Manifest | `prop` | `manifest` | clipboard, gate log, manifests |
| Container 4-4-1 | `prop` | `container 4-4-1` | the four-forties |
| Container 4-4-2 | `prop` | `container 4-4-2` | 4-4-2 |
| Container seals | `prop` | `container seals` | the seal, duplicate seal |
| Paperback | `prop` | `paperback` | his book, turns a page |
| Desk radio | `prop` | `desk radio` | a radio, radio |
| Keyboard | `prop` | `keyboard` | a keyboard |
| Umbrella | `prop` | `umbrella` | an umbrella |
| Coffees | `prop` | `coffees` | two coffees, coffee |
| Paper towel | `prop` | `paper towel` | a paper towel |
| Torch | `prop` | `torch` | torchlight, different torch |
| Mara's phone | `prop` | `mara's phone` | her phone, the phone, phone |
| Seal photographs | `prop` | `seal photographs` | the photographs, the duplicate seal, photographs |
| Newspaper | `prop` | `newspaper` | |
| Fault log | `prop` | `fault log` | the fault log |
| Car keys | `prop` | `car keys` | keys out |
| Loose change | `prop` | `loose change` | counts change |
| Tomas's second phone | `prop` | `tomas's second phone` | not his phone |
| Rain | `vfx` | `rain` | |
| Permissions banner | `vfx` | `permissions banner` | red banner, PERMISSIONS UPDATED |
| Dock footage | `vfx` | `dock footage` | the footage |
| Car horn | `sound` | `car horn` | a car horn |
| Radio crackle | `sound` | `radio crackle` | crackles |
| Sodium lamp buzz | `sound` | `sodium lamp buzz` | buzzes |
| Boots on wet concrete | `sound` | `boots on wet concrete` | boots |
| Loading dock | `location` | `loading dock` | the dock |
| Dispatch office | `location` | `dispatch office` | the office |
| Container stack | `location` | `container stack` | the stack |
| Records room | `location` | `records room` | |
| Depot yard | `location` | `depot yard` | the yard |
| Harbour Road | `location` | `harbour road` | |
| Depot gate | `location` | `depot gate` | the gate |
| Weighbridge | `location` | `weighbridge` | the weighbridge |
| Depot canteen | `location` | `depot canteen` | |
| Guard hut | `location` | `guard hut` | the hut |
| Mara's flat | `location` | `mara's flat` | the flat |
| Depot car park | `location` | `depot car park` | the car park |
| Petrol station | `location` | `petrol station` | |
| Petrol station shop | `location` | `petrol station shop` | the shop |
| Dev's kitchen | `location` | `dev's kitchen` | |
| Callie's office | `location` | `callie's office` | |

The inventory is complete: every named object, role, sound, and location in the script has a row, so an edit to any stated detail resolves to a node the impact preview can cite. Aliases mirror the literal phrasings the screenplay uses, which keeps evidence citations anchored to the lines that state them.

## 2b. Expected attributes

Structured facts the text states about an entity. The
evidence scene is where the stating line lives. One row per entity and key; where the script restates a fact later, the first statement is the one recorded.

| Entity | Key | Value | Evidence scene |
|---|---|---|---|
| Blue sedan | color | blue | 14 |
| Grey parka | color | grey | 3 |
| Dead forklift | condition | dead | 14 |
| Stacked containers | stack_height | four high | 1 |
| Stacked containers | color | orange and rust | 1 |
| Tomas Adeyemi | age | 50s | 2 |
| Paperback | condition | spine broken back on itself | 2 |
| Car horn | pattern | two short taps | 2 |
| Mara Okonjo | age | 30s | 3 |
| Dispatch monitors | count | six | 3 |
| Dispatch monitors | dead_count | four | 3 |
| Grey parka | condition | soaked at the shoulders | 3 |
| Dev Ramirez | age | 40s | 5 |
| Twelve-metre trailer | length | twelve metres | 5 |
| Weighbridge display | reading | 0.00 T | 5 |
| Weighbridge display | readout_color | red | 5 |
| Callie Voss | age | 50s | 6 |
| Callie Voss | role | night supervisor | 6 |
| Weighbridge plate | residue | wet and black | 7 |
| Scar on Dev's hand | position | knuckle of index finger to wrist, right hand | 8 |
| Scar on Dev's hand | finish | old, silvered | 8 |
| Coffees | count | two | 8 |
| Manifest | count | three | 9 |
| Container 4-4-1 | seal | intact | 10 |
| Container 4-4-2 | seal | intact | 11 |
| Container seals | stamp | same number on two containers | 11 |
| Gate log hook | condition | empty | 12 |
| Blue sedan | wipers | going | 14 |
| Pallet jack | count | two | 14 |
| Mara's flat | condition | small, clean | 18 |
| Seal photographs | count | four | 19 |
| Manifest | missing | Tuesdays across eleven weeks | 21 |
| Forecourt cameras | count | four | 24 |
| Cashier | age | 20s | 26 |
| Permissions banner | color | red | 27 |
| Permissions banner | text | PERMISSIONS UPDATED. CONTACT YOUR SUPERVISOR. | 27 |
| Plateless trailer | plates | none | 33 |
| Flat door | ajar | four inches | 35 |
| Seal stock box | count | forty units | 40 |
| Seal stock box | condition | unopened | 40 |

## 3. Expected assertions

Per-scene tables in scene order, following the defined predicate signatures. `occurs_at` edges derive from scene headings and speaking-cast `appears_in` edges derive from character cues, so each table lists what derivation cannot supply: silent or off-screen presences, non-cast presences, first introductions, department requirements, and cast relations. The five chain entities take their `establishes` edges from the chains table in section 1.

### Scene 1

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 1 | `establishes` | Chain-link fence | high |
| Sc 1 | `establishes` | Stacked containers | high |
| Sc 1 | `establishes` | Rain | high |
| Chain-link fence | `appears_in` | Sc 1 | high |
| Stacked containers | `appears_in` | Sc 1 | high |
| Rain | `appears_in` | Sc 1 | high |
| Sodium lamp buzz | `appears_in` | Sc 1 | high |
| Sc 1 | `requires` | Chain-link fence | high |
| Sc 1 | `requires` | Stacked containers | high |
| Sc 1 | `requires` | Rain | high |
| Sc 1 | `requires` | Sodium wash | high |
| Sc 1 | `requires` | Sodium lamp buzz | high |

### Scene 2

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 2 | `establishes` | Tomas Adeyemi | high |
| Sc 2 | `establishes` | Paperback | high |
| Sc 2 | `establishes` | Car horn | high |
| Paperback | `appears_in` | Sc 2 | high |
| Car horn | `appears_in` | Sc 2 | high |
| Tomas Adeyemi | `carries` | Paperback | high |
| Tomas Adeyemi | `uses` | Paperback | high |
| Sc 2 | `requires` | Paperback | high |
| Sc 2 | `requires` | Car horn | high |
| Sc 2 | `requires` | Rain | medium |

### Scene 3

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 3 | `establishes` | Mara Okonjo | high |
| Sc 3 | `establishes` | Voice on Radio | high |
| Sc 3 | `establishes` | Dispatch monitors | high |
| Sc 3 | `establishes` | Strip lighting | high |
| Sc 3 | `establishes` | Dispatch desk | high |
| Sc 3 | `establishes` | Desk radio | high |
| Sc 3 | `establishes` | Keyboard | high |
| Voice on Radio | `appears_in` | Sc 3 | high |
| Dispatch monitors | `appears_in` | Sc 3 | high |
| Strip lighting | `appears_in` | Sc 3 | high |
| Dispatch desk | `appears_in` | Sc 3 | high |
| Desk radio | `appears_in` | Sc 3 | high |
| Keyboard | `appears_in` | Sc 3 | high |
| Radio crackle | `appears_in` | Sc 3 | high |
| Mara Okonjo | `wears` | Grey parka | high |
| Mara Okonjo | `uses` | Keyboard | high |
| Mara Okonjo | `uses` | Desk radio | high |
| Mara Okonjo | `interacts_with` | Voice on Radio | high |
| Sc 3 | `requires` | Dispatch monitors | high |
| Sc 3 | `requires` | Strip lighting | high |
| Sc 3 | `requires` | Dispatch desk | high |
| Sc 3 | `requires` | Desk radio | high |
| Sc 3 | `requires` | Keyboard | high |
| Sc 3 | `requires` | Grey parka | high |
| Sc 3 | `requires` | Radio crackle | high |

### Scene 4

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 4 | `establishes` | Manifest | high |
| Manifest | `appears_in` | Sc 4 | high |
| Clipboard nail | `appears_in` | Sc 4 | high |
| Mara Okonjo | `carries` | Manifest | high |
| Sc 4 | `requires` | Manifest | high |
| Sc 4 | `requires` | Clipboard nail | high |

### Scene 5

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 5 | `establishes` | Dev Ramirez | high |
| Sc 5 | `establishes` | Twelve-metre trailer | high |
| Sc 5 | `establishes` | Weighbridge display | high |
| Sc 5 | `establishes` | Weighbridge plate | high |
| Sc 5 | `establishes` | Dev's jacket | high |
| Mara Okonjo | `appears_in` | Sc 5 | high |
| Twelve-metre trailer | `appears_in` | Sc 5 | high |
| Weighbridge display | `appears_in` | Sc 5 | high |
| Weighbridge plate | `appears_in` | Sc 5 | high |
| Rain | `appears_in` | Sc 5 | high |
| Dev Ramirez | `wears` | Dev's jacket | high |
| Dev Ramirez | `travels_by` | Twelve-metre trailer | high |
| Dev Ramirez | `interacts_with` | Mara Okonjo | high |
| Sc 5 | `requires` | Twelve-metre trailer | high |
| Sc 5 | `requires` | Weighbridge display | high |
| Sc 5 | `requires` | Weighbridge plate | high |
| Sc 5 | `requires` | Dev's jacket | high |
| Sc 5 | `requires` | Rain | high |

### Scene 6

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 6 | `establishes` | Callie Voss | high |
| Sc 6 | `establishes` | Umbrella | high |
| Umbrella | `appears_in` | Sc 6 | high |
| Callie Voss | `carries` | Umbrella | high |
| Callie Voss | `interacts_with` | Mara Okonjo | high |
| Sc 6 | `requires` | Umbrella | high |

### Scene 7

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 7 | `establishes` | Grease on Dev's hand | high |
| Weighbridge plate | `appears_in` | Sc 7 | high |
| Grease on Dev's hand | `appears_in` | Sc 7 | high |
| Rain | `appears_in` | Sc 7 | high |
| Scar on Dev's hand | `appears_in` | Sc 7 | medium |
| Dev Ramirez | `wears` | Grease on Dev's hand | high |
| Dev Ramirez | `wears` | Scar on Dev's hand | medium |
| Dev Ramirez | `uses` | Weighbridge plate | medium |
| Sc 7 | `requires` | Weighbridge plate | high |
| Sc 7 | `requires` | Grease on Dev's hand | high |
| Sc 7 | `requires` | Rain | high |
| Sc 7 | `requires` | Scar on Dev's hand | medium |

### Scene 8

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 8 | `establishes` | Vending machine | high |
| Sc 8 | `establishes` | Coffees | high |
| Sc 8 | `establishes` | Paper towel | high |
| Vending machine | `appears_in` | Sc 8 | high |
| Coffees | `appears_in` | Sc 8 | high |
| Paper towel | `appears_in` | Sc 8 | high |
| Mara Okonjo | `carries` | Coffees | high |
| Dev Ramirez | `uses` | Paper towel | high |
| Dev Ramirez | `wears` | Scar on Dev's hand | high |
| Dev Ramirez | `interacts_with` | Mara Okonjo | high |
| Sc 8 | `requires` | Vending machine | high |
| Sc 8 | `requires` | Coffees | high |
| Sc 8 | `requires` | Paper towel | high |
| Sc 8 | `requires` | Scar on Dev's hand | high |

### Scene 9

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Manifest | `appears_in` | Sc 9 | high |
| Dispatch desk | `appears_in` | Sc 9 | high |
| Mara Okonjo | `wears` | Grey parka | high |
| Mara Okonjo | `uses` | Manifest | high |
| Sc 9 | `requires` | Manifest | high |
| Sc 9 | `requires` | Dispatch desk | high |
| Sc 9 | `requires` | Grey parka | high |

### Scene 10

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 10 | `establishes` | Torch | high |
| Sc 10 | `establishes` | Container 4-4-1 | high |
| Sc 10 | `establishes` | Container seals | high |
| Mara Okonjo | `appears_in` | Sc 10 | high |
| Torch | `appears_in` | Sc 10 | high |
| Container 4-4-1 | `appears_in` | Sc 10 | high |
| Container seals | `appears_in` | Sc 10 | high |
| Mara's phone | `appears_in` | Sc 10 | medium |
| Mara Okonjo | `uses` | Torch | high |
| Mara Okonjo | `uses` | Mara's phone | medium |
| Sc 10 | `requires` | Torch | high |
| Sc 10 | `requires` | Container 4-4-1 | high |
| Sc 10 | `requires` | Container seals | high |
| Sc 10 | `requires` | Mara's phone | medium |

### Scene 11

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 11 | `establishes` | Container 4-4-2 | high |
| Container 4-4-2 | `appears_in` | Sc 11 | high |
| Container seals | `appears_in` | Sc 11 | high |
| Torch | `appears_in` | Sc 11 | medium |
| Sc 11 | `requires` | Container 4-4-2 | high |
| Sc 11 | `requires` | Container seals | high |
| Sc 11 | `requires` | Torch | medium |

### Scene 12

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 12 | `establishes` | Gate log hook | high |
| Mara Okonjo | `appears_in` | Sc 12 | high |
| Paperback | `appears_in` | Sc 12 | high |
| Gate log hook | `appears_in` | Sc 12 | high |
| Tomas Adeyemi | `uses` | Paperback | high |
| Tomas Adeyemi | `interacts_with` | Mara Okonjo | high |
| Sc 12 | `requires` | Paperback | high |
| Sc 12 | `requires` | Gate log hook | high |

### Scene 13

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 13 | `establishes` | Container door | high |
| Mara Okonjo | `appears_in` | Sc 13 | high |
| Container door | `appears_in` | Sc 13 | high |
| Rain | `appears_in` | Sc 13 | high |
| Mara Okonjo | `wears` | Grey parka | high |
| Sc 13 | `requires` | Container door | high |
| Sc 13 | `requires` | Rain | high |
| Sc 13 | `requires` | Grey parka | high |

### Scene 14

Scene 14 is the demo target. A correct extraction produces at least these, following the defined predicate signatures.

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
| Sc 14 | `establishes` | Pallet jack | high |
| Sc 14 | `establishes` | Dock awning | high |
| Sc 14 | `establishes` | Dock steps | high |
| Dock awning | `appears_in` | Sc 14 | high |
| Dock steps | `appears_in` | Sc 14 | high |
| Rain | `appears_in` | Sc 14 | high |
| Sc 14 | `requires` | Blue sedan | high |
| Sc 14 | `requires` | Dock awning | high |
| Sc 14 | `requires` | Dock steps | high |
| Sc 14 | `requires` | Rain | high |

Scene 14 holds no `wears` assertion. The grey parka is present on Mara but the scene text doesn't restate it, which is correct behaviour: the costume dependency lives on chain 2 and is retrieved through continuity, not re-extracted per scene.

### Scene 15

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Mara Okonjo | `appears_in` | Sc 15 | high |
| Container door | `appears_in` | Sc 15 | high |
| Mara Okonjo | `uses` | Blue sedan | high |
| Sc 15 | `requires` | Blue sedan | high |
| Sc 15 | `requires` | Container door | high |

### Scene 16

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 16 | `establishes` | Mara's phone | high |
| Sc 16 | `establishes` | Seal photographs | high |
| Mara Okonjo | `appears_in` | Sc 16 | high |
| Mara's phone | `appears_in` | Sc 16 | high |
| Seal photographs | `appears_in` | Sc 16 | high |
| Mara Okonjo | `uses` | Mara's phone | high |
| Sc 16 | `requires` | Blue sedan | high |
| Sc 16 | `requires` | Mara's phone | high |
| Sc 16 | `requires` | Seal photographs | high |

### Scene 17

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 17 | `establishes` | Passing car | high |
| Passing car | `appears_in` | Sc 17 | high |
| Chain-link fence | `appears_in` | Sc 17 | high |
| Sc 17 | `requires` | Passing car | high |
| Sc 17 | `requires` | Chain-link fence | high |

### Scene 18

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 18 | `establishes` | Newspaper | high |
| Sc 18 | `establishes` | Flat door | high |
| Mara Okonjo | `appears_in` | Sc 18 | high |
| Newspaper | `appears_in` | Sc 18 | high |
| Flat door | `appears_in` | Sc 18 | high |
| Sc 18 | `requires` | Grey parka | high |
| Sc 18 | `requires` | Newspaper | high |
| Sc 18 | `requires` | Flat door | high |

### Scene 19

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 19 | `establishes` | Kitchen table | high |
| Mara Okonjo | `appears_in` | Sc 19 | high |
| Seal photographs | `appears_in` | Sc 19 | high |
| Kitchen table | `appears_in` | Sc 19 | high |
| Mara Okonjo | `uses` | Seal photographs | high |
| Sc 19 | `requires` | Seal photographs | high |
| Sc 19 | `requires` | Kitchen table | high |

### Scene 20

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 20 | `establishes` | Fault log | high |
| Sc 20 | `establishes` | Dispatch window | high |
| Fault log | `appears_in` | Sc 20 | high |
| Dispatch window | `appears_in` | Sc 20 | high |
| Dispatch monitors | `appears_in` | Sc 20 | medium |
| Callie Voss | `uses` | Fault log | high |
| Callie Voss | `interacts_with` | Mara Okonjo | high |
| Sc 20 | `requires` | Fault log | high |
| Sc 20 | `requires` | Dispatch window | high |
| Sc 20 | `requires` | Dead forklift | high |
| Sc 20 | `requires` | Dispatch monitors | medium |

### Scene 21

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 21 | `establishes` | Box files | high |
| Mara Okonjo | `appears_in` | Sc 21 | high |
| Box files | `appears_in` | Sc 21 | high |
| Manifest | `appears_in` | Sc 21 | high |
| Mara Okonjo | `uses` | Box files | high |
| Sc 21 | `requires` | Box files | high |
| Sc 21 | `requires` | Manifest | high |

### Scene 22

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 22 | `establishes` | Car keys | high |
| Car keys | `appears_in` | Sc 22 | high |
| Mara Okonjo | `carries` | Car keys | high |
| Dev Ramirez | `interacts_with` | Mara Okonjo | high |
| Sc 22 | `requires` | Car keys | high |
| Sc 22 | `requires` | Blue sedan | high |
| Sc 22 | `requires` | Grey parka | high |

### Scene 23

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Dev Ramirez | `wears` | Dev's jacket | high |
| Dev Ramirez | `travels_by` | Blue sedan | high |
| Mara Okonjo | `travels_by` | Blue sedan | high |
| Dev Ramirez | `interacts_with` | Mara Okonjo | high |
| Sc 23 | `requires` | Blue sedan | high |
| Sc 23 | `requires` | Dev's jacket | high |
| Sc 23 | `requires` | Rain | medium |

### Scene 24

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 24 | `establishes` | Forecourt cameras | high |
| Sc 24 | `establishes` | Canopy fluorescents | high |
| Mara Okonjo | `appears_in` | Sc 24 | high |
| Blue sedan | `appears_in` | Sc 24 | high |
| Forecourt cameras | `appears_in` | Sc 24 | high |
| Canopy fluorescents | `appears_in` | Sc 24 | high |
| Mara Okonjo | `travels_by` | Blue sedan | high |
| Sc 24 | `requires` | Blue sedan | high |
| Sc 24 | `requires` | Forecourt cameras | high |
| Sc 24 | `requires` | Canopy fluorescents | high |

### Scene 25

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Dev Ramirez | `interacts_with` | Mara Okonjo | high |
| Sc 25 | `requires` | Blue sedan | high |

### Scene 26

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 26 | `establishes` | Cashier | high |
| Sc 26 | `establishes` | Loose change | high |
| Cashier | `appears_in` | Sc 26 | high |
| Coffees | `appears_in` | Sc 26 | high |
| Loose change | `appears_in` | Sc 26 | high |
| Dev Ramirez | `carries` | Coffees | high |
| Dev Ramirez | `carries` | Loose change | high |
| Dev Ramirez | `wears` | Scar on Dev's hand | high |
| Cashier | `interacts_with` | Dev Ramirez | high |
| Sc 26 | `requires` | Coffees | high |
| Sc 26 | `requires` | Loose change | high |
| Sc 26 | `requires` | Scar on Dev's hand | high |

### Scene 27

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 27 | `establishes` | Permissions banner | high |
| Mara Okonjo | `appears_in` | Sc 27 | high |
| Permissions banner | `appears_in` | Sc 27 | high |
| Dispatch monitors | `appears_in` | Sc 27 | high |
| Keyboard | `appears_in` | Sc 27 | medium |
| Mara Okonjo | `uses` | Dispatch monitors | high |
| Mara Okonjo | `uses` | Keyboard | medium |
| Sc 27 | `requires` | Permissions banner | high |
| Sc 27 | `requires` | Dispatch monitors | high |
| Sc 27 | `requires` | Keyboard | medium |

### Scene 28

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 28 | `establishes` | Callie's office door | high |
| Callie's office door | `appears_in` | Sc 28 | high |
| Callie Voss | `uses` | Callie's office door | high |
| Callie Voss | `interacts_with` | Mara Okonjo | high |
| Sc 28 | `requires` | Callie's office door | high |

### Scene 29

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Mara Okonjo | `appears_in` | Sc 29 | high |
| Tomas Adeyemi | `appears_in` | Sc 29 | high |
| Paperback | `appears_in` | Sc 29 | medium |
| Sc 29 | `requires` | Paperback | medium |

### Scene 30

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 30 | `establishes` | Tomas's second phone | high |
| Tomas Adeyemi | `appears_in` | Sc 30 | high |
| Tomas's second phone | `appears_in` | Sc 30 | high |
| Tomas Adeyemi | `carries` | Tomas's second phone | high |
| Tomas Adeyemi | `uses` | Tomas's second phone | high |
| Sc 30 | `requires` | Tomas's second phone | high |

### Scene 31

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 31 | `establishes` | Two men | high |
| Sc 31 | `establishes` | Dock ramp | high |
| Mara Okonjo | `appears_in` | Sc 31 | high |
| Two men | `appears_in` | Sc 31 | high |
| Container door | `appears_in` | Sc 31 | high |
| Pallet jack | `appears_in` | Sc 31 | high |
| Dock ramp | `appears_in` | Sc 31 | high |
| Mara Okonjo | `uses` | Blue sedan | high |
| Two men | `uses` | Pallet jack | high |
| Sc 31 | `requires` | Blue sedan | high |
| Sc 31 | `requires` | Container door | high |
| Sc 31 | `requires` | Pallet jack | high |
| Sc 31 | `requires` | Dock ramp | high |

### Scene 32

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Two men | `appears_in` | Sc 32 | high |
| Torch | `appears_in` | Sc 32 | high |
| Two men | `uses` | Torch | high |
| Sc 32 | `requires` | Torch | high |

### Scene 33

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 33 | `establishes` | Plateless trailer | high |
| Pallet jack | `appears_in` | Sc 33 | high |
| Dock ramp | `appears_in` | Sc 33 | high |
| Plateless trailer | `appears_in` | Sc 33 | high |
| Two men | `appears_in` | Sc 33 | medium |
| Two men | `uses` | Pallet jack | medium |
| Sc 33 | `requires` | Pallet jack | high |
| Sc 33 | `requires` | Dock ramp | high |
| Sc 33 | `requires` | Plateless trailer | high |

### Scene 34

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 34 | `establishes` | Dock footage | high |
| Mara Okonjo | `appears_in` | Sc 34 | high |
| Mara's phone | `appears_in` | Sc 34 | medium |
| Mara Okonjo | `uses` | Mara's phone | high |
| Mara Okonjo | `uses` | Blue sedan | high |
| Sc 34 | `requires` | Blue sedan | high |
| Sc 34 | `requires` | Mara's phone | high |

### Scene 35

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Mara Okonjo | `appears_in` | Sc 35 | high |
| Flat door | `appears_in` | Sc 35 | high |
| Sc 35 | `requires` | Flat door | high |

### Scene 36

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Mara Okonjo | `appears_in` | Sc 36 | high |
| Seal photographs | `appears_in` | Sc 36 | high |
| Kitchen table | `appears_in` | Sc 36 | high |
| Sc 36 | `requires` | Seal photographs | high |
| Sc 36 | `requires` | Kitchen table | high |

### Scene 37

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Chain-link fence | `appears_in` | Sc 37 | high |
| Mara's phone | `appears_in` | Sc 37 | high |
| Mara Okonjo | `wears` | Grey parka | high |
| Mara Okonjo | `uses` | Mara's phone | high |
| Sc 37 | `requires` | Grey parka | high |
| Sc 37 | `requires` | Mara's phone | high |
| Sc 37 | `requires` | Chain-link fence | high |

### Scene 38

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Coffees | `appears_in` | Sc 38 | high |
| Dev Ramirez | `uses` | Coffees | high |
| Dev Ramirez | `interacts_with` | Mara Okonjo | high |
| Sc 38 | `requires` | Coffees | high |

### Scene 39

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Mara Okonjo | `appears_in` | Sc 39 | high |
| Mara's phone | `appears_in` | Sc 39 | high |
| Dock footage | `appears_in` | Sc 39 | high |
| Mara Okonjo | `uses` | Mara's phone | high |
| Dev Ramirez | `interacts_with` | Mara Okonjo | high |
| Sc 39 | `requires` | Mara's phone | high |
| Sc 39 | `requires` | Dock footage | high |

### Scene 40

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 40 | `establishes` | Seal stock box | high |
| Mara Okonjo | `appears_in` | Sc 40 | high |
| Box files | `appears_in` | Sc 40 | high |
| Seal stock box | `appears_in` | Sc 40 | high |
| Mara Okonjo | `uses` | Box files | high |
| Sc 40 | `requires` | Box files | high |
| Sc 40 | `requires` | Seal stock box | high |

### Scene 41

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Mara Okonjo | `appears_in` | Sc 41 | high |
| Rain | `appears_in` | Sc 41 | high |
| Seal stock box | `appears_in` | Sc 41 | high |
| Plateless trailer | `appears_in` | Sc 41 | high |
| Mara Okonjo | `carries` | Seal stock box | high |
| Mara Okonjo | `wears` | Grey parka | high |
| Sc 41 | `requires` | Rain | high |
| Sc 41 | `requires` | Seal stock box | high |
| Sc 41 | `requires` | Grey parka | high |
| Sc 41 | `requires` | Dead forklift | high |
| Sc 41 | `requires` | Plateless trailer | high |

### Scene 42

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Mara Okonjo | `appears_in` | Sc 42 | high |
| Tomas Adeyemi | `appears_in` | Sc 42 | high |
| Plateless trailer | `appears_in` | Sc 42 | high |
| Boots on wet concrete | `appears_in` | Sc 42 | high |
| Two men | `appears_in` | Sc 42 | low |
| Sc 42 | `requires` | Plateless trailer | high |
| Sc 42 | `requires` | Boots on wet concrete | high |
| Sc 42 | `requires` | Rain | medium |

### Scene 43

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Sc 43 | `establishes` | Gate barrier | high |
| Tomas Adeyemi | `appears_in` | Sc 43 | high |
| Gate barrier | `appears_in` | Sc 43 | high |
| Plateless trailer | `appears_in` | Sc 43 | high |
| Tomas Adeyemi | `uses` | Gate barrier | high |
| Sc 43 | `requires` | Gate barrier | high |
| Sc 43 | `requires` | Plateless trailer | high |

### Scene 44

| Subject | Predicate | Object | Confidence band |
|---|---|---|---|
| Rain | `appears_in` | Sc 44 | high |
| Seal stock box | `appears_in` | Sc 44 | high |
| Mara Okonjo | `carries` | Seal stock box | high |
| Dev Ramirez | `wears` | Scar on Dev's hand | high |
| Dev Ramirez | `wears` | Dev's jacket | high |
| Dev Ramirez | `interacts_with` | Mara Okonjo | high |
| Sc 44 | `requires` | Seal stock box | high |
| Sc 44 | `requires` | Rain | high |
| Sc 44 | `requires` | Blue sedan | high |
| Sc 44 | `requires` | Dead forklift | high |
| Sc 44 | `requires` | Scar on Dev's hand | high |
| Sc 44 | `requires` | Dev's jacket | high |

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

This reproduces the ripple preview design mockup. Keep the two in sync: if the script changes, the mockup is stale.

## 5. Adversarial value

What this script is built to break:

- **Duplicate seal numbers** in scenes 10 and 11 give two entities with near-identical descriptions and distinct identity. Alias resolution must keep them apart.
- **`(O.S.)` and `(V.O.)` speaker suffixes** in scenes 5, 42, and the radio scenes must normalize away before entity resolution.
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
