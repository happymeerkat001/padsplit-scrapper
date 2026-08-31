# Guarantee Address and Room Number in Slack Message Summaries

Created: 2026-07-26

## Problem

User: "for the slack summaries, I want the address and room number every time."

`message_summarizer.py` posts urgent-tenant-message summaries to Slack via `SLACK_WEBHOOK_MESSAGES`. Room number is currently requested via a soft prompt instruction to MiniMax ("Also include the tenant's room number... in each summary") and address isn't requested at all. Prompt compliance is not a guarantee — the AI can omit either field, especially under retries/timeouts or verbose input.

`slack_task_digest.py` (the sibling script posting to `SLACK_WEBHOOK_TASKS`) already solves this correctly for tasks: it pulls `property_address`/`room_number` from structured dict fields and formats them in Python (`collect_tasks`, `format_message` in `slack_task_digest.py:?`), never trusting the AI. That's the pattern this plan applies to `message_summarizer.py`.

## Scope

- In scope: `message_summarizer.py` only. `slack_task_digest.py` already guarantees address/room deterministically — no changes needed there.
- Out of scope: changing the MiniMax model, changing `SLACK_WEBHOOK_MESSAGES` delivery mechanics, changing scraper data collection (address/room data already exists in `padsplit_scraper/scraper.py`'s `baseChatListFields` GraphQL fragment — `occupancy.room.roomNumber`, `property.address.{street1,street2,zip,city.name,city.state.name}`).

## Approach

Stop relying on prompt wording alone. Change what MiniMax is asked to return (structured JSON keyed by chat id) and have Python deterministically attach address + room number from the original scraped data before sending to Slack — matching the `slack_task_digest.py` precedent.

1. MiniMax is asked to return **only** a JSON array identifying which chats are urgent and why: `[{"chat_id": "...", "summary": "...", "sent_at": "..."}]`. No formatting/field-inclusion burden is placed on the AI beyond picking urgent chats and writing a short reason.
2. Python builds a `chat_id -> chat` lookup from `data["messages"]` (each item already matches `baseChatListFields`).
3. Python renders the final Slack text itself: for each urgent item, look up the chat, extract room number and address deterministically (with `"Unknown"` fallbacks matching `slack_task_digest.py`'s existing convention), and format a line that always contains address + room + the AI's summary/timestamp.
4. If MiniMax's response isn't valid JSON, retry once with a reinforced "respond with JSON only" instruction. If it still fails, fall back to sending the raw AI text prefixed with a visible `⚠️ Formatting fallback` marker — partial success (deliver something, don't crash), consistent with the repo's existing partial-success error-handling convention (see `CLAUDE.md`).

## Implementation Units

### U1 — Structured field extraction helpers
**Files:** `message_summarizer.py`

Add two small functions:
- `format_room(chat: dict) -> str` — reads `chat["occupancy"]["room"]["roomNumber"]`, returns `str(value)` or `"Unknown"` if missing/None at any level.
- `format_address(chat: dict) -> str` — reads `chat["property"]["address"]`, builds `"{street1}, {city.name}, {city.state.name}"`, using `"Unknown"` for any missing piece (mirrors the short-form convention already used in `slack_task_digest.py`'s `collect_tasks`).

Both must tolerate missing/partial dicts without raising (`.get()` chains, no bare indexing).

**Test scenarios:**
- Full chat node (all fields present) → correct room string and full address string.
- Chat node missing `occupancy` entirely → room = `"Unknown"`.
- Chat node with `occupancy.room.roomNumber = None` → room = `"Unknown"`.
- Chat node missing `property.address.city.state` → address falls back to `"Unknown"` for just that segment, not the whole string.
- Chat node missing `property` entirely → address = `"Unknown"`.

### U2 — Structured MiniMax prompt + response parser
**Files:** `message_summarizer.py`

- Replace `PROMPT` with an instruction that asks MiniMax to return **only** a JSON array of `{"chat_id": str, "summary": str, "sent_at": str}` for urgent chats — no prose, no markdown fences, no field formatting responsibility.
- Add `parse_urgent_items(raw: str) -> list[dict]`: strips common wrapping (code fences like ` ```json ... ``` `), `json.loads`s the result, validates it's a list of dicts each containing `chat_id` and `summary` (drop/skip entries missing `chat_id`). Raise a `ValueError` on unparseable input (empty list is valid — "no urgent messages").

**Test scenarios:**
- Clean JSON array → parses to matching list of dicts.
- JSON wrapped in ` ```json ... ``` ` fences → still parses correctly.
- Empty array `[]` → returns `[]` (no urgent messages is valid, not an error).
- Entries missing `chat_id` → dropped, not raising.
- Non-JSON garbage text → raises `ValueError`.

### U3 — Deterministic Slack message renderer
**Files:** `message_summarizer.py`

Add `render_summary(urgent_items: list[dict], messages_by_id: dict) -> str`:
- For each item, look up `messages_by_id[item["chat_id"]]`; skip with a print warning if the id isn't found in the source data (stale/hallucinated id).
- Build one line per item: address + room number (via U1 helpers) + AI-provided `summary`/`sent_at`, formatted so address and room are always visible regardless of what the AI wrote.
- Return `"No urgent tenant messages."` when `urgent_items` is empty (mirrors `slack_task_digest.py`'s empty-state message style).

**Test scenarios:**
- Two urgent items across two different properties → both lines present, each with its own address+room, correctly attributed (not swapped).
- Urgent item referencing a `chat_id` not present in `messages_by_id` → skipped, other valid items still rendered, no crash.
- Empty `urgent_items` list → returns the no-urgent-messages message.
- Item whose chat has missing room/address data → line still renders with `"Unknown"` in place, never a blank/absent field.

### U4 — Wire main() with fallback path
**Files:** `message_summarizer.py`

- Build `messages_by_id` from `data["messages"]` (keyed by `id`).
- Call MiniMax with the new structured prompt; attempt `parse_urgent_items`.
- On `ValueError`: retry once, appending an explicit "Your last response was not valid JSON. Respond with ONLY a JSON array, no other text." reinforcement to the prompt.
- On second failure: send the raw AI text to Slack prefixed with `"⚠️ Formatting fallback (AI response was not valid JSON):\n\n"` instead of crashing — matches the repo's partial-success philosophy (continue and report, don't abort).
- On success: call `render_summary(...)` and send that to Slack instead of the raw AI text.

**Test scenarios:**
- Happy path: valid JSON on first try → `render_summary` output sent to Slack, contains address+room for every item.
- First response invalid JSON, retry succeeds → second MiniMax call made with reinforced prompt, final message uses parsed/rendered output.
- Both attempts invalid JSON → raw text sent with the fallback warning prefix, `send_to_slack` still called (no `sys.exit`).
- No urgent messages → Slack receives the "No urgent tenant messages." text, not silently skipped.

## Test File

New file: `test_message_summarizer.py` (repo root, matching existing naming convention alongside `test_padsplit_scraper.py`, `test_thermostat_scraper.py`). Covers all test scenarios listed under U1-U4. Mock `call_minimax` (monkeypatch or a stub) rather than hitting the real MiniMax API — no network calls in tests.

## Risks

- **AI still ignores the "JSON only" instruction on both attempts.** Mitigated by the fallback path in U4 — degrades to old raw-text behavior with a visible warning rather than losing the notification entirely. Address/room guarantee only holds on the JSON path; the fallback path is an explicit, visible exception, not a silent one.
- **Chat `id` drift between the AI's response and `messages_by_id`** (e.g., AI slightly mangles an id string). Handled by the skip-with-warning behavior in U3 rather than crashing the whole summary.

## Explicitly Out of Scope

- `slack_task_digest.py` — already guarantees address/room; untouched.
- Any change to `padsplit_scraper/scraper.py`'s data collection — address/room data already flows through unchanged.
- Any change to the MiniMax retry/backoff logic in `call_minimax` for HTTP errors (429/529/URLError) — unrelated to this fix.
