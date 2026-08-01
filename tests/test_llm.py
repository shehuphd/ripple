"""Provider adapters.

No credential is present in CI, so these tests attack the parts that must hold
without one: modality filtering, tier inference, credential absence, and the
guarantee that no credential value ever appears in a result.
"""

from __future__ import annotations

import pytest

from ripple.llm import (
    PROVIDERS,
    SUBMISSION_PROVIDER,
    configured_providers,
    get_provider,
)
from ripple.llm.base import (
    ProviderError,
    ProviderNotConfigured,
    Tier,
    infer_tier,
    is_text_model,
    strip_code_fence,
)


class TestModalityFilter:
    """A picker offering an embedding model produces an unexplained runtime error."""

    @pytest.mark.parametrize(
        "model_id",
        [
            "text-embedding-004",
            "gemini-embedding-001",
            "imagen-3.0-generate-002",
            "veo-2.0-generate-001",
            "gpt-4o-realtime-preview",
            "whisper-1",
            "tts-1-hd",
            "dall-e-3",
            "omni-moderation-latest",
            "text-moderation-stable",
            "rerank-english-v3.0",
            "sora-2",
            "gpt-4o-audio-preview",
            "gpt-4o-transcribe",
        ],
    )
    def test_non_text_models_are_excluded(self, model_id):
        assert not is_text_model(model_id)

    @pytest.mark.parametrize(
        "model_id",
        [
            "gemini-2.5-flash",
            "gemini-3-pro",
            "gpt-5",
            "claude-opus-5",
            "deepseek-chat",
            "o3-mini",
        ],
    )
    def test_text_models_survive(self, model_id):
        assert is_text_model(model_id)


class TestTierInference:
    def test_cheap_markers_win_over_strong_ones(self):
        """A flash variant of a pro family must route to the cheap tier.

        Sending bulk extraction to an unexpectedly expensive model costs money;
        the reverse costs a retry.
        """
        assert infer_tier("gemini-2.5-flash-lite") is Tier.CHEAP
        assert infer_tier("some-pro-flash-model") is Tier.CHEAP

    def test_strong_and_unknown_are_distinguished(self):
        assert infer_tier("claude-opus-5") is Tier.STRONG
        assert infer_tier("an-unfamiliar-model") is Tier.MID


class TestRegistry:
    def test_gemini_is_the_submission_provider(self):
        assert SUBMISSION_PROVIDER == "google"
        assert SUBMISSION_PROVIDER in PROVIDERS

    def test_every_provider_satisfies_the_contract(self):
        for name, provider in PROVIDERS.items():
            assert provider.name == name or name == "google"
            assert callable(provider.is_configured)
            assert callable(provider.list_models)
            assert callable(provider.generate)
            assert provider.credential_variable.endswith("_API_KEY")

    def test_an_unknown_provider_names_the_valid_options(self):
        with pytest.raises(ProviderError) as caught:
            get_provider("bard")
        assert caught.value.code == "unknown_provider"
        assert "google" in caught.value.message


class TestCredentialHandling:
    def test_an_unconfigured_provider_refuses_and_names_the_variable(self, monkeypatch):
        for provider in PROVIDERS.values():
            monkeypatch.delenv(provider.credential_variable, raising=False)
        for name, provider in PROVIDERS.items():
            assert not provider.is_configured(), name
            with pytest.raises(ProviderNotConfigured) as caught:
                provider.list_models()
            assert provider.credential_variable in caught.value.message

    def test_a_blank_credential_counts_as_absent(self, monkeypatch):
        provider = get_provider("deepseek")
        monkeypatch.setenv(provider.credential_variable, "   ")
        assert not provider.is_configured()

    def test_no_credential_value_reaches_an_error_message(self, monkeypatch):
        secret = "sk-do-not-leak-this-value"
        provider = get_provider("openai")
        monkeypatch.setenv(provider.credential_variable, secret)
        assert provider.is_configured()
        with pytest.raises(ProviderError) as caught:
            provider.list_models()
        assert secret not in str(caught.value)
        assert secret not in caught.value.message

    def test_configured_providers_reports_names_only(self, monkeypatch):
        for provider in PROVIDERS.values():
            monkeypatch.delenv(provider.credential_variable, raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret")
        listed = configured_providers()
        assert listed == ["deepseek"]
        assert all("sk-" not in name for name in listed)


class TestJsonHandling:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ('```json\n{"a": 1}\n```', '{"a": 1}'),
            ('```\n{"a": 1}\n```', '{"a": 1}'),
            ('{"a": 1}', '{"a": 1}'),
            ('  {"a": 1}  ', '{"a": 1}'),
        ],
    )
    def test_a_fenced_json_reply_is_unwrapped(self, raw, expected):
        assert strip_code_fence(raw) == expected


class TestActionableErrors:
    """A pasted 401 tells the user their key failed, which they knew."""

    @pytest.mark.parametrize(
        "detail, expected",
        [
            ("401 UNAUTHENTICATED ... ACCESS_TOKEN_TYPE_UNSUPPORTED", "aistudio"),
            ("API key not valid. Please pass a valid API key.", "Generative Language"),
            ("Error 401 Authentication Fails, Your api key is invalid", "revoked"),
            ("429 insufficient_quota", "quota or billing"),
            ("403 PERMISSION_DENIED on this resource", "restricted"),
        ],
    )
    def test_a_failure_names_a_cause_and_a_fix(self, detail, expected):
        from ripple.llm.base import explain_auth_failure

        message = explain_auth_failure("Google", detail)
        assert expected in message
        assert len(message) > 40

    def test_an_unrecognised_failure_still_says_something(self):
        from ripple.llm.base import explain_auth_failure

        assert "could not be reached" in explain_auth_failure("openai", "socket closed")

    def _validate_google(self, monkeypatch, tmp_path, key):
        from ripple.config.secrets import SecretStore
        from ripple.services.settings import SettingsService

        monkeypatch.setenv("GOOGLE_API_KEY", key)
        return SettingsService(SecretStore(tmp_path / "s.env")).validate("google")

    @pytest.mark.parametrize(
        "key",
        [
            "AQ.Ab8RN6KWfzGBqBkazzf0Smxxxxxxxxxxxxxxxxxxxx",
            "AIzaSyC-an-older-format-key-0000000000000",
            "some-future-format-nobody-has-seen-yet",
            "sk-proj-lots-of-dashes-and_underscores.and.dots",
            '{"looks":"like json but might not be"}',
            "a key with a space in it",
            "x" * 1024,
        ],
    )
    def test_only_the_provider_judges_a_key(self, monkeypatch, tmp_path, key):
        """No local check may reject a value for its shape.

        AI Studio has issued keys beginning `AIza` and, more recently, `AQ.`,
        and any allowlist of prefixes, character sets, or lengths will be wrong
        again the next time a format changes. Rejecting a working key is worse
        than the provider error it was meant to pre-empt: the user cannot get
        past it, and the app is confidently wrong about their credential.
        """
        result = self._validate_google(monkeypatch, tmp_path, key)
        assert result.error_code != "unusable_credential", key

    @pytest.mark.parametrize(
        "value, because",
        [
            (b"\x89PNG\r\n\x1a\n", "text"),
            ("key-with-a-\x00-null-byte", "binary"),
            ("x" * 1025, "longer than any API key"),
            ("   ", "Enter a key"),
        ],
    )
    def test_a_value_that_cannot_be_sent_at_all_is_refused(self, value, because):
        """Type and size only: what a credential must be to reach an endpoint."""
        from ripple.services.settings import unusable_credential

        message = unusable_credential(value)
        assert message and because in message
