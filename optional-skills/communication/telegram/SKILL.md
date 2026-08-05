---
name: telegram
description: "Telegram bot management, automation, messaging, and macOS Keychain secret storage."
version: 1.1.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [telegram, communication, bot, automation, keychain, messaging]
    category: communication
    related_skills: [telegram-gateway-troubleshooting]
---

# Telegram Bot Management & Automation

## Trigger Conditions
- User asks to manage Telegram bot
- User asks to send Telegram messages
- User asks to configure Telegram settings
- User asks about Telegram gateway issues
- Keywords: "telegram", "bot", "tg", "message", "chat", "telegram gateway"

## Steps

### 1. Bot Configuration
- The Telegram bot token can be resolved from macOS Keychain (preferred) or `~/.hermes/.env` (legacy).
- **Keychain storage (macOS only, most secure):**
  Service name: `TELEGRAM_BOT_TOKEN`, Account: `$USER` (your macOS username)

  ```sh
  # Store once (replace with your real @BotFather token):
  security add-generic-password -a "$USER" -s TELEGRAM_BOT_TOKEN \
      -w "YOUR_BOT_TOKEN_HERE" -T /usr/bin/security

  # Verify:
  security find-generic-password -s TELEGRAM_BOT_TOKEN -a "$USER" -w

  # Update later:
  security delete-generic-password -a "$USER" -s TELEGRAM_BOT_TOKEN 2>/dev/null
  security add-generic-password -a "$USER" -s TELEGRAM_BOT_TOKEN \
      -w "NEW_TOKEN" -T /usr/bin/security
  ```

  ```yaml
  # Activate the Keychain resolution in ~/.hermes/config.yaml:
  secrets:
    command:
      enabled: true
      override_existing: false
      command: printf TELEGRAM_BOT_TOKEN=%s "$(security find-generic-password -s TELEGRAM_BOT_TOKEN -a "$USER" -w)"
  ```

  At every gateway startup, this runs the `security` lookup and hydrates `TELEGRAM_BOT_TOKEN` into `os.environ` via Hermes' built-in `secrets.command` secret source — **zero core code changes required**. On non-macOS, the command degrades gracefully (logs a warning, resolves empty) and you fall back to `.env`.

- **Legacy `.env` method:**
  ```sh
  echo 'TELEGRAM_BOT_TOKEN=your_bot_token_here' >> ~/.hermes/.env
  ```

- Key settings: `TELEGRAM_BOT_TOKEN` (from keychain/env), `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_HOME_CHANNEL`.
- Use `hermes config set` to update non-token settings (never edit `.env` directly for credentials).
- Restart gateway: `hermes gateway restart`

### 2. Sending Messages
- Use `cronjob(action='create', deliver='telegram:...')` to send scheduled messages
- Use `cronjob(action='create', deliver='telegram:chat_id')` for one-time messages
- Find chat IDs from gateway logs: `grep "inbound message" ~/.hermes/logs/gateway.log`

### 3. Gateway Management
- Check status: `hermes gateway status`
- View logs: `tail -30 ~/.hermes/logs/gateway.log`
- Restart: `hermes gateway restart`
- Check connection: `cat ~/.hermes/gateway_state.json`

### 4. Troubleshooting
- "Token not found" / "No bot token configured" = the keychain entry is missing or `secrets.command` isn't enabled. Verify with: `security find-generic-password -s TELEGRAM_BOT_TOKEN -a "$USER" -w` and check `~/.hermes/config.yaml` → `secrets.command.enabled: true`.
- "Token rejected" = bot token is invalid or corrupted (re-store in keychain).
- "Blocked unauthorized user" = user ID not in `TELEGRAM_ALLOWED_USERS`.
- "Chat not found" = bot hasn't been started by the user yet.
- Always use numeric user IDs, not usernames.

## Available Tools
- `cronjob(action, schedule, prompt, deliver)` — Schedule messages
- `terminal(command)` — Check gateway status and logs
- `read_file(path)` — Read config files
- `skill_view(name='telegram-gateway-troubleshooting')` — Troubleshooting guide

## Examples
- "Send a message to my Telegram chat"
- "Check if the Telegram gateway is running"
- "Restart the Telegram gateway"
- "Send me a daily summary on Telegram"

## Pitfalls
- `TELEGRAM_ALLOWED_USERS` must use numeric IDs, not usernames
- Never write placeholder values (like `***`) to `.env` — use `hermes config set`
- Bot must be started by the user before it can receive messages
- Chat IDs are numeric (positive for DMs, negative for groups)
- The bot token should live in macOS Keychain (service `TELEGRAM_BOT_TOKEN`, account `$USER`), NOT in `.env`. Store it via the `security add-generic-password` command in §1 — the secret never passes through Hermes or chat.
- `secrets.command.enabled` must be `true` in `~/.hermes/config.yaml` for the keychain token to hydrate into the environment at startup.
- The `secrets.command` source is POSIX-only (`/bin/sh`). It degrades gracefully on non-macOS — you'd need a different helper command there.
- The token was **never** in `~/.hermes/.env` to begin with (only `TELEGRAM_ALLOWED_USERS` and `TELEGRAM_HOME_CHANNEL` live there) — nothing needed removing from `.env`.

## Verification
- Confirm gateway shows "telegram connected"
- Verify messages appear in gateway logs as "inbound message"
- Check that scheduled messages are delivered
- Confirm user authorization is working
- `hermes config get TELEGRAM_BOT_TOKEN` returns a non-empty value (length > 0) after restart
