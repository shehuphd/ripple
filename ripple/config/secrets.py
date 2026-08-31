"""Credential storage. Never the database, never the browser.

Provider credentials live outside the database schema.
The settings screen still needs somewhere to put a key the user typed, so:

- On Replit, Replit Secrets supplies them as environment variables and this
  module only reads. `save` refuses, because a value written to the container
  filesystem there does not survive a redeploy and would silently diverge from
  the Secret of the same name.
- Locally, `save` writes to a gitignored file with owner-only permissions and
  loads it into the process environment.

Nothing here ever returns a stored value to a caller that could serialise it.
`status` reports presence and nothing else, so a masked fragment cannot leak
through an API response or a log line.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SECRETS_PATH = Path("data/secrets.env")
# The variable that points the store somewhere else. The test suite sets it
# to a throwaway file so a run never loads the live credential; without an
# override, a web test's startup would repopulate the key the test fixture
# just removed from the environment.
SECRETS_PATH_VARIABLE = "RIPPLE_SECRETS_PATH"
OWNER_READ_WRITE = stat.S_IRUSR | stat.S_IWUSR


class SecretsError(Exception):
    """A credential could not be stored."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CredentialStatus:
    """What the settings screen is allowed to know about a credential.

    Deliberately holds no fragment of the value. A masked key is still key
    material: the prefix identifies the account for several providers, and the
    suffix is what support channels ask for.
    """

    provider: str
    variable: str
    configured: bool
    source: str


def on_replit() -> bool:
    """True when running inside a Replit deployment."""
    return bool(os.environ.get("REPL_ID") or os.environ.get("REPLIT_DEPLOYMENT"))


class SecretStore:
    """Reads credentials from the environment; writes to a local file."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            override = os.environ.get(SECRETS_PATH_VARIABLE, "").strip()
            path = Path(override) if override else DEFAULT_SECRETS_PATH
        self.path = Path(path)

    def load(self) -> int:
        """Load the local file into the environment. Returns how many were set.

        Values already in the environment win, so a Replit Secret is never
        overwritten by a stale local file.
        """
        if not self.path.exists():
            return 0
        loaded = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            name, value = name.strip(), value.strip()
            if name and value and not os.environ.get(name):
                os.environ[name] = value
                loaded += 1
        return loaded

    def status(self, provider: str, variable: str) -> CredentialStatus:
        """Whether a credential is present, and where it came from."""
        present = bool(os.environ.get(variable, "").strip())
        if not present:
            source = "none"
        elif on_replit():
            source = "replit_secrets"
        elif self._in_file(variable):
            source = "local_file"
        else:
            source = "environment"
        return CredentialStatus(provider, variable, present, source)

    def save(self, variable: str, value: str) -> None:
        """Store a credential locally and put it in the environment.

        Refuses on Replit: a file written into a deployment container is lost
        on redeploy, so accepting the write would promise persistence that does
        not exist. The user is told to add the Secret instead.
        """
        cleaned = value.strip()
        if not cleaned:
            raise SecretsError("empty_value", "A credential cannot be blank.")
        if on_replit():
            raise SecretsError(
                "use_replit_secrets",
                f"Add {variable} through Replit Secrets. A key saved into the "
                "deployment filesystem is lost on the next redeploy.",
            )

        entries = self._read_file()
        entries[variable] = cleaned
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create with owner-only permissions before writing, so the value is
        # never briefly readable by another account on the machine.
        descriptor = os.open(
            self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, OWNER_READ_WRITE
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("# Ripple credentials. Gitignored. Never commit this file.\n")
            for name, stored in sorted(entries.items()):
                handle.write(f"{name}={stored}\n")
        os.chmod(self.path, OWNER_READ_WRITE)

        os.environ[variable] = cleaned
        logger.info("stored credential for %s", variable)

    def forget(self, variable: str) -> bool:
        """Remove a credential from the local file and the environment."""
        entries = self._read_file()
        removed = entries.pop(variable, None) is not None
        if removed:
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, OWNER_READ_WRITE
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(
                    "# Ripple credentials. Gitignored. Never commit this file.\n"
                )
                for name, stored in sorted(entries.items()):
                    handle.write(f"{name}={stored}\n")
        os.environ.pop(variable, None)
        return removed

    def _read_file(self) -> dict[str, str]:
        """Parse the local file into a mapping."""
        if not self.path.exists():
            return {}
        entries: dict[str, str] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                name, _, value = stripped.partition("=")
                entries[name.strip()] = value.strip()
        return entries

    def _in_file(self, variable: str) -> bool:
        return variable in self._read_file()


def timestamp() -> str:
    """An ISO timestamp for a validation record."""
    return datetime.now(UTC).isoformat()
