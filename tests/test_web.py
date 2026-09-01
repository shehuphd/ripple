"""The web layer.

Routes are thin, so the tests target what a thin layer can still get wrong:
leaking a credential, returning a 500 for an expected refusal, accepting a
malformed identifier, and letting one script's delete touch another.
"""

from __future__ import annotations

import os
import shutil

import pytest
from fastapi.testclient import TestClient

from ripple.web import app as web


@pytest.fixture(scope="session")
def template_db(tmp_path_factory):
    """One corpus import and graph seeding for the whole session.

    Startup now imports three screenplays and seeds their ground-truth
    graphs, which is far too slow to repeat per test. Each test copies this
    file instead, so tests stay isolated without re-paying the build.
    """
    path = tmp_path_factory.mktemp("template") / "t.db"
    saved = {
        key: os.environ.get(key)
        for key in (
            "DATABASE_URL",
            "RIPPLE_TRACING",
            "RIPPLE_SECRETS_PATH",
            "GOOGLE_API_KEY",
        )
    }
    os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{path}"
    os.environ["RIPPLE_TRACING"] = "off"
    # Startup loads the secret store into the environment, so pointing the
    # store at an empty throwaway file is what keeps the live credential out
    # of the suite; deleting the variable alone is undone by that load.
    os.environ["RIPPLE_SECRETS_PATH"] = str(path.parent / "no-secrets.env")
    os.environ.pop("GOOGLE_API_KEY", None)
    try:
        with TestClient(web.app):
            pass
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return path


@pytest.fixture
def client(tmp_path, monkeypatch, template_db):
    """A client over a throwaway copy of the seeded template database."""
    database = tmp_path / "t.db"
    shutil.copy(template_db, database)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database}")
    monkeypatch.setenv("RIPPLE_TRACING", "off")
    monkeypatch.setenv("RIPPLE_SECRETS_PATH", str(tmp_path / "no-secrets.env"))
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with TestClient(web.app) as instance:
        yield instance


@pytest.fixture
def judged(monkeypatch):
    """Route the app's model calls to a canned judge, no network involved."""
    from tests.test_preview import FakeJudge

    fake = FakeJudge()
    monkeypatch.setattr(web, "get_provider", lambda name: fake)
    monkeypatch.setattr(
        web.settings_service, "selected_model", lambda session: ("google", "fake-judge")
    )
    return fake


def _first_script(client) -> str:
    """The first script in the library. Rows carry their id as a data attribute."""
    import re

    match = re.search(r'data-id="([0-9a-f-]{36})"', client.get("/").text)
    assert match, "the library did not seed"
    return match.group(1)


def _units(client, script_id) -> list[str]:
    import re

    return re.findall(
        r'data-unit="([0-9a-f-]{36})"', client.get(f"/scripts/{script_id}").text
    )


class TestBadInput:
    @pytest.mark.parametrize(
        "path",
        [
            "/scripts/not-a-uuid",
            "/api/units/not-a-uuid/requirements",
            "/api/units/../../etc/passwd/graph",
            "/api/scenes/%00/units",
        ],
    )
    def test_a_malformed_identifier_is_refused(self, client, path):
        assert client.get(path).status_code in (400, 404)

    def test_an_unknown_script_is_a_404(self, client):
        missing = "11111111-1111-1111-1111-111111111111"
        assert client.get(f"/scripts/{missing}").status_code == 404

    def test_an_unparseable_upload_is_a_400_not_a_500(self, client):
        """A file that cannot become a screenplay is an expected outcome."""
        response = client.post(
            "/api/scripts", files={"file": ("junk.txt", b"\x00\x01\x02" * 80)}
        )
        assert response.status_code == 400
        assert response.json()["code"]

    def test_an_oversized_upload_is_refused(self, client):
        from ripple.adapters.base import MAX_UPLOAD_BYTES

        response = client.post(
            "/api/scripts",
            files={"file": ("big.fountain", b"x" * (MAX_UPLOAD_BYTES + 10))},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "payload_too_large"

    def test_extraction_without_a_model_is_refused_with_a_reason(self, client):
        script_id = _first_script(client)
        response = client.post(f"/api/scripts/{script_id}/extract")
        assert response.status_code == 400
        assert "Settings" in response.json()["detail"]


class TestNoCredentialLeaks:
    def test_the_settings_page_shows_no_key(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-must-not-appear-in-html")
        body = client.get("/settings").text
        assert "sk-must-not-appear-in-html" not in body
        assert "GOOGLE_API_KEY" in body  # the variable name is fine to show

    def test_the_provider_api_returns_no_key(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-must-not-appear-in-json")
        body = client.get("/api/settings/providers").text
        assert "sk-must-not-appear-in-json" not in body
        assert "configured" in body

    def test_validating_a_bad_key_does_not_echo_it(self, client):
        response = client.post(
            "/api/settings/validate",
            data={"provider": "google", "api_key": "sk-echo-me-please"},
        )
        assert "sk-echo-me-please" not in response.text


class TestPages:
    def test_the_library_seeds_the_demo_corpus(self, client):
        body = client.get("/").text
        assert "NIGHT FREIGHT" in body
        assert "THE UNDERSTUDY" in body
        assert "SEVEN MINUTES" in body

    def test_the_reader_renders_units_as_selectable_lines(self, client):
        script_id = _first_script(client)
        body = client.get(f"/scripts/{script_id}").text
        assert 'class="u ' in body
        assert (
            "the blue sedan idles by the gate" in body
            or len(_units(client, script_id)) > 20
        )

    def test_settings_lists_the_provider(self, client):
        body = client.get("/settings").text
        assert "google" in body


class TestUnitEndpoints:
    def test_requirements_are_empty_before_extraction(
        self, client, night_freight_fountain
    ):
        """A unit with no extracted facts answers an empty list, not an error.

        The demo corpus seeds full ground-truth graphs at startup, so this
        needs a copy uploaded after startup, which stays graphless.
        """
        uploaded = client.post(
            "/api/scripts",
            files={"file": ("fresh-upload.fountain", night_freight_fountain)},
        ).json()
        unit_id = _units(client, uploaded["id"])[0]
        payload = client.get(f"/api/units/{unit_id}/requirements").json()
        assert payload["assertions"] == []
        assert payload["unit"]["text"]

    def test_the_preview_without_a_model_is_refused_with_a_reason(self, client):
        """The judge needs a model, so the refusal has to say what to do."""
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        response = client.post(
            f"/api/units/{unit_id}/preview",
            data={"proposed_text": "A bicycle leans against the gate."},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "no_model"
        assert "Settings" in body["message"]


class TestErrorPages:
    """Page routes render an in-app error view; API routes stay JSON."""

    def test_an_unknown_page_unit_renders_the_error_view(self, client):
        response = client.get("/graph/11111111-1111-1111-1111-111111111111")
        assert response.status_code == 404
        assert "text/html" in response.headers["content-type"]
        assert "Open the library" in response.text
        assert "No such unit" in response.text

    def test_an_unknown_script_page_renders_the_error_view(self, client):
        response = client.get("/scripts/11111111-1111-1111-1111-111111111111")
        assert response.status_code == 404
        assert "text/html" in response.headers["content-type"]
        assert "Open the library" in response.text

    def test_a_malformed_page_identifier_renders_the_error_view(self, client):
        response = client.get("/scripts/not-a-uuid")
        assert response.status_code in (400, 404)
        assert "text/html" in response.headers["content-type"]
        assert "Open the library" in response.text

    def test_api_errors_stay_json(self, client):
        response = client.get(
            "/api/units/11111111-1111-1111-1111-111111111111/requirements"
        )
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["detail"]


class TestDeletion:
    def test_a_preview_precedes_the_delete(self, client):
        script_id = _first_script(client)
        counts = client.get(f"/api/scripts/{script_id}/deletion-preview").json()
        assert counts["scenes"] > 0
        # Still there: a preview must not delete.
        assert client.get(f"/scripts/{script_id}").status_code == 200

    def test_deleting_one_script_leaves_the_others(self, client):
        import re

        script_id = _first_script(client)
        before = len(re.findall(r'data-id="', client.get("/").text))
        assert client.delete(f"/api/scripts/{script_id}").status_code == 200
        after = len(re.findall(r'data-id="', client.get("/").text))
        assert after == before - 1
        assert client.get(f"/scripts/{script_id}").status_code == 404

    def test_clearing_graphs_keeps_the_scripts(self, client):
        client.post("/api/graphs/clear")
        assert "NIGHT FREIGHT" in client.get("/").text


class TestDecisionFlow:
    """Preview, then accept or reject, through the HTTP layer."""

    def _preview(self, client, text="A bicycle leans against the gate."):
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        response = client.post(
            f"/api/units/{unit_id}/preview", data={"proposed_text": text}
        )
        assert response.status_code == 200, response.text
        return unit_id, response.json()

    def test_a_preview_applies_nothing(self, client, judged):
        unit_id, _ = self._preview(client)
        before = client.get(f"/api/units/{unit_id}/requirements").json()
        assert before["unit"]["text"] != "A bicycle leans against the gate."

    def test_accepting_applies_the_text(self, client, judged):
        unit_id, preview = self._preview(client)
        response = client.post(f"/api/changes/{preview['change_set_id']}/accept")
        assert response.status_code == 200
        after = client.get(f"/api/units/{unit_id}/requirements").json()
        assert after["unit"]["text"] == "A bicycle leans against the gate."

    def test_accepted_words_render_marked_in_the_reader(self, client, judged):
        script_id = _first_script(client)
        _unit_id, preview = self._preview(client)
        client.post(f"/api/changes/{preview['change_set_id']}/accept")
        body = client.get(f"/scripts/{script_id}").text
        # The changed word carries the revision mark; the rest of the line
        # renders plain, and the whole line still matches data-accepted.
        assert '<mark class="rev">bicycle leans against</mark>' in body
        assert 'data-accepted="A bicycle leans against the gate."' in body

    def test_an_undone_change_leaves_no_marks(self, client, judged):
        script_id = _first_script(client)
        unit_id, preview = self._preview(client)
        client.post(f"/api/changes/{preview['change_set_id']}/accept")
        assert client.post(f"/api/units/{unit_id}/undo").status_code == 200
        body = client.get(f"/scripts/{script_id}").text
        assert '<mark class="rev">' not in body

    def test_rejecting_applies_nothing(self, client, judged):
        unit_id, preview = self._preview(client)
        assert (
            client.post(
                f"/api/changes/{preview['change_set_id']}/reject", data={}
            ).status_code
            == 200
        )
        after = client.get(f"/api/units/{unit_id}/requirements").json()
        assert after["unit"]["text"] != "A bicycle leans against the gate."

    def test_accepting_twice_is_refused(self, client, judged):
        _, preview = self._preview(client)
        client.post(f"/api/changes/{preview['change_set_id']}/accept")
        second = client.post(f"/api/changes/{preview['change_set_id']}/accept")
        assert second.status_code == 400
        assert second.json()["code"] == "invalid_operation"

    def test_a_stale_proposal_is_a_409(self, client, judged):
        """Two previews, accept the second, then the first is stale."""
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        first = client.post(
            f"/api/units/{unit_id}/preview", data={"proposed_text": "One."}
        ).json()
        second = client.post(
            f"/api/units/{unit_id}/preview", data={"proposed_text": "Two."}
        ).json()
        assert (
            client.post(f"/api/changes/{second['change_set_id']}/accept").status_code
            == 200
        )
        stale = client.post(f"/api/changes/{first['change_set_id']}/accept")
        assert stale.status_code == 409
        assert stale.json()["code"] == "stale_proposal"

    def test_undo_restores_the_original_text(self, client, judged):
        unit_id, preview = self._preview(client)
        original = client.get(f"/api/units/{unit_id}/requirements").json()["unit"][
            "text"
        ]
        client.post(f"/api/changes/{preview['change_set_id']}/accept")
        assert client.post(f"/api/units/{unit_id}/undo").status_code == 200
        after = client.get(f"/api/units/{unit_id}/requirements").json()
        assert after["unit"]["text"] == original

    def test_undo_with_nothing_accepted_is_refused(self, client, judged):
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        assert client.post(f"/api/units/{unit_id}/undo").status_code == 400


class TestMultiUnitPreview:
    """Several edited lines become one proposal."""

    def _two_units(self, client):
        script_id = _first_script(client)
        return script_id, _units(client, script_id)[:2]

    def test_two_edits_come_back_as_one_change_set(self, client, judged):
        script_id, (first, second) = self._two_units(client)
        body = client.post(
            f"/api/scripts/{script_id}/preview",
            json={
                "edits": [
                    {"unit_id": first, "proposed_text": "A bicycle by the gate."},
                    {"unit_id": second, "proposed_text": "Rain hammers the roof."},
                ]
            },
        ).json()
        assert body["change_set_id"]
        assert len(body["edits"]) == 2
        assert all("segments" in edit for edit in body["edits"])
        assert all("scene_number" in edit for edit in body["edits"])

    def test_the_segments_mark_words_not_lines(self, client, judged):
        script_id, (first, _) = self._two_units(client)
        body = client.post(
            f"/api/scripts/{script_id}/preview",
            json={"edits": [{"unit_id": first, "proposed_text": "Entirely new."}]},
        ).json()
        ops = {segment["op"] for segment in body["edits"][0]["segments"]}
        assert "ins" in ops
        assert "del" in ops

    def test_an_identical_preview_repeats_without_a_model_call(self, client, judged):
        script_id, (first, _) = self._two_units(client)
        edits = {"edits": [{"unit_id": first, "proposed_text": "A bicycle."}]}
        one = client.post(f"/api/scripts/{script_id}/preview", json=edits).json()
        calls_after_first = judged.calls
        two = client.post(f"/api/scripts/{script_id}/preview", json=edits).json()
        assert judged.calls == calls_after_first
        assert two["cached"] is True
        assert two["change_set_id"] == one["change_set_id"]

    def test_an_empty_edit_list_is_refused(self, client, judged):
        script_id, _ = self._two_units(client)
        response = client.post(
            f"/api/scripts/{script_id}/preview", json={"edits": []}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "no_change"

    def test_accepting_a_two_line_proposal_applies_both(self, client, judged):
        script_id, (first, second) = self._two_units(client)
        body = client.post(
            f"/api/scripts/{script_id}/preview",
            json={
                "edits": [
                    {"unit_id": first, "proposed_text": "A bicycle by the gate."},
                    {"unit_id": second, "proposed_text": "Rain hammers the roof."},
                ]
            },
        ).json()
        accepted = client.post(f"/api/changes/{body['change_set_id']}/accept")
        assert accepted.status_code == 200
        first_text = client.get(f"/api/units/{first}/requirements").json()
        second_text = client.get(f"/api/units/{second}/requirements").json()
        assert first_text["unit"]["text"] == "A bicycle by the gate."
        assert second_text["unit"]["text"] == "Rain hammers the roof."


class TestSettingsTabs:
    """Settings is a tab rail, one category per tab."""

    def test_the_page_carries_the_three_tabs(self, client):
        body = client.get("/settings").text
        assert 'data-tab="keys"' in body
        assert 'data-tab="spend"' in body
        assert 'data-tab="interface"' in body
        assert 'role="tablist"' in body

    def test_the_models_card_offers_main_and_fallback(self, client):
        body = client.get("/settings").text
        assert 'id="models-card"' in body

    def test_a_fallback_is_stored_and_shown(self, client, monkeypatch):
        from ripple.llm.base import ModelInfo, Tier

        class FakeProvider:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def is_configured(self):
                return True

            def list_models(self):
                return [
                    ModelInfo(id=m, provider="google", display_name=m,
                              tier=Tier.CHEAP)
                    for m in ("main-model", "backup-model")
                ]

        monkeypatch.setattr(
            "ripple.services.settings.get_provider", lambda name: FakeProvider()
        )
        assert client.post(
            "/api/settings/model",
            data={"provider": "google", "model_id": "main-model"},
        ).status_code == 200
        assert client.post(
            "/api/settings/fallback",
            data={"provider": "google", "model_id": "backup-model"},
        ).status_code == 200
        assert 'data-fallback="backup-model"' in client.get("/settings").text

    def test_a_fallback_equal_to_the_main_is_refused(self, client, monkeypatch):
        from ripple.llm.base import ModelInfo, Tier

        class FakeProvider:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def is_configured(self):
                return True

            def list_models(self):
                return [
                    ModelInfo(id="main-model", provider="google",
                              display_name="main-model", tier=Tier.CHEAP)
                ]

        monkeypatch.setattr(
            "ripple.services.settings.get_provider", lambda name: FakeProvider()
        )
        client.post(
            "/api/settings/model",
            data={"provider": "google", "model_id": "main-model"},
        )
        refused = client.post(
            "/api/settings/fallback",
            data={"provider": "google", "model_id": "main-model"},
        )
        assert refused.status_code == 400
        assert refused.json()["code"] == "fallback_is_main"


class TestLandingPreference:
    """Where a library click goes is a stored choice; the reader is default."""

    def test_the_default_landing_is_the_reader(self, client):
        assert 'data-landing="reader"' in client.get("/").text

    def test_choosing_the_graph_changes_the_library_rows(self, client):
        response = client.post(
            "/api/settings/landing", data={"landing_view": "graph"}
        )
        assert response.status_code == 200
        assert 'data-landing="graph"' in client.get("/").text

    def test_settings_shows_the_stored_choice(self, client):
        import re

        client.post("/api/settings/landing", data={"landing_view": "graph"})
        body = client.get("/settings").text
        assert re.search(r'value="graph"\s+checked', body)
        assert not re.search(r'value="reader"\s+checked', body)

    def test_an_unknown_view_is_refused(self, client):
        response = client.post(
            "/api/settings/landing", data={"landing_view": "dashboard"}
        )
        assert response.status_code == 400


class TestTracesAndBudget:
    def test_the_traces_page_renders_with_the_ledger(self, client):
        body = client.get("/traces").text
        assert "Traces" in body
        assert "call(s)" in body

    def test_a_judged_preview_lands_in_the_audit(self, client, judged):
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        client.post(
            f"/api/units/{unit_id}/preview",
            data={"proposed_text": "A bicycle leans against the gate."},
        )
        ledger = client.get("/api/settings/budget").json()
        # One judge call plus one continuity call: the seeded graph gives the
        # edited unit stored facts, so the evidence packet is never empty.
        assert ledger["calls"] == 2
        assert ledger["by_purpose"].get("judge", 0) > 0
        assert ledger["by_purpose"].get("continuity", 0) > 0

    def test_a_spent_budget_refuses_the_preview_with_a_next_step(
        self, client, judged
    ):
        script_id = _first_script(client)
        first, second = _units(client, script_id)[:2]
        client.post(
            f"/api/units/{first}/preview",
            data={"proposed_text": "A bicycle leans against the gate."},
        )
        assert client.post(
            "/api/settings/budget", data={"max_total_tokens": "10"}
        ).status_code == 200
        refused = client.post(
            f"/api/units/{second}/preview",
            data={"proposed_text": "Rain hammers the roof."},
        )
        assert refused.status_code == 400
        body = refused.json()
        assert body["code"] == "budget_exceeded"
        assert "Settings" in body["message"]

    def test_clearing_the_budget_reopens_previews(self, client, judged):
        script_id = _first_script(client)
        first, second = _units(client, script_id)[:2]
        client.post(
            f"/api/units/{first}/preview",
            data={"proposed_text": "A bicycle leans against the gate."},
        )
        client.post("/api/settings/budget", data={"max_total_tokens": "10"})
        client.post("/api/settings/budget", data={"max_total_tokens": ""})
        response = client.post(
            f"/api/units/{second}/preview",
            data={"proposed_text": "Rain hammers the roof."},
        )
        assert response.status_code == 200, response.text

    def test_a_junk_budget_is_refused(self, client):
        response = client.post(
            "/api/settings/budget", data={"max_total_tokens": "a lot"}
        )
        assert response.status_code == 400

    def test_settings_shows_the_ledger(self, client):
        body = client.get("/settings").text
        assert "recorded call(s)" in body
        assert 'id="budget-field"' in body


class TestAskTheGraph:
    def test_asking_an_empty_graph_says_so(self, client):
        script_id = _first_script(client)
        body = client.post(
            f"/api/scripts/{script_id}/ask",
            data={"question": "Which scenes have a car?"},
        ).json()
        assert body["grounded_in"] == 0
        assert "accepted graph" in body["answer"]

    def test_the_question_is_logged_for_audit(self, client):
        script_id = _first_script(client)
        client.post(f"/api/scripts/{script_id}/ask", data={"question": "Anything?"})
        assert client.get("/ask").status_code == 200


class TestSceneNumbering:
    def test_an_unnumbered_scene_shows_no_number(self, client):
        """Intercut sub-scenes carry no number, and one must not be invented.

        Substituting the position would collide with the real scene holding
        that number later in the script, so the sidebar would list two 9s.
        """
        import re

        rows = client.get("/").text
        match = re.search(
            r'data-id="([0-9a-f-]{36})"[^>]*data-title="SEVEN MINUTES"', rows
        )
        script_id = match.group(1) if match else _first_script(client)
        body = client.get(f"/scripts/{script_id}").text
        numbers = re.findall(r'<span class="no">([^<]*)</span>', body)
        filled = [n.strip() for n in numbers if n.strip()]
        assert len(filled) == len(set(filled)), f"duplicate scene numbers: {filled}"


class TestEveryNavLinkResolves:
    """A 404 from the app's own sidebar is worse than an absent entry."""

    def test_no_nav_link_is_dead(self, client):
        import re

        body = client.get("/").text
        hrefs = {
            href
            for href in re.findall(r'class="srow[^"]*" href="([^"]+)"', body)
            if href.startswith("/")
        }
        assert hrefs, "the sidebar rendered no links"
        for href in sorted(hrefs):
            assert client.get(href).status_code == 200, f"dead nav link: {href}"

    @pytest.mark.parametrize(
        "path", ["/reports", "/findings", "/entities", "/assertions", "/ask"]
    )
    def test_each_analysis_page_renders(self, client, path):
        assert client.get(path).status_code == 200

    def test_findings_can_filter_to_one_script(self, client):
        """The reader's findings chip links here; the filter must hold."""
        script_id = _first_script(client)
        response = client.get(f"/findings?script={script_id}")
        assert response.status_code == 200
        assert "Continuity findings" in response.text
        assert client.get("/findings?script=not-a-uuid").status_code == 400

    def test_the_reader_findings_chip_is_a_link(self, client):
        """A notice that counts findings must open them; every notice is
        actionable."""
        import uuid

        from ripple.db.models import ChangeSet, ContinuityFinding

        script_id = _first_script(client)
        with web._sessions() as session:
            change_set = ChangeSet(
                script_id=uuid.UUID(script_id),
                kind="edit",
                status="pending",
                base_script_version=1,
            )
            session.add(change_set)
            session.flush()
            session.add(
                ContinuityFinding(
                    change_set_id=change_set.id,
                    finding_type="established_reference",
                    severity="high",
                    message="A later scene references what this edit removes.",
                )
            )
            session.commit()

        body = client.get(f"/scripts/{script_id}").text
        assert "1 continuity finding<" in body, "the count lost its singular form"
        assert f'href="/findings?script={script_id}"' in body


class TestListSearch:
    def test_a_populated_list_offers_search_and_pagination(self, client):
        body = client.get("/entities").text
        assert 'id="list-search"' in body
        assert 'id="pager"' in body


class TestFindingActions:
    """The findings page acts on its rows: review opens the script, and an
    open finding can be dismissed."""

    def _make_finding(self, script_id: str) -> str:
        import uuid

        from ripple.db.models import ChangeSet, ContinuityFinding

        with web._sessions() as session:
            change_set = ChangeSet(
                script_id=uuid.UUID(script_id),
                kind="edit",
                status="pending",
                base_script_version=1,
            )
            session.add(change_set)
            session.flush()
            finding = ContinuityFinding(
                change_set_id=change_set.id,
                finding_type="orphaned_reference",
                severity="high",
                message="A later scene references what this edit removes.",
            )
            session.add(finding)
            session.flush()
            finding_id = str(finding.id)
            session.commit()
        return finding_id

    def test_an_open_finding_offers_dismiss_and_review(self, client):
        script_id = _first_script(client)
        finding_id = self._make_finding(script_id)
        body = client.get("/findings").text
        assert f'data-post="/api/findings/{finding_id}/dismiss"' in body
        # Review deep-links to the script with the finding, so the reader
        # can scroll to and mark the cited lines.
        assert f'href="/scripts/{script_id}?finding={finding_id}"' in body

    def test_a_finding_reports_the_units_its_evidence_cites(self, client):
        import uuid

        from ripple.db.models import FindingEvidence

        script_id = _first_script(client)
        finding_id = self._make_finding(script_id)
        unit_id = _units(client, script_id)[0]
        with web._sessions() as session:
            session.add(
                FindingEvidence(
                    finding_id=uuid.UUID(finding_id),
                    script_unit_id=uuid.UUID(unit_id),
                    rank=0,
                    match_reason="reference",
                )
            )
            session.commit()
        detail = client.get(f"/api/findings/{finding_id}").json()
        assert detail["cited_units"] == [unit_id]
        assert detail["cited_scenes"] == []
        assert detail["status"] == "open"
        assert client.get(f"/api/findings/{uuid.uuid4()}").status_code == 404

    def test_dismissing_removes_the_offer(self, client):
        script_id = _first_script(client)
        finding_id = self._make_finding(script_id)
        assert client.post(f"/api/findings/{finding_id}/dismiss").status_code == 200
        body = client.get("/findings").text
        assert f'data-post="/api/findings/{finding_id}/dismiss"' not in body
        assert ">dismissed<" in body


class TestRecentlyOpened:
    def test_it_is_empty_until_a_script_is_opened(self, client):
        import re

        body = client.get("/?filter=recent").text
        assert "Recently opened" in body
        assert not re.findall(r'data-id="', body)

    def test_opening_a_script_puts_it_there(self, client):
        script_id = _first_script(client)
        response = client.post(f"/api/scripts/{script_id}/opened")
        assert response.status_code == 200
        body = client.get("/?filter=recent").text
        assert script_id in body

    def test_fetching_the_reader_page_does_not_count_as_opening(self, client):
        """Only the page's own POST records an open, never the GET.

        A prefetch or a crawler fetching the reader must not reorder
        Recently opened.
        """
        import re

        script_id = _first_script(client)
        client.get(f"/scripts/{script_id}")
        body = client.get("/?filter=recent").text
        assert not re.findall(r'data-id="', body)

    def test_it_differs_from_all_scripts(self, client):
        """The two entries must not be the same list under two names."""
        import re

        script_id = _first_script(client)
        client.post(f"/api/scripts/{script_id}/opened")
        every = len(re.findall(r'data-id="', client.get("/").text))
        recent = len(re.findall(r'data-id="', client.get("/?filter=recent").text))
        assert every == 3
        assert recent == 1


class TestLockedFeatures:
    """The demo corpus seeds with graphs, so emptiness has to be created."""

    def test_ask_is_locked_when_the_graph_is_empty(self, client):
        client.post("/api/graphs/clear")
        body = client.get("/ask").text
        assert 'class="locked"' in body
        assert "disabled" in body
        assert "No graph has been built yet" in body
        assert "/settings" in body

    def test_the_reader_says_why_the_graph_cannot_be_built(self, client):
        script_id = _first_script(client)
        body = client.get(f"/scripts/{script_id}").text
        assert "No model is selected" in body
        assert 'href="/settings"' in body

    def test_the_graph_pages_name_the_missing_step(self, client):
        client.post("/api/graphs/clear")
        for path in ("/entities", "/assertions"):
            body = client.get(path).text
            assert "No graph has been built yet" in body, path
            assert 'href="/settings"' in body, path

    def test_a_locked_page_offers_no_live_control(self, client):
        """Greying alone is not enough: the control has to be disabled."""
        import re

        client.post("/api/graphs/clear")
        body = client.get("/ask").text
        form = re.search(r'id="askform".*?</div>\s*</div>', body, re.DOTALL)
        assert form, "the ask form did not render"
        assert form.group(0).count("disabled") >= 2

    def test_ask_is_live_on_the_seeded_corpus(self, client):
        """First open must not show a locked page: the graphs ship seeded."""
        body = client.get("/ask").text
        assert "No graph has been built yet" not in body


class TestCollapsiblePanes:
    """Every side pane has a visible control that hides it."""

    def test_the_sidebar_toggle_is_on_every_page(self, client):
        for path in ("/", "/settings", "/ask", "/entities", "/reports"):
            body = client.get(path).text
            assert 'id="toggle-side"' in body, path
            assert "aria-label" in body, path

    def test_the_reader_has_a_pane_toggle(self, client):
        script_id = _first_script(client)
        body = client.get(f"/scripts/{script_id}").text
        assert 'data-pane="reader"' in body
        assert 'data-target=".reader"' in body

    def test_the_graph_page_has_a_detail_toggle(self, client):
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        body = client.get(f"/graph/{unit_id}").text
        assert 'data-pane="graph"' in body
        assert 'data-target=".gsplit"' in body


class TestAssetVersioning:
    def test_every_asset_link_is_versioned(self, client):
        """An unversioned asset leaves a browser running old CSS against new
        markup, which fails invisibly: the file on disk is right and only the
        loaded stylesheet is stale."""
        import re

        body = client.get("/").text
        assets = re.findall(r'(?:href|src)="(/static/[^"]+)"', body)
        assert assets
        for asset in assets:
            assert "?v=" in asset, f"unversioned asset: {asset}"

    def test_the_version_changes_when_a_file_changes(self, tmp_path, monkeypatch):
        """A newer file must produce a different version, checked against a
        throwaway static tree so the test can fail and touches nothing in the
        repository."""
        import time

        from ripple.web import app as web

        static = tmp_path / "static"
        static.mkdir()
        sheet = static / "app.css"
        sheet.write_text("body{}", encoding="utf-8")
        monkeypatch.setattr(web, "HERE", tmp_path)

        first = web.asset_version()
        later = time.time() + 30
        os.utime(sheet, (later, later))
        second = web.asset_version()
        assert second != first
        assert int(second) > int(first)


class TestInlineEditing:
    """The script itself is editable; nothing is applied until accept."""

    def test_every_line_is_editable(self, client):
        import re

        script_id = _first_script(client)
        body = client.get(f"/scripts/{script_id}").text
        units = re.findall(r'<div class="u [^"]*"[^>]*>', body)
        assert units
        assert all('contenteditable="plaintext-only"' in unit for unit in units)

    def test_each_line_carries_its_accepted_text(self, client):
        """The draft has to be comparable against what is actually accepted."""
        script_id = _first_script(client)
        body = client.get(f"/scripts/{script_id}").text
        assert 'data-accepted="' in body

    def test_the_reader_offers_a_way_to_discard_edits(self, client):
        script_id = _first_script(client)
        body = client.get(f"/scripts/{script_id}").text
        assert 'id="revert-all"' in body
        assert 'id="draft-count"' in body

    def test_editing_alone_changes_no_stored_text(self, client, judged):
        """A draft lives in the page. Only acceptance touches the database.

        The judged fixture lets the preview complete: without a model
        the request is refused up front, and a refused preview proves
        nothing about what a completed one writes.
        """
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        before = client.get(f"/api/units/{unit_id}/requirements").json()["unit"]["text"]
        response = client.post(
            f"/api/units/{unit_id}/preview", data={"proposed_text": "Something else."}
        )
        assert response.status_code == 200
        assert response.json()["change_set_id"]
        after = client.get(f"/api/units/{unit_id}/requirements").json()["unit"]["text"]
        assert after == before


class TestScriptGraph:
    """The script-level graph, the landing view for a script."""

    def test_the_graph_page_renders_for_a_seeded_script(self, client):
        script_id = _first_script(client)
        body = client.get(f"/scripts/{script_id}/graph").text
        assert 'id="script-canvas"' in body
        assert "Open the script" in body

    def test_the_payload_places_every_scene_on_the_spine(self, client):
        script_id = _first_script(client)
        payload = client.get(f"/api/scripts/{script_id}/graph").json()
        scenes = [n for n in payload["nodes"] if n["kind"] == "scene"]
        assert len(scenes) >= 30
        assert all(0 < n["x"] < 1 and 0 < n["y"] < 1 for n in payload["nodes"])

    def test_the_seeded_graph_has_entities_and_edges(self, client):
        script_id = _first_script(client)
        payload = client.get(f"/api/scripts/{script_id}/graph").json()
        entities = [n for n in payload["nodes"] if n["kind"] == "entity"]
        assert len(entities) >= 15
        assert len(payload["links"]) >= 50

    def test_the_department_filter_narrows_the_nodes(self, client):
        script_id = _first_script(client)
        payload = client.get(
            f"/api/scripts/{script_id}/graph?departments=cast"
        ).json()
        kinds = {n["entity_type"] for n in payload["nodes"] if n["kind"] == "entity"}
        assert kinds == {"cast"}

    def test_the_confidence_threshold_reports_what_it_hid(self, client):
        script_id = _first_script(client)
        payload = client.get(
            f"/api/scripts/{script_id}/graph?min_confidence=0.95"
        ).json()
        assert payload["links"] == []
        assert payload["hidden_below_threshold"] > 0

    def test_an_unknown_script_is_a_404(self, client):
        import uuid

        response = client.get(f"/api/scripts/{uuid.uuid4()}/graph")
        assert response.status_code == 404

    def test_an_unnumbered_scene_never_borrows_a_number(self, client):
        """An intercut sub-heading or an OMITTED slug has no scene number.

        Substituting its position invents a number that collides with a
        numbered scene further along the spine, so an unnumbered scene shows
        a visible placeholder instead.
        """
        import uuid

        from ripple.db.models import Scene

        script_id = _first_script(client)
        with web._sessions() as session:
            scene = (
                session.query(Scene)
                .filter(Scene.script_id == uuid.UUID(script_id))
                .order_by(Scene.sequence_index)
                .first()
            )
            original = f"Sc {scene.display_scene_number}"
            scene.display_scene_number = None
            session.commit()

        payload = client.get(f"/api/scripts/{script_id}/graph").json()
        labels = [n["label"] for n in payload["nodes"] if n["kind"] == "scene"]
        assert "Sc —" in labels
        assert original not in labels
        numbered = [label for label in labels if label != "Sc —"]
        assert len(set(numbered)) == len(numbered)


class TestEntityDetail:
    def _an_entity(self, client):
        script_id = _first_script(client)
        payload = client.get(f"/api/scripts/{script_id}/graph").json()
        return next(n for n in payload["nodes"] if n["kind"] == "entity")

    def test_the_card_carries_attributes_with_evidence(self, client):
        script_id = _first_script(client)
        payload = client.get(f"/api/scripts/{script_id}/graph").json()
        with_attributes = None
        for node in payload["nodes"]:
            if node["kind"] != "entity":
                continue
            card = client.get(f"/api/entities/{node['id']}/detail").json()
            if card["attributes"]:
                with_attributes = card
                break
        assert with_attributes, "no seeded entity carries an attribute"
        attribute = with_attributes["attributes"][0]
        assert attribute["key"] and attribute["value"]
        assert attribute["provenance"] == "system"

    def test_the_card_lists_assertions_and_scenes(self, client):
        entity = self._an_entity(client)
        card = client.get(f"/api/entities/{entity['id']}/detail").json()
        assert card["name"] == entity["label"]
        assert isinstance(card["assertions"], list)
        assert isinstance(card["scenes"], list)

    def test_an_unknown_entity_is_a_404(self, client):
        import uuid

        response = client.get(f"/api/entities/{uuid.uuid4()}/detail")
        assert response.status_code == 404


class TestStaleLinksAreHonest:
    """A link naming a script that is gone answers 404, never a silent swap."""

    MISSING = "00000000-0000-0000-0000-000000000000"

    def test_ask_with_an_unknown_script_is_404(self, client):
        assert client.get(f"/ask?script={self.MISSING}").status_code == 404

    def test_findings_with_an_unknown_script_is_404(self, client):
        assert client.get(f"/findings?script={self.MISSING}").status_code == 404

    def test_ask_without_a_script_still_lands_on_the_newest(self, client):
        assert client.get("/ask").status_code == 200


class TestDeletionPreviewScoping:
    def test_one_scripts_preview_excludes_anothers_findings(self, client):
        """Regression: the confirmation used to count every script's
        findings, overstating what one delete would remove."""
        import re
        import uuid as uuid_module

        from ripple.db.models import ChangeSet, ContinuityFinding

        ids = re.findall(r'data-id="([0-9a-f-]{36})"', client.get("/").text)
        keep, doomed = ids[0], ids[1]
        with web._sessions() as session:
            change_set = ChangeSet(
                script_id=uuid_module.UUID(keep),
                kind="edit",
                status="pending",
                base_script_version=1,
            )
            session.add(change_set)
            session.flush()
            session.add(
                ContinuityFinding(
                    change_set_id=change_set.id,
                    finding_type="orphaned_reference",
                    severity="high",
                    message="a finding on the script being kept",
                    status="open",
                )
            )
            session.commit()

        doomed_preview = client.get(f"/api/scripts/{doomed}/deletion-preview").json()
        keep_preview = client.get(f"/api/scripts/{keep}/deletion-preview").json()
        assert doomed_preview["findings"] == 0
        assert keep_preview["findings"] == 1


class TestDurableProposalStatus:
    def test_accepting_a_stale_proposal_marks_it_stale_durably(
        self, client, judged
    ):
        """Regression: the stale mark was rolled back with the 409, so the
        proposal stayed pending and kept matching the preview cache."""
        import uuid as uuid_module

        from sqlalchemy import select

        from ripple.db.models import ChangeSet, Scene, Script, ScriptUnit

        script_id = _first_script(client)
        with web._sessions() as session:
            unit_id = str(
                session.execute(
                    select(ScriptUnit.id)
                    .join(Scene, ScriptUnit.scene_id == Scene.id)
                    .where(Scene.script_id == uuid_module.UUID(script_id))
                    .limit(1)
                ).scalar_one()
            )

        body = client.post(
            f"/api/units/{unit_id}/preview",
            data={"proposed_text": "A wholly rewritten line for this test."},
        ).json()
        change_set_id = body["change_set_id"]

        with web._sessions() as session:
            script = session.get(Script, uuid_module.UUID(script_id))
            script.current_version += 1
            session.commit()

        response = client.post(f"/api/changes/{change_set_id}/accept")
        assert response.status_code == 409

        with web._sessions() as session:
            stored = session.get(ChangeSet, uuid_module.UUID(change_set_id))
            assert stored.status == "stale"


class TestSecretsIsolation:
    def test_the_suite_runs_without_the_live_credential(self, client):
        """Startup loads the secret store into the environment; the fixtures
        point it at an empty file, so the live key must never be present
        while a web test runs."""
        assert "GOOGLE_API_KEY" not in os.environ


class TestSceneStructure:
    """The insertion, omission, and restoration routes."""

    def _scene_id(self, client, script_id) -> str:
        import re

        page = client.get(f"/scripts/{script_id}").text
        match = re.search(r'data-scene-body="([0-9a-f-]{36})"', page)
        assert match
        return match.group(1)

    def test_a_scene_inserts_through_the_api(self, client):
        script_id = _first_script(client)
        first_scene = self._scene_id(client, script_id)
        response = client.post(
            f"/api/scripts/{script_id}/scenes",
            json={
                "heading": "INT. BREAK ROOM - NIGHT",
                "body": "A kettle rattles.",
                "after_scene_id": first_scene,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["scene"]["heading"] == "INT. BREAK ROOM - NIGHT"
        # No model is configured in the test client, so no run starts.
        assert body["run"] is None
        assert body["scene"]["heading"] in client.get(f"/scripts/{script_id}").text

    def test_a_bad_heading_is_a_422_with_a_reason(self, client):
        script_id = _first_script(client)
        response = client.post(
            f"/api/scripts/{script_id}/scenes",
            json={"heading": "", "body": "Text."},
        )
        assert response.status_code == 422
        assert "heading" in response.json()["detail"].lower()

    def test_omit_and_restore_round_trip(self, client):
        script_id = _first_script(client)
        scene_id = self._scene_id(client, script_id)

        omitted = client.post(f"/api/scenes/{scene_id}/omit")
        assert omitted.status_code == 200, omitted.text
        assert omitted.json()["assertions_deactivated"] > 0
        assert "omit-tag" in client.get(f"/scripts/{script_id}").text

        again = client.post(f"/api/scenes/{scene_id}/omit")
        assert again.status_code == 400

        restored = client.post(f"/api/scenes/{scene_id}/restore")
        assert restored.status_code == 200, restored.text
        assert "omit-tag" not in client.get(f"/scripts/{script_id}").text

    def test_an_unknown_scene_is_a_404(self, client):
        missing = "11111111-1111-1111-1111-111111111111"
        assert client.post(f"/api/scenes/{missing}/omit").status_code == 404


class TestDraftLinking:
    """Upload-then-link: the API surface over the drafts service."""

    def _upload_revision(self, client, night_freight_fountain) -> dict:
        revised = night_freight_fountain.replace(
            b"Six monitors", b"Twelve monitors"
        )
        response = client.post(
            "/api/scripts",
            files={"file": ("night-freight-d2.fountain", revised)},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_an_upload_offers_the_same_titled_script(
        self, client, night_freight_fountain
    ):
        body = self._upload_revision(client, night_freight_fountain)
        titles = [c["title"] for c in body["draft_candidates"]]
        assert titles == ["NIGHT FREIGHT"]

    def test_linking_carries_the_graph_and_shows_the_chain(
        self, client, night_freight_fountain
    ):
        body = self._upload_revision(client, night_freight_fountain)
        predecessor = body["draft_candidates"][0]["id"]
        linked = client.post(
            f"/api/scripts/{body['id']}/link-draft",
            json={"predecessor_script_id": predecessor},
        )
        assert linked.status_code == 200, linked.text
        payload = linked.json()
        assert payload["link"]["draft_number"] == 2
        assert payload["link"]["modified"] == 1
        assert payload["link"]["assertions_carried"] > 300
        # No model configured in the test client, so no run starts.
        assert payload["run"] is None

        page = client.get(f"/scripts/{body['id']}").text
        assert "Draft 2" in page

        again = client.post(
            f"/api/scripts/{body['id']}/link-draft",
            json={"predecessor_script_id": predecessor},
        )
        assert again.status_code == 409


class TestSceneEntityCounts:
    def test_a_seeded_scene_reports_its_entities(self, client):
        """The count reads the opposite end of each scene edge; selecting the
        scene's own side is always NULL and rendered every scene as empty."""
        import re

        script_id = _first_script(client)
        page = client.get(f"/scripts/{script_id}").text
        counts = [int(n) for n in re.findall(r"(\d+) entities", page)]
        assert counts and max(counts) > 0


class TestDraftReportRoutes:
    def test_an_unlinked_script_gets_a_409_with_the_reason(self, client):
        script_id = _first_script(client)
        response = client.post(f"/api/scripts/{script_id}/draft-report")
        assert response.status_code == 409
        assert "not linked" in response.json()["detail"]

    def test_an_identical_link_builds_its_report_in_the_link_call(
        self, client, night_freight_fountain
    ):
        uploaded = client.post(
            "/api/scripts",
            files={"file": ("night-freight-d2.fountain", night_freight_fountain)},
        ).json()
        predecessor = uploaded["draft_candidates"][0]["id"]
        linked = client.post(
            f"/api/scripts/{uploaded['id']}/link-draft",
            json={"predecessor_script_id": predecessor},
        ).json()
        assert linked["link"]["to_extract"] == 0

        # Nothing was left to extract, so the link built the report itself.
        report = client.post(f"/api/scripts/{uploaded['id']}/draft-report").json()
        assert report["already_existed"] is True

    def test_the_preview_offers_no_suggestions_for_a_clean_revision(
        self, client, night_freight_fountain
    ):
        revised = night_freight_fountain.replace(
            b"Six monitors", b"Twelve monitors"
        )
        uploaded = client.post(
            "/api/scripts",
            files={"file": ("night-freight-d2.fountain", revised)},
        ).json()
        predecessor = uploaded["draft_candidates"][0]["id"]
        preview = client.post(
            f"/api/scripts/{uploaded['id']}/link-draft/preview",
            json={"predecessor_script_id": predecessor},
        ).json()
        assert preview["modified"] == 1
        assert preview["suggestions"] == []
        # Writing nothing means linking still works afterwards.
        linked = client.post(
            f"/api/scripts/{uploaded['id']}/link-draft",
            json={"predecessor_script_id": predecessor},
        )
        assert linked.status_code == 200


class TestPreviewFailureSurface:
    """A failed preview names the cause and the run that recorded it."""

    def test_a_truncated_reply_names_the_model_and_the_limits(
        self, client, monkeypatch
    ):
        from ripple.llm.base import GenerationResult
        from tests.test_preview import FakeJudge

        class TruncatingJudge(FakeJudge):
            def generate(self, model_id, prompt, **kwargs):
                result = super().generate(model_id, prompt, **kwargs)
                return GenerationResult(
                    text=result.text[:40],
                    model_id=model_id,
                    provider=self.name,
                    input_tokens=900,
                    output_tokens=1024,
                    finish_reason="max_tokens",
                )

        fake = TruncatingJudge()
        monkeypatch.setattr(web, "get_provider", lambda name: fake)
        monkeypatch.setattr(
            web.settings_service,
            "selected_model",
            lambda session: ("google", "fake-judge"),
        )
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        response = client.post(
            f"/api/units/{unit_id}/preview",
            data={"proposed_text": "A bicycle leans against the gate."},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "output_truncated"
        # The message says which model stopped, where, and against what,
        # and names the reasoning budget as the likely cause of stopping
        # short of the cap rather than mis-blaming the model's own limit.
        assert "fake-judge" in body["message"]
        assert "1024" in body["message"]
        assert "8192" in body["message"]
        assert "max_tokens" in body["message"]
        assert "reason before answering" in body["message"]
        # The trace id field is always present; with tracing off it is null.
        assert "trace_id" in body

    def test_reported_reasoning_tokens_are_stated_as_the_cause(
        self, client, monkeypatch
    ):
        from ripple.llm.base import GenerationResult
        from tests.test_preview import FakeJudge

        class ThinkingJudge(FakeJudge):
            def generate(self, model_id, prompt, **kwargs):
                result = super().generate(model_id, prompt, **kwargs)
                return GenerationResult(
                    text=result.text[:40],
                    model_id=model_id,
                    provider=self.name,
                    input_tokens=900,
                    output_tokens=1476,
                    reasoning_tokens=6716,
                    finish_reason="MAX_TOKENS",
                )

        fake = ThinkingJudge()
        monkeypatch.setattr(web, "get_provider", lambda name: fake)
        monkeypatch.setattr(
            web.settings_service,
            "selected_model",
            lambda session: ("google", "fake-judge"),
        )
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        body = client.post(
            f"/api/units/{unit_id}/preview",
            data={"proposed_text": "A bicycle leans against the gate."},
        ).json()
        assert body["code"] == "output_truncated"
        assert "1476 answer tokens" in body["message"]
        assert "6716 hidden reasoning tokens" in body["message"]


class TestTraceViewerLaunch:
    """POST /api/traces/{id}/viewer deep-links the TraceAct viewer."""

    def test_a_malformed_trace_id_is_refused(self, client):
        assert client.post("/api/traces/trc%20nope/viewer").status_code == 400

    def test_the_url_opens_the_map_filtered_to_the_trace(
        self, client, monkeypatch, tmp_path
    ):
        import traceact.viewer.instance as viewer_instance

        import ripple.tracing as tracing_module

        monkeypatch.setattr(tracing_module, "DEFAULT_TRACE_DIR", tmp_path)
        monkeypatch.setattr(
            viewer_instance,
            "launch_or_connect",
            lambda source, name: "http://127.0.0.1:8765/?source=ripple",
        )
        response = client.post("/api/traces/trc_9f3a1c7b2d44/viewer")
        assert response.status_code == 200
        url = response.json()["url"]
        assert url.startswith("http://127.0.0.1:8765/?source=ripple")
        assert "view=map" in url
        assert "open=latest" in url
        assert "pf_trace_id=trc_9f3a1c7b2d44" in url

    def test_no_trace_directory_is_a_404_with_the_reason(
        self, client, monkeypatch, tmp_path
    ):
        import ripple.tracing as tracing_module

        monkeypatch.setattr(
            tracing_module, "DEFAULT_TRACE_DIR", tmp_path / "absent"
        )
        response = client.post("/api/traces/trc_9f3a1c7b2d44/viewer")
        assert response.status_code == 404
        assert "RIPPLE_TRACING" in response.json()["detail"]


class TestBuildGraphGate:
    """Build graph is gated on billable work: changed or unextracted scenes."""

    def test_a_current_graph_disables_the_button(
        self, client, judged, monkeypatch
    ):
        monkeypatch.setattr(
            web, "pending_scene_count", lambda session, script_id, model: 0
        )
        body = client.get(f"/scripts/{_first_script(client)}").text
        assert 'id="extract" disabled' in body
        assert "The graph matches every scene" in body

    def test_changed_scenes_offer_an_update_with_the_count(
        self, client, judged, monkeypatch
    ):
        monkeypatch.setattr(
            web, "pending_scene_count", lambda session, script_id, model: 2
        )
        body = client.get(f"/scripts/{_first_script(client)}").text
        assert "Update graph" in body
        assert "2 scenes changed since the last build" in body
        assert 'id="extract" disabled' not in body

    def test_an_unbuilt_script_offers_a_plain_build(self, client, judged):
        response = client.post(
            "/api/scripts",
            files={
                "file": (
                    "gate.fountain",
                    b"INT. GATE ROOM - DAY\n\nA console blinks.\n\n"
                    b"OPERATOR\nStill green.\n\n"
                    b"EXT. GATE YARD - DAY\n\nRain on gravel.\n\n"
                    b"OPERATOR\nStill wet.\n",
                )
            },
        )
        assert response.status_code == 200, response.text
        upload = response.json()
        body = client.get(f"/scripts/{upload['id']}").text
        assert "Build graph" in body
        assert "Update graph" not in body
        assert 'id="extract" disabled' not in body


class TestSpendSurface:
    """Chargeable actions report their tokens and their cost."""

    def test_a_preview_reports_its_spend(self, client, judged):
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        body = client.post(
            f"/api/units/{unit_id}/preview",
            data={"proposed_text": "A bicycle leans against the gate."},
        ).json()
        spend = body["spend"]
        assert spend["tokens"] > 0
        # The fake judge is not in the pricing registry, so no figure is
        # invented for it.
        assert spend["cost"] is None

    def test_extraction_progress_carries_spend_fields(self, client, judged):
        # No run is started here; the shape is asserted through the preview
        # test above and the RunProgress dataclass defaults.
        from ripple.extraction.service import RunProgress

        progress = RunProgress(
            run_id="r", status="ready", total=1, completed=1, failed=0, pending=0
        )
        assert progress.tokens == 0
        assert progress.cost is None
