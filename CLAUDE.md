# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Multi-scraper data collection system for:
- **Padsplit** (`padsplit_scraper/`): rental property metrics (occupancy, earnings, flip rates) via GraphQL/REST APIs
- **Thermostat** (`thermostat/`): HVAC data from mytotalconnectcomfort.com
- Outputs versioned JSON to `padsplit_scraper/output/`, `thermostat/output/`, and `docs/data/` (`latest.json`, `stats.json`, `occupancy.json`)

## Running Scrapers

```bash
# Activate virtualenv first
source venv/bin/activate

# Run individual scrapers
python3 padsplit_scraper/scraper.py
python3 thermostat/scraper.py

# PadSplit Ops mention listener (long-running; wakes Slate, does not reply)
python3 padsplit_scraper/discord_slate_wake.py

# Scheduled runs (also commit/push rolling output to git)
./run_morning.sh    # padsplit + thermostat
./run_afternoon.sh  # padsplit only
```

## Tests

```bash
python3 test_padsplit_scraper.py
python3 test_padsplit_occupancy.py
python3 test_thermostat_scraper.py
python3 test_thermostat_set_temps.py
python3 test_thermostat_schedule.py
python3 test_obsidian_daily_digest.py
python3 padsplit_scraper/test_reply_address_parser.py
python3 test_discord_slate_wake.py
```

No build step or linter configuration; tests use direct Python execution.

## Operations

- Morning and afternoon scraper runs are scheduled by launchd as
  `com.padsplit.scraper.morning` and `com.padsplit.scraper.afternoon`; their
  plist files live in `~/Library/LaunchAgents/` and call `run_morning.sh` and
  `run_afternoon.sh`.
- PadSplit Ops mention wake (`padsplit_scraper/discord_slate_wake.py`) is a
  long-running Discord gateway process, not a scheduled scrape. Keep it alive
  (launchd KeepAlive or equivalent) with `DISCORD_BOT_TOKEN`,
  `SLATE_ASK_WEBHOOK_URL`, and `SLATE_ASK_WEBHOOK_KEY`. It does not answer in
  Discord.
- Thermostat schedule enforcement is launchd-managed by
  `com.padsplit.thermostat.enforcer`. Do not change `thermostat/set_temps.py`
  or enforcement behavior without a safe occupied-schedule test window.
- Runtime and LaunchAgent logs belong in the ignored `logs/` directory. Do not
  add logs or timestamped snapshots to Git; scheduled scripts stage only the
  rolling JSON outputs they explicitly list.

## Architecture

**Data flow**: `.env` credentials → HTTP session → API auth → scrape (GraphQL or REST) → write JSON → git commit

**Key files:**
- `padsplit_scraper/scraper.py` — main Padsplit scraper; GraphQL queries, property/earnings/metrics collection
- `padsplit_scraper/occupancy.py` — presence from messages + tasks (`occupancy.json`). `kpis.vacancy_rooms` is listed-status, not presence.
- `thermostat/scraper.py` — thermostat portal scraper; HTTP session + fallback logic
- `padsplit_scraper/discord_notifier.py` — Discord bot alerts on error
- `padsplit_scraper/discord_slate_wake.py` — PadSplit Ops gateway listener; @mention in #ask-ai-agent or #communication-mgmt POSTs to Slate (notify-only)
- `slack_task_digest.py` — scheduled DFW weather and task digest, posts to Discord
- `padsplit_scraper/firestore_status_monitor.py` — Firestore integration
- `obsidian_daily_digest.py` — daily note generation from scraped data
- `docs/data/` — aggregated outputs: `latest.json`, `stats.json`, `occupancy.json`, `monthly_history.json`

**Error handling pattern**: scrapers use partial-success logic — if one property fails, continue and report via Discord rather than aborting entirely. `.env` is loaded from project root by all scripts.

## Environment

Copy `.env.example` (if present) or create `.env` at project root with:

```
PADSPLIT_EMAIL=
PADSPLIT_PASSWORD=
TCC_EMAIL=
TCC_PASSWORD=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-3-5-haiku-latest
MINIMAX_API_KEY=
MINIMAX_MODEL=MiniMax-M2.5
DISCORD_WEBHOOK_MESSAGES=
DISCORD_WEBHOOK_TASKS=   # Liaison Ops #ai-tasks-temp (task digest + Leanna temp alert)
DISCORD_WEBHOOK_URL=     # scrape / events failure alerts only
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
SLATE_ASK_WEBHOOK_URL=
SLATE_ASK_WEBHOOK_KEY=
OBSIDIAN_DAILY_NOTES_DIR=
```
