"""Settings: choose a provider and model, enter and validate credentials.

The screen this backs shows one row per provider with a key field, a Validate
button, and a Save button. What crosses the boundary in each direction is
deliberately asymmetric: a key goes in, and only a status comes back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ripple.config.secrets import SecretsError, SecretStore, timestamp
from ripple.db.repository import (
    get_active_model,
    get_fallback_model,
    set_active_model,
    set_fallback_model,
    unavailable_models,
)
from ripple.llm import PROVIDERS, ProviderError, get_provider
from ripple.llm.base import ModelInfo

logger = logging.getLogger(__name__)


# Long enough that no credential any provider issues could exceed it, short
# enough to catch a pasted file. Not a guess at any provider's key length: the
# point is only to refuse a value that cannot be a credential at all.
MAX_CREDENTIAL_LENGTH = 1024


def unusable_credential(value: object) -> str | None:
    """Say why a value cannot be sent to a provider, or None to send it.

    This is the whole of Ripple's local checking, and it is deliberately blind
    to format. Prefixes, character sets, and lengths differ per provider and
    change without notice, so the provider's own endpoint decides whether a key
    is valid. A local gate that rejects a working key is worse than the error it
    was meant to pre-empt: the user cannot get past it, and the app is
    confidently wrong about their credential.

    What is left is the type and size a credential has to be to be sent at all.
    """
    if not isinstance(value, str):
        return "A credential must be text."
    key = value.strip()
    if not key:
        return "Enter a key."
    if len(key) > MAX_CREDENTIAL_LENGTH:
        return (
            f"This is {len(key)} characters long, which is longer than any API "
            "key. It looks like a file or a document rather than a credential."
        )
    if any(character in key for character in _CONTROL_CHARACTERS):
        return "This contains binary data rather than text. Paste the key itself."
    return None


# Control characters, which no credential contains and which mark a paste as
# binary rather than text. Tab, carriage return, and newline are excluded: they
# come from ordinary copying and are stripped rather than refused.
_CONTROL_CHARACTERS = frozenset(
    chr(code) for code in [*range(32), 127] if chr(code) not in "\t\r\n"
)


@dataclass(frozen=True)
class ProviderStatus:
    """One row of the settings screen. Holds no credential material."""

    provider: str
    credential_variable: str
    configured: bool
    source: str


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of pressing Validate."""

    provider: str
    valid: bool
    model_count: int = 0
    checked_at: str = field(default_factory=timestamp)
    error_code: str | None = None
    error_message: str | None = None


class SettingsService:
    """Reads and writes provider configuration."""

    def __init__(self, store: SecretStore | None = None) -> None:
        self.store = store or SecretStore()

    def provider_statuses(self) -> list[ProviderStatus]:
        """Every provider and whether it holds a credential."""
        return [
            ProviderStatus(
                provider=name,
                credential_variable=provider.credential_variable,
                configured=self.store.status(
                    name, provider.credential_variable
                ).configured,
                source=self.store.status(name, provider.credential_variable).source,
            )
            for name, provider in sorted(PROVIDERS.items())
        ]

    def validate(
        self, provider_name: str, api_key: str | None = None
    ) -> ValidationResult:
        """Check a credential by listing models, without storing it.

        A key passed here is used for the call and discarded, so a user can
        test a key before committing to saving it. Nothing about the key value
        appears in the result or the logs.
        """
        provider = get_provider(provider_name)

        # A submitted key is handed to the provider call directly and never
        # enters the process environment: writing it to os.environ, even
        # briefly, would let a concurrent model call read the wrong key.
        candidate: str | None = None
        if api_key is not None:
            unusable = unusable_credential(api_key)
            if unusable:
                return ValidationResult(
                    provider=provider_name,
                    valid=False,
                    error_code="unusable_credential",
                    error_message=unusable,
                )
            candidate = api_key.strip()

        try:
            # Only a value the user just submitted is checked. Re-validating
            # what is already stored goes straight to the provider, and with
            # nothing stored the provider's own "not configured" error names
            # the variable to set.
            models = provider.list_models(api_key=candidate)
            return ValidationResult(
                provider=provider_name, valid=True, model_count=len(models)
            )
        except ProviderError as error:
            logger.info("validation failed for %s: %s", provider_name, error.code)
            return ValidationResult(
                provider=provider_name,
                valid=False,
                error_code=error.code,
                error_message=error.message,
            )

    def save_credential(self, provider_name: str, api_key: str) -> ValidationResult:
        """Validate a credential, then store it only if it works.

        Storing an invalid key produces a settings screen that claims to be
        configured and fails on first use, so validation gates the write.
        """
        provider = get_provider(provider_name)
        result = self.validate(provider_name, api_key)
        if not result.valid:
            return result

        try:
            self.store.save(provider.credential_variable, api_key)
        except SecretsError as error:
            return ValidationResult(
                provider=provider_name,
                valid=False,
                error_code=error.code,
                error_message=error.message,
            )
        return result

    def forget_credential(self, provider_name: str) -> bool:
        """Remove a stored credential."""
        provider = get_provider(provider_name)
        return self.store.forget(provider.credential_variable)

    def available_models(self, provider_name: str) -> list[ModelInfo]:
        """Selectable models for a configured provider.

        Already filtered to text generation by the adapter, so an embedding or
        image model cannot reach the picker.
        """
        return get_provider(provider_name).list_models()

    def select_model(self, session: Session, provider_name: str, model_id: str) -> None:
        """Persist the chosen provider and model identifiers.

        Identifiers only. The credential stays outside the database.
        """
        provider = get_provider(provider_name)
        available = {model.id for model in provider.list_models()}
        if model_id not in available:
            raise ProviderError(
                "unknown_model",
                f"{model_id!r} is not offered by {provider_name}.",
            )
        _refuse_dead_model(session, provider_name, model_id)
        set_active_model(session, provider_name, model_id)

    def selected_model(self, session: Session) -> tuple[str | None, str | None]:
        """The chosen provider and model, or (None, None) before a choice.

        The default is no selection rather than a guessed one.
        """
        return get_active_model(session)

    def select_fallback(
        self, session: Session, provider_name: str, model_id: str | None
    ) -> None:
        """Persist the fallback model, or clear it when model_id is None.

        The fallback answers a call the main model refused for an availability
        reason, so a fallback identical to the main would never help and is
        refused rather than stored.
        """
        if model_id is None or not model_id.strip():
            set_fallback_model(session, None, None)
            return
        provider = get_provider(provider_name)
        available = {model.id for model in provider.list_models()}
        if model_id not in available:
            raise ProviderError(
                "unknown_model",
                f"{model_id!r} is not offered by {provider_name}.",
            )
        _refuse_dead_model(session, provider_name, model_id)
        _, main = get_active_model(session)
        if model_id == main:
            raise ProviderError(
                "fallback_is_main",
                "The fallback must differ from the main model, or it can "
                "never answer when the main model is unavailable.",
            )
        set_fallback_model(session, provider_name, model_id)

    def fallback_model(self, session: Session) -> tuple[str | None, str | None]:
        """The fallback provider and model, or (None, None) when none is set."""
        return get_fallback_model(session)


def _refuse_dead_model(session: Session, provider_name: str, model_id: str) -> None:
    """Refuse a model a live call has found dead on this account.

    A provider's catalog can list models the account cannot invoke: Gemini
    keeps retired models in its list response and withdraws them per account,
    with no lifecycle field to filter on. The picker disables a marked entry,
    and this is the same rule enforced where the choice is stored, so a direct
    API call cannot select a model every extraction would then fail on. The
    mark clears when a later call to the model succeeds.
    """
    if model_id in unavailable_models(session, provider_name):
        raise ProviderError(
            "model_not_available",
            f"{provider_name} refused a live call to {model_id!r} on this "
            "account's key, so it cannot be chosen. Pick a model the picker "
            "does not mark unavailable.",
        )
