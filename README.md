# padsplit-scraper

Multi-scraper data collection repo for:

- PadSplit rental metrics via GraphQL/REST APIs
- Thermostat data and thermostat setpoint control via Total Connect Comfort
- Versioned JSON outputs in `padsplit_scraper/output/`, `thermostat/output/`, and `docs/data/`

## Setup

```bash
source venv/bin/activate
```

Create `.env` at repo root with:

```env
PADSPLIT_EMAIL=
PADSPLIT_PASSWORD=
TCC_EMAIL=
TCC_PASSWORD=
ANTHROPIC_API_KEY=
SLACK_WEBHOOK_URL=
OBSIDIAN_DAILY_NOTES_DIR=
```

## Commands

Run individual scrapers:

```bash
python3 padsplit_scraper/scraper.py
python3 thermostat/scraper.py
```

Run scheduled scripts:

```bash
./run_morning.sh
./run_afternoon.sh
```

Write Obsidian daily digest:

```bash
python3 obsidian_daily_digest.py
```

Run tests:

```bash
python3 test_padsplit_scraper.py
python3 test_thermostat_scraper.py
python3 test_obsidian_daily_digest.py
```

## Thermostat Set Temps

Manual test when TCC reachable:

```bash
source venv/bin/activate
python3 thermostat/set_temps.py --target "6623 Leanna"
```

Default targets:

- `cool=75`
- `heat=63`

Change targets:

```bash
python3 thermostat/set_temps.py --cool 78 --heat 60 --target "6623 Leanna"
python3 thermostat/set_temps.py --location-id 7712909
python3 thermostat/set_temps.py --all
python3 thermostat/set_temps.py --resume-schedule --target "6623 Leanna" --stop-launchagent
python3 thermostat/set_temps.py --resume-schedule --all
```

Resume note:

- `--resume-schedule --target "6623 Leanna"` clears the hold for that house.
- Add `--stop-launchagent` when resuming Leanna, or the 30-minute LaunchAgent will put it back on hold on its next run.

Logs:

```text
thermostat/set_temps.stderr.log
```

Unload launch agent:

```bash
launchctl unload ~/Library/LaunchAgents/com.padsplit.thermostat-set-temps.plist
```
