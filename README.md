# padsplit-scraper

Multi-scraper data collection repo for:

- PadSplit rental metrics via GraphQL/REST APIs
- Thermostat data and thermostat setpoint control via Total Connect Comfort
- Versioned JSON outputs in `padsplit_scraper/output/`, `thermostat/output/`, and `docs/data/`

## Setup

```bash
source venv/bin/activate
```

Create `.env` at repo root (see `.env.example`):

```env
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
python3 test_thermostat_schedule.py
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

## Thermostat Schedule

Install one-house schedule:

```bash
python3 thermostat/schedule.py install \
  --target "6623 Leanna" \
  --slot 7:00am 76 68 \
  --slot 8:30am 77 68 \
  --slot 6:00pm 78 68
```

Re-running `install` for same target replaces that target's existing schedule.

Install one-house schedule with more slots:

```bash
python3 thermostat/schedule.py install \
  --target "1404 PIONEER" \
  --slot 8:00am 76 62 \
  --slot 2:00pm 77 62 \
  --slot 5:30pm 77 62 \
  --slot 7:00pm 76 62
```

Install same schedule for all houses:

```bash
python3 thermostat/schedule.py install \
  --all \
  --slot 7:00am 76 68 \
  --slot 8:30am 77 68
```

Install command shape:

```bash
python3 thermostat/schedule.py install \
  --target "HOUSE NAME" \
  --slot TIME COOL HEAT \
  --slot TIME COOL HEAT
```

Example meanings:

- `--slot 6:00am 76 68` means cool `76`, heat `68` at `6:00 AM`
- `--slot 7:00am 75 68` means cool `75`, heat `68` at `7:00 AM`
- `--slot 6:30pm 78 68` means cool `78`, heat `68` at `6:30 PM`

Remove schedule automation:

```bash
python3 thermostat/schedule.py uninstall --target "10235 Ridge Oak"
python3 thermostat/schedule.py uninstall --all
```

Remove schedule automation and resume TCC schedule:

```bash
python3 thermostat/schedule.py uninstall --target "6623 Leanna" --resume-schedule
python3 thermostat/schedule.py uninstall --all --resume-schedule
```

Uninstall command shape:

```bash
python3 thermostat/schedule.py uninstall --target "HOUSE NAME"
python3 thermostat/schedule.py uninstall --target "HOUSE NAME" --resume-schedule
python3 thermostat/schedule.py uninstall --all
python3 thermostat/schedule.py uninstall --all --resume-schedule
```

Meaning:

- `uninstall --target ...` removes LaunchAgent schedule automation only
- `uninstall --target ... --resume-schedule` removes automation and tells TCC to resume built-in schedule for that house
- `uninstall --all` removes all schedule-managed LaunchAgents only
- `uninstall --all --resume-schedule` removes all schedule-managed LaunchAgents and tells TCC to resume built-in schedule for all houses

Show installed thermostat schedules (not status):

```bash
python3 thermostat/schedule.py status
```

`status` shows configured schedule time, cool, heat, target, and LaunchAgent label from installed plist files.

Time format:

- Use 12-hour input with `am` or `pm`, such as `7am`, `7:00am`, or `6:30pm`.
- `19:30`, `1700`, and `7:00` are rejected.

Generated files:

- LaunchAgents are written under `~/Library/LaunchAgents/` as `com.padsplit.thermostat.<target>.<hhmm>.plist`.
- Slot logs are written to `thermostat/schedule-<target>-<hhmm>.stdout.log` and `.stderr.log`.

Current limitation:

- `python3 thermostat/schedule.py status` shows only schedule-managed thermostat LaunchAgents created by `thermostat/schedule.py`.
- It does not show the older legacy `com.padsplit.thermostat-set-temps.plist`.
