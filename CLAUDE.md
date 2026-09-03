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

# Scheduled runs (also commit/push rolling output to git)
./run_morning.sh      # padsplit + thermostat
./run_afternoon.sh    # padsplit only
./run_field_mms.sh    # Don-field group MMS (6am / 7pm CT; skip if both sources empty)
./run_seo_monthly.sh  # monthly SEO / vacancy advice (1st 9:00am CT; CI must not post)
# Spanish Moss back-door lock codes run from morning/afternoon (Mac only; CI no-op)
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
python3 test_field_mms.py
python3 test_seo_monthly.py
python3 test_lock_codes.py
```

No build step or linter configuration; tests use direct Python execution.

## Operations

- Morning and afternoon scraper runs are scheduled by launchd as
  `com.padsplit.scraper.morning` and `com.padsplit.scraper.afternoon`; their
  plist files live in `~/Library/LaunchAgents/` and call `run_morning.sh` and
  `run_afternoon.sh`.
- Don-field group MMS is launchd-managed by `com.padsplit.field-mms`
  (`launchd/com.padsplit.field-mms.plist`) at 6:00am and 7:00pm CT daily,
  including weekends. After merge + Mac pull: `python3 padsplit_scraper/field_mms.py --install-launchd`.
  First send is the next 6am/7pm CT slot. Group MMS only (never a 1:1). CI must not send.
- Monthly SEO / vacancy advice is launchd-managed by `com.padsplit.seo-monthly`
  (`launchd/com.padsplit.seo-monthly.plist`) at 9:00am CT on the 1st.
  After merge + Mac pull: `python3 padsplit_scraper/seo_monthly.py --install-launchd`.
  Prefers live partner rooms + occupancy; stale `stats.json` is fallback only and is
  called out. Instant Book = skip. 10% promo / $0 move-in already assumed on.
  No auto price changes. Optional Discord #ai-tasks-temp @Joe only (never Cindy;
  never as Ang). CI must not post. Chief Grok Bot cron stays until this agent is live.
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
- `padsplit_scraper/field_mms.py` — 6am/7pm CT Don-field group MMS (PadSplit host inbox + Discord #ai-tasks-temp)
- `padsplit_scraper/seo_monthly.py` — 1st 9:00am CT SEO / vacancy advice pack (live rooms + occupancy; Joe-only Discord dry-run in CI)
- `padsplit_scraper/lock_codes.py` — Spanish Moss back-door Sifely lock-code v1 (Mac morning/afternoon; CI must not rotate or post)
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
DISCORD_WEBHOOK_NEW_TENANTS= # #new-tenants pack (Joe only; CI must not post)
DISCORD_JOE_USER_ID=     # Discord snowflake for @Joe on #new-tenants (never @ Cindy)
DISCORD_BOT_TOKEN=
DISCORD_CHANNEL_ID=
SIFELY_API_KEY=          # raw sk- key, no Bearer; missing = Need-you no-op
SIFELY_LOCK_ID=          # optional Spanish Moss back-door lock id
SIFELY_KEYBOARD_PWD_ID=  # optional tenant passcode id
OBSIDIAN_DAILY_NOTES_DIR=
```
