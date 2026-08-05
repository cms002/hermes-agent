# OS Keyring Secret Source

Resolve credentials from the platform-native credential store (macOS Keychain, Windows Credential Manager, Linux Secret Service) at process startup — instead of storing them in `~/.hermes/.env`.

## How it works

1. You list which env-var names to pull in `config.yaml` under `secrets.keyring.env`.
2. At startup, after `.env` loads, Hermes looks up each name in the OS keyring:
   - **macOS:** `security find-generic-password -a "$USER" -s <name> -w` (login keychain)
   - **Windows:** PowerShell `Get-StoredCredential -Target <name>` (or `cmdkey` fallback)
   - **Linux:** `secret-tool lookup env <name>` (libsecret) or `pass show <name>`
3. Found values flow through the same precedence ladder as [Bitwarden](./bitwarden) and [1Password](./onepassword) — `.env`/shell wins unless `override_existing: true`; first claim wins.

```yaml
secrets:
  keyring:
    enabled: true
    env:
      OPENAI_API_KEY:
      ANTHROPIC_API_KEY:
      AGENTMAIL_API_KEY:
```

The lookup name defaults to the env-var name itself. Leave the value empty or omit it — the platform looks up `OPENAI_API_KEY` using `OPENAI_API_KEY` as the service/target name.

## Storing your credentials

Hermes only **reads** from the keyring — you store credentials once using the platform-native tooling.

### macOS

```sh
security add-generic-password -a "$USER" -s "OPENAI_API_KEY" -w "your-key-here"
```

You can verify it was stored:
```sh
security find-generic-password -a "$USER" -s "OPENAI_API_KEY" -w
```

### Windows (PowerShell)

```powershell
cmdkey /add:OPENAI_API_KEY /user:user /pass:"your-key-here"
```

Verify:
```powershell
cmdkey /list
```

### Linux

**Using libsecret (GNOME Keyring / Secret Service):**
```bash
# Store — the "env" attribute must match what Hermes looks up
secret-tool store --label="OPENAI_API_KEY" --env OPENAI_API_KEY <<< "your-key-here"
```

**Using pass (password-store):**
```bash
pass insert OPENAI_API_KEY
```

## Config

| Key | Default | What it does |
|---|---|---|
| `enabled` | `false` | Master switch. |
| `env` | `{}` | Map of env-var name → lookup name. Leave value empty to use the env-var name as the lookup name. |
| `override_existing` | `false` | Keyring values overwrite `.env`/shell values. Off by default — a local keyring is not a central rotation authority. |
| `cache_ttl_seconds` | `300` | Seconds to cache in-process and on disk. `0` disables both (every startup hits the keyring). |

## Security model

- **Encrypted at rest.** OS keyrings encrypt every secret with a key derived from your login session. A plaintext `.env` file is readable by any process with filesystem access to `~/.hermes/`.
- **Access-controlled reads.** macOS Keychain ACLs let you control which applications can read an item. Windows Credential Manager scopes by target name. A subprocess inheriting your shell env can't decrypt your keyring.
- **Startup is never blocked.** Missing keyring entry, missing CLI binary, or a locked keyring produces a warning — Hermes continues with `.env`/shell values.
- **Never raises.** The source degrades gracefully on every failure path.

## Failure modes

Startup continues on every failure — you'll see a warning:

| Symptom | Cause | Fix |
|---|---|---|
| `No OS keyring entry found for 'OPENAI_API_KEY'` | Credential not stored in the OS keyring | Store it with the platform-native tooling (see above) |
| `secrets.keyring.enabled is true but env map is empty` | Enabled without listing env vars | Add `env:` entries in config.yaml |
| `secrets.keyring.enabled is true but no Linux keyring backend was found` | No `secret-tool`, `pass`, or `keyring` package | Install one: `apt install libsecret-tools` or `pip install keyring` |
| `secrets.keyring.enabled is true but neither PowerShell nor the keyring Python package is available on Windows` | No PowerShell or keyring package | Enable PowerShell or `pip install keyring` |

## When to use this vs a plugin

The keyring source covers the three desktop platforms out of the box. For server-side / CI environments (no desktop session, no interactive unlock), use [Bitwarden](./bitwarden) or a [secret-source plugin](/developer-guide/secret-source-plugin) instead — OS keyrings require a logged-in user session.

For enterprise vaults (HashiCorp Vault, AWS Secrets Manager, etc.), ship a plugin — those belong in third-party repos, not core.
