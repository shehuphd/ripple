"""Provider adapter backed by KeyCall.

KeyCall (https://github.com/shehuphd/keycall) normalizes credential
validation, model discovery, and generation across providers, so one
parametrized adapter replaces what used to be a separate hand-rolled wrapper
per vendor SDK (google-genai, openai, anthropic). It also enforces JSON
schemas natively per provider, so no caller-side code-fence stripping is
needed, and it already returns typed, sanitised, actionable errors, so no
caller-side regex sniffing of a raw provider error string is needed either.
"""

from __future__ import annotations

import os
from typing import Any

from keycall import KeyCall, KeyCallError, Message, TextInput

from ripple.llm.base import (
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderNotConfigured,
    infer_tier,
    is_text_model,
)


class KeycallProvider:
    """One provider's credential, listing, and generation, via KeyCall.

    `name` is passed straight through as KeyCall's own `provider=` argument:
    KeyCall accepts Ripple's provider names directly, including "google" as
    a built-in alias for Gemini, so no translation table is needed.
    """

    def __init__(
        self, name: str, credential_variable: str, *, base_url: str | None = None
    ) -> None:
        self.name = name
        self.credential_variable = credential_variable
        self.base_url = base_url

    def is_configured(self) -> bool:
        """True when a credential is in the environment."""
        return bool(os.environ.get(self.credential_variable, "").strip())

    def _client(self, api_key: str | None = None) -> KeyCall:
        """Build a client, or say which variable is missing.

        `api_key` overrides the stored credential for this one client, which
        is how a key under validation is checked without ever entering the
        process environment: writing it to os.environ, even briefly, would
        let a concurrent extraction read the wrong key.
        """
        if api_key is None:
            if not self.is_configured():
                raise ProviderNotConfigured(self.name, self.credential_variable)
            api_key = os.environ[self.credential_variable]
        kwargs: dict[str, Any] = {
            "provider": self.name,
            "api_key": api_key,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return KeyCall(**kwargs)

    def list_models(self, *, api_key: str | None = None) -> list[ModelInfo]:
        """Text-generation models, read from the provider's own endpoint."""
        with self._client(api_key) as client:
            try:
                discovery = client.list_models(refresh=True)
            except KeyCallError as error:
                raise _to_provider_error(error) from error

        models = [
            ModelInfo(
                id=model.id,
                provider=self.name,
                display_name=model.display_name or model.id,
                tier=infer_tier(model.id),
                context_window=model.context_limit,
            )
            for model in discovery.models
            if is_text_model(model.id)
        ]
        return sorted(models, key=lambda model: model.id)

    def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> GenerationResult:
        """Generate text, with native structured output when a schema is given."""
        messages = []
        if system:
            messages.append(Message(role="system", content=[TextInput(text=system)]))
        messages.append(Message(role="user", content=[TextInput(text=prompt)]))

        with self._client() as client:
            try:
                result = client.generate_text(
                    model=model_id,
                    messages=messages,
                    max_output_tokens=max_output_tokens,
                    response_schema=json_schema,
                )
            except KeyCallError as error:
                raise _to_provider_error(error) from error

        usage = result.usage
        return GenerationResult(
            text=result.text or "",
            model_id=result.model,
            provider=self.name,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            finish_reason=result.finish_reason,
        )


def _to_provider_error(error: KeyCallError) -> ProviderError:
    """Rename a typed KeyCall failure to Ripple's own ProviderError.

    KeyCall's code and message are already sanitised and actionable, so this
    carries them through rather than reinterpreting them.
    """
    return ProviderError(error.code.value, error.message)
