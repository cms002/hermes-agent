"""External secret source integrations.

A secret source is anything that can supply environment-variable-shaped
credentials at process startup, _after_ ~/.hermes/.env has loaded.

The contract every source implements is
:class:`agent.secret_sources.base.SecretSource`; the orchestrator that
runs the enabled sources (ordering, mapped-beats-bulk precedence,
first-claim-wins conflicts, ``override_existing`` semantics, provenance)
is :func:`agent.secret_sources.registry.apply_all`.  Multiple sources
can be enabled at once — see the registry module docstring for the
precedence ladder.  The atomic-write / 0600 / TTL disk-cache substrate
is shared across backends in ``agent.secret_sources._cache`` so the
security-sensitive bits live in exactly one place.

Currently bundled:

  - ``bitwarden`` — Bitwarden Secrets Manager (`bws` CLI).  See
    ``agent.secret_sources.bitwarden`` for the integration and
    ``hermes_cli.secrets_cli`` for the user-facing setup wizard.
  - ``onepassword`` — 1Password ``op://`` secret references (`op` CLI).
    See ``agent.secret_sources.onepassword`` for the integration and
    ``hermes_cli.onepassword_secrets_cli`` for the user-facing commands.
  - ``command`` — a user-configured POSIX helper that prints KEY=VALUE on
    stdout (keepassxc-cli, secret-tool, pass, …).  See
    ``agent.secret_sources.command`` for the integration.
  - ``keyring`` — the OS-native credential store (macOS Keychain via
    ``security``, Windows Credential Manager via PowerShell ``cmdkey``,
    Linux via ``secret-tool``/``pass``).  See
    ``agent.secret_sources.keyring``.
"""

from agent.secret_sources.base import (  # noqa: F401
    SECRET_SOURCE_API_VERSION,
    ErrorKind,
    FetchResult,
    SecretSource,
    is_valid_env_name,
    run_secret_cli,
    scrub_ansi,
)
