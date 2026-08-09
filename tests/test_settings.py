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

    def test_a_validation_failure_does_not_echo_the_key(self, service):
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
    def test_an_invalid_key_is_not_stored(self, service, store):
        """A stored bad key produces a screen that claims to work and does not."""
        result = service.save_credential("google", "obviously-not-a-key")
        assert not result.valid
        assert not store.status("google", "GOOGLE_API_KEY").configured

    def test_validating_does_not_leave_the_key_in_the_environment(self, service):
        service.validate("google", SECRET)
        assert not os.environ.get("GOOGLE_API_KEY")

    def test_validating_restores_a_previous_environment_value(
        self, service, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_API_KEY", "the-original")
        service.validate("google", "a-temporary-key")
        assert os.environ["GOOGLE_API_KEY"] == "the-original"

    def test_an_unconfigured_provider_reports_rather_than_raises(self, service):
        result = service.validate("google")
        assert not result.valid
        assert result.error_code == "provider_not_configured"


class TestModelSelection:
    def test_no_model_is_selected_before_a_choice(self, session, service):
        """PRD section 11 defaults to manual selection rather than guessing."""
        assert service.selected_model(session) == (None, None)

    def test_selecting_a_model_the_provider_does_not_offer_is_refused(
        self, session, service, monkeypatch
    ):
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-test")
        with pytest.raises(ProviderError):
            service.select_model(session, "google", "a-model-that-does-not-exist")

    def test_an_unknown_provider_is_refused(self, session, service):
        with pytest.raises(ProviderError) as caught:
            service.select_model(session, "bard", "anything")
        assert caught.value.code == "unknown_provider"


class TestFixtureProvider:
    def test_it_is_absent_from_the_shipped_registry(self):
        """A test double reachable in production is a supply-chain surprise."""
        from ripple.llm import PROVIDERS

        assert "fixture" not in PROVIDERS

    def test_an_unrecorded_prompt_raises_rather_than_answering_emptily(self, tmp_path):
        """An empty answer is a legitimate extraction outcome, so the two must
        not be confusable."""
        from ripple.llm.fixture import FixtureProvider

        provider = FixtureProvider(tmp_path)
        with pytest.raises(ProviderError) as caught:
            provider.generate("fixture-cheap", "a prompt nobody recorded")
        assert caught.value.code == "fixture_missing"

    def test_replies_are_deterministic_for_one_prompt(self, tmp_path):
        from ripple.llm.fixture import FixtureProvider

        provider = FixtureProvider(tmp_path)
        provider.put("fixture-cheap", "the prompt", '{"ok": true}')
        first = provider.generate("fixture-cheap", "the prompt")
        second = provider.generate("fixture-cheap", "the prompt")
        assert first.text == second.text == '{"ok": true}'

    def test_it_filters_modality_like_a_real_provider(self, tmp_path):
        from ripple.llm.fixture import FixtureProvider

        models = FixtureProvider(tmp_path).list_models()
        assert {model.tier.value for model in models} == {"cheap", "mid", "strong"}
