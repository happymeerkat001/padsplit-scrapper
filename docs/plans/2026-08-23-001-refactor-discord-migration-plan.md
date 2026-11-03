# refactor: Migrate Slack notifications to Discord

**Created:** 2026-08-23
**Type:** refactor

---

## Summary

Every outbound notification in this repo currently posts to Slack (webhooks and Slack Web API). Replace all of it with Discord equivalents — simple webhook posts for one-way alerts, and a Discord bot for the one flow that also reads messages back (task-completion reply detection). This is a full cutover, not a dual-send: Slack env vars, code paths, and dependencies on `slack.com` are removed once the Discord path is in place.

---

## Problem Frame

Six code paths currently talk to Slack:

1. `message_summarizer.py` — `send_to_slack()`, posts via `SLACK_WEBHOOK_MESSAGES` (urllib).
2. `slack_task_digest.py` — `send_to_slack()`, posts via `SLACK_WEBHOOK_TASKS` (urllib).
3. `thermostat/schedule.py` — `_post_temp_alert()`, posts via `SLACK_WEBHOOK_URL` (requests).
4. `padsplit_scraper/slack_notifier.py` — `post_slack_message()`, posts via Slack Web API `chat.postMessage` using `SLACK_BOT_TOKEN` + `SLACK_CHANNEL_ID`. Returns the posted message's channel/ts, which it writes to `docs/data/slack_digest_meta.json`. Also imported directly by `message_drafter.py` for critical tenant-message alerts.
5. `padsplit_scraper/slack_reply_monitor.py` — reads replies posted under the digest message (`conversations.replies`, `SLACK_BOT_TOKEN`) using the meta file from #4, matches replies to tasks by address, and marks PadSplit tasks complete.
6. `message_drafter.py` — imports `post_slack_message` from #4 for critical alerts; no independent Slack logic of its own.

Paths 1–3 are pure one-way webhook posts (text only, no response needed). Paths 4–5 are a pair: #4 posts and records where it posted, #5 reads what came back in that spot. Discord webhooks can send messages but cannot read a channel back, so #4/#5 need a Discord bot (bot token + channel read permission) instead of a plain webhook.

## Scope Boundaries

**In scope:** replacing all Slack send/read calls with Discord equivalents; new `DISCORD_*` env vars; removing `SLACK_*` env vars and the `slack.com` dependency; updating `CLAUDE.md`'s documented env var list.

**Out of scope / not hardened:** the address-matching logic inside `slack_reply_monitor.py` (`_build_address_matcher`, `_extract_completed_task_ids`, street-abbreviation tables) carries over unchanged. It was already unreliable on Slack; this plan ports the transport, not the matching quality.

**Deferred to follow-up work:** richer Discord formatting (embeds, mentions-as-roles) — plain text messages matching current Slack content are sufficient for this migration.

---

## Key Technical Decisions

**KTD1 — Two notification mechanisms, matching the Slack split.**
- Simple alerts (message_summarizer, slack_task_digest, thermostat temp alert) become plain Discord webhook posts (`POST {DISCORD_WEBHOOK_URL}` with `{"content": "..."}`). No bot needed — mirrors the existing Slack webhook shape 1:1.
- The digest-post + reply-read pair (`slack_notifier.py` / `slack_reply_monitor.py`) becomes a Discord bot integration: `POST /channels/{channel_id}/messages` (Bot `Authorization` header) to post, `GET /channels/{channel_id}/messages?after={message_id}` to read what came back. Discord's REST API is used directly (no discord.py dependency) to match the existing "small script + `requests`/`urllib`" style used throughout the repo.

**KTD2 — No Discord thread creation.** Slack's version already used a flat "read replies after the digest message" model, not real threading logic beyond storing `thread_ts`. Discord's `after={message_id}` on the same channel reproduces that exact behavior without adding thread-creation calls. Simpler and matches current behavior.

**KTD3 — Env var naming.** Rename 1:1:
| Slack | Discord |
|---|---|
| `SLACK_WEBHOOK_MESSAGES` | `DISCORD_WEBHOOK_MESSAGES` |
| `SLACK_WEBHOOK_TASKS` | `DISCORD_WEBHOOK_TASKS` |
| `SLACK_WEBHOOK_URL` (thermostat) | `DISCORD_WEBHOOK_URL` |
| `SLACK_BOT_TOKEN` | `DISCORD_BOT_TOKEN` |
| `SLACK_CHANNEL_ID` | `DISCORD_CHANNEL_ID` |

**KTD4 — Full cutover, not dual-send.** "Instead go to Discord" reads as replace, not add. All `SLACK_*` reads and the `slack.com` call sites are deleted, not kept behind a flag.

**KTD5 — Shared Discord client module.** Create `padsplit_scraper/discord_notifier.py` mirroring today's `padsplit_scraper/slack_notifier.py` shape (`post_discord_message()`, digest formatting, meta-file write) so `message_drafter.py`'s import swap is a one-line change (`from padsplit_scraper.discord_notifier import post_discord_message`).

---

## Implementation Units

### U1. Discord webhook helper + simple-alert migration

**Goal:** Replace the three plain webhook posters with Discord webhook calls.

**Requirements:** Core ask — Slack outputs go to Discord instead.

**Dependencies:** None.

**Files:**
- `message_summarizer.py` (`send_to_slack` → `send_to_discord`, `SLACK_WEBHOOK_MESSAGES` → `DISCORD_WEBHOOK_MESSAGES`)
- `slack_task_digest.py` (`send_to_slack` → `send_to_discord`, `SLACK_WEBHOOK_TASKS` → `DISCORD_WEBHOOK_TASKS`)
- `thermostat/schedule.py` (`_post_temp_alert`, `SLACK_WEBHOOK_URL` → `DISCORD_WEBHOOK_URL`)
- `test_message_summarizer.py`

**Approach:** Discord webhooks accept `POST {webhook_url}` with JSON body `{"content": "<text up to 2000 chars>"}` and return `204 No Content` on success (vs Slack's `200 OK`+`"ok"` body). Update success/error handling to match: treat `204` (or `200`) as success, log status + body on non-2xx. Keep the same "skip silently if env var unset" behavior. Discord's 2000-char message limit is shorter than Slack's ~4000 — if any existing message could exceed 2000 chars (check `slack_task_digest.py`'s digest, which concatenates multiple task lines), truncate with a trailing marker rather than letting the POST fail.

**Patterns to follow:** Keep each file's existing urllib/requests choice (don't introduce a new HTTP dependency) — only change the URL shape, payload key (`text` → `content`), and status-code check.

**Test scenarios:**
- `test_message_summarizer.py::test_send_to_discord_success` — webhook URL set, mock returns 204, assert no exception and success log.
- `test_message_summarizer.py::test_send_to_discord_missing_env` — `DISCORD_WEBHOOK_MESSAGES` unset, assert skip (no HTTP call attempted).
- `test_message_summarizer.py::test_send_to_discord_http_error` — mock raises `HTTPError`/non-2xx, assert error is logged and function does not raise.
- `test_message_summarizer.py::test_send_to_discord_truncates_long_content` — content > 2000 chars, assert payload sent is truncated to the limit.

**Verification:** Existing message-summarizer and thermostat test suites pass; manual webhook post to a test Discord channel shows the message.

---

### U2. Discord bot notifier module (digest post + meta)

**Goal:** Replace `padsplit_scraper/slack_notifier.py` with a Discord-bot-backed equivalent that posts the digest and records where it posted.

**Requirements:** Digest post must still trigger from the same call sites (`message_drafter.py`'s critical alert, the digest cron path) and still write a meta file the reply-reader can consume.

**Dependencies:** None (independent of U1).

**Files:**
- `padsplit_scraper/discord_notifier.py` (new)
- `padsplit_scraper/slack_notifier.py` (delete)
- `message_drafter.py` (import swap: `post_slack_message` → `post_discord_message`)
- `padsplit_scraper/test_reply_address_parser.py` (check for slack_notifier references)

**Approach:** `post_discord_message(text, *, token=None, channel=None)` mirrors the current signature. Calls `POST https://discord.com/api/v10/channels/{channel_id}/messages` with header `Authorization: Bot {DISCORD_BOT_TOKEN}` and body `{"content": text}`. Response JSON's `"id"` field is the message ID (Discord's equivalent of Slack's `ts`). Write `docs/data/discord_digest_meta.json` with `{"channel": channel_id, "message_id": message_id}` — new filename to avoid ambiguity with the old Slack meta format, and `slack_reply_monitor.py`'s replacement (U3) reads this new file, not the old one.

`firebase_admin`/Firestore logic in the current `slack_notifier.py` (`_init_firestore_app`, `_load_ac_filter_dates`) is unrelated to Slack and carries over unchanged into `discord_notifier.py`.

**Patterns to follow:** `padsplit_scraper/slack_notifier.py`'s existing structure (digest formatting, `_load_latest_payload`, `main()` entrypoint) — port structure, swap only the HTTP client and env vars.

**Test scenarios:**
- `post_discord_message` with valid token/channel and mocked 200 response containing an `id` — assert returned dict includes that id.
- `post_discord_message` with missing `DISCORD_BOT_TOKEN` or `DISCORD_CHANNEL_ID` — assert `RuntimeError` (matches current Slack behavior).
- Digest `main()` run against a fixture `latest.json` — assert `docs/data/discord_digest_meta.json` is written with the posted channel + message_id.
- `message_drafter.py`'s critical-alert path — assert it calls `post_discord_message` (import swap verified, not just present in source).

**Verification:** Manual run posts a digest message to a test Discord channel and `docs/data/discord_digest_meta.json` is created with a real message id.

---

### U3. Discord reply monitor (task-completion detection)

**Goal:** Replace `padsplit_scraper/slack_reply_monitor.py`'s Slack thread-reply fetch with a Discord channel-message fetch, keeping the existing address-matching and task-update logic intact.

**Requirements:** Preserve current (imperfect) task-completion-by-reply behavior on the new transport; do not attempt to fix matching accuracy as part of this migration.

**Dependencies:** U2 (reads the meta file U2 writes).

**Files:**
- `padsplit_scraper/discord_reply_monitor.py` (new, ports from `padsplit_scraper/slack_reply_monitor.py`)
- `padsplit_scraper/slack_reply_monitor.py` (delete)

**Approach:** Replace `_fetch_replies(token, channel, thread_ts)` with a Discord fetch: `GET https://discord.com/api/v10/channels/{channel_id}/messages?after={message_id}` with header `Authorization: Bot {DISCORD_BOT_TOKEN}`. Discord returns messages newest-first; reverse before processing so replies are handled in chronological order (matches current oldest-first assumption in the Slack version). Each Discord message object's `content` field maps to Slack's `text`, and `id` maps to `ts` for the `processed_ts` dedup set. `_build_address_matcher`, `_extract_completed_task_ids`, `_tokenize`, and the street-abbreviation tables are copied over unmodified — this unit does not touch matching logic. Read `docs/data/discord_digest_meta.json` (from U2) instead of `docs/data/slack_digest_meta.json`; keep `docs/data/processed_replies.json` as-is (transport-agnostic dedup store, just tracks message IDs).

**Patterns to follow:** `padsplit_scraper/slack_reply_monitor.py` end-to-end structure — this is a transport swap inside an otherwise-unchanged file.

**Test scenarios:**
- Fetch with a mocked Discord response containing 3 messages after `message_id` — assert they're processed oldest-first.
- A message whose `content` matches an address + "Complete" — assert `update_task_status` is called with the right task id (existing matcher behavior, exercised through the new fetch path).
- A message `id` already in `processed_replies.json` — assert it's skipped (dedup still works with Discord IDs).
- Missing `discord_digest_meta.json` — assert `RuntimeError` (matches current missing-meta behavior).
- Missing `DISCORD_BOT_TOKEN` — assert `RuntimeError`.

**Verification:** Manual run against a test Discord channel with a reply posted after the digest message shows the matching task marked complete in PadSplit (or a dry-run log if no real task available).

---

### U4. Env var + docs cleanup

**Goal:** Remove all `SLACK_*` references and document the new `DISCORD_*` vars.

**Requirements:** Nothing left pointing at `slack.com`; `CLAUDE.md` env var block reflects reality.

**Dependencies:** U1, U2, U3.

**Files:**
- `CLAUDE.md` (env var list in the Environment section)
- `.env` (user's local file — not committed; note in verification, not a file this unit edits directly)
- Any launchd plist / `run_morning.sh` / `run_afternoon.sh` references to `SLACK_*` (verify none exist — grep confirmed no direct references in these scripts, but double-check after U1–U3 land)

**Approach:** Replace the `SLACK_EMAIL`-adjacent `SLACK_WEBHOOK_URL` line and friends in `CLAUDE.md`'s Environment section with `DISCORD_WEBHOOK_MESSAGES`, `DISCORD_WEBHOOK_TASKS`, `DISCORD_WEBHOOK_URL`, `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`. Grep the repo for any remaining `slack.com`, `SLACK_`, or `Slack` string after U1–U3 to confirm nothing was missed (test files and this plan itself excluded).

**Test scenarios:**
- `Test expectation: none -- documentation-only unit, no behavioral change.`

**Verification:** `grep -ri "slack" --include="*.py"` (excluding tests that assert absence) returns no hits in production code paths; `CLAUDE.md` env block lists only `DISCORD_*`.

---

## Risks & Dependencies

- **Discord bot setup is external to this repo.** A Discord bot application must be created, invited to the target server with `View Channel` + `Read Message History` + `Send Messages` permissions, and its token placed in `.env` as `DISCORD_BOT_TOKEN`. This is a manual one-time setup step outside code changes — flag it as a prerequisite before U2/U3 can be verified end-to-end.
- **2000-char message limit** (Discord) vs Slack's larger limit — U1 must handle truncation for the task digest, which is the most likely message to run long.
- **Rate limits** — Discord's REST API has per-route rate limits; existing single-message-per-run usage patterns are well under any threshold, so no backoff logic is needed.
