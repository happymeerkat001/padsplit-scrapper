# Move Hermes Interactive Handoff to Telegram; Slack Becomes Summary-Only

Created: 2026-07-20

## Context

A Slack workspace-wide message block (investigated separately) traced back to two duplicate Hermes gateway LaunchAgents fighting over the same Slack bot connection ("Gateway shutting down" restart loop). That incident is remediated, but the underlying risk remains: Hermes's *interactive* Slack bot shares the same workspace used for padsplit summaries and communication with dad/the VA. Any future Hermes bug on that bot risks another workspace-wide lockout for everyone, not just Leon.

Goal: move all interactive Hermes session handoff (approve/reply/steer sessions from a phone) to Telegram, and reduce Slack to what it does today via webhook — posting padsplit summaries — with zero bot-token surface area left for Hermes to misbehave on.

Locked-in decisions from discussion:
- **Two separate Telegram bots**, one per profile — `lean` (desk/coding) and `telegram` (mobile/away-from-desk, replacing `mobile`'s old Slack role). Each profile keeps its own credentials so one gateway restarting can't affect the other.
- **Rename `mobile` → `telegram`**: this profile currently has no active LaunchAgent, so renaming is a plain directory rename, no live process to disturb.
- **Archive a frozen `slack` profile**: a copy of `mobile`'s current (pre-rename) config, Slack tokens intact, no LaunchAgent ever created for it. This preserves a ready-to-revive interactive Slack setup if ever wanted again, without leaving anything live.
- **`lean` keeps its name** — "lean" describes toolset weight, not access surface (it already includes `computer_use`/`browser`); that's orthogonal to which chat platform reaches it, so no rename needed there.
- The padsplit-scraper repo's Slack **webhooks** (`SLACK_WEBHOOK_MESSAGES`, `SLACK_WEBHOOK_TASKS`, used by `message_summarizer.py`/`slack_notifier.py`) are a completely separate mechanism — no bot token, no gateway, no socket connection — and are untouched by any of this.
- Leon has already removed the connection on Slack's side for the currently-stuck `ai.hermes.gateway-lean` process, so the automated restart during execution is low-risk and preferred for efficiency (per Leon: "do what is most efficient and effective").

## Current State (verified)

- `~/.hermes/profiles/lean/` — active profile, LaunchAgent `ai.hermes.gateway-lean` running (pid 17543), Slack connection stuck `"failed to reconnect"` (Slack-side connection already pulled by Leon).
- `~/.hermes/profiles/mobile/` — inactive, no LaunchAgent loaded (`launchctl list` confirms only `ai.hermes.gateway-lean` and `com.leon.hermes-worker` exist). `.env` still has live `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN`/etc.
- Both profiles' `.env` files have identical Slack key sets: `SLACK_ALLOWED_USERS`, `SLACK_APP_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_HOME_CHANNEL`, `SLACK_HOME_CHANNEL_THREAD_ID`. Neither has any `TELEGRAM_*` key today.
- Platform activation is purely env-var-presence-based (`hermes_cli/tools_config.py::_get_enabled_platforms()`): setting `TELEGRAM_BOT_TOKEN` turns Telegram on; removing `SLACK_BOT_TOKEN` turns the Slack bot off. No other flag needed.
- `channel_directory.json` shows zero registered Telegram chats — Telegram has never been used on this machine.
- Each profile has its own `config.yaml` with `slack:`, `telegram:`, and `platform_toolsets:` blocks (not just a global default) — e.g. `~/.hermes/profiles/lean/config.yaml:469-473` already has an (unused) `telegram:` stanza with `allowed_chats: ''`.

## Plan

### 1. Leon: create two Telegram bots (manual, outside this plan's execution)

For **each** of `lean` and `telegram` (renamed mobile):
1. Message **@BotFather** → `/newbot` → pick a display name (e.g. "Hermes Lean", "Hermes Mobile") and a unique `*_bot` username.
2. Save the returned API token (format `123456789:ABC...`).
3. Message **@userinfobot** to get Leon's numeric Telegram user ID (same ID works for both bots).
4. Optional but recommended: `/mybots` → select bot → **Bot Settings → Group Privacy** stays on default (irrelevant for DM-only use).

Two distinct bot tokens are required — Hermes profiles running concurrently must not share a token (per `website/docs/user-guide/messaging/telegram.md`, "Multiple Hermes bots" section).

### 2. Archive current `mobile` config as frozen `slack` profile

```bash
cp -R ~/.hermes/profiles/mobile ~/.hermes/profiles/slack
```
- Edit `~/.hermes/profiles/slack/profile.yaml` description to note it's a frozen/dormant archive (e.g. "Archived Slack-based mobile profile — kept for reference, not live. See `telegram` profile for the active replacement.").
- No LaunchAgent is ever created for `slack` — it's disk-only, reactivate manually later if ever needed.

### 3. Rename `mobile` → `telegram`

```bash
mv ~/.hermes/profiles/mobile ~/.hermes/profiles/telegram
```
- Update `~/.hermes/profiles/telegram/profile.yaml` description to reflect its new role (e.g. "Telegram-based mobile/away-from-desk Hermes profile — full interactive handoff via Telegram, replacing the old Slack-based `mobile` profile.").

### 4. Edit `telegram` profile's `.env`

In `~/.hermes/profiles/telegram/.env`:
- Remove: `SLACK_ALLOWED_USERS`, `SLACK_APP_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_HOME_CHANNEL`, `SLACK_HOME_CHANNEL_THREAD_ID`.
- Add:
  ```bash
  TELEGRAM_BOT_TOKEN=<telegram-profile-bot-token>
  TELEGRAM_ALLOWED_USERS=<leon-telegram-user-id>
  ```

### 5. Edit `lean` profile's `.env`

In `~/.hermes/profiles/lean/.env`:
- Remove the same five `SLACK_*` keys.
- Add:
  ```bash
  TELEGRAM_BOT_TOKEN=<lean-profile-bot-token>
  TELEGRAM_ALLOWED_USERS=<leon-telegram-user-id>
  ```

### 6. Config.yaml tweaks (both `lean` and `telegram` profiles)

Both already have a `telegram:` stanza; just confirm/set:
```yaml
telegram:
  reactions: false
  allowed_chats: ''    # leave blank: DM-only, gated by TELEGRAM_ALLOWED_USERS instead
  extra:
    rich_messages: true
```
No changes needed to the `slack:` stanza — it becomes inert once `SLACK_BOT_TOKEN` is absent from `.env`.

### 7. Create LaunchAgent for the `telegram` profile

New file `~/Library/LaunchAgents/ai.hermes.gateway-telegram.plist`, cloned from `ai.hermes.gateway-lean.plist` with these substitutions:
- `Label` → `ai.hermes.gateway-telegram`
- `--profile` arg → `telegram`
- `WorkingDirectory` → `/Users/leon/.hermes/profiles/telegram`
- `HERMES_HOME` env → `/Users/leon/.hermes/profiles/telegram`
- `StandardOutPath`/`StandardErrorPath` → `/Users/leon/.hermes/profiles/telegram/logs/gateway.log` / `gateway.error.log`

Load it:
```bash
launchctl load ~/Library/LaunchAgents/ai.hermes.gateway-telegram.plist
```

### 8. Restart the `lean` gateway to pick up its new `.env`

Leon has already disconnected this bot on Slack's side, so this is low-risk:
```bash
launchctl unload ~/Library/LaunchAgents/ai.hermes.gateway-lean.plist
launchctl load ~/Library/LaunchAgents/ai.hermes.gateway-lean.plist
```
(LaunchAgent has `RunAtLoad`/`KeepAlive`, so unload+load is the clean restart — no need to manually kill pid 17543 first.)

### 9. Verification

- `launchctl list | grep hermes` → expect `ai.hermes.gateway-lean` and `ai.hermes.gateway-telegram` both present with pid > 0, no `ai.hermes.gateway-mobile` or `-slack` entries.
- `cat ~/.hermes/profiles/lean/gateway_state.json` and `.../telegram/gateway_state.json` → `platforms` should show `telegram: {state: "connected"}` (or similar), no `slack` entry at all.
- Send a DM to each bot from Telegram and confirm a reply from the correct profile (lean vs telegram).
- Confirm padsplit-summary still works untouched: `cd /Users/leon/Documents/Code/padsplit-scraper && ./venv/bin/python3 slack_notifier.py` (or run the existing `/padsplit-summary` command) — should post normally, since it never touched a bot token.
- `grep -c SLACK ~/.hermes/profiles/lean/.env ~/.hermes/profiles/telegram/.env` → expect `0` in both.

## Explicitly out of scope

- No changes to `com.leon.hermes-worker` (doesn't use Slack).
- No changes to padsplit-scraper's webhook-based Slack posting.
- The archived `slack` profile is never started as part of this plan.
