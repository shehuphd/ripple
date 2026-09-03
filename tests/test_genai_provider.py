"""The google-genai adapter: the Ask path's Google SDK, mapped to Ripple's
provider contract. No network: the SDK client is faked.

The hackathon requires an accepted Google Cloud SDK imported and called at
runtime. These tests pin the mapping the adapter performs; the live call is
exercised on the Ask page against a live key.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ripple.llm import QUERY_PROVIDERS, get_query_provider
from ripple.llm.base import LLMProvider, ProviderError, ProviderNotConfigured
from ripple.llm.genai_provider import (
    GoogleGenaiProvider,
    _to_provider_error,
    _to_result,
)


def _response(text, *, finish="STOP", prompt=11, out=7, thoughts=3, model="v1"):
    """A fake GenerateContentResponse shaped like the fields the adapter reads."""
    return SimpleNamespace(
        text=text,
        model_version=model,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=out,
            thoughts_token_count=thoughts,
        ),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
    )


class _FakeModels:
    def __init__(self, response=None, models=None, raise_with=None):
        self._response = response
        self._models = models or []
        self._raise = raise_with
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._raise:
            raise self._raise
        return self._response

    def list(self):
        return iter(self._models)


class _FakeClient:
    def __init__(self, models):
        self.models = models


class TestContract:
    def test_it_satisfies_the_provider_protocol(self):
        provider = GoogleGenaiProvider("google", "GOOGLE_API_KEY")
        assert isinstance(provider, LLMProvider)

    def test_is_configured_reads_the_environment(self, monkeypatch):
        provider = GoogleGenaiProvider("google", "GOOGLE_API_KEY")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert provider.is_configured() is False
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        assert provider.is_configured() is True

    def test_generate_without_a_credential_names_the_variable(self, monkeypatch):
        provider = GoogleGenaiProvider("google", "GOOGLE_API_KEY")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ProviderNotConfigured):
            provider.generate("gemini-2.5-flash", "hi")


class TestGenerate:
    def _provider(self, monkeypatch, fake_models):
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        monkeypatch.setattr(
            "ripple.llm.genai_provider.genai.Client",
            lambda *, api_key: _FakeClient(fake_models),
        )
        return GoogleGenaiProvider("google", "GOOGLE_API_KEY")

    def test_it_calls_the_sdk_and_maps_the_result(self, monkeypatch):
        models = _FakeModels(response=_response("The blue sedan is in scenes 14 and 15."))
        provider = self._provider(monkeypatch, models)

        result = provider.generate(
            "gemini-2.5-flash", "which scenes?", system="answer from the packet"
        )

        # The SDK was called with the system prompt and cap in config.
        assert models.calls, "generate_content was not called"
        call = models.calls[0]
        assert call["model"] == "gemini-2.5-flash"
        assert call["contents"] == "which scenes?"
        assert call["config"].system_instruction == "answer from the packet"
        # The response was mapped onto Ripple's GenerationResult.
        assert result.text == "The blue sedan is in scenes 14 and 15."
        assert result.input_tokens == 11
        assert result.output_tokens == 7
        assert result.reasoning_tokens == 3
        assert result.finish_reason == "stop"
        assert result.provider == "google"

    def test_a_length_finish_maps_to_truncated(self, monkeypatch):
        models = _FakeModels(response=_response("half", finish="MAX_TOKENS"))
        provider = self._provider(monkeypatch, models)
        result = provider.generate("gemini-2.5-flash", "q")
        assert result.finish_reason == "length"
        assert result.truncated is True

    def test_the_default_reasoning_effort_rides_in_the_config(self, monkeypatch):
        models = _FakeModels(response=_response("ok"))
        provider = self._provider(monkeypatch, models)
        provider.generate("gemini-2.5-flash", "q")
        thinking = models.calls[0]["config"].thinking_config
        assert thinking is not None
        # The SDK coerces the string to its ThinkingLevel enum (name MEDIUM).
        assert "medium" in str(thinking.thinking_level).lower()

    def test_a_json_schema_sets_structured_output(self, monkeypatch):
        models = _FakeModels(response=_response("{}"))
        provider = self._provider(monkeypatch, models)
        provider.generate(
            "gemini-2.5-flash", "q", json_schema={"type": "object"}
        )
        config = models.calls[0]["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_json_schema == {"type": "object"}


class TestErrorMapping:
    def _api_error(self, code, status, message):
        from google.genai import errors

        error = errors.APIError.__new__(errors.APIError)
        error.code = code
        error.status = status
        error.message = message
        return error

    def test_rate_limit_maps_to_an_availability_code(self):
        error = self._api_error(429, "RESOURCE_EXHAUSTED", "slow down")
        assert _to_provider_error(error).code == "rate_limited"

    def test_a_missing_model_maps_to_model_not_available(self):
        mapped = _to_provider_error(
            self._api_error(404, "NOT_FOUND", "model gemini-x not found")
        )
        assert mapped.code == "model_not_available"

    def test_a_server_error_maps_to_provider_unavailable(self):
        mapped = _to_provider_error(self._api_error(503, "UNAVAILABLE", "try later"))
        assert mapped.code == "provider_unavailable"

    def test_the_transport_failure_becomes_a_provider_error(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "k")

        class _Boom:
            def generate_content(self, **_):
                raise RuntimeError("connection reset")

        monkeypatch.setattr(
            "ripple.llm.genai_provider.genai.Client",
            lambda *, api_key: _FakeClient(_Boom()),
        )
        provider = GoogleGenaiProvider("google", "GOOGLE_API_KEY")
        with pytest.raises(ProviderError) as caught:
            provider.generate("gemini-2.5-flash", "q")
        assert caught.value.code == "network_error"


class TestRouting:
    def test_the_query_provider_is_the_google_sdk(self):
        """The Ask path resolves to google-genai; extraction/judgement do not."""
        assert isinstance(QUERY_PROVIDERS["google"], GoogleGenaiProvider)
        assert isinstance(get_query_provider("google"), GoogleGenaiProvider)

    def test_an_unknown_provider_falls_back_to_the_registry(self):
        # Not a query-specific provider, so it comes from the standard registry,
        # which raises its own clear error for a truly unknown name.
        with pytest.raises(ProviderError):
            get_query_provider("nope")


def test_no_candidates_yields_empty_text():
    """A reply with no text part (truncated after hidden reasoning) is empty,
    not an exception; the caller falls back on the deterministic answer."""
    response = SimpleNamespace(
        text=None, model_version="v", usage_metadata=None, candidates=[]
    )
    result = _to_result(response, "gemini-2.5-flash", "google")
    assert result.text == ""
    assert result.finish_reason is None
