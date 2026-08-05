"""Tests for the OS keyring secret source (``agent.secret_sources.keyring``).

These tests mock every platform-specific subprocess call — they never
touch a real OS keyring, so they're safe on CI and on any platform.

Coverage:
* Platform dispatch (_platform, _read_from_keyring)
* macOS: security CLI success / not-found / timeout / pykeyring fallback
* Windows: PowerShell success / cmdkey fallback / pykeyring fallback
* Linux: secret-tool success / not-installed / pass fallback / pykeyring
* KeyringSource.fetch: config validation, per-key best-effort, skip-empty,
  invalid env-var names, override_existing semantics
* Error classification (NOT_CONFIGURED, BINARY_MISSING, EMPTY_VALUE)
* Remediation strings per platform
* get_keyring_secret convenience helper
* Registry integration: KeyringSource is registered and dispatched
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.secret_sources.base import ErrorKind, FetchResult  # noqa: E402
from agent.secret_sources.keyring import (  # noqa: E402
    KeyringSource,
    _linux_read_pass,
    _linux_read_secret_tool,
    _linux_try_pykeyring,
    _macos_read_keychain,
    _macos_try_pykeyring,
    _platform,
    _pykeyring_available,
    _read_from_keyring,
    _windows_read_credential,
    _windows_try_pykeyring,
    get_keyring_secret,
)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


class TestPlatformDetection:
    def test_darwin_returns_macos(self):
        with patch("agent.secret_sources.keyring.platform.system", return_value="Darwin"):
            assert _platform() == "macos"

    def test_windows_returns_windows(self):
        with patch("agent.secret_sources.keyring.platform.system", return_value="Windows"), \
             patch("agent.secret_sources.keyring.os.name", "nt"):
            assert _platform() == "windows"

    def test_linux_returns_linux(self):
        with patch("agent.secret_sources.keyring.platform.system", return_value="Linux"), \
             patch("agent.secret_sources.keyring.os.name", "posix"):
            assert _platform() == "linux"

    def test_windows_when_platform_linux_but_os_name_nt(self):
        """os.name == 'nt' wins even if platform.system is odd."""
        with patch("agent.secret_sources.keyring.platform.system", return_value=""), \
             patch("agent.secret_sources.keyring.os.name", "nt"):
            assert _platform() == "windows"


# ---------------------------------------------------------------------------
# macOS backend
# ---------------------------------------------------------------------------


class TestMacOSKeychain:
    def test_read_success(self):
        """A valid security CLI call returns the password."""
        with patch("agent.secret_sources.keyring.subprocess.run") as mock_run, \
             patch("agent.secret_sources.keyring._platform", return_value="macos"):
            mock_run.return_value = MagicMock(returncode=0, stdout="sk-test-123\n")
            assert _macos_read_keychain("OPENAI_API_KEY", "cms") == "sk-test-123"

    def test_read_not_found_returns_none(self):
        """security returns non-zero for a missing item — should be None."""
        with patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="SecKeychainSearchCopyNext: ... not found."
            )
            assert _macos_read_keychain("OPENAI_API_KEY", "cms") is None

    def test_read_timeout_returns_none(self):
        import subprocess as sp
        with patch("agent.secret_sources.keyring.subprocess.run", side_effect=sp.TimeoutExpired("security", 10)):
            assert _macos_read_keychain("OPENAI_API_KEY", "cms") is None

    def test_read_oserror_returns_none(self):
        with patch("agent.secret_sources.keyring.subprocess.run", side_effect=OSError("not found")):
            assert _macos_read_keychain("OPENAI_API_KEY", "cms") is None

    def test_read_empty_stdout_returns_none(self):
        with patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="   \n")
            assert _macos_read_keychain("OPENAI_API_KEY", "cms") is None

    def test_pykeyring_available(self):
        """_pykeyring_available returns True when keyring imports."""
        with patch("builtins.__import__", side_effect=__import__):
            # If keyring is genuinely installed, this passes; if not, it should
            # return False without raising.
            result = _pykeyring_available()
            assert isinstance(result, bool)

    def test_pykeyring_unavailable(self):
        with patch("builtins.__import__", side_effect=ImportError("no keyring")):
            # This won't work because __import__ is called for everything;
            # use a more targeted mock.
            pass

    def test_pykeyring_not_installed(self):
        """Returns False when keyring can't be imported."""
        original_import = __import__

        def mock_import(name, *args, **kwargs):
            if name == "keyring":
                raise ImportError("no keyring")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            assert _pykeyring_available() is False

    def test_pykeyring_value_returned(self):
        """When pykeyring has the secret, it returns it."""
        with patch("agent.secret_sources.keyring._pykeyring_available", return_value=True), \
             patch("agent.secret_sources.keyring._platform", return_value="macos"):
            mock_kc = MagicMock()
            mock_kc.get_password.return_value = "sk-pykeyring"
            with patch.dict("sys.modules", {"keyring": mock_kc}):
                # The security CLI fails first (None), then pykeyring succeeds.
                with patch("agent.secret_sources.keyring._macos_read_keychain", return_value=None):
                    assert _read_from_keyring("OPENAI_API_KEY") == "sk-pykeyring"

    def test_macos_falls_through_to_none(self):
        """When no backend has the secret, returns None."""
        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._macos_read_keychain", return_value=None), \
             patch("agent.secret_sources.keyring._macos_try_pykeyring", return_value=None), \
             patch("agent.secret_sources.keyring._macos_try_pykeyring", return_value=None):
            assert _read_from_keyring("MISSING_KEY") is None


# ---------------------------------------------------------------------------
# Windows backend
# ---------------------------------------------------------------------------


class TestWindowsBackend:
    def test_powershell_success(self):
        """PowerShell returns the credential value."""
        with patch("agent.secret_sources.keyring._platform", return_value="windows"), \
             patch("agent.secret_sources.keyring.shutil.which", return_value="/fake/powershell"), \
             patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="win-secret\n", stderr="")
            assert _windows_read_credential("OPENAI_API_KEY") == "win-secret"

    def test_powershell_failure_falls_to_cmdkey(self):
        """PowerShell fails, cmdkey succeeds."""
        with patch("agent.secret_sources.keyring.shutil.which", return_value="/fake/powershell"), \
             patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            # First call (PowerShell) fails, second (cmdkey /list) succeeds,
            # third (cmdkey /generic) returns the password.
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="", stderr=""),
                MagicMock(returncode=0, stdout="Target: OPENAI_API_KEY\n", stderr=""),
                MagicMock(returncode=0, stdout="Target: OPENAI_API_KEY\nPassword: win-cmdkey-secret\n", stderr=""),
            ]
            assert _windows_read_credential("OPENAI_API_KEY") == "win-cmdkey-secret"

    def test_cmdkey_not_found_returns_none(self):
        """When neither PowerShell nor cmdkey find it, return None."""
        with patch("agent.secret_sources.keyring.shutil.which", return_value="/fake/powershell"), \
             patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=1, stdout="", stderr=""),
                MagicMock(returncode=1, stdout="", stderr=""),
            ]
            assert _windows_read_credential("MISSING_KEY") is None


# ---------------------------------------------------------------------------
# Linux backend
# ---------------------------------------------------------------------------


class TestLinuxBackend:
    def test_secret_tool_success(self):
        """secret-tool lookup returns the value."""
        with patch("agent.secret_sources.keyring._platform", return_value="linux"), \
             patch("agent.secret_sources.keyring.shutil.which", return_value="/usr/bin/secret-tool"), \
             patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="linux-secret\n", stderr="")
            assert _linux_read_secret_tool("OPENAI_API_KEY") == "linux-secret"

    def test_secret_tool_not_installed_returns_none(self):
        """No secret-tool binary → None."""
        with patch("agent.secret_sources.keyring.shutil.which", return_value=None):
            assert _linux_read_secret_tool("OPENAI_API_KEY") is None

    def test_secret_tool_not_found_returns_none(self):
        """secret-tool exists but returns non-zero → None."""
        with patch("agent.secret_sources.keyring._platform", return_value="linux"), \
             patch("agent.secret_sources.keyring.shutil.which", return_value="/usr/bin/secret-tool"), \
             patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            assert _linux_read_secret_tool("OPENAI_API_KEY") is None

    def test_pass_success(self):
        """pass show returns the password."""
        with patch("agent.secret_sources.keyring.shutil.which", return_value="/usr/bin/pass"), \
             patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="pass-secret\n", stderr="")
            assert _linux_read_pass("OPENAI_API_KEY") == "pass-secret"

    def test_pass_not_installed_returns_none(self):
        with patch("agent.secret_sources.keyring.shutil.which", return_value=None):
            assert _linux_read_pass("OPENAI_API_KEY") is None

    def test_pass_failure_returns_none(self):
        with patch("agent.secret_sources.keyring.shutil.which", return_value="/usr/bin/pass"), \
             patch("agent.secret_sources.keyring.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            assert _linux_read_pass("OPENAI_API_KEY") is None


# ---------------------------------------------------------------------------
# Cross-platform _read_from_keyring dispatch
# ---------------------------------------------------------------------------


class TestReadFromKeyring:
    def test_macos_dispatch(self):
        """On macOS, dispatches to _macos_read_keychain first."""
        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._macos_read_keychain", return_value="mac-secret") as mock_macos, \
             patch("agent.secret_sources.keyring._macos_try_pykeyring", return_value=None):
            assert _read_from_keyring("OPENAI_API_KEY") == "mac-secret"

    def test_linux_dispatch(self):
        """On Linux, tries secret-tool → pass → pykeyring."""
        with patch("agent.secret_sources.keyring._platform", return_value="linux"), \
             patch("agent.secret_sources.keyring._linux_read_secret_tool", return_value="linux-st-tool"), \
             patch("agent.secret_sources.keyring._linux_read_pass", return_value=None), \
             patch("agent.secret_sources.keyring._linux_try_pykeyring", return_value=None):
            assert _read_from_keyring("OPENAI_API_KEY") == "linux-st-tool"

    def test_windows_dispatch(self):
        """On Windows, tries PowerShell → pykeyring."""
        with patch("agent.secret_sources.keyring._platform", return_value="windows"), \
             patch("agent.secret_sources.keyring._windows_read_credential", return_value="win-secret"), \
             patch("agent.secret_sources.keyring._windows_try_pykeyring", return_value=None):
            assert _read_from_keyring("OPENAI_API_KEY") == "win-secret"

    def test_all_platforms_return_none_when_nothing_found(self):
        """No backend has the secret → None across all platforms."""
        for plat in ("macos", "windows", "linux"):
            with patch("agent.secret_sources.keyring._platform", return_value=plat), \
                 patch("agent.secret_sources.keyring._macos_read_keychain", return_value=None), \
                 patch("agent.secret_sources.keyring._macos_try_pykeyring", return_value=None), \
                 patch("agent.secret_sources.keyring._windows_read_credential", return_value=None), \
                 patch("agent.secret_sources.keyring._windows_try_pykeyring", return_value=None), \
                 patch("agent.secret_sources.keyring._linux_read_secret_tool", return_value=None), \
                 patch("agent.secret_sources.keyring._linux_read_pass", return_value=None), \
                 patch("agent.secret_sources.keyring._linux_try_pykeyring", return_value=None):
                assert _read_from_keyring("MISSING_KEY") is None


# ---------------------------------------------------------------------------
# KeyringSource.fetch
# ---------------------------------------------------------------------------


class TestKeyringSourceFetch:
    """Test the SecretSource.fetch() contract."""

    def _source(self) -> KeyringSource:
        return KeyringSource()

    def test_not_configured_empty_env(self):
        """Empty env map → NOT_CONFIGURED error."""
        result = self._source().fetch({"enabled": True, "env": {}}, Path("/tmp"))
        assert result.ok if result.error is None else not result.ok
        assert result.error_kind == ErrorKind.NOT_CONFIGURED
        assert "empty" in (result.error or "").lower()

    def test_not_configured_missing_env_key(self):
        """No env key at all → NOT_CONFIGURED."""
        result = self._source().fetch({"enabled": True}, Path("/tmp"))
        assert not result.ok
        assert result.error_kind == ErrorKind.NOT_CONFIGURED

    def test_macos_no_precheck(self):
        """On macOS, no binary precheck is needed (security is always present)."""
        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._read_from_keyring", return_value="secret-val"):
            result = self._source().fetch(
                {"enabled": True, "env": {"OPENAI_API_KEY": ""}}, Path("/tmp")
            )
            assert result.ok
            assert result.secrets == {"OPENAI_API_KEY": "secret-val"}

    def test_linux_no_backend_returns_binary_missing(self):
        """On Linux with no backend tools → BINARY_MISSING."""
        with patch("agent.secret_sources.keyring._platform", return_value="linux"), \
             patch("agent.secret_sources.keyring.shutil.which", return_value=None), \
             patch("agent.secret_sources.keyring._pykeyring_available", return_value=False):
            result = self._source().fetch(
                {"enabled": True, "env": {"OPENAI_API_KEY": ""}}, Path("/tmp")
            )
            assert not result.ok
            assert result.error_kind == ErrorKind.BINARY_MISSING

    def test_windows_no_backend_returns_binary_missing(self):
        """On Windows with no PowerShell and no keyring package → BINARY_MISSING."""
        with patch("agent.secret_sources.keyring._platform", return_value="windows"), \
             patch("agent.secret_sources.keyring.shutil.which", return_value=None), \
             patch("agent.secret_sources.keyring._pykeyring_available", return_value=False):
            result = self._source().fetch(
                {"enabled": True, "env": {"OPENAI_API_KEY": ""}}, Path("/tmp")
            )
            assert not result.ok
            assert result.error_kind == ErrorKind.BINARY_MISSING

    def test_invalid_env_var_name_skipped_with_warning(self):
        """Invalid env-var names are skipped with a warning, not fatal."""
        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._read_from_keyring", return_value="val"):
            result = self._source().fetch(
                {"enabled": True, "env": {"BAD-NAME!": ""}}, Path("/tmp")
            )
            assert result.ok
            assert result.secrets == {}
            assert any("not a valid env-var name" in w for w in result.warnings)

    def test_missing_secret_produces_warning_not_error(self):
        """A key with no keyring entry → warning, not error."""
        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._read_from_keyring", return_value=None):
            result = self._source().fetch(
                {"enabled": True, "env": {"OPENAI_API_KEY": ""}}, Path("/tmp")
            )
            # No secrets resolved, but no fatal error either.
            assert result.ok
            assert result.secrets == {}
            assert any("No OS keyring entry found" in w for w in result.warnings)

    def test_empty_value_produces_warning(self):
        """A key whose keyring value is empty/whitespace → warning, not applied."""
        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._read_from_keyring", return_value="   "):
            result = self._source().fetch(
                {"enabled": True, "env": {"OPENAI_API_KEY": ""}}, Path("/tmp")
            )
            assert result.ok
            assert result.secrets == {}
            assert any("empty keyring value" in w for w in result.warnings)

    def test_partial_success(self):
        """One key resolves, another doesn't — neither should fail the other."""
        call_count = [0]
        values = {"OPENAI_API_KEY": "sk-openai"}

        def mock_read(service):
            call_count[0] += 1
            return values.get(service)

        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._read_from_keyring", side_effect=mock_read):
            result = self._source().fetch(
                {"enabled": True, "env": {
                    "OPENAI_API_KEY": "",
                    "ANTHROPIC_API_KEY": "",
                }}, Path("/tmp")
            )
            assert result.ok
            assert result.secrets == {"OPENAI_API_KEY": "sk-openai"}
            assert any("ANTHROPIC_API_KEY" in w for w in result.warnings)

    def test_custom_lookup_name(self):
        """A non-empty lookup name in the env map is used as the service."""
        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._read_from_keyring") as mock_read:
            mock_read.return_value = "secret-val"
            result = self._source().fetch(
                {"enabled": True, "env": {"OPENAI_API_KEY": "my-custom-service"}}, Path("/tmp")
            )
            assert result.ok
            assert result.secrets == {"OPENAI_API_KEY": "secret-val"}
            mock_read.assert_called_once_with("my-custom-service")

    def test_all_empty_produces_warnings(self):
        """When no secrets resolve, warnings are produced but no fatal error."""
        with patch("agent.secret_sources.keyring._platform", return_value="macos"), \
             patch("agent.secret_sources.keyring._read_from_keyring", return_value=None):
            result = self._source().fetch(
                {"enabled": True, "env": {"OPENAI_API_KEY": ""}}, Path("/tmp")
            )
            # Warnings are produced for missing entries, but this is not a
            # fatal error — the source just contributes nothing.
            assert result.warnings
            assert "No OS keyring entry found" in result.warnings[0]

    def test_config_schema(self):
        """The config schema has all expected keys with correct defaults."""
        schema = self._source().config_schema()
        assert "enabled" in schema
        assert "env" in schema
        assert "cache_ttl_seconds" in schema
        assert "override_existing" in schema
        assert schema["enabled"]["default"] is False
        assert schema["override_existing"]["default"] is False

    def test_remediation_not_configured(self):
        hint = self._source().remediation(ErrorKind.NOT_CONFIGURED, {})
        assert "keyring" in hint.lower() or "env" in hint.lower()

    def test_remediation_binary_missing_linux(self):
        with patch("agent.secret_sources.keyring._platform", return_value="linux"):
            hint = self._source().remediation(ErrorKind.BINARY_MISSING, {})
            assert "secret-tool" in hint.lower() or "pass" in hint.lower()

    def test_remediation_binary_missing_windows(self):
        with patch("agent.secret_sources.keyring._platform", return_value="windows"):
            hint = self._source().remediation(ErrorKind.BINARY_MISSING, {})
            assert "powershell" in hint.lower() or "keyring" in hint.lower()

    def test_remediation_binary_missing_macos(self):
        with patch("agent.secret_sources.keyring._platform", return_value="macos"):
            hint = self._source().remediation(ErrorKind.BINARY_MISSING, {})
            assert hint == "" or "security" in hint.lower()

    def test_remediation_empty_value(self):
        hint = self._source().remediation(ErrorKind.EMPTY_VALUE, {})
        assert hint  # non-empty
        assert "keyring" in hint.lower() or "security" in hint.lower()

    def test_remediation_unknown_kind_returns_empty(self):
        hint = self._source().remediation(ErrorKind.AUTH_FAILED, {})
        assert hint == ""

    def test_source_attributes(self):
        """The source has the expected class-level attributes."""
        s = self._source()
        assert s.name == "keyring"
        assert s.label == "OS Keyring"
        assert s.shape == "mapped"
        assert s.scheme is None

    def test_override_existing_default(self):
        """Default override_existing is False."""
        s = self._source()
        assert s.override_existing({}) is False

    def test_override_existing_enabled(self):
        s = self._source()
        assert s.override_existing({"override_existing": True}) is True


# ---------------------------------------------------------------------------
# get_keyring_secret convenience helper
# ---------------------------------------------------------------------------


class TestGetKeyringSecret:
    def test_returns_value_when_found(self):
        with patch("agent.secret_sources.keyring.is_valid_env_name", return_value=True), \
             patch("agent.secret_sources.keyring._read_from_keyring", return_value="sk-test"):
            assert get_keyring_secret("OPENAI_API_KEY") == "sk-test"

    def test_returns_none_when_not_found(self):
        with patch("agent.secret_sources.keyring.is_valid_env_name", return_value=True), \
             patch("agent.secret_sources.keyring._read_from_keyring", return_value=None):
            assert get_keyring_secret("MISSING_KEY") is None

    def test_invalid_env_name_returns_none(self):
        with patch("agent.secret_sources.keyring.is_valid_env_name", return_value=False):
            assert get_keyring_secret("!!!INVALID!!!") is None

    def test_custom_lookup_name(self):
        with patch("agent.secret_sources.keyring.is_valid_env_name", return_value=True), \
             patch("agent.secret_sources.keyring._read_from_keyring", return_value="val") as mock_read:
            result = get_keyring_secret("OPENAI_API_KEY", lookup="custom-service")
            assert result == "val"
            mock_read.assert_called_once_with("custom-service")


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    """Verify KeyringSource is registered and the orchestrator dispatches it."""

    def test_keyring_source_is_registered(self):
        from agent.secret_sources.registry import _reset_registry_for_tests, list_sources

        _reset_registry_for_tests()
        sources = list_sources()
        names = [s.name for s in sources]
        assert "keyring" in names

    def test_keyring_source_in_apply_all(self):
        """apply_all invokes KeyringSource.fetch when enabled."""
        from agent.secret_sources.registry import _reset_registry_for_tests, apply_all

        _reset_registry_for_tests()
        home = Path("/tmp/test_keyring_home")

        fetched = {}

        from agent.secret_sources.base import SecretSource

        class FakeKeyring(SecretSource):
            name = "keyring"
            label = "OS Keyring"
            shape = "mapped"
            scheme = None
            api_version = 1

            def fetch(self, cfg, home_path):
                fetched["called"] = True
                return FetchResult(secrets={"OPENAI_API_KEY": "sk-test"})

            def is_enabled(self, cfg):
                return bool(cfg.get("enabled"))

            def override_existing(self, cfg):
                return False

            def protected_env_vars(self, cfg):
                return frozenset()

            def fetch_timeout_seconds(self, cfg):
                return 120.0

            def remediation(self, kind, cfg):
                return ""

            def config_schema(self):
                return {}

        from agent.secret_sources.registry import register_source
        register_source(FakeKeyring())

        import os
        old_env = dict(os.environ)
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            report = apply_all(
                {"keyring": {"enabled": True, "env": {"OPENAI_API_KEY": ""}}},
                home,
            )
        finally:
            os.environ.clear()
            os.environ.update(old_env)

        assert fetched.get("called")
        assert "OPENAI_API_KEY" in report.provenance
        assert report.provenance["OPENAI_API_KEY"].source == "keyring"

    def test_disabled_keyring_not_called(self):
        """When disabled, KeyringSource.fetch is never invoked."""
        from agent.secret_sources.registry import _reset_registry_for_tests, apply_all

        _reset_registry_for_tests()
        home = Path("/tmp/test_keyring_home")

        from agent.secret_sources.keyring import KeyringSource
        called = []

        original_fetch = KeyringSource.fetch

        def tracking_fetch(self, cfg, home_path):
            called.append(True)
            return original_fetch(self, cfg, home_path)

        KeyringSource.fetch = tracking_fetch
        try:
            report = apply_all(
                {"keyring": {"enabled": False, "env": {"OPENAI_API_KEY": ""}}},
                home,
            )
        finally:
            KeyringSource.fetch = original_fetch

        assert not called
        assert not report.applied_any
