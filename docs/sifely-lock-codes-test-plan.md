# Sifely lock-code test plan

Automation scope is Ridge Oak (`ridge_oak_10235`) and Pebble Shores (`pebbleshores_3414`). Every other house is a no-op: no Sifely rotate, no PadSplit send, no Discord ask.

`LOCK_CODES_ENABLE` defaults off. GitHub Actions / CI must not rotate or post. Do not put lock-code digits, phone last-four, or the vacant-room default in Discord, logs, this plan, or README examples. PadSplit member threads may contain the code. Tests use `REDACTED` or obvious placeholders.

Run:

```bash
python3 test_lock_codes.py
python3 test_lockout_reply.py
python3 test_runtime.py
```

## Move-in sets the room code to the phone last four

1. Enable only in a unit test (patch `live_actions_enabled`) or, on the Mac after review, `LOCK_CODES_ENABLE=1` plus `PADSPLIT_ENABLE_ACTION_HOOKS=1`.
2. A current Ridge Oak or Pebble Shores occupant whose move-in date is within the last three days, with a phone on the occupancy thread.
3. Expect the matching room lock `change` to that phone's last four, Firestore `property_codes/{slug}` field `r{room}` updated, a PadSplit message to that member only, and one digit-free `#ai-automations` notice.
4. A second run does not rotate or message again.
5. Missing phone, missing Firebase service account, or no unique room lock is Need-you and does not invent a code.

## Environment names

Set these in the Mac `.env` only. No values belong in git.

- `ANG_DISCORD_USER_ID` — Discord user id whose replies may approve or decline shared-door changes. No default. Unset or blank means every reply is ignored and a warning is logged.
- `VACANT_ROOM_DEFAULT` — room code written on move-out or terminate. No default. Unset or blank skips the room reset and posts a missing-config notice. Never guess a code.

## Move-out or terminate resets the room only

1. Recent move-out date, or a terminated/cancelled booking, at Ridge Oak or Pebble Shores.
2. With `VACANT_ROOM_DEFAULT` set, expect that room lock set to that value and the codes page updated in the same run. If the variable is missing, expect no rotate and no codes-page write.
3. Expect an Ang ask about front/back doors only. The ask names house, spelled room, and member. It does not include the default or any digits.
4. Shared door locks are not changed in this step.

## Shared doors change only after explicit Ang approval

1. With a pending ask, a digit-free yes whose Discord `author.id` equals `ANG_DISCORD_USER_ID` rotates front and back, or the single shared Pebble Shores door, updates those codes-page fields, and PadSplit-messages remaining current housemates. The departed member is not included.
2. A yes or ok from anyone else, including Joe or a bot, is ignored. Doors stay unchanged.
3. If `ANG_DISCORD_USER_ID` is unset, even a yes from Ang is not an approval. A warning is logged and the ask stays pending.
4. An Ang reply of no leaves front and back unchanged and does not message housemates.
5. No reply leaves the ask pending. Doors stay as they are.
6. A pending ask for any other house is dropped and does not call Sifely.

## Fail closed when the Sifely API errors

1. HTTP 200 with a non-success application `code` raises `SifelyUnavailable`. Nothing is marked done and nothing is sent.
2. `list_locks` / `change` failure on a move-in does not write the codes page, does not message the member, and does not record the move-in as processed.
3. Missing `SIFELY_API_KEY` is Need-you. CI returns `skip_ci` even if a key is present.

## Retries

1. After a rejected rotate, the next run tries that lock again.
2. After a rotate that succeeded but the codes-page write or member notify failed, the next run does not call `change` for that lock again. It re-reads the live passcode (doors) or recomputes the phone last four (move-in) and finishes delivery.
3. If one shared door rotates and the other is rejected, the successful lock is not rotated again. The failed lock is retried. Housemates are not blasted and the Ang-yes confirm is not posted until every matched door and the member send have succeeded.
4. Lockout auto-reply does not rotate the Spanish Moss back door. It uses the current passcode, or the newest unused `#new-tenants` PIN-shaped share if the API is down. A sentence such as "Spanish Moss code changed." is not a share.

## Digits never leave PadSplit

1. Discord templates and every outbound Discord post contain no digits.
2. `redact_for_log` replaces 4–8 digit runs and API keys before anything is written to stderr.
3. A move-in plus move-out run asserts the phone last four and the vacant default are absent from stderr and from Discord posts.

## Lockout gates folded from the older draft

1. Auto-send requires the house street number (a Parker alias at the wrong number does not receive codes).
2. A future move-in is not a current occupant.
3. Departed threads and threads already handled in the idempotency window do not call Sifely.
4. `ask_joe` / `needs_tap` / `missing_codes` Discord posts are recorded once per window and do not mark a door send.
