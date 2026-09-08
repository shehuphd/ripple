"""Provider adapters.

No credential is present in CI, so these tests attack the parts that must hold
without one: modality filtering, tier inference, credential absence, and the
guarantee that no credential value ever appears in a result.
"""

from __future__ import annotations

import pytest

from ripple.llm import (
    PROVIDERS,
    configured_providers,
    get_provider,
)
from ripple.llm.base import (
    ProviderError,
    ProviderNotConfigured,
    Tier,
    infer_tier,
    is_text_model,
)


class TestSeedCapabilityProbe:
    """KeyCall gained `seed` after 1.8.0, so the adapter reads the installed
    signature rather than assuming. The probe decides whether a seed is sent,
    and both answers have to behave."""

    def test_the_probe_reads_the_installed_signature(self):
        import inspect

        from keycall import KeyCall

        from ripple.llm.keycall_provider import accepts_seed

        expected = "seed" in inspect.signature(KeyCall.generate_text).parameters
        assert accepts_seed() is expected

    def test_a_seed_is_sent_only_where_the_installed_client_takes_one(
        self, monkeypatch
    ):
        """On a build with the parameter the seed reaches KeyCall; on one
        without, it is dropped rather than raising a TypeError."""
        from ripple.llm import keycall_provider

        sent = {}

        class FakeResult:
            text = "{}"
            model = "gemini-flash-lite-latest"
            usage = None
            finish_reason = "stop"

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def generate_text(self, **kwargs):
                sent.clear()
                sent.update(kwargs)
                return FakeResult()

        provider = keycall_provider.KeycallProvider("google", "GOOGLE_API_KEY")
        monkeypatch.setattr(provider, "_client", lambda: FakeClient())

        monkeypatch.setattr(keycall_provider, "accepts_seed", lambda: True)
        provider.generate("gemini-flash-lite-latest", "hi", seed=20260908)
        assert sent["seed"] == 20260908
        assert sent["temperature"] == 0.0

        monkeypatch.setattr(keycall_provider, "accepts_seed", lambda: False)
        provider.generate("gemini-flash-lite-latest", "hi", seed=20260908)
        assert "seed" not in sent, "an older KeyCall must not be sent a seed"
        assert sent["temperature"] == 0.0


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
    def test_gemini_is_the_only_provider(self):
        """The runtime registry holds Google alone; no other provider ships.

        Ripple builds and tests against Gemini only now, rather than carrying
        OpenAI/Anthropic/DeepSeek dev-only adapters to strip out later.
        """
        assert set(PROVIDERS) == {"google"}

    def test_every_provider_satisfies_the_contract(self):
        for name, provider in PROVIDERS.items():
            assert provider.name == name
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
        provider = get_provider("google")
        monkeypatch.setenv(provider.credential_variable, "   ")
        assert not provider.is_configured()

    def test_no_credential_value_reaches_an_error_message(self, monkeypatch):
        """The provider's typed rejection carries through with no key in it.

        The client is stubbed so the test never contacts the network: the
        guarantee under test is Ripple's error carrying, not Google's reply.
        """
        from keycall import ErrorCode, KeyCallError

        secret = "sk-do-not-leak-this-value"

        class RejectingClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *ignored):
                return False

            def list_models(self, refresh=True):
                raise KeyCallError(
                    "API key not valid for this provider.",
                    code=ErrorCode("invalid_api_key"),
                )

        monkeypatch.setattr(
            "ripple.llm.keycall_provider.KeyCall", RejectingClient
        )
        provider = get_provider("google")
        monkeypatch.setenv(provider.credential_variable, secret)
        assert provider.is_configured()
        with pytest.raises(ProviderError) as caught:
            provider.list_models()
        assert caught.value.code == "invalid_api_key"
        assert secret not in str(caught.value)
        assert secret not in caught.value.message

    def test_configured_providers_reports_names_only(self, monkeypatch):
        for provider in PROVIDERS.values():
            monkeypatch.delenv(provider.credential_variable, raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "sk-secret")
        listed = configured_providers()
        assert listed == ["google"]
        assert all("sk-" not in name for name in listed)


class TestActionableErrors:
    """A pasted 401 tells the user their key failed, which they knew.

    KeyCall's own typed errors (`error.code`, `error.message`) already name
    the cause and stay actionable; there is nothing left for Ripple to
    reinterpret. What this class still checks is Ripple's side of the
    contract: the local format-blind gate lets every real-looking key
    through to the provider, and the provider's own rejection surfaces as a
    `ProviderError`, not a crash.
    """

    def _validate_google(self, monkeypatch, tmp_path, key):
        """Run a key through the local gate, with the provider stubbed.

        The stub answers the way the live endpoint answers a bad key, so the
        test proves the local gate let the value through without a network
        call being involved.
        """
        from ripple.config.secrets import SecretStore
        from ripple.services.settings import SettingsService

        class ProviderJudges:
            name = "google"
            credential_variable = "GOOGLE_API_KEY"

            def list_models(self, *, api_key=None):
                raise ProviderError(
                    "invalid_api_key", "The provider rejected this key."
                )

        monkeypatch.setattr(
            "ripple.services.settings.get_provider", lambda name: ProviderJudges()
        )
        return SettingsService(SecretStore(tmp_path / "s.env")).validate(
            "google", key
        )

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
