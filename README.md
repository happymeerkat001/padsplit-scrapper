# padsplit-scraper

Multi-scraper data collection repo for:

- PadSplit rental metrics via GraphQL/REST APIs
- Thermostat data and thermostat setpoint control via Total Connect Comfort
- SmartHome / Midea window-AC cloud control (`smarthome/`)
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
SMARTHOME_EMAIL=
SMARTHOME_PASSWORD=
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

Primary send path is **Quo SMS** from `+14693732048` (A2P approved) via `POST https://api.quo.com/v1/messages`, one group send (Don `+12147798338`, Dad `+19452413070`, and Ang GV `+14696267260` by default; no Joe; override with `FIELD_MMS_QUO_TO`). Quo’s `to` field is the full recipient list in a single POST so Don, Dad, and Ang GV share one conversation. Fallbacks are Google Voice group SMS (Ang’s already-signed-in Mac Chrome) then the Messages.app chat named exactly `Don Field`. Prefer the Mac job. Quo HTTP does not need a residential IP the way Google Voice does; still do not run live sends from CI or a box/VPS by default. Never paste Quo keys, Google passwords, or message-body secrets into the repo.

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

Monthly SEO / vacancy advice (9:00am CT on the 1st) is Mac launchd only. Not live until merge + Mac pull + `python3 padsplit_scraper/seo_monthly.py --install-launchd`. Uses live partner rooms + occupancy (stale `padsplit_scraper/output/stats.json` only if live fetch fails, and the report says so). Instant Book = skip. 10% promo / $0 move-in already assumed on. No auto price changes. Optional `#ai-tasks-temp` lines @Joe only. GitHub Actions must not post. Chief Grok Bot cron fallback stays until this LaunchAgent is loaded.

Spanish Moss back-door lock-code automation (Sifely, v1) is Mac morning/afternoon only. Not live until Ang merges. Missing `SIFELY_API_KEY` is a Need-you no-op. GitHub Actions must not rotate locks or post Discord. Outbound Discord never includes lock-code digits.

Lockout auto-reply (`padsplit_scraper/lockout_reply.py`) detects member lockout messages and SENDS sequentially on the PadSplit member thread only when house and room are 100% known: door code(s) first (deadbolt tip + fail ladder); lockbox / room code + location only after the member later says the door still failed; member got-it / I’m in / code-worked after the door stage stops the ladder (no lockbox SEND, no Discord escalate). Default off until Mac `.env` sets `LOCKOUT_REPLY_ENABLE=1`. CI must not send. Spanish Moss back door uses the Sifely path (never a static Firestore/Tinghui back-door value). Discord `#ai-automations` drafts may say lockout detected / needs a tap / ask Joe, and never include codes or any digits.

Water-leak auto-reply (`padsplit_scraper/leak_reply.py`) detects an **active** member water emergency only: pipe burst / pipe leak, water main, flooding, or water leaking from wall or ceiling. Bare `leak`/`leaking`, slow leaks, drips, seepage, and toilet-only cases do **not** fire (no curb-key / whole-house shutoff). Flooding still fires even with a toilet mention. SENDS a 1:1 adaptation of Firestore `templates/shared` **t5** (`n5` Water leak announcement) on that PadSplit thread, with Quo call/text added (t5 is house-wide and has no Quo). Live t5 is preferred; baked t5 is the fallback. Canonical water-key YouTube: `https://youtube.com/shorts/SCryjPiyZcs`. It also posts a `WATER_KEY_ORDER` event to Discord `#ai-automations` (digit-free except the leaking property ship-to) for Cart: house label, full street address, Orbit ASIN / `water curb key`. No Amazon purchase in this scraper. No Spanish Moss address override. Host reminder blasts and historical “previous leak” chatter do not fire. Default off until Mac `.env` sets `LEAK_REPLY_ENABLE=1`. CI must not send. Discord posts never include lock codes. Nest is interim-handling leak replies until enable is live — tell Nest to stop duplicates when this is turned on.

Leak alert planner (`padsplit_scraper/leak_alert.py`) plans one incident. `LEAK_ALERT_ENABLE` defaults off and CI must not send. Bland voice is planned for Don and Tom (`LEAK_ALERT_DON_E164`, `LEAK_ALERT_TOM_E164`). The Don, Tom, and Ang notice is one Quo group post, not a 1:1: `POST https://api.quo.com/v1/messages` with `to` set from `LEAK_ALERT_GROUP_E164S` (comma list of the group participants, excluding the from-line). If that list is unset, the plan records a skip. Quo v1 cannot post by conversation id. The send body is `content`, `from`, and `to` (max 10); there is no conversation id field ([Send a text message](https://www.quo.com/docs/mdx/api-reference/messages/send-a-text-message)). A group message is that `to` list ([changelog](https://www.quo.com/docs/changelog)). The same participant set is how `GET /v1/messages` loads the group thread ([List messages](https://www.quo.com/docs/mdx/api-reference/messages/list-messages)). Optional `LEAK_ALERT_GROUP_CONVERSATION_ID` is a dry-run check only: `GET /v1/conversations` confirms those participants match ([List conversations](https://www.quo.com/docs/mdx/api-reference/conversations/list-conversations)). Numbers are not written to the plan, state, logs, or Discord. State keys are `group:{incident}` and `tenant:{incident}:{hash}`. Tenant texts stay private 1:1 posts. No code defaults for any number.

Quo SMS for that plan lives in `padsplit_scraper/quo_sender.py`. It does not send on merge. Real HTTP requires every gate below. `LEAK_ALERT_DRY_RUN` defaults on (unset means dry-run). Header is `Authorization: <QUO_API_KEY>` with no Bearer, plus `Quo-Api-Version: 2026-03-30`. Body is `{content, from, to}` and is SMS only. 4xx is not retried. `0206400` (unapproved or unregistered) and `0204403` (daily cap) are marked failed and not retried. 429 and 5xx retry up to 3 attempts. Timeouts are not retried. Before POST the state key is set to `sending` and flushed with an atomic replace, so a crash cannot double-send (a dropped text is preferred over a second text). Phones are held in memory for the request and are not written to `logs/leak_alert_state.json` or logs.

Preview the Quo texts (no HTTP, sample house, fictional numbers masked to the last two digits). Writes `logs/leak_alert_preview.txt` (gitignored):

```bash
python3 -m padsplit_scraper.quo_sender --preview
```

That preview's `call_script` line is a hook (`preview_call_script`) and is not the Bland body. The exact Bland POST is below.

Go-live checklist (leave these off until Ang turns them on; CI must stay a no-op):

- `QUO_API_KEY` set on the Mac only (raw key, never logged, never committed)
- `QUO_FROM_NUMBER` set to the Quo from-line
- `LEAK_ALERT_GROUP_E164S` set to the group participants, excluding that from-line
- `PADSPLIT_ENABLE_ACTION_HOOKS=1` and `PADSPLIT_COLLECTION_ONLY=0`
- `LEAK_ALERT_ENABLE=1` (or `PADSPLIT_SEND_LEAK_ALERT=1`)
- `LEAK_ALERT_DRY_RUN=0` (this is the switch that leaves dry-run; unset stays dry-run)
- Confirm the preview copy, then run from the Mac launchd job, not GitHub Actions

Bland calls are placed by `padsplit_scraper/leak_alert_bland.py`. `LEAK_ALERT_DRY_RUN` defaults on. Nothing calls Bland or Quo until enable is on and dry-run is explicitly `0`.

Bland voice is Don and Tom only (`LEAK_ALERT_DON_E164`, `LEAK_ALERT_TOM_E164`). The live POST is `https://api.bland.ai/v1/calls` with `Authorization: Bearer <BLAND_API_KEY>` ([Send Call](https://docs.bland.ai/api-v1/post/calls)). `first_sentence` and `task` use the shared planner script. The voicemail `leave_message` is that script plus `Check the Quo group text for details.` `max_duration` is 2 minutes. The body also sends `summary_prompt`, `analysis_schema` (`confirmed_going` boolean, `eta_minutes` number), and `dispositions`. `metadata` is `incident_id` and `role` only. Phones are not logged. State key `voice:{incident}:{role}` dedupes. A failed place is not retried for 24 hours. The next Mac run polls `GET /v1/calls/{call_id}` when Firestore has no webhook record ([Call details](https://docs.bland.ai/api-v1/get/calls-id)).

The Mac has no public inbound URL. Bland posts the finished call to a Firebase HTTPS function (deployable, not deployed):

```bash
firebase functions:secrets:set BLAND_WEBHOOK_SECRET --project padsplit-scrapper
firebase deploy --only functions:leak-alert:leak_alert_bland --project padsplit-scrapper
```

URL shape: `https://us-central1-padsplit-scrapper.cloudfunctions.net/leak_alert_bland` (set this as `LEAK_ALERT_BLAND_WEBHOOK_URL`). Confirm tool path: the same URL plus `/confirm`. Bland signs the raw body with HMAC-SHA256 and sends the hex digest in `X-Webhook-Signature` ([Webhook signing](https://docs.bland.ai/tutorials/webhook-signing)). Create the secret in the Bland Dev Portal: Account Settings → Keys → Replace Secret (shown once). Put that value in `BLAND_WEBHOOK_SECRET` on the function and on the Mac. The per-call webhook is the API `webhook` field; the dashboard equivalent is Send Call → Advanced → webhook ([Post call webhooks](https://docs.bland.ai/tutorials/post-call-webhooks)). The function checks the signature with a constant-time compare, rejects timestamps older than 24 hours when Bland sends them, and rejects any `call_id` or `metadata.incident_id` that is not in `leak_alert_pending`. It writes `leak_alert_calls/{incident}/roles/{role}` (outcome, confirmed, eta_minutes, short summary, call_id; no phones and no full transcript) and enqueues `leak_alert_result_queue/{call_id}`. Firestore rules deny client access; only the admin SDK writes. Do not deploy from CI.

`confirm_dispatch` is an inline custom tool on the call ([Create a Custom Tool](https://docs.bland.ai/api-v1/post/tools)). Bland can POST it mid-call to the `/confirm` path. Tool calls are not HMAC-signed; the tool sends `Authorization: Bearer <BLAND_WEBHOOK_SECRET>`, and the function compares that header in constant time. The tool is attached only when the webhook URL and secret are both set. You do not have to create it in the Bland dashboard.

The call-result line is separate (`padsplit_scraper/leak_alert_bland.py`, `post_group_result_line`): `Don: going, ETA 20m`, `Don: not going`, `Tom: voicemail`, `Tom: no answer`. It needs `LEAK_ALERT_RESULT_POST_ENABLE` (default off) and dry-run off, and it is deduped per `call_id`. Dry-run appends the line with no numbers to `logs/leak_alert_result.jsonl`. State key for the group notice is `group:{incident}`.

Preview the exact Bland body and the result lines without calling anyone (fake numbers, masked to the last two digits):

```bash
python -m padsplit_scraper.leak_alert_bland --preview
```

That writes `logs/leak_alert_bland_preview.txt`.

Env:

```
LEAK_ALERT_ENABLE=                  # default off. Alias PADSPLIT_SEND_LEAK_ALERT
LEAK_ALERT_DRY_RUN=                 # default on. Set 0 for live Bland/Quo HTTP
LEAK_ALERT_DON_E164=
LEAK_ALERT_TOM_E164=
LEAK_ALERT_GROUP_E164S=
BLAND_API_KEY=
BLAND_WEBHOOK_SECRET=
LEAK_ALERT_BLAND_WEBHOOK_URL=
LEAK_ALERT_RESULT_POST_ENABLE=      # default off
# LEAK_ALERT_BLAND_CITATION_SCHEMA_ID=  # optional enterprise citation schema
```

Write Obsidian daily digest:

```bash
python3 obsidian_daily_digest.py
```

Codes dashboard (GitHub Pages `docs/codes.html`, password-gated): after login, Contact holds house AC filter date/size and dryer lint date/notes. The Rooms table holds room code, lockbox code/location/notes, and optional per-room AC filter size. Extra lockboxes are for non-room boxes (front door, gate). Saves merge into Firestore `property_codes/{slug}`. History (top of page) or Previous (next to Save) opens dated snapshots in Firestore (`code_versions`, 90 days) so a bad save can be restored. Older pages past the first 50. Occupancy JSON does not store codes or filter sizes. History is never written to disk or git.

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
python3 test_smarthome_watcher.py
python3 test_smarthome_cloud.py
python3 test_smarthome_clocks.py
python3 test_smarthome_intent.py
python3 test_smarthome_cli.py
python3 test_smarthome_identity.py
python3 test_obsidian_daily_digest.py
python3 test_field_mms.py
python3 test_seo_monthly.py
python3 test_lock_codes.py
python3 test_lockout_reply.py
python3 test_leak_reply.py
python3 test_leak_alert.py
python3 test_quo_sender.py
python3 test_leak_alert_bland.py
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

## SmartHome window ACs

Cloud control of Midea/SmartHome window units. The Mac persists one client identity under gitignored `logs/` and reuses it. Cloud error 65027 starts a one-hour cooldown.

```bash
source venv/bin/activate
pip install -r smarthome/requirements.txt
python3 -m smarthome list
python3 -m smarthome set "window unit name" 72
python3 -m smarthome off "window unit name"
python3 -m smarthome status
python3 -m smarthome.watcher
```

Watcher: hourly (`StartInterval` 3600). Daytime floor is 74°F (a higher setpoint is left alone). Off 1:00–5:59, back on at 6:00. Discord status digest at 06:00 / 14:00 / 20:00, once per clock hour. Posts go through the Discord bot (`DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID`), the same PadSplit Ops channel used by `post_discord_message`.

```bash
python3 -m smarthome.watcher write-plist
launchctl load ~/Library/LaunchAgents/com.padsplit.smarthome.watcher.plist
```

Label: `com.padsplit.smarthome.watcher`. Logs: `logs/smarthome-watcher.log`.

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
