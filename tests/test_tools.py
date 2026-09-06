"""The commands under tools/.

They run against a database and a provider, so the logic that picks rows and
the logic that rewrites text are tested here directly; the command bodies
stay thin enough to read.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from ripple.db.models import (
    ChangeSet,
    ContinuityFinding,
    FindingEvidence,
    Scene,
    Script,
    ScriptUnit,
)
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.llm.base import GenerationResult
from ripple.services.synthesizer import reads_as_argued, restate_message
from tools.restate_findings import cited_lines, stale_findings

ARGUED = "Changing the grey walking-costume to a green one contradicts scene 1."
STATED = "Grey walking-costume is now green"


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


class Speaker:
    """A provider that answers with one canned reply and records the ask."""

    name = "google"
    credential_variable = "GOOGLE_API_KEY"

    def __init__(self, text=STATED, finish_reason=None):
        self.text = text
        self.finish_reason = finish_reason
        self.calls = []

    def is_configured(self):
        return True

    def list_models(self):
        return []

    def generate(self, model_id, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        return GenerationResult(
            text=self.text,
            model_id=model_id,
            provider=self.name,
            input_tokens=20,
            output_tokens=8,
            finish_reason=self.finish_reason,
        )


def _script(session, title="Hedda Gabler") -> Script:
    script = Script(title=title, import_status="accepted")
    session.add(script)
    session.flush()
    return script


def _finding(
    session, script, message=ARGUED, status="open", payload=None, lines=("A line.",)
) -> ContinuityFinding:
    """One finding on a fresh change set, citing one line per entry."""
    change_set = ChangeSet(
        script_id=script.id, kind="edit", status="pending", base_script_version=1
    )
    session.add(change_set)
    session.flush()
    scene = Scene(
        script_id=script.id,
        sequence_index=session.scalar(
            select(func.count()).select_from(Scene).where(Scene.script_id == script.id)
        ),
        heading="INT. ROOM - DAY",
    )
    session.add(scene)
    session.flush()
    finding = ContinuityFinding(
        change_set_id=change_set.id,
        finding_type="continuity_conflict",
        severity="medium",
        message=message,
        status=status,
        payload_json=payload,
    )
    session.add(finding)
    session.flush()
    for index, text in enumerate(lines):
        unit = ScriptUnit(
            scene_id=scene.id,
            unit_type="action",
            sequence_index=index,
            current_text=text,
            parser_method="fountain",
        )
        session.add(unit)
        session.flush()
        session.add(
            FindingEvidence(
                finding_id=finding.id,
                script_unit_id=unit.id,
                rank=index,
                match_reason="test",
            )
        )
    session.flush()
    return finding


class TestTheArguedVoice:
    def test_the_pre_v2_forms_are_recognised(self):
        assert reads_as_argued(ARGUED)
        assert reads_as_argued("This CONTRADICTS the line above.")
        assert reads_as_argued("The change conflicts with scene 4.")
        assert reads_as_argued("Removing it breaks the established colour.")

    def test_a_stated_message_is_not_argued(self):
        assert not reads_as_argued(STATED)
        assert not reads_as_argued(
            "Blue sedan is used in scene 22 but no longer introduced."
        )


class TestRestatingOneMessage:
    def test_the_model_is_held_to_the_live_wording_rule(self, session):
        from ripple.extraction.continuity_judge import CONTINUITY_SYSTEM

        speaker = Speaker()
        restated = restate_message(
            ARGUED, ["dressed in a green walking-costume."], speaker, "m", session
        )
        assert restated == STATED
        system = speaker.calls[0]["system"]
        # The rule comes from the continuity prompt itself, not a copy of it.
        assert "Name the thing first" in system
        assert system.endswith(
            CONTINUITY_SYSTEM[
                CONTINUITY_SYSTEM.index("Write each message") : CONTINUITY_SYSTEM.index(
                    "Severity:"
                )
            ].strip()
        )
        # The old message and the line it is about both reach the model.
        assert ARGUED in speaker.calls[0]["prompt"]
        assert "dressed in a green walking-costume." in speaker.calls[0]["prompt"]

    def test_the_call_is_audited_as_a_continuity_call(self, session):
        from sqlalchemy import select

        from ripple.db.models import ModelCall

        script = _script(session)
        restate_message(ARGUED, [], Speaker(), "m", session, script_id=script.id)
        call = session.scalars(select(ModelCall)).one()
        assert call.purpose == "continuity"
        assert call.prompt_version == "continuity.v2"
        assert call.script_id == script.id
        assert call.outcome == "ok"

    def test_a_truncated_reply_is_refused(self, session):
        """The failure this guard exists for: a 256-token ceiling once wrote
        "Grey walking-costume is now a" over a whole message."""
        speaker = Speaker(text="Grey walking-costume is now a", finish_reason="length")
        assert restate_message(ARGUED, [], speaker, "m", session) is None

    def test_a_fragment_is_refused(self, session):
        short = Speaker(text="Grey costume")
        assert restate_message(ARGUED, [], short, "m", session) is None
        assert restate_message(ARGUED, [], Speaker(text="  "), "m", session) is None

    def test_the_sentence_loses_its_quotes(self, session):
        speaker = Speaker(text=f'  "{STATED}"  ')
        assert restate_message(ARGUED, [], speaker, "m", session) == STATED

    def test_the_ceiling_leaves_room_for_reasoning(self, session):
        speaker = Speaker()
        restate_message(ARGUED, [], speaker, "m", session)
        assert speaker.calls[0]["max_output_tokens"] >= 1024


class TestChoosingWhatToRestate:
    def test_only_open_findings_in_the_older_voice(self, session):
        script = _script(session)
        argued = _finding(session, script)
        _finding(session, script, message=STATED)
        _finding(session, script, status="dismissed")
        _finding(session, script, status="resolved")
        chosen = stale_findings(session)
        assert [f.id for f, _ in chosen] == [argued.id]

    def test_every_open_finding_when_asked(self, session):
        script = _script(session)
        _finding(session, script)
        _finding(session, script, message=STATED)
        _finding(session, script, status="dismissed")
        assert len(stale_findings(session, every=True)) == 2

    def test_a_row_already_restated_is_left_alone(self, session):
        script = _script(session)
        _finding(
            session,
            script,
            message=STATED,
            payload={"restated_from": ARGUED, "restated_under": "continuity.v2"},
        )
        assert stale_findings(session) == []
        # --all reaches it, and reads the original rather than the restatement.
        finding, _script_row = stale_findings(session, every=True)[0]
        assert (finding.payload_json or {}).get("restated_from") == ARGUED

    def test_one_script_at_a_time(self, session):
        hedda = _script(session)
        cherry = _script(session, "The Cherry Orchard")
        _finding(session, hedda)
        theirs = _finding(session, cherry)
        assert [f.id for f, _ in stale_findings(session, script="cherry")] == [theirs.id]
        assert [f.id for f, _ in stale_findings(session, script=str(cherry.id))] == [
            theirs.id
        ]
        assert stale_findings(session, script="no such play") == []

    def test_the_cited_lines_are_listed_once_each(self, session):
        script = _script(session)
        finding = _finding(session, script, lines=("A line.", "A line.", "Another."))
        assert cited_lines(session, finding) == ["A line.", "Another."]

    def test_evidence_citing_no_line_is_skipped(self, session):
        """Evidence can cite an assertion rather than a line; the prompt wants
        the lines, so a row without one contributes nothing."""
        script = _script(session)
        finding = _finding(session, script, lines=())
        session.add(
            FindingEvidence(
                finding_id=finding.id,
                script_unit_id=None,
                rank=0,
                match_reason="assertion_only",
            )
        )
        session.flush()
        assert cited_lines(session, finding) == []
