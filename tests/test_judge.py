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
            {UNIT: "The gown is gone."},
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
            {UNIT: "The gown is gone."},
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
                    {"entity_local_id": "e1", "key": "color", "value": "red",
                     "confidence": 0.2, "source_unit_id": UNIT}
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
                    {"entity_local_id": "e1", "key": "color", "value": "red",
                     "confidence": 0.9,
                     "source_unit_id": "99999999-9999-9999-9999-999999999999"}
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
                    {"entity_local_id": "e1", "key": "color", "value": "red",
                     "confidence": 0.9, "source_unit_id": UNIT}
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
