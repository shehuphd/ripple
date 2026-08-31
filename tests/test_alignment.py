"""The deterministic draft aligner.

Pure-function tests: no database, no model. Every case states the classic
revision it represents.
"""

from __future__ import annotations

from ripple.graph.alignment import (
    Alignment,
    SceneContent,
    align_scenes,
    align_units,
)


def scene(scene_id, number, heading, *units) -> SceneContent:
    return SceneContent(
        scene_id=scene_id, number=number, heading=heading, unit_texts=tuple(units)
    )


def kinds(alignment: Alignment) -> dict[str, tuple[str, str]]:
    return {p.new_id: (p.old_id, p.kind) for p in alignment.pairs}


class TestExactContent:
    def test_identical_drafts_align_completely_unchanged(self):
        old = [scene("o1", "1", "INT. A - DAY", "One."),
               scene("o2", "2", "EXT. B - DAY", "Two.")]
        new = [scene("n1", "1", "INT. A - DAY", "One."),
               scene("n2", "2", "EXT. B - DAY", "Two.")]
        result = align_scenes(old, new)
        assert kinds(result) == {"n1": ("o1", "unchanged"), "n2": ("o2", "unchanged")}
        assert result.inserted == []
        assert result.deleted == []

    def test_a_moved_scene_still_matches_by_content(self):
        old = [scene("o1", None, "INT. A - DAY", "One."),
               scene("o2", None, "EXT. B - DAY", "Two.")]
        new = [scene("n1", None, "EXT. B - DAY", "Two."),
               scene("n2", None, "INT. A - DAY", "One.")]
        result = align_scenes(old, new)
        assert kinds(result) == {"n1": ("o2", "unchanged"), "n2": ("o1", "unchanged")}


class TestLockedNumbering:
    def test_matching_numbers_link_even_when_content_moved_on(self):
        old = [scene("o1", "12", "INT. DOCK - NIGHT", "Rain hammers the roof.")]
        new = [scene("n1", "12", "INT. DOCK - NIGHT", "Snow settles on the roof.")]
        result = align_scenes(old, new)
        assert kinds(result) == {"n1": ("o1", "modified")}
        assert result.pairs[0].method == "number"

    def test_an_omitted_placeholder_declares_its_number_deleted(self):
        old = [
            scene("o1", "5", "INT. VAULT - NIGHT", "The seal cracks."),
            scene("o2", "6", "EXT. STREET - NIGHT", "Sirens."),
        ]
        new = [
            scene("n1", "5", "OMITTED"),
            scene("n2", "6", "EXT. STREET - NIGHT", "Sirens."),
        ]
        result = align_scenes(old, new)
        assert result.deleted == ["o1"]
        assert kinds(result) == {"n2": ("o2", "unchanged")}
        assert result.inserted == []

    def test_a_lettered_number_with_no_predecessor_is_an_insertion(self):
        old = [scene("o1", "12", "INT. DOCK - NIGHT", "Rain.")]
        new = [
            scene("n1", "12", "INT. DOCK - NIGHT", "Rain."),
            scene("n2", "12A", "INT. OFFICE - NIGHT", "A phone rings."),
        ]
        result = align_scenes(old, new)
        assert result.inserted == ["n2"]


class TestSimilarity:
    def test_an_edited_unnumbered_scene_links_when_mostly_the_same(self):
        old = [scene("o1", None, "INT. DISPATCH OFFICE - NIGHT",
                     "Strip lighting. Six monitors, four of them dead.",
                     "Mara works a keyboard with two fingers.")]
        new = [scene("n1", None, "INT. DISPATCH OFFICE - NIGHT",
                     "Strip lighting. Twelve monitors, four of them dead.",
                     "Mara works a keyboard with two fingers.")]
        result = align_scenes(old, new)
        assert kinds(result) == {"n1": ("o1", "modified")}
        assert result.pairs[0].method == "similarity"
        assert result.pairs[0].score >= 0.65

    def test_an_unrelated_scene_does_not_link_on_a_guess(self):
        old = [scene("o1", None, "INT. VAULT - NIGHT",
                     "The seal cracks under the pry bar.")]
        new = [scene("n1", None, "EXT. MEADOW - DAY",
                     "A kite drifts over the long grass.")]
        result = align_scenes(old, new)
        assert result.pairs == []
        assert result.inserted == ["n1"]
        assert result.deleted == ["o1"]

    def test_order_is_preserved_over_greedy_scores(self):
        # Two similar-ish scenes: the aligner must not cross the streams.
        old = [
            scene("o1", None, "INT. HALL - DAY", "The hall is empty. Dust motes."),
            scene("o2", None, "INT. HALL - NIGHT", "The hall is empty. Moonlight."),
        ]
        new = [
            scene("n1", None, "INT. HALL - DAY", "The hall is empty. Dust motes float."),
            scene("n2", None, "INT. HALL - NIGHT", "The hall is empty. Moonlight pools."),
        ]
        result = align_scenes(old, new)
        assert kinds(result) == {"n1": ("o1", "modified"), "n2": ("o2", "modified")}


class TestPageOneRewrite:
    def test_nothing_aligns_and_both_sides_say_so(self):
        old = [scene(f"o{i}", None, f"INT. ROOM {i} - DAY", f"Old text {i}.")
               for i in range(4)]
        new = [scene(f"n{i}", None, f"EXT. FIELD {i} - DAY", f"Completely new {i}.")
               for i in range(4)]
        result = align_scenes(old, new)
        assert result.pairs == []
        assert len(result.inserted) == 4
        assert len(result.deleted) == 4


class TestUnitAlignment:
    def test_unchanged_lines_map_and_edited_lines_do_not(self):
        old = ["Rain.", "MARA", "Not again.", "She turns away."]
        new = ["Rain.", "MARA", "Never again.", "She turns away."]
        mapping = align_units(old, new)
        assert mapping == [0, 1, None, 3]

    def test_an_inserted_line_shifts_nothing(self):
        old = ["Rain.", "She turns away."]
        new = ["Rain.", "Thunder rolls.", "She turns away."]
        assert align_units(old, new) == [0, None, 1]


class TestSuggestions:
    def test_a_middling_match_is_suggested_not_made(self):
        old = [scene("o1", None, "INT. DOCK OFFICE - NIGHT",
                     "Rain hammers the corrugated roof over the dock office.")]
        new = [scene("n1", None, "INT. HARBOUR OFFICE - NIGHT",
                     "Sleet rattles the corrugated roof over the harbour office.")]
        result = align_scenes(old, new)
        if result.pairs:
            # Similar enough to link outright; the suggestion band is empty.
            assert result.suggestions == []
        else:
            assert [s.new_id for s in result.suggestions] == ["n1"]
            assert result.suggestions[0].kind == "suggested"
            # Unconfirmed, the scenes stay inserted and deleted.
            assert result.inserted == ["n1"]
            assert result.deleted == ["o1"]

    def test_noise_is_not_even_suggested(self):
        old = [scene("o1", None, "INT. VAULT - NIGHT",
                     "The seal cracks under the pry bar.")]
        new = [scene("n1", None, "EXT. MEADOW - DAY",
                     "A kite drifts over the long grass.")]
        result = align_scenes(old, new)
        assert result.suggestions == []


class TestForcedPairs:
    def test_a_confirmed_pair_links_whatever_the_score(self):
        old = [scene("o1", None, "INT. VAULT - NIGHT",
                     "The seal cracks under the pry bar.")]
        new = [scene("n1", None, "EXT. MEADOW - DAY",
                     "A kite drifts over the long grass.")]
        result = align_scenes(old, new, forced_pairs={"n1": "o1"})
        assert kinds(result) == {"n1": ("o1", "modified")}
        assert result.pairs[0].method == "confirmed"
        assert result.inserted == []
        assert result.deleted == []

    def test_a_forced_pair_naming_a_missing_scene_is_ignored(self):
        old = [scene("o1", None, "INT. A - DAY", "One.")]
        new = [scene("n1", None, "INT. A - DAY", "One.")]
        result = align_scenes(old, new, forced_pairs={"nx": "o1"})
        assert kinds(result) == {"n1": ("o1", "unchanged")}
