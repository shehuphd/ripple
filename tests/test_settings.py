"""Provider settings and credential storage.

The rule under test is asymmetry: a key goes in, and only a status comes back.
Every test here tries to get a credential value out through some path.
"""

from __future__ import annotations

import os
import stat

import pytest

from ripple.config.secrets import SecretsError, SecretStore
from ripple.db.repository import get_active_model
from ripple.db.session import create_all, create_db_engine, session_factory
from ripple.llm import ProviderError
from ripple.services.settings import SettingsService

SECRET = "sk-this-value-must-never-escape"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("REPL_ID", raising=False)
    monkeypatch.delenv("REPLIT_DEPLOYMENT", raising=False)
    return SecretStore(tmp_path / "secrets.env")


@pytest.fixture
def service(store):
    return SettingsService(store)


@pytest.fixture
def session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    create_all(engine)
    instance = session_factory(engine)()
    yield instance
    instance.close()


class TestCredentialsNeverLeak:
    def test_status_holds_no_fragment_of_the_value(self, store):
        store.save("GOOGLE_API_KEY", SECRET)
        status = store.status("google", "GOOGLE_API_KEY")
        rendered = repr(status)
        assert status.configured
        assert SECRET not in rendered
        # Not even a prefix: for several providers the prefix identifies the
        # account, and the suffix is what support channels ask for.
        assert SECRET[:8] not in rendered
        assert SECRET[-6:] not in rendered

    def test_provider_statuses_carry_no_key_material(self, service, store):
        store.save("GOOGLE_API_KEY", SECRET)
        rendered = repr(service.provider_statuses())
        assert SECRET not in rendered
        assert "sk-" not in rendered

    def test_a_validation_failure_does_not_echo_the_key(self, service, monkeypatch):
        class Rejecting:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def list_models(self, *, api_key=None):
                raise ProviderError(
                    "invalid_api_key", "The provider rejected this key."
                )

        monkeypatch.setattr(
            "ripple.services.settings.get_provider", lambda name: Rejecting()
        )
        result = service.validate("google", SECRET)
        assert not result.valid
        assert SECRET not in repr(result)

    def test_the_database_never_receives_a_credential(self, session, service, store):
        store.save("GOOGLE_API_KEY", SECRET)
        from ripple.db.repository import set_active_model

        set_active_model(session, "google", "some-model")
        from ripple.db.models import AppConfiguration

        rows = repr(list(session.query(AppConfiguration).all()))
        assert SECRET not in rows
        assert get_active_model(session) == ("google", "some-model")


class TestStorage:
    def test_the_file_is_owner_only(self, store):
        store.save("GOOGLE_API_KEY", SECRET)
        mode = stat.S_IMODE(os.stat(store.path).st_mode)
        assert mode == 0o600, f"secrets file is {oct(mode)}"

    def test_a_blank_credential_is_refused(self, store):
        with pytest.raises(SecretsError) as caught:
            store.save("GOOGLE_API_KEY", "   ")
        assert caught.value.code == "empty_value"

    def test_saving_on_replit_is_refused_with_a_reason(self, store, monkeypatch):
        """A file written into a deployment container is lost on redeploy."""
        monkeypatch.setenv("REPL_ID", "some-repl")
        with pytest.raises(SecretsError) as caught:
            store.save("GOOGLE_API_KEY", SECRET)
        assert caught.value.code == "use_replit_secrets"
        assert "Replit Secrets" in caught.value.message

    def test_an_existing_environment_value_wins_over_the_file(self, store, monkeypatch):
        store.save("GOOGLE_API_KEY", "from-file")
        monkeypatch.setenv("GOOGLE_API_KEY", "from-environment")
        store.load()
        assert os.environ["GOOGLE_API_KEY"] == "from-environment"

    def test_forget_clears_both_the_file_and_the_environment(self, store):
        store.save("GOOGLE_API_KEY", SECRET)
        assert store.forget("GOOGLE_API_KEY")
        assert not store.status("google", "GOOGLE_API_KEY").configured
        assert SECRET not in store.path.read_text()

    def test_saving_one_key_does_not_drop_another(self, store):
        """The store holds any variable name, not just a registered provider's.

        Exercised with a second, non-provider variable name to prove the
        storage mechanics stay generic even though Ripple registers Gemini
        alone.
        """
        store.save("GOOGLE_API_KEY", "first-value")
        store.save("SOME_OTHER_KEY", "second-value")
        assert store.status("google", "GOOGLE_API_KEY").configured
        assert store.status("other", "SOME_OTHER_KEY").configured


class TestValidation:
    @pytest.fixture
    def rejecting_provider(self, monkeypatch):
        """A provider that rejects every key, standing in for the endpoint.

        Stubbed so no test here contacts the network: what is under test is
        the service's handling, not Google's reply.
        """

        class Rejecting:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def list_models(self, *, api_key=None):
                raise ProviderError(
                    "invalid_api_key", "The provider rejected this key."
                )

        monkeypatch.setattr(
            "ripple.services.settings.get_provider", lambda name: Rejecting()
        )
        return Rejecting()

    def test_an_invalid_key_is_not_stored(self, service, store, rejecting_provider):
        """A stored bad key produces a screen that claims to work and does not."""
        result = service.save_credential("google", "obviously-not-a-key")
        assert not result.valid
        assert not store.status("google", "GOOGLE_API_KEY").configured

    def test_validating_does_not_leave_the_key_in_the_environment(
        self, service, rejecting_provider
    ):
        service.validate("google", SECRET)
        assert not os.environ.get("GOOGLE_API_KEY")

    def test_validating_never_disturbs_an_environment_value(
        self, service, monkeypatch, rejecting_provider
    ):
        """The candidate is passed to the call, never written process-wide,
        so whatever the environment held stays untouched throughout."""
        monkeypatch.setenv("GOOGLE_API_KEY", "the-original")
        service.validate("google", "a-temporary-key")
        assert os.environ["GOOGLE_API_KEY"] == "the-original"

    def test_an_unconfigured_provider_reports_rather_than_raises(self, service):
        result = service.validate("google")
        assert not result.valid
        assert result.error_code == "provider_not_configured"


class TestModelSelection:
    def test_no_model_is_selected_before_a_choice(self, session, service):
        """Model selection is manual rather than guessed."""
        assert service.selected_model(session) == (None, None)

    def test_selecting_a_model_the_provider_does_not_offer_is_refused(
        self, session, service, monkeypatch
    ):
        from ripple.llm.base import ModelInfo, Tier

        class Catalog:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def list_models(self, *, api_key=None):
                return [
                    ModelInfo(
                        id="offered-model",
                        provider="google",
                        display_name="offered-model",
                        tier=Tier.CHEAP,
                    )
                ]

        monkeypatch.setattr(
            "ripple.services.settings.get_provider", lambda name: Catalog()
        )
        with pytest.raises(ProviderError) as caught:
            service.select_model(session, "google", "a-model-that-does-not-exist")
        assert caught.value.code == "unknown_model"

    def test_an_unknown_provider_is_refused(self, session, service):
        with pytest.raises(ProviderError) as caught:
            service.select_model(session, "bard", "anything")
        assert caught.value.code == "unknown_provider"

    def test_a_model_a_live_call_found_dead_is_refused(
        self, session, service, monkeypatch
    ):
        """The catalog lists models the account cannot invoke, so the dead
        mark gates the choice where it is stored, matching the disabled
        picker entry."""
        from ripple.db.repository import (
            clear_model_unavailable,
            mark_model_unavailable,
        )
        from ripple.llm.base import ModelInfo, Tier

        class Catalog:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def list_models(self, *, api_key=None):
                return [
                    ModelInfo(
                        id="gemini-2.5-flash",
                        provider="google",
                        display_name="gemini-2.5-flash",
                        tier=Tier.CHEAP,
                    )
                ]

        monkeypatch.setattr(
            "ripple.services.settings.get_provider", lambda name: Catalog()
        )
        mark_model_unavailable(session, "google", "gemini-2.5-flash")
        with pytest.raises(ProviderError) as caught:
            service.select_model(session, "google", "gemini-2.5-flash")
        assert caught.value.code == "model_not_available"
        assert service.selected_model(session) == (None, None)

        clear_model_unavailable(session, "google", "gemini-2.5-flash")
        service.select_model(session, "google", "gemini-2.5-flash")
        assert service.selected_model(session) == ("google", "gemini-2.5-flash")


class TestFixtureProvider:
    def test_it_is_absent_from_the_shipped_registry(self):
        """A test double reachable in production is a supply-chain surprise."""
        from ripple.llm import PROVIDERS

        assert "fixture" not in PROVIDERS

    def test_an_unrecorded_prompt_raises_rather_than_answering_emptily(self, tmp_path):
        """An empty answer is a legitimate extraction outcome, so the two must
        not be confusable."""
        from tests.support.fixture_provider import FixtureProvider

        provider = FixtureProvider(tmp_path)
        with pytest.raises(ProviderError) as caught:
            provider.generate("fixture-cheap", "a prompt nobody recorded")
        assert caught.value.code == "fixture_missing"

    def test_replies_are_deterministic_for_one_prompt(self, tmp_path):
        from tests.support.fixture_provider import FixtureProvider

        provider = FixtureProvider(tmp_path)
        provider.put("fixture-cheap", "the prompt", '{"ok": true}')
        first = provider.generate("fixture-cheap", "the prompt")
        second = provider.generate("fixture-cheap", "the prompt")
        assert first.text == second.text == '{"ok": true}'

    def test_it_filters_modality_like_a_real_provider(self, tmp_path):
        from tests.support.fixture_provider import FixtureProvider

        models = FixtureProvider(tmp_path).list_models()
        assert {model.tier.value for model in models} == {"cheap", "mid", "strong"}


class TestFallbackSelection:
    """The fallback model: stored like the main, validated the same way."""

    @pytest.fixture
    def catalog(self, monkeypatch):
        from ripple.llm.base import ModelInfo, Tier

        class FakeProvider:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def list_models(self):
                return [
                    ModelInfo(id=m, provider="google", display_name=m,
                              tier=Tier.CHEAP)
                    for m in ("main-model", "backup-model")
                ]

        monkeypatch.setattr(
            "ripple.services.settings.get_provider", lambda name: FakeProvider()
        )
        return FakeProvider()

    @pytest.fixture
    def db(self):
        from ripple.db.session import create_all, create_db_engine, session_factory

        engine = create_db_engine("sqlite+pysqlite:///:memory:")
        create_all(engine)
        instance = session_factory(engine)()
        yield instance
        instance.close()

    def test_a_fallback_is_stored_and_read_back(self, catalog, db):
        service = SettingsService()
        service.select_fallback(db, "google", "backup-model")
        assert service.fallback_model(db) == ("google", "backup-model")

    def test_an_unknown_fallback_is_refused(self, catalog, db):
        from ripple.llm import ProviderError

        service = SettingsService()
        with pytest.raises(ProviderError) as caught:
            service.select_fallback(db, "google", "invented-model")
        assert caught.value.code == "unknown_model"

    def test_a_fallback_equal_to_the_main_is_refused(self, catalog, db):
        from ripple.llm import ProviderError

        service = SettingsService()
        service.select_model(db, "google", "main-model")
        with pytest.raises(ProviderError) as caught:
            service.select_fallback(db, "google", "main-model")
        assert caught.value.code == "fallback_is_main"

    def test_clearing_the_fallback(self, catalog, db):
        service = SettingsService()
        service.select_fallback(db, "google", "backup-model")
        service.select_fallback(db, "google", None)
        assert service.fallback_model(db) == (None, None)

    def test_a_fallback_a_live_call_found_dead_is_refused(self, catalog, db):
        from ripple.db.repository import mark_model_unavailable
        from ripple.llm import ProviderError

        service = SettingsService()
        mark_model_unavailable(db, "google", "backup-model")
        with pytest.raises(ProviderError) as caught:
            service.select_fallback(db, "google", "backup-model")
        assert caught.value.code == "model_not_available"
        assert service.fallback_model(db) == (None, None)


class TestValidationNeverTouchesTheEnvironment:
    def test_a_candidate_key_is_passed_to_the_call_not_the_process(
        self, service, monkeypatch
    ):
        """Regression: validate() used to write the candidate into os.environ
        for the duration of the network call, so a concurrent extraction
        could read the wrong key mid-window."""
        from ripple.llm import PROVIDERS

        observed = {}

        class SpyProvider:
            name = "spy"
            credential_variable = "SPY_PROVIDER_KEY"

            def is_configured(self):
                return False

            def list_models(self, *, api_key=None):
                observed["api_key"] = api_key
                observed["env"] = os.environ.get("SPY_PROVIDER_KEY")
                return []

        monkeypatch.delenv("SPY_PROVIDER_KEY", raising=False)
        monkeypatch.setitem(PROVIDERS, "spy", SpyProvider())
        result = service.validate("spy", "  candidate-key-123  ")
        assert result.valid
        assert observed["api_key"] == "candidate-key-123"
        assert observed["env"] is None
        assert "SPY_PROVIDER_KEY" not in os.environ


class TestAgentSettings:
    """Ask Ripple's behaviour is stored as interface preferences: what the
    agent may do is fixed in code, and these only say how far it goes before
    it stops and asks."""

    @pytest.fixture
    def session(self):
        engine = create_db_engine("sqlite+pysqlite:///:memory:")
        create_all(engine)
        instance = session_factory(engine)()
        yield instance
        instance.close()

    def test_the_defaults_apply_with_no_rows_stored(self, session):
        from ripple.db.repository import get_agent_settings

        settings = get_agent_settings(session)
        assert settings.draft_around_cut is True
        assert settings.show_plan is True
        assert settings.keep_conversations is True
        assert settings.tool_ceiling == 12

    def test_a_stored_choice_survives_a_new_session(self, session):
        from ripple.db.repository import get_agent_settings, set_agent_setting

        set_agent_setting(session, "agent_draft_around_cut", "off")
        set_agent_setting(session, "agent_tool_ceiling", "24")
        session.commit()
        settings = get_agent_settings(session)
        assert settings.draft_around_cut is False
        assert settings.tool_ceiling == 24

    def test_an_unknown_setting_or_value_is_refused(self, session):
        from ripple.db.repository import set_agent_setting

        with pytest.raises(ValueError):
            set_agent_setting(session, "agent_light_theme", "on")
        with pytest.raises(ValueError):
            set_agent_setting(session, "agent_show_plan", "maybe")
        with pytest.raises(ValueError):
            set_agent_setting(session, "agent_tool_ceiling", "7")

    def test_a_ceiling_stored_out_of_range_falls_back_to_the_default(
        self, session
    ):
        """A row written by an older build, or by hand, cannot uncap a turn."""
        from ripple.db.models import UiPreference
        from ripple.db.repository import get_agent_settings

        session.add(UiPreference(key="agent_tool_ceiling", value="9999"))
        session.flush()
        assert get_agent_settings(session).tool_ceiling == 12
