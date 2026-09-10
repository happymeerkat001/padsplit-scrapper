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
./run_seo_monthly.sh
```

Don-field Quo SMS blast (7:00am CT only, every day including weekends; no 7pm / evening send) is the Mac launchd job. Not live until merge + Mac pull + `python3 padsplit_scraper/field_mms.py --install-launchd` so Hour=7 Minute=0 is loaded. Skips when PadSplit host messages and Discord `#ai-tasks-temp` are both empty. GitHub Actions must not send it. Do not send from a box/VPS IP.

Primary send path is **Quo SMS** from `+14693732048` (A2P approved) via `POST https://api.quo.com/v1/messages`, one 1:1 per recipient (Don `+12147798338`, Dad `+19452413070`, and Ang GV `+14696267260` by default; no Joe; override with `FIELD_MMS_QUO_TO`). Quo’s `to` field is a list (batch / group is supported); this job still posts once per number so Don, Dad, and Ang GV do not share a new group thread. Fallbacks are Google Voice group SMS (Ang’s already-signed-in Mac Chrome) then the Messages.app chat named exactly `Don Field`. Prefer the Mac job. Quo HTTP does not need a residential IP the way Google Voice does; still do not run live sends from CI or a box/VPS by default. Never paste Quo keys, Google passwords, or message-body secrets into the repo.

Chrome fallback must already be signed into Google Voice as **mr.angli** / Voice 469. Install Playwright on the Mac once (`pip install playwright`; uses system Chrome, no Voice API key). Then set:

```env
# auto (default) = Quo first, then Google Voice, then Messages "Don Field"
# quo = Quo only (requires QUO_API_KEY; no fallback)
# google_voice = Voice only (no fallback)
# messages = Messages.app only
FIELD_MMS_TRANSPORT=auto
QUO_API_KEY=
# optional; defaults to +14693732048
# QUO_FROM_NUMBER=
# FIELD_MMS_QUO_FROM=
# optional comma/space list; defaults to Don + Dad + Ang GV (no Joe)
# FIELD_MMS_QUO_TO=+12147798338,+19452413070,+14696267260
FIELD_MMS_CHROME_USER_DATA_DIR=   # Chrome user-data-dir already signed into Voice
FIELD_MMS_CHROME_PROFILE_DIRECTORY=Default   # mr.angli Chrome profile directory
```

`FIELD_MMS_CHROME_USER_DATA_DIR` is the Chrome *root* (the folder that contains `Default` / `Profile 1`), not the profile folder itself. Prefer a dedicated copy of that profile so the daily Chrome window is not locked. If Quo fails, `auto` tries Google Voice; if Google shows a login wall / captcha / challenge, create (or reuse) a Messages group named exactly `Don Field`. `FIELD_MMS_TRANSPORT=quo` and `FIELD_MMS_TRANSPORT=google_voice` do not fall back.

Monthly SEO / vacancy advice (9:00am CT on the 1st) is Mac launchd only. Not live until merge + Mac pull + `python3 padsplit_scraper/seo_monthly.py --install-launchd`. Uses live partner rooms + occupancy (stale `docs/data/stats.json` only if live fetch fails, and the report says so). Instant Book = skip. 10% promo / $0 move-in already assumed on. No auto price changes. Optional `#ai-tasks-temp` lines @Joe only. GitHub Actions must not post. Chief Grok Bot cron fallback stays until this LaunchAgent is loaded.

Spanish Moss back-door lock-code automation (Sifely, v1) is Mac morning/afternoon only. Not live until Ang merges. Missing `SIFELY_API_KEY` is a Need-you no-op. GitHub Actions must not rotate locks or post Discord. Outbound Discord never includes lock-code digits.

Lockout auto-reply (`padsplit_scraper/lockout_reply.py`) detects member lockout messages and SENDS sequentially on the PadSplit member thread only when house and room are 100% known: door code(s) first (deadbolt tip + fail ladder); lockbox / room code + location only after the member later says the door still failed; member got-it / I’m in / code-worked after the door stage stops the ladder (no lockbox SEND, no Discord escalate). Default off until Mac `.env` sets `LOCKOUT_REPLY_ENABLE=1`. CI must not send. Spanish Moss back door uses the Sifely path (never a static Firestore/Tinghui back-door value). Discord `#ai-automations` drafts may say lockout detected / needs a tap / ask Joe, and never include codes or any digits.

Water-leak auto-reply (`padsplit_scraper/leak_reply.py`) detects an **active** member water emergency only: pipe burst / pipe leak, water main, flooding, or water leaking from wall or ceiling. Bare `leak`/`leaking`, slow leaks, drips, seepage, and toilet-only cases do **not** fire (no curb-key / whole-house shutoff). Flooding still fires even with a toilet mention. SENDS a 1:1 adaptation of Firestore `templates/shared` **t5** (`n5` Water leak announcement) on that PadSplit thread, with Quo call/text added (t5 is house-wide and has no Quo). Live t5 is preferred; baked t5 is the fallback. Canonical water-key YouTube: `https://youtube.com/shorts/SCryjPiyZcs`. It also posts a `WATER_KEY_ORDER` event to Discord `#ai-automations` (digit-free except the leaking property ship-to) for Cart: house label, full street address, Orbit ASIN / `water curb key`. No Amazon purchase in this scraper. No Spanish Moss address override. Host reminder blasts and historical “previous leak” chatter do not fire. Default off until Mac `.env` sets `LEAK_REPLY_ENABLE=1`. CI must not send. Discord posts never include lock codes. Nest is interim-handling leak replies until enable is live — tell Nest to stop duplicates when this is turned on.

Write Obsidian daily digest:

```bash
python3 obsidian_daily_digest.py
```

Codes dashboard (GitHub Pages `docs/codes.html`, password-gated): after login, Contact holds house AC filter date/size and dryer lint date/notes. The Rooms table holds room code, lockbox code/location/notes, and optional per-room AC filter size. Extra lockboxes are for non-room boxes (front door, gate). Saves merge into Firestore `property_codes/{slug}`. Occupancy JSON does not store codes or filter sizes.

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
python3 test_seo_monthly.py
python3 test_lock_codes.py
python3 test_lockout_reply.py
python3 test_leak_reply.py
python3 test_codes_dashboard.py
node test_codes_dashboard_render.mjs
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
