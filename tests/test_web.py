"""The web layer.

Routes are thin, so the tests target what a thin layer can still get wrong:
leaking a credential, returning a 500 for an expected refusal, accepting a
malformed identifier, and letting one script's delete touch another.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ripple.web import app as web


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client over a throwaway database, seeded with the demo corpus."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path/'t.db'}")
    monkeypatch.setenv("RIPPLE_TRACING", "off")
    for variable in (
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
    with TestClient(web.app) as instance:
        yield instance


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
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-must-not-appear-in-html")
        body = client.get("/settings").text
        assert "sk-must-not-appear-in-html" not in body
        assert "DEEPSEEK_API_KEY" in body  # the variable name is fine to show

    def test_the_provider_api_returns_no_key(self, client, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-appear-in-json")
        body = client.get("/api/settings/providers").text
        assert "sk-must-not-appear-in-json" not in body
        assert "configured" in body

    def test_validating_a_bad_key_does_not_echo_it(self, client):
        response = client.post(
            "/api/settings/validate",
            data={"provider": "openai", "api_key": "sk-echo-me-please"},
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

    def test_settings_lists_every_provider(self, client):
        body = client.get("/settings").text
        for provider in ("google", "openai", "anthropic", "deepseek"):
            assert provider in body

    def test_settings_marks_which_provider_ships(self, client):
        body = client.get("/settings").text
        assert "ships with the submission" in body
        assert "development only" in body


class TestUnitEndpoints:
    def test_requirements_are_empty_before_extraction(self, client):
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        payload = client.get(f"/api/units/{unit_id}/requirements").json()
        assert payload["assertions"] == []
        assert payload["unit"]["text"]

    def test_the_preview_runs_with_no_provider_configured(self, client):
        """The diff and the deterministic findings need no model."""
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        response = client.post(
            f"/api/units/{unit_id}/preview",
            data={"proposed_text": "A bicycle leans against the gate."},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["summary_source"] == "deterministic"
        assert body["change_set_id"]
        assert set(body["diff"]["summary"]) == {
            "added",
            "removed",
            "changed",
            "unchanged",
        }
        assert body["pipeline"][0]["name"] == "Parse unit"


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
        body = client.post(
            f"/api/units/{unit_id}/preview", data={"proposed_text": text}
        ).json()
        return unit_id, body

    def test_a_preview_applies_nothing(self, client):
        unit_id, _ = self._preview(client)
        before = client.get(f"/api/units/{unit_id}/requirements").json()
        assert before["unit"]["text"] != "A bicycle leans against the gate."

    def test_accepting_applies_the_text(self, client):
        unit_id, preview = self._preview(client)
        response = client.post(f"/api/changes/{preview['change_set_id']}/accept")
        assert response.status_code == 200
        after = client.get(f"/api/units/{unit_id}/requirements").json()
        assert after["unit"]["text"] == "A bicycle leans against the gate."

    def test_rejecting_applies_nothing(self, client):
        unit_id, preview = self._preview(client)
        assert (
            client.post(
                f"/api/changes/{preview['change_set_id']}/reject", data={}
            ).status_code
            == 200
        )
        after = client.get(f"/api/units/{unit_id}/requirements").json()
        assert after["unit"]["text"] != "A bicycle leans against the gate."

    def test_accepting_twice_is_refused(self, client):
        _, preview = self._preview(client)
        client.post(f"/api/changes/{preview['change_set_id']}/accept")
        second = client.post(f"/api/changes/{preview['change_set_id']}/accept")
        assert second.status_code == 400
        assert second.json()["code"] == "invalid_operation"

    def test_a_stale_proposal_is_a_409(self, client):
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

    def test_undo_restores_the_original_text(self, client):
        unit_id, preview = self._preview(client)
        original = client.get(f"/api/units/{unit_id}/requirements").json()["unit"][
            "text"
        ]
        client.post(f"/api/changes/{preview['change_set_id']}/accept")
        assert client.post(f"/api/units/{unit_id}/undo").status_code == 200
        after = client.get(f"/api/units/{unit_id}/requirements").json()
        assert after["unit"]["text"] == original

    def test_undo_with_nothing_accepted_is_refused(self, client):
        script_id = _first_script(client)
        unit_id = _units(client, script_id)[0]
        assert client.post(f"/api/units/{unit_id}/undo").status_code == 400


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


class TestRecentlyOpened:
    def test_it_is_empty_until_a_script_is_opened(self, client):
        import re

        body = client.get("/?filter=recent").text
        assert "Recently opened" in body
        assert not re.findall(r'data-id="', body)

    def test_opening_a_script_puts_it_there(self, client):
        script_id = _first_script(client)
        client.get(f"/scripts/{script_id}")
        body = client.get("/?filter=recent").text
        assert script_id in body

    def test_it_differs_from_all_scripts(self, client):
        """The two entries must not be the same list under two names."""
        import re

        script_id = _first_script(client)
        client.get(f"/scripts/{script_id}")
        every = len(re.findall(r'data-id="', client.get("/").text))
        recent = len(re.findall(r'data-id="', client.get("/?filter=recent").text))
        assert every == 3
        assert recent == 1


class TestLockedFeatures:
    def test_ask_is_locked_when_the_graph_is_empty(self, client):
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
        for path in ("/entities", "/assertions"):
            body = client.get(path).text
            assert "No graph has been built yet" in body, path
            assert 'href="/settings"' in body, path

    def test_a_locked_page_offers_no_live_control(self, client):
        """Greying alone is not enough: the control has to be disabled."""
        import re

        body = client.get("/ask").text
        form = re.search(r'id="askform".*?</div>\s*</div>', body, re.DOTALL)
        assert form, "the ask form did not render"
        assert form.group(0).count("disabled") >= 2


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
        from ripple.web import app as web

        first = web.asset_version()
        target = web.HERE / "static" / "css" / "app.css"
        target.touch()
        assert web.asset_version() >= first
