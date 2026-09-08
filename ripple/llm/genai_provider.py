"""Provider adapter backed by Google's own google-genai SDK.

Ripple reaches Gemini two ways on purpose. `KeycallProvider` speaks to the API
over HTTP and drives extraction and judgement, for KeyCall's native schema
enforcement and typed errors. This adapter calls the official `google-genai`
SDK directly and drives the grounded query (Ask the graph), so a
`from google import genai` generation runs on every asked question. Both paths
record the same model-call audit rows and are gated by the same budget.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from ripple.llm.base import (
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TEMPERATURE,
    AgentReply,
    GenerationResult,
    ModelInfo,
    ProviderError,
    ProviderNotConfigured,
    ToolCall,
    infer_tier,
    is_text_model,
)

# The SDK's finish reasons, mapped to the two names the rest of Ripple reads;
# anything else passes through lowercased. "MAX_TOKENS" is Ripple's "length",
# the reason the truncation message keys on.
_FINISH_REASONS = {"STOP": "stop", "MAX_TOKENS": "length"}


class GoogleGenaiProvider:
    """One provider's credential, listing, and generation, via google-genai.

    Satisfies the same `LLMProvider` contract as `KeycallProvider`, so a caller
    hands it a model id and a prompt and gets a `GenerationResult` back, with no
    knowledge of which SDK produced it.
    """

    def __init__(self, name: str, credential_variable: str) -> None:
        self.name = name
        self.credential_variable = credential_variable

    def is_configured(self) -> bool:
        """True when a credential is in the environment."""
        return bool(os.environ.get(self.credential_variable, "").strip())

    def _api_key(self, api_key: str | None) -> str:
        """The credential to use, or say which variable is missing.

        A passed key checks a candidate credential without it ever entering the
        process environment, the same rule the KeyCall adapter follows.
        """
        if api_key is None:
            if not self.is_configured():
                raise ProviderNotConfigured(self.name, self.credential_variable)
            api_key = os.environ[self.credential_variable]
        return api_key

    def list_models(self, *, api_key: str | None = None) -> list[ModelInfo]:
        """Text-generation models the SDK lists from the provider's endpoint."""
        client = genai.Client(api_key=self._api_key(api_key))
        try:
            listed = list(client.models.list())
        except genai_errors.APIError as error:
            raise _to_provider_error(error) from error

        models: list[ModelInfo] = []
        for model in listed:
            actions = model.supported_actions or []
            if actions and "generateContent" not in actions:
                continue
            ident = (model.name or "").removeprefix("models/")
            if not ident or not is_text_model(ident):
                continue
            models.append(
                ModelInfo(
                    id=ident,
                    provider=self.name,
                    display_name=model.display_name or ident,
                    tier=infer_tier(ident),
                    context_window=model.input_token_limit,
                )
            )
        return sorted(models, key=lambda model: model.id)

    def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
        temperature: float = DEFAULT_TEMPERATURE,
        seed: int | None = None,
    ) -> GenerationResult:
        """Generate text, with native structured output when a schema is given."""
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            seed=seed,
            # The query never passes tools, so the SDK's automatic function
            # calling is off; leaving it on logs a warning on every call.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        if reasoning_effort:
            config.thinking_config = types.ThinkingConfig(
                thinking_level=reasoning_effort
            )
        if json_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = json_schema

        client = genai.Client(api_key=self._api_key(None))
        try:
            response = client.models.generate_content(
                model=model_id, contents=prompt, config=config
            )
        except genai_errors.APIError as error:
            raise _to_provider_error(error) from error
        except Exception as error:  # transport failures below the SDK
            raise ProviderError("network_error", str(error)) from error

        return _to_result(response, model_id, self.name)

    def converse(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int = 2048,
        reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> AgentReply:
        """One agent turn: the model answers or asks for tools.

        Automatic function calling stays off. The SDK would otherwise run a
        Python callable on the model's say-so; Ripple executes tools itself,
        after validating the arguments against the conversation's own script.
        """
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        if tools:
            config.tools = [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(**declaration)
                        for declaration in tools
                    ]
                )
            ]
        if reasoning_effort:
            config.thinking_config = types.ThinkingConfig(
                thinking_level=reasoning_effort
            )

        client = genai.Client(api_key=self._api_key(None))
        try:
            response = client.models.generate_content(
                model=model_id, contents=_to_contents(messages), config=config
            )
        except genai_errors.APIError as error:
            raise _to_provider_error(error) from error
        except Exception as error:  # transport failures below the SDK
            raise ProviderError("network_error", str(error)) from error

        return _to_agent_reply(response, model_id, self.name)


def _to_contents(messages: list[dict[str, Any]]) -> list[types.Content]:
    """Map Ripple's neutral message list onto the SDK's contents.

    A tool result goes back as a function response part rather than as text,
    so the model reads it as its own call returning rather than as a user
    saying something. That distinction is what keeps a screenplay line quoted
    inside a tool result from reading as an instruction.
    """
    contents: list[types.Content] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=message["name"],
                            response=message.get("response") or {},
                        )
                    ],
                )
            )
        elif role == "model" and message.get("tool_calls"):
            parts = []
            for call in message["tool_calls"]:
                part = types.Part.from_function_call(
                    name=call["name"], args=call.get("arguments") or {}
                )
                # The signature the model attached when it made this call.
                # Gemini refuses a replayed function call without it.
                signature = call.get("thought_signature")
                if signature:
                    part.thought_signature = base64.b64decode(signature)
                parts.append(part)
            contents.append(types.Content(role="model", parts=parts))
        else:
            contents.append(
                types.Content(
                    role="model" if role == "model" else "user",
                    parts=[types.Part.from_text(text=message.get("text") or "")],
                )
            )
    return contents


def _to_agent_reply(
    response: types.GenerateContentResponse, model_id: str, provider_name: str
) -> AgentReply:
    """Map the SDK response onto a turn that may carry tool calls."""
    result = _to_result(response, model_id, provider_name)
    calls: list[ToolCall] = []
    if response.candidates:
        for part in response.candidates[0].content.parts or []:
            call = getattr(part, "function_call", None)
            if call is not None and call.name:
                signature = getattr(part, "thought_signature", None)
                calls.append(
                    ToolCall(
                        name=call.name,
                        arguments=dict(call.args or {}),
                        call_id=getattr(call, "id", None),
                        thought_signature=(
                            base64.b64encode(signature).decode("ascii")
                            if signature
                            else None
                        ),
                    )
                )
    return AgentReply(
        text=result.text,
        tool_calls=calls,
        model_id=result.model_id,
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        reasoning_tokens=result.reasoning_tokens,
        finish_reason=result.finish_reason,
    )


def _response_text(response: types.GenerateContentResponse) -> str:
    """The reply text, or empty when the model produced none.

    The SDK's `.text` accessor returns None (and warns) when a candidate holds
    no text part, which is a legitimate outcome: a reply truncated after only
    hidden reasoning has no visible text, and the caller falls back on that.
    """
    try:
        return response.text or ""
    except (ValueError, AttributeError):
        return ""


def _to_result(
    response: types.GenerateContentResponse, model_id: str, provider_name: str
) -> GenerationResult:
    """Map the SDK response onto Ripple's provider-agnostic result."""
    usage = response.usage_metadata
    finish: str | None = None
    if response.candidates:
        raw = response.candidates[0].finish_reason
        name = getattr(raw, "name", None) or (str(raw) if raw else None)
        finish = _FINISH_REASONS.get(name, name.lower() if name else None)
    return GenerationResult(
        text=_response_text(response),
        model_id=response.model_version or model_id,
        provider=provider_name,
        input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
        output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
        reasoning_tokens=getattr(usage, "thoughts_token_count", None) if usage else None,
        finish_reason=finish,
    )


def _to_provider_error(error: genai_errors.APIError) -> ProviderError:
    """Rename an SDK error to Ripple's ProviderError, mapping its stable code.

    The availability codes route a retry to the fallback model; everything else
    is reported as a plain generation failure. The message the SDK built is
    already actionable, so it carries through unchanged.
    """
    code = getattr(error, "code", None)
    status = (getattr(error, "status", "") or "").lower()
    message = getattr(error, "message", None) or str(error)
    mapped = "generation_failed"
    if code == 429 or "resource_exhausted" in status:
        mapped = "rate_limited"
    elif code in (400, 404) and "model" in message.lower():
        mapped = "model_not_available"
    elif isinstance(error, genai_errors.ServerError) or (
        isinstance(code, int) and code >= 500
    ):
        mapped = "provider_unavailable"
    return ProviderError(mapped, message)
