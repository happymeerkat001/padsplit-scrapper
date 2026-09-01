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
DISCORD_WEBHOOK_URL=
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
./run_field_mms.sh
```

Don-field group MMS (6:00am and 7:00pm CT, every day) is Mac launchd only. Not live until merge + Mac pull + `python3 padsplit_scraper/field_mms.py --install-launchd`. Skips when PadSplit host messages and Discord `#ai-tasks-temp` are both empty. GitHub Actions must not send it.

Write Obsidian daily digest:

```bash
python3 obsidian_daily_digest.py
```

Generate message drafts:

```bash
# Dry run — template matching only, no API calls
python3 message_drafter.py --template-only --stdout

# Live run — calls Claude API, writes drafts.json
python3 message_drafter.py --stdout
```

Run tests:

```bash
python3 test_padsplit_scraper.py
python3 test_thermostat_scraper.py
python3 test_thermostat_set_temps.py
python3 test_thermostat_schedule.py
python3 test_obsidian_daily_digest.py
python3 test_field_mms.py
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
logs/
```

All runtime and LaunchAgent logs are ignored under `logs/`.

Unload launch agent:

```bash
launchctl unload ~/Library/LaunchAgents/com.padsplit.thermostat-set-temps.plist
```

## Thermostat Schedule

Install one-house schedule:

```bash
python3 thermostat/schedule.py install \
  --target "6623 Leanna" \
  --slot 7:00am 74 68 \
  --slot 8:30am 75 68 \
  --slot 6:00pm 74 68
```

Re-running `install` for same target replaces that target's existing schedule.

Install one-house schedule with more slots:

```bash
python3 thermostat/schedule.py install \
  --target "3414 pebbleshores" \
  --slot 8:00am 74 62 \
  --slot 2:00pm 75 62 \
  --slot 5:30pm 75 62 \
  --slot 7:00pm 74 62
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

Show the full configured schedule for one house:

```bash
python3 thermostat/schedule.py status --target "3406 Green Hill"
```

This prints every configured slot time plus cool/heat values for that target.

Time format:

- Use 12-hour input with `am` or `pm`, such as `7am`, `7:00am`, or `6:30pm`.
- `19:30`, `1700`, and `7:00` are rejected.

Generated files:

- LaunchAgents are written under `~/Library/LaunchAgents/` as `com.padsplit.thermostat.<target>.<hhmm>.plist`.
- Slot logs are written to `logs/schedule-<target>-<hhmm>.stdout.log` and `.stderr.log`.

Current limitation:

- ``python3 thermostat/schedule.py status`` shows only schedule-managed thermostat LaunchAgents created by `thermostat/schedule.py`.
- ``python3 thermostat/schedule.py status --target "6623 Leanna"`` shows the full configured schedule for that house from `thermostat/config/schedules.json`.
- It does not show the older legacy `com.padsplit.thermostat-set-temps.plist`.
