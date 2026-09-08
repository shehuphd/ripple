"""The deterministic pre-pass: cast from cues, location from the heading."""

from __future__ import annotations

from ripple.extraction.prepass import provided_for_scene
from ripple.extraction.validate import validate_response


def _units(*rows):
    return [
        (f"unit-{index}", unit_type, text)
        for index, (unit_type, text) in enumerate(rows)
    ]


class TestProvidedForScene:
    def test_cues_become_cast_and_the_heading_becomes_the_location(self):
        units = _units(
            ("scene_heading", "INT. DISPATCH OFFICE - NIGHT"),
            ("action", "Mara types."),
            ("character", "MARA"),
            ("dialogue", "Which yard?"),
            ("character", "DEV (V.O.)"),
            ("dialogue", "The north one."),
        )
        provided = provided_for_scene("INT. DISPATCH OFFICE - NIGHT", units)
        rows = {(p.entity_type, p.name, p.predicate) for p in provided}
        assert rows == {
            ("cast", "MARA", "appears_in"),
            ("cast", "DEV", "appears_in"),
            ("location", "DISPATCH OFFICE", "occurs_at"),
        }

    def test_a_repeated_cue_yields_one_entity(self):
        units = _units(
            ("scene_heading", "INT. OFFICE - DAY"),
            ("character", "MARA"),
            ("dialogue", "One."),
            ("character", "MARA (CONT'D)"),
            ("dialogue", "Two."),
        )
        provided = provided_for_scene("INT. OFFICE - DAY", units)
        assert sum(1 for p in provided if p.entity_type == "cast") == 1

    def test_direction_and_junk_cues_do_not_become_cast(self):
        """A flattened scan can type a transition, a shot, a line of screen
        text, or a group label as a character cue. None is a speaker, so none
        becomes a cast entity; the real cue beside them still does."""
        units = _units(
            ("scene_heading", "INT. LOFT - NIGHT"),
            ("character", "INTERCUT"),
            ("character", "END INTERCUT"),
            ("character", "PERMISSIONS UPDATED. CONTACT YOUR SUPERVISOR"),
            ("character", "SECOND PERSON"),
            ("character", "SURGEON"),
            ("dialogue", "Seven minutes."),
        )
        cast = {p.name for p in provided_for_scene("INT. LOFT - NIGHT", units)
                if p.entity_type == "cast"}
        assert cast == {"SURGEON"}

    def test_each_row_cites_its_unit_with_a_span(self):
        units = _units(
            ("scene_heading", "EXT. LOADING DOCK - NIGHT"),
            ("character", "MARA"),
            ("dialogue", "Hey."),
        )
        provided = provided_for_scene("EXT. LOADING DOCK - NIGHT", units)
        by_name = {p.name: p for p in provided}
        cue = by_name["MARA"]
        assert cue.source_unit_id == "unit-1"
        assert (cue.evidence_start, cue.evidence_end) == (0, 4)
        location = by_name["LOADING DOCK"]
        assert location.source_unit_id == "unit-0"
        heading = units[0][2]
        assert heading[location.evidence_start : location.evidence_end] == (
            "LOADING DOCK"
        )

    def test_no_heading_unit_means_no_location(self):
        units = _units(("action", "A truck idles."))
        provided = provided_for_scene("EXT. YARD - DAY", units)
        assert provided == []

    def test_a_described_set_heading_yields_no_location(self):
        """A stage play opens an act with prose about the set. Reading a
        location out of it would name the whole sentence ("The table has been
        placed in the middle of the stage"), which is a description, not a
        place; the pre-pass emits no location rather than a sentence node."""
        heading = "The table has been placed in the middle of the stage."
        units = _units(
            ("scene_heading", heading),
            ("character", "NORA"),
            ("dialogue", "Is he home?"),
        )
        provided = provided_for_scene(heading, units)
        assert [(p.entity_type, p.name) for p in provided] == [("cast", "NORA")]

    def test_a_clean_stage_play_location_still_seeds(self):
        """A place-shaped heading, period and all, still becomes a location."""
        heading = "Elsinore. A platform before the Castle"
        units = _units(("scene_heading", heading), ("action", "The guard waits."))
        provided = provided_for_scene(heading, units)
        assert ("location", heading, "occurs_at") in {
            (p.entity_type, p.name, p.predicate) for p in provided
        }


class TestProvidedReferences:
    """The validator accepts references to pre-registered ids."""

    def _reply(self, assertions="", entities=""):
        return (
            '{"entities": [' + entities + '], "assertions": [' + assertions + "]}"
        )

    def test_an_assertion_citing_a_provided_id_resolves(self):
        report = validate_response(
            self._reply(
                entities='{"id": "e1", "type": "wardrobe", '
                '"name": "Grey parka", "conf": 0.9}',
                assertions='{"s": "c1", "p": "wears", "o": "e1", '
                '"unit": "u1", "conf": 0.9}',
            ),
            {"u1"},
            provided={"c1": ("cast", "MARA")},
        )
        assert len(report.assertions) == 1
        assert report.assertions[0].subject_local_id == "c1"

    def test_a_provided_redeclaration_keeps_the_pinned_name_and_type(self):
        report = validate_response(
            self._reply(
                entities='{"id": "c1", "type": "prop", "name": "Wrong", '
                '"conf": 0.9, "attrs": [{"k": "age", "v": "30s", '
                '"unit": "u1", "conf": 0.9}]}'
            ),
            {"u1"},
            provided={"c1": ("cast", "MARA OKONJO")},
        )
        assert len(report.entities) == 1
        entity = report.entities[0]
        assert entity.entity_type == "cast"
        assert entity.canonical_name == "MARA OKONJO"
        assert entity.attributes[0].key == "age"

    def test_an_unknown_endpoint_is_still_rejected(self):
        report = validate_response(
            self._reply(
                assertions='{"s": "c9", "p": "appears_in", "o": "scene", '
                '"unit": "u1", "conf": 0.9}'
            ),
            {"u1"},
            provided={"c1": ("cast", "MARA")},
        )
        assert report.assertions == []
        assert "unresolved_endpoint" in report.rejection_codes


class TestSpanBounds:
    """A span past the unit's end is dropped, keeping the item."""

    def test_an_out_of_bounds_span_is_dropped_to_no_span(self):
        report = validate_response(
            '{"entities": [{"id": "e1", "type": "prop", "name": "Key", '
            '"conf": 0.9}], "assertions": [{"s": "scene", "p": "requires", '
            '"o": "e1", "unit": "u1", "start": 0, "end": 99, "conf": 0.9}]}',
            {"u1"},
            unit_texts={"u1": "A key."},
        )
        assert len(report.assertions) == 1
        assert report.assertions[0].evidence_start is None

    def test_a_span_within_bounds_survives(self):
        report = validate_response(
            '{"entities": [{"id": "e1", "type": "prop", "name": "Key", '
            '"conf": 0.9}], "assertions": [{"s": "scene", "p": "requires", '
            '"o": "e1", "unit": "u1", "start": 2, "end": 5, "conf": 0.9}]}',
            {"u1"},
            unit_texts={"u1": "A key."},
        )
        assert (
            report.assertions[0].evidence_start,
            report.assertions[0].evidence_end,
        ) == (2, 5)
