"""OS keyring secret source — resolve credentials from the platform's native
credential store at process startup.

Supported backends:

* **macOS** — the ``security`` CLI talking to the login keychain.  A user
  stores a credential with::

      security add-generic-password -a "$USER" -s "ENV_VAR_NAME" -w "secret-value"

  and Hermes reads it back with ``security find-generic-password -a "$USER"
  -s "ENV_VAR_NAME" -w``.  The service name is the env-var name (e.g.
  ``OPENAI_API_KEY``); the account is the current username.

* **Windows** — PowerShell's ``Get-StoredCredential`` from the
  ``Microsoft.PowerShell.SecretManagement`` / ``CredentialManager`` module,
  or the legacy ``cmdkey /list`` + ``cmdkey /generic`` fallback.  The
  target name is the env-var name.

* **Linux** — ``secret-tool lookup`` from libsecret (GNOME Keyring /
  KWallet via the Secret Service DBus API).  The secret's *attributes*
  dict carries ``env=ENV_VAR_NAME`` so we can look it up by the env-var
  name without enumerating all secrets.  Falls back to ``pass`` (the
  standard unix "password-store") reading ``pass show ENV_VAR_NAME``
  when libsecret isn't installed.

Users list which env-var names to pull in ``secrets.keyring.env``::

    secrets:
      keyring:
        enabled: true
        env:
          OPENAI_API_KEY:            # service / attribute name = env-var name
          ANTHROPIC_API_KEY:
          AGENTMAIL_API_KEY:

Design summary
--------------

* **Mapped shape** — like the 1Password source, the user explicitly binds
  each env var to a lookup name.  Mapped sources take precedence over bulk
  sources (Bitwarden BSM project dumps) on contested variables.

* **Read-only, startup-time, never raises** — ``fetch()`` returns a
  :class:`FetchResult`; any failure (missing binary, no desktop session,
  bad permissions) sets ``result.error`` and leaves ``os.environ``
  untouched.  The orchestrator applies values and owns precedence.

* **Best-effort per key** — a failure for one env var is recorded as a
  warning; the remaining names are still attempted.  This is important
  for the mapped shape: one stale entry shouldn't sink the other
  credentials in the same ``env:`` map.

* **No credentials stored by Hermes** — we read, we never write.  The user
  populates their OS keyring with the platform-native tooling.  This
  keeps the surface tiny and avoids needing to ship/store a write-back
  path.

* **No extra Python dependencies** — we call the platform CLIs via
  ``subprocess``.  Optionally, if the ``keyring`` PyPI package is
  installed, we use it as a convenience wrapper on macOS and Linux
  (it delegates to the same keychain/secret-service under the hood and
  adds nothing the CLIs don't already do).  When it's absent we fall back
  to the native commands.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from agent.secret_sources.base import (
    ErrorKind,
    SecretSource,
    get_source_environment,
    is_valid_env_name,
    run_secret_cli,
)
from agent.secret_sources.base import FetchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def _platform() -> str:
    """Return a normalized platform name: 'macos' | 'windows' | 'linux'."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows" or os.name == "nt":
        return "windows"
    return "linux"


# ---------------------------------------------------------------------------
# macOS backend
# ---------------------------------------------------------------------------


def _macos_read_keychain(service: str, account: str) -> Optional[str]:
    """Read a single generic-password item from the macOS login keychain.

    Uses the ``security`` CLI (always present on macOS).  Returns the
    decrypted password on stdout, or ``None`` if the item doesn't exist
    or the call fails for any reason.
    """
    try:
        result = subprocess.run(
            [
                "security", "find-generic-password",
                "-a", account,
                "-s", service,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        # security prints "SecKeychainSearchCopyNext..: The specified item
        #  can't be found." to stderr for a missing entry — that's the
        # expected case, not an error worth surfacing.
        return None
    value = result.stdout.strip()
    return value if value else None


def _macos_try_pykeyring(service: str, account: str) -> Optional[str]:
    """Attempt a read via the optional ``keyring`` PyPI package.

    Returns ``None`` on any failure (including the package not being
    installed).  Only used as a secondary path after the ``security``
    CLI — the CLI is preferred because it's always present and
    ``keyring``'s macOS backend has historically had more edge-case
    quirks.
    """
    try:
        import keyring  # type: ignore
    except ImportError:
        return None
    try:
        value = keyring.get_password(service, account)
    except Exception:  # noqa: BLE001 — third-party, must never block startup
        return None
    if value:
        return value
    return None


# ---------------------------------------------------------------------------
# Windows backend
# ---------------------------------------------------------------------------


def _windows_read_credential(service: str) -> Optional[str]:
    """Read a generic credential from the Windows Credential Manager.

    Tries PowerShell ``Get-StoredCredential`` (from the
    ``Microsoft.PowerShell.SecretManagement`` / built-in
    ``CredentialManager`` module) first, then falls back to the
    ``cmdkey`` CLI.  The *target* name is the env-var name.
    """
    # --- PowerShell path (preferred) ---
    ps_script = (
        "try { "
        "Import-Module CredentialManager -ErrorAction SilentlyContinue | Out-Null; "
        "$c = Get-StoredCredential -Target '" + service + "' -ErrorAction SilentlyContinue; "
        "if ($c -and $c.GetPassword()) { $c.GetPassword() } "
        "} catch { }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass

    # --- cmdkey fallback ---
    try:
        find = subprocess.run(
            ["cmdkey", "/list"],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if find.returncode != 0 or service not in find.stdout:
        return None
    try:
        get = subprocess.run(
            ["cmdkey", "/generic:" + service],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if get.returncode != 0:
        return None
    # cmdkey /generic prints "Target: ...\nUser: ...\nPassword: ...\n"
    for line in get.stdout.splitlines():
        line = line.strip()
        if line.startswith("Password:"):
            return line[len("Password:"):].strip()
    return None


def _windows_try_pykeyring(service: str) -> Optional[str]:
    """Attempt a read via the optional ``keyring`` PyPI package on Windows.

    On Windows ``keyring`` uses the Windows Credential Manager directly,
    which is the same backend as ``Get-StoredCredential``.  Used as a
    secondary path when the PowerShell command fails.
    """
    try:
        import keyring  # type: ignore
    except ImportError:
        return None
    try:
        value = keyring.get_password(service, service)
    except Exception:  # noqa: BLE001
        return None
    return value


# ---------------------------------------------------------------------------
# Linux backend
# ---------------------------------------------------------------------------


def _linux_read_secret_tool(service: str) -> Optional[str]:
    """Read a secret via ``secret-tool`` (libsecret / Secret Service).

    Looks up by the ``env`` attribute matching the env-var name.  The
    user is responsible for storing the secret with that attribute, e.g.::

        secret-tool store --label="OPENAI_API_KEY" --env OPENAI_API_KEY \
            <<< "$(cat /dev/stdin)" 
        # or interactively:
        secret-tool store --label="OPENAI_API_KEY" --env OPENAI_API_KEY
    """
    secret_tool = shutil.which("secret-tool")
    if not secret_tool:
        return None
    try:
        result = subprocess.run(
            [secret_tool, "lookup", "env", service],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if value else None


def _linux_read_pass(service: str) -> Optional[str]:
    """Read a secret via ``pass`` (the standard unix password-store).

    Looks up ``pass show <service>``.  Falls back to a path-style lookup
    (``pass show path/to/<service>``) if the flat lookup fails.
    """
    pass_bin = shutil.which("pass")
    if not pass_bin:
        return None
    try:
        result = subprocess.run(
            [pass_bin, "show", service],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.rstrip("\n")


def _linux_try_pykeyring(service: str) -> Optional[str]:
    """Attempt a read via the optional ``keyring`` PyPI package on Linux.

    ``keyring`` on Linux uses the Secret Service API (same as
    ``secret-tool``) but may be configured with a different backend.
    Only used as a secondary path.
    """
    try:
        import keyring  # type: ignore
    except ImportError:
        return None
    try:
        value = keyring.get_password(service, service)
    except Exception:  # noqa: BLE001
        return None
    return value


# ---------------------------------------------------------------------------
# Unified per-platform reader
# ---------------------------------------------------------------------------


def _read_from_keyring(service: str) -> Optional[str]:
    """Read a single secret from the OS keyring for the current platform.

    Tries the native CLI first (always present on macOS, commonly
    available on Linux/Windows), then falls back to the optional
    ``keyring`` Python package if installed.

    ``service`` is the env-var name (e.g. ``OPENAI_API_KEY``).
    On macOS the *account* defaults to the current username, matching
    the ``security add-generic-password -a "$USER"`` convention.
    """
    plat = _platform()
    account = os.environ.get("USER", os.environ.get("LOGNAME", ""))

    if plat == "macos":
        value = _macos_read_keychain(service, account)
        if value is not None:
            return value
        # Secondary: the keyring package may use a different service/account
        # convention than our security-CLI fallback.
        value = _macos_try_pykeyring(service, account)
        if value is not None:
            return value
        # Tertiary: some users store with service=env var name and account
        # matching as well (i.e. both fields are the env-var name).
        value = _macos_try_pykeyring(service, service)
        if value is not None:
            return value

    elif plat == "windows":
        value = _windows_read_credential(service)
        if value is not None:
            return value
        value = _windows_try_pykeyring(service)
        if value is not None:
            return value

    else:  # linux
        value = _linux_read_secret_tool(service)
        if value is not None:
            return value
        value = _linux_read_pass(service)
        if value is not None:
            return value
        value = _linux_try_pykeyring(service)
        if value is not None:
            return value

    return None


# ---------------------------------------------------------------------------
# SecretSource adapter
# ---------------------------------------------------------------------------


class KeyringSource(SecretSource):
    """OS-native keyring as a registered secret source.

    A **mapped** source: the user binds each env-var name to a lookup
    under ``secrets.keyring.env`` (the values are currently unused —
    the lookup name defaults to the env-var name itself — but the map
    lets users opt into per-name overrides and is forward-compatible
    with richer ref syntaxes).

    Config::

        secrets:
          keyring:
            enabled: true
            env:
              OPENAI_API_KEY:     # → looked up as service "OPENAI_API_KEY"
              ANTHROPIC_API_KEY:
    """

    name = "keyring"
    label = "OS Keyring"
    shape = "mapped"
    scheme = None  # no URI scheme; env-var names are the lookup keys

    def config_schema(self) -> dict:
        return {
            "enabled": {
                "description": "Master switch.  When false, the OS keyring "
                               "is never queried for secrets.",
                "default": False,
            },
            "env": {
                "description": "Mapping of ENV_VAR → lookup name.  The lookup "
                               "name defaults to the env-var name itself when the "
                               "value is empty or omitted.  On macOS the account "
                               "is the current username (matching `security "
                               "add-generic-password -a \"$USER\"`).",
                "default": {},
            },
            "override_existing": {
                "description": "Keyring values overwrite existing .env/shell "
                               "values for the same env var.",
                "default": False,
            },
            "cache_ttl_seconds": {
                "description": "Seconds to cache resolved values in-process "
                               "and on disk.  0 disables caching (every "
                               "startup hits the keyring).",
                "default": 300,
            },
        }

    def fetch(self, cfg: dict, home_path: Path) -> FetchResult:
        cfg = cfg if isinstance(cfg, dict) else {}
        result = FetchResult()

        env_map = cfg.get("env")
        env_map = env_map if isinstance(env_map, dict) else None
        if not env_map:
            result.error = (
                "secrets.keyring.enabled is true but secrets.keyring.env is "
                "empty.  Add ENV_VAR: lookup_name entries (or an empty value "
                "to use the env-var name as the lookup name)."
            )
            result.error_kind = ErrorKind.NOT_CONFIGURED
            return result

        plat = _platform()
        if plat == "linux":
            # On Linux there's no guaranteed keyring binary; warn so the
            # user knows why nothing came back.
            has_secret_tool = bool(shutil.which("secret-tool"))
            has_pass = bool(shutil.which("pass"))
            has_pykeyring = _pykeyring_available()
            if not (has_secret_tool or has_pass or has_pykeyring):
                result.error = (
                    "secrets.keyring.enabled is true but no Linux keyring "
                    "backend was found.  Install 'secret-tool' (libsecret) "
                    "or 'pass', or install the 'keyring' PyPI package."
                )
                result.error_kind = ErrorKind.BINARY_MISSING
                return result

        elif plat == "windows":
            if not shutil.which("powershell") and not _pykeyring_available():
                result.error = (
                    "secrets.keyring.enabled is true but neither PowerShell "
                    "nor the 'keyring' Python package is available on Windows."
                )
                result.error_kind = ErrorKind.BINARY_MISSING
                return result

        elif plat == "macos":
            # `security` is always present; nothing to pre-check.
            pass

        # Resolve each env var.  Best-effort per key: a failure for one
        # name is a warning, not a fatal error.
        for var_name, lookup in env_map.items():
            if not is_valid_env_name(var_name):
                result.warnings.append(
                    f"Skipping {var_name!r}: not a valid env-var name"
                )
                continue

            # The lookup name defaults to the env-var name.
            service = (str(lookup).strip() if lookup else "") or var_name

            value = _read_from_keyring(service)
            if value is None:
                result.warnings.append(
                    f"No OS keyring entry found for {var_name!r} "
                    f"(service={service!r})"
                )
                continue

            if value.strip() == "":
                result.warnings.append(
                    f"Ignoring empty keyring value for {var_name!r} "
                    "(would clobber a good credential with nothing)"
                )
                continue

            result.secrets[var_name] = value

        if not result.secrets and not result.warnings:
            result.error = "keyring source returned no secrets"
            result.error_kind = ErrorKind.EMPTY_VALUE

        return result

    def remediation(self, kind: Optional[ErrorKind], cfg: dict) -> str:
        if kind == ErrorKind.NOT_CONFIGURED:
            return (
                "Set secrets.keyring.enabled: true and add an env: map in "
                "config.yaml listing the ENV_VAR names to pull."
            )
        if kind == ErrorKind.BINARY_MISSING:
            plat = _platform()
            if plat == "linux":
                return (
                    "Install libsecret-tools (apt install libsecret-tools) "
                    "for secret-tool, or install pass, or pip install keyring."
                )
            if plat == "windows":
                return (
                    "Ensure PowerShell is available, or pip install keyring."
                )
            return ""
        if kind == ErrorKind.EMPTY_VALUE:
            return (
                "Verify you stored the credential in the OS keyring with the "
                "matching service/account name (macOS: "
                "`security add-generic-password -a \"$USER\" -s ENV_VAR -w \"VALUE\"`)."
            )
        return ""


def _pykeyring_available() -> bool:
    """True when the optional ``keyring`` PyPI package can be imported."""
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Convenience helpers — mirror the command_source API for ad-hoc reads
# ---------------------------------------------------------------------------


def get_keyring_secret(
    env_var: str,
    *,
    lookup: Optional[str] = None,
    timeout_seconds: float = 10.0,
) -> Optional[str]:
    """Read a single env-var's value from the OS keyring.

    Returns ``None`` on any failure — never raises.  Primarily used by
    tools that need a just-in-time read outside the startup path.
    """
    service = (lookup or env_var).strip() or env_var
    if not is_valid_env_name(env_var):
        return None
    return _read_from_keyring(service)


__all__ = [
    "KeyringSource",
    "get_keyring_secret",
    "FetchResult",
]
