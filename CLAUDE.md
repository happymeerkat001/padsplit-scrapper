# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Multi-scraper data collection system for:
- **Padsplit** (`padsplit_scraper/`): rental property metrics (occupancy, earnings, flip rates) via GraphQL/REST APIs
- **Thermostat** (`thermostat/`): HVAC data from mytotalconnectcomfort.com
- Outputs versioned JSON to `padsplit_scraper/output/`, `thermostat/output/`, and `docs/data/`

## Running Scrapers

```bash
# Activate virtualenv first
source venv/bin/activate

# Run individual scrapers
python3 padsplit_scraper/scraper.py
python3 thermostat/scraper.py

# Scheduled runs (also commit/push output to git)
./run_morning.sh    # padsplit + thermostat
./run_afternoon.sh  # padsplit only
```

## Tests

```bash
python3 test_padsplit_scraper.py
python3 test_thermostat_scraper.py
python3 test_obsidian_daily_digest.py
```

No build step, no linter config — just direct Python execution.

## GitHub Actions

Active workflows live in `.github/workflows/`:

| Workflow | Schedule | Purpose |
|---|---|---|
| `scrape.yml` | Every 30 min | PadSplit scrape + publish to `docs/data/` |
| `thermostat.yml` | Daily | Thermostat scrape |
| `slack_digest.yml` | Daily 11:00 UTC | Webhook task digest (`slack_notifier.py`) |
| `slack_bot_digest.yml` | Daily 13:05 UTC | Bot digest thread for reply monitor |
| `slack_reply_monitor.yml` | Every 15 min | Process "Complete" Slack replies |
| `firestore_status_monitor.yml` | Every 15 min | Sync Firestore → PadSplit task status |
| `summarize_messages.yml` | On schedule | AI message summary via MiniMax |

## Architecture

**Data flow**: `.env` credentials → HTTP session → API auth → scrape (GraphQL or REST) → write JSON → git commit

**Key files:**
- `padsplit_scraper/scraper.py` — main Padsplit scraper; GraphQL queries, property/earnings/metrics collection
- `thermostat/scraper.py` — thermostat portal scraper; HTTP session + fallback logic
- `slack_notifier.py` — webhook task digest (weather, vacancy, tasks) via `SLACK_WEBHOOK_TASKS`
- `padsplit_scraper/slack_notifier.py` — Slack bot digest thread + Firestore AC filter alerts
- `padsplit_scraper/firestore_status_monitor.py` — sync Firestore task status → PadSplit
- `padsplit_scraper/slack_reply_monitor.py` — process "Complete" replies in digest thread
- `obsidian_daily_digest.py` — daily note generation from scraped data
- `docs/data/` — aggregated outputs: `latest.json`, `stats.json`, `monthly_history.json`

**Error handling pattern**: scrapers use partial-success logic — if one property fails, continue and report via Slack rather than aborting entirely. `.env` is loaded from project root by all scripts.

## Environment

Copy `.env.example` to `.env` at project root:

```
PADSPLIT_EMAIL=
PADSPLIT_PASSWORD=
TCC_EMAIL=
TCC_PASSWORD=
SLACK_WEBHOOK_URL=
SLACK_WEBHOOK_TASKS=
SLACK_WEBHOOK_MESSAGES=
SLACK_BOT_TOKEN=
SLACK_CHANNEL_ID=
MINIMAX_API_KEY=
FIREBASE_SERVICE_ACCOUNT_JSON=
OBSIDIAN_DAILY_NOTES_DIR=
```
