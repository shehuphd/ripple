"""Verification of the continuity judgement reply.

Every claim must cite evidence the model was shown; everything else in the
reply is dropped with a counted reason. These tests run against a hand-built
packet, no database and no provider.
"""

from __future__ import annotations

import json

import pytest

from ripple.extraction.continuity_judge import (
    build_continuity_prompt,
    validate_continuity,
)
from ripple.extraction.validate import MalformedResponse
from ripple.graph.continuity import EvidenceItem, EvidencePacket


def item(assertion_id: str, relation: str = "later_same_entity") -> EvidenceItem:
    return EvidenceItem(
        assertion_id=assertion_id,
        subject_label="Blue sedan",
        predicate="appears_in",
        object_label="Sc 12",
        scene_id="scene-12",
        scene_number="12",
        scene_index=11,
        unit_id="unit-12",
        unit_text="The blue sedan idles at the kerb.",
        confidence=0.9,
        relation=relation,
        rank=100.0,
    )


def packet_with(*assertion_ids: str) -> EvidencePacket:
    packet = EvidencePacket(entity_ids=["e1"])
    packet.later = [item(assertion_id) for assertion_id in assertion_ids]
    return packet


def reply(*findings) -> str:
    return json.dumps({"findings": list(findings)})


class TestValidation:
    def test_a_cited_finding_survives(self):
        kept, summary = validate_continuity(
            reply(
                {
                    "message": "Scene 12 still relies on the sedan.",
                    "severity": "high",
                    "evidence_ids": ["a1"],
                    "confidence": 0.8,
                }
            ),
            packet_with("a1"),
        )
        assert len(kept) == 1
        assert kept[0].severity == "high"
        assert kept[0].evidence_ids == ["a1"]
        assert kept[0].confidence == 0.8
        assert summary["kept"] == 1

    def test_an_unknown_citation_is_dropped_from_the_list(self):
        kept, _ = validate_continuity(
            reply(
                {
                    "message": "Scene 12 still relies on the sedan.",
                    "severity": "low",
                    "evidence_ids": ["a1", "invented"],
                }
            ),
            packet_with("a1"),
        )
        assert kept[0].evidence_ids == ["a1"]

    def test_a_claim_citing_nothing_known_is_dropped(self):
        kept, summary = validate_continuity(
            reply(
                {
                    "message": "Something feels off.",
                    "severity": "high",
                    "evidence_ids": ["invented"],
                }
            ),
            packet_with("a1"),
        )
        assert kept == []
        assert summary["dropped_uncited"] == 1

    def test_bad_shapes_are_dropped_not_fatal(self):
        kept, summary = validate_continuity(
            reply(
                {"message": "", "severity": "high", "evidence_ids": ["a1"]},
                {"message": "No severity.", "severity": "urgent",
                 "evidence_ids": ["a1"]},
                {"message": "No list.", "severity": "low", "evidence_ids": "a1"},
                "not an object",
            ),
            packet_with("a1"),
        )
        assert kept == []
        assert summary["dropped_invalid"] == 4

    def test_a_duplicate_message_is_dropped(self):
        finding = {
            "message": "Scene 12 still relies on the sedan.",
            "severity": "low",
            "evidence_ids": ["a1"],
        }
        kept, _ = validate_continuity(
            reply(finding, dict(finding, severity="high")), packet_with("a1")
        )
        assert len(kept) == 1

    def test_an_out_of_range_confidence_becomes_none(self):
        kept, _ = validate_continuity(
            reply(
                {
                    "message": "Scene 12 still relies on the sedan.",
                    "severity": "low",
                    "evidence_ids": ["a1"],
                    "confidence": 87,
                }
            ),
            packet_with("a1"),
        )
        assert kept[0].confidence is None

    def test_non_json_raises(self):
        with pytest.raises(MalformedResponse):
            validate_continuity("not json", packet_with("a1"))

    def test_a_reply_without_a_findings_list_raises(self):
        with pytest.raises(MalformedResponse):
            validate_continuity(json.dumps({"answer": 42}), packet_with("a1"))


class TestPrompt:
    def test_the_prompt_carries_edits_diff_and_evidence(self):
        prompt = build_continuity_prompt(
            [{"unit_id": "u1", "current_text": "a", "proposed_text": "b"}],
            {"removed": ["Blue sedan appears_in Sc 12"], "added": [], "changed": []},
            packet_with("a1"),
        )
        payload = json.loads(prompt[prompt.index("{"):])
        assert payload["edited_units"][0]["unit_id"] == "u1"
        assert payload["graph_changes"]["removed"] == [
            "Blue sedan appears_in Sc 12"
        ]
        # The payload's citable id is the assertion id, matching both the
        # system prompt's instruction and the validator's accepted set; the
        # unit id stays out so the model cannot cite the wrong id.
        assert payload["evidence"]["later_evidence"][0]["assertion_id"] == "a1"
        assert "unit_id" not in payload["evidence"]["later_evidence"][0]


class TestThePromptAsksForOneLine:
    """The message a finding carries is read in a list and acted on, so the
    prompt asks for a statement of the new state, not an argument."""

    def test_the_system_prompt_bans_the_argued_form(self):
        from ripple.extraction.continuity_judge import (
            CONTINUITY_PROMPT_VERSION,
            CONTINUITY_SYSTEM,
        )

        assert "what the script now says" in CONTINUITY_SYSTEM
        assert "Name the thing first" in CONTINUITY_SYSTEM
        for banned in ("contradicts", "the established", "this conflicts with"):
            assert banned in CONTINUITY_SYSTEM, "the prompt must name what to avoid"
        # A wording change moves the version, so the audit rows say which
        # prompt wrote a stored message.
        assert CONTINUITY_PROMPT_VERSION == "continuity.v2"
