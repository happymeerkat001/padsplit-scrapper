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

## Architecture

**Data flow**: `.env` credentials → HTTP session → API auth → scrape (GraphQL or REST) → write JSON → git commit

**Key files:**
- `padsplit_scraper/scraper.py` — main Padsplit scraper; GraphQL queries, property/earnings/metrics collection
- `thermostat/scraper.py` — thermostat portal scraper; HTTP session + fallback logic
- `padsplit_scraper/slack_notifier.py` — Slack webhook alerts on error
- `padsplit_scraper/firestore_status_monitor.py` — Firestore integration
- `obsidian_daily_digest.py` — daily note generation from scraped data
- `docs/data/` — aggregated outputs: `latest.json`, `stats.json`, `monthly_history.json`

**Error handling pattern**: scrapers use partial-success logic — if one property fails, continue and report via Slack rather than aborting entirely. `.env` is loaded from project root by all scripts.

## Environment

Copy `.env.example` (if present) or create `.env` at project root with:

```
PADSPLIT_EMAIL=
PADSPLIT_PASSWORD=
TCC_EMAIL=
TCC_PASSWORD=
ANTHROPIC_API_KEY=
SLACK_WEBHOOK_URL=
OBSIDIAN_DAILY_NOTES_DIR=
```
