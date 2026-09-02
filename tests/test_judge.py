"""The judgement validator.

The verification rules run in a fixed order. Each test
feeds the validator a reply a model could plausibly produce and checks the
rule fires, because the validator is the only thing between model output and
stored graph operations.
"""

from __future__ import annotations

import json

import pytest

from ripple.extraction.judge import (
    build_judge_prompt,
    validate_judgement,
)
from ripple.extraction.validate import MalformedResponse

UNIT = "11111111-1111-1111-1111-111111111111"
A1 = "aaaaaaaa-0000-0000-0000-000000000001"
A2 = "aaaaaaaa-0000-0000-0000-000000000002"
T1 = "bbbbbbbb-0000-0000-0000-000000000001"


def listed_assertion(assertion_id=A1, evidence="emerald gown"):
    return {
        assertion_id: {
            "id": assertion_id,
            "subject": "Emerald gown",
            "predicate": "appears_in",
            "object": "Sc 14",
            "source_unit_id": UNIT,
            "evidence": evidence,
            "confidence": 0.9,
        }
    }


def listed_attribute(attribute_id=T1):
    return {
        attribute_id: {
            "id": attribute_id,
            "entity": "Emerald gown",
            "entity_id": "cccccccc-0000-0000-0000-000000000001",
            "key": "color",
            "value": "emerald",
            "source_unit_id": UNIT,
            "evidence": "emerald",
            "confidence": 0.9,
        }
    }


def reply(**parts):
    return json.dumps(parts)


class TestMalformedReplies:
    def test_a_non_json_reply_is_refused(self):
        with pytest.raises(MalformedResponse):
            validate_judgement("the gown still appears", {}, {}, {})

    def test_a_json_array_is_refused(self):
        with pytest.raises(MalformedResponse):
            validate_judgement("[]", {}, {}, {})

    def test_one_bad_verdict_does_not_discard_the_rest(self):
        listed = {**listed_assertion(A1), **listed_assertion(A2)}
        report = validate_judgement(
            reply(
                assertion_verdicts=[
                    {"assertion_id": A1, "verdict": "invented_verdict"},
                    {"assertion_id": A2, "verdict": "removed"},
                ]
            ),
            listed,
            {},
            {UNIT: "The rail is bare."},
        )
        by_id = {v.assertion_id: v.verdict for v in report.assertion_verdicts}
        assert by_id[A2] == "removed"
        # The bad one collapses to the coverage-miss default, not silence.
        assert by_id[A1] == "holds" or A1 in report.coverage_misses


class TestVerdictRules:
    def test_an_unlisted_id_is_dropped_and_recorded(self):
        report = validate_judgement(
            reply(
                assertion_verdicts=[
                    {"assertion_id": "ffffffff-0000-0000-0000-000000000009",
                     "verdict": "removed"}
                ]
            ),
            listed_assertion(),
            {},
            {UNIT: "text"},
        )
        assert all(
            v.assertion_id != "ffffffff-0000-0000-0000-000000000009"
            for v in report.assertion_verdicts
        )
        assert any("unlisted" in why for _, why in report.rejected)

    def test_a_duplicate_verdict_keeps_the_first(self):
        report = validate_judgement(
            reply(
                assertion_verdicts=[
                    {"assertion_id": A1, "verdict": "removed"},
                    {"assertion_id": A1, "verdict": "holds"},
                ]
            ),
            listed_assertion(),
            {},
            {UNIT: "The rail is bare."},
        )
        verdicts = [v for v in report.assertion_verdicts if v.assertion_id == A1]
        assert len(verdicts) == 1
        assert verdicts[0].verdict == "removed"
        assert any("duplicate" in why for _, why in report.rejected)

    def test_holds_on_vanished_evidence_is_downgraded_to_removed(self):
        report = validate_judgement(
            reply(assertion_verdicts=[{"assertion_id": A1, "verdict": "holds"}]),
            listed_assertion(evidence="emerald gown"),
            {},
            {UNIT: "VERA wears a plain black dress."},
        )
        assert report.assertion_verdicts[0].verdict == "removed"
        assert any("vanished" in why for _, why in report.rejected)

    def test_holds_on_surviving_evidence_stays(self):
        report = validate_judgement(
            reply(assertion_verdicts=[{"assertion_id": A1, "verdict": "holds"}]),
            listed_assertion(evidence="emerald gown"),
            {},
            {UNIT: "VERA smooths the emerald gown and waits."},
        )
        assert report.assertion_verdicts[0].verdict == "holds"

    def test_a_missing_verdict_defaults_to_holds_and_is_recorded(self):
        report = validate_judgement(
            reply(assertion_verdicts=[]),
            listed_assertion(),
            listed_attribute(),
            {UNIT: "text"},
        )
        assert {v.assertion_id for v in report.assertion_verdicts} == {A1}
        assert {v.attribute_id for v in report.attribute_verdicts} == {T1}
        assert set(report.coverage_misses) == {A1, T1}


class TestAttributeVerdicts:
    def test_changed_without_a_value_collapses_to_holds(self):
        report = validate_judgement(
            reply(attribute_verdicts=[{"attribute_id": T1, "verdict": "changed"}]),
            {},
            listed_attribute(),
            {UNIT: "text"},
        )
        assert report.attribute_verdicts[0].verdict == "holds"
        assert report.attribute_verdicts[0].new_value is None

    def test_changed_with_a_value_carries_it(self):
        report = validate_judgement(
            reply(
                attribute_verdicts=[
                    {"attribute_id": T1, "verdict": "changed",
                     "new_value": "crimson", "confidence": 0.9}
                ]
            ),
            {},
            listed_attribute(),
            {UNIT: "VERA wears the crimson gown."},
        )
        assert report.attribute_verdicts[0].verdict == "changed"
        assert report.attribute_verdicts[0].new_value == "crimson"


class TestNewItems:
    def test_a_new_attribute_below_the_floor_is_rejected(self):
        report = validate_judgement(
            reply(
                new_attributes=[
                    {"e": "e1", "k": "color", "v": "red",
                     "conf": 0.2, "unit": UNIT}
                ]
            ),
            {},
            {},
            {UNIT: "text"},
        )
        assert report.new_attributes == []
        assert any("floor" in why for _, why in report.rejected)

    def test_a_new_attribute_citing_an_unshown_unit_is_rejected(self):
        report = validate_judgement(
            reply(
                new_attributes=[
                    {"e": "e1", "k": "color", "v": "red",
                     "conf": 0.9,
                     "unit": "99999999-9999-9999-9999-999999999999"}
                ]
            ),
            {},
            {},
            {UNIT: "text"},
        )
        assert report.new_attributes == []
        assert any("unshown" in why for _, why in report.rejected)

    def test_a_valid_new_attribute_is_kept(self):
        report = validate_judgement(
            reply(
                new_attributes=[
                    {"e": "e1", "k": "color", "v": "red",
                     "conf": 0.9, "unit": UNIT}
                ]
            ),
            {},
            {},
            {UNIT: "text"},
        )
        assert len(report.new_attributes) == 1
        assert report.new_attributes[0]["key"] == "color"


class TestPrompt:
    def test_the_prompt_carries_ids_and_both_texts(self):
        prompt = build_judge_prompt(
            "INT. DRESSING ROOM - NIGHT",
            "14",
            [
                {
                    "unit_id": UNIT,
                    "unit_type": "action",
                    "current_text": "The emerald gown hangs ready.",
                    "proposed_text": "The crimson gown hangs ready.",
                }
            ],
            list(listed_assertion().values()),
            list(listed_attribute().values()),
        )
        assert A1 in prompt
        assert T1 in prompt
        assert "emerald gown hangs" in prompt
        assert "crimson gown hangs" in prompt
        assert "Scene 14" in prompt


class TestEvidenceGuardContent:
    def test_offsets_cannot_rescue_a_hold_on_deleted_text(self):
        """Regression: any in-bounds offset pair used to keep the hold alive
        without the evidence text being compared at all."""
        report = validate_judgement(
            reply(
                assertion_verdicts=[
                    {
                        "assertion_id": A1,
                        "verdict": "holds",
                        "evidence_start": 0,
                        "evidence_end": 10,
                    }
                ]
            ),
            listed_assertion(evidence="emerald gown"),
            {},
            {UNIT: "VERA wears a plain black dress."},
        )
        assert report.assertion_verdicts[0].verdict == "removed"
        assert any("vanished" in why for _, why in report.rejected)


class TestConfidenceRange:
    def test_an_out_of_range_confidence_is_refused_not_clamped(self):
        """Regression: 87 used to clamp to a trusted 1.0. Refusing it makes
        the caller fall back to the stored confidence instead."""
        report = validate_judgement(
            reply(
                assertion_verdicts=[
                    {"assertion_id": A1, "verdict": "holds", "confidence": 87}
                ]
            ),
            listed_assertion(evidence="emerald gown"),
            {},
            {UNIT: "The emerald gown hangs ready."},
        )
        assert report.assertion_verdicts[0].confidence is None

    def test_an_in_range_confidence_is_kept(self):
        report = validate_judgement(
            reply(
                assertion_verdicts=[
                    {"assertion_id": A1, "verdict": "holds", "confidence": 0.7}
                ]
            ),
            listed_assertion(evidence="emerald gown"),
            {},
            {UNIT: "The emerald gown hangs ready."},
        )
        assert report.assertion_verdicts[0].confidence == 0.7


class TestRemovalVisibility:
    """A removal must be visible in the edit itself."""

    @staticmethod
    def _removed(listed, current, proposed, attributes=None):
        return validate_judgement(
            json.dumps(
                {
                    "assertion_verdicts": [
                        {"assertion_id": "a1", "verdict": "removed"}
                    ],
                    "attribute_verdicts": [],
                }
            ),
            listed,
            attributes or {},
            {"u1": proposed},
            {"u1": current},
        )

    def test_an_entity_named_only_by_alias_can_be_removed(self):
        # The graph says Paperback; the line says book. The alias is how the
        # edit names the entity, so the vanished alias makes removal visible.
        listed = {
            "a1": {
                "id": "a1",
                "subject": "Paperback",
                "predicate": "appears_in",
                "object": "Sc 29",
                "subject_entity_id": "e-paperback",
                "subject_names": ["his book", "turns a page"],
                "source_unit_id": "u1",
                "evidence": "his book",
                "confidence": 0.9,
            }
        }
        report = self._removed(
            listed,
            "Tomas watches her go and does not read his book.",
            "Tomas watches her go and does not read his newspaper.",
        )
        assert report.assertion_verdicts[0].verdict == "removed"

    def test_a_compound_name_losing_a_word_can_be_removed(self):
        # "Boots on wet concrete" losing concrete is a different sound, even
        # though boots survives in the proposed text.
        listed = {
            "a1": {
                "id": "a1",
                "subject": "Boots on wet concrete",
                "predicate": "appears_in",
                "object": "Sc 42",
                "subject_entity_id": "e-boots",
                "subject_names": ["boots"],
                "source_unit_id": "u1",
                "evidence": "Boots on wet concrete",
                "confidence": 0.9,
            }
        }
        report = self._removed(
            listed,
            "The trailer stops. Boots on wet concrete.",
            "The trailer stops. Boots on wet quicksand.",
        )
        assert report.assertion_verdicts[0].verdict == "removed"

    def test_a_changed_stored_descriptor_stays_an_attribute_change(self):
        # Blue sedan losing blue, with color: blue on record, is the
        # attribute path's case; the removal stays downgraded.
        listed = {
            "a1": {
                "id": "a1",
                "subject": "Blue sedan",
                "predicate": "appears_in",
                "object": "Sc 31",
                "subject_entity_id": "e-sedan",
                "subject_names": ["the sedan", "the car"],
                "source_unit_id": "u1",
                "evidence": "blue sedan",
                "confidence": 0.9,
            }
        }
        attributes = {
            "at1": {
                "id": "at1",
                "entity": "Blue sedan",
                "entity_id": "e-sedan",
                "key": "color",
                "value": "blue",
            }
        }
        report = self._removed(
            listed,
            "The blue sedan waits by the gate.",
            "The black sedan waits by the gate.",
            attributes,
        )
        assert report.assertion_verdicts[0].verdict == "holds"
        assert any("not visible" in reason for _, reason in report.rejected)

    def _listed(self):
        return {
            "a1": {
                "id": "a1",
                "subject": "Sc 3",
                "predicate": "establishes",
                "object": "Dispatch monitors",
                "source_unit_id": "u1",
                "evidence": "Six monitors",
                "confidence": 0.9,
            }
        }

    def test_a_removal_of_a_still_named_entity_downgrades_to_holds(self):
        report = validate_judgement(
            json.dumps(
                {
                    "assertion_verdicts": [
                        {"assertion_id": "a1", "verdict": "removed"}
                    ],
                    "attribute_verdicts": [],
                }
            ),
            self._listed(),
            {},
            {"u1": "Twelve monitors, four of them dead."},
            {"u1": "Six monitors, four of them dead."},
        )
        assert report.assertion_verdicts[0].verdict == "holds"
        assert any("not visible" in reason for _, reason in report.rejected)

    def test_a_removal_of_a_never_named_fact_downgrades_to_holds(self):
        listed = self._listed()
        listed["a1"]["object"] = "Desk radio"
        report = validate_judgement(
            json.dumps(
                {
                    "assertion_verdicts": [
                        {"assertion_id": "a1", "verdict": "removed"}
                    ],
                    "attribute_verdicts": [],
                }
            ),
            listed,
            {},
            {"u1": "Twelve monitors, four of them dead."},
            {"u1": "Six monitors, four of them dead."},
        )
        assert report.assertion_verdicts[0].verdict == "holds"

    def test_a_visible_removal_stands(self):
        listed = self._listed()
        listed["a1"]["object"] = "Blue sedan"
        report = validate_judgement(
            json.dumps(
                {
                    "assertion_verdicts": [
                        {"assertion_id": "a1", "verdict": "removed"}
                    ],
                    "attribute_verdicts": [],
                }
            ),
            listed,
            {},
            {"u1": "A bicycle leans against the gate."},
            {"u1": "The blue sedan idles by the gate."},
        )
        assert report.assertion_verdicts[0].verdict == "removed"


class TestAttributeReach:
    """An attribute can only move through a line that carries its value."""

    def _listed(self):
        return {
            "at1": {
                "id": "at1",
                "entity": "Grey parka",
                "key": "condition",
                "value": "soaked at the shoulders",
                "source_unit_id": "u1",
                "evidence": "",
                "confidence": 0.9,
            }
        }

    def test_a_value_the_edit_never_stated_cannot_be_removed(self):
        report = validate_judgement(
            json.dumps(
                {
                    "assertion_verdicts": [],
                    "attribute_verdicts": [
                        {"attribute_id": "at1", "verdict": "removed"}
                    ],
                }
            ),
            {},
            self._listed(),
            {"u1": "Twelve monitors. MARA in a grey parka."},
            {"u1": "Six monitors. MARA in a grey parka."},
        )
        assert report.attribute_verdicts[0].verdict == "holds"
        assert any("not stated" in reason for _, reason in report.rejected)

    def test_a_stated_value_can_still_change(self):
        listed = self._listed()
        listed["at1"]["value"] = "emerald"
        report = validate_judgement(
            json.dumps(
                {
                    "assertion_verdicts": [],
                    "attribute_verdicts": [
                        {
                            "attribute_id": "at1",
                            "verdict": "changed",
                            "new_value": "crimson",
                        }
                    ],
                }
            ),
            {},
            listed,
            {"u1": "The crimson gown hangs ready."},
            {"u1": "The emerald gown hangs ready."},
        )
        assert report.attribute_verdicts[0].verdict == "changed"
        assert report.attribute_verdicts[0].new_value == "crimson"
