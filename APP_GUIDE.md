# PadSplit Scraper: app, database and function reference

Reviewed from the cloned repository on 2026-11-04. This document describes the checked-in code, not a verified inventory of the live Firebase project. Project ID: `padsplit-scrapper` ([Firebase console](https://console.firebase.google.com/project/padsplit-scrapper/overview)). The signed-in account opened the project and default Firestore database on 2026-11-04. A read-only request using the dashboard project configuration successfully retrieved `notes/dashboard`; authenticated app access and write operations have not been verified.

## What it does

This is a property-operations system, consisting of Python data collectors, a static web dashboard, scheduled workflows and optional automations. It gathers PadSplit host metrics, tasks, messages, booking and occupancy information; reads Honeywell Total Connect Comfort thermostats; and can control SmartHome/Midea window ACs. The dashboard presents occupancy, tasks, climate data, notes, private messages, templates, codes, vendors and statistics. Data can be stored as local JSON, in Firestore, or both depending on the component. Some features send messages, make calls, rotate lock codes, or control devices when explicitly enabled.

Flow: service credentials → PadSplit/TCC/SmartHome API → Python collectors and derived metrics → rolling JSON and, for certain private data, Firestore → static dashboard. Optional integrations include Discord, Quo, Bland, Sifely, Google Voice, Anthropic and Obsidian. GitHub Actions and macOS LaunchAgents provide scheduling.

## Local setup and current run

The repository is in the sibling `padsplit-scrapper` folder. A `venv` was created there and the PadSplit, thermostat and SmartHome requirements installed. The preview is available at <http://127.0.0.1:8765/> while the local server is running. The dashboard uses checked-in JSON; it is not proof of a fresh scrape. Firebase-backed dashboard operations target the real project, and an authorized login may permit real writes. Some Firebase features require the domain and account to be authorized in the project.

To restart from the repository root:

```sh
source venv/bin/activate
python -m http.server 8765 --bind 127.0.0.1 --directory docs
```

For a fresh setup, use Python 3.12 as in CI and run `python3 -m venv venv`, then `venv/bin/pip install -r padsplit_scraper/requirements.txt -r thermostat/requirements.txt -r smarthome/requirements.txt`. The local virtual environment now uses Python 3.12.15. Cloud Functions have separate `functions/requirements.txt` and a Python 3.12 runtime. The optional Google Voice path also requires Playwright and an already authenticated Chrome profile.

Create a gitignored root `.env` with only the credentials needed: `PADSPLIT_EMAIL`/`PADSPLIT_PASSWORD`, `TCC_EMAIL`/`TCC_PASSWORD`, and `SMARTHOME_EMAIL`/`SMARTHOME_PASSWORD`. Server-side Firestore integrations use `FIREBASE_SERVICE_ACCOUNT_JSON` or `GOOGLE_APPLICATION_CREDENTIALS` (a path); specific modules can impose narrower requirements. Keep credentials out of Git and chat. The dashboard Firebase login is separate from Google Cloud console access and from service-account credentials.

Running a scraper needs the corresponding service credentials. The morning and afternoon scripts contain paths for another Mac and perform Git operations; adapt and review them before use. Live jobs can send messages or change devices; read the flags and operating instructions in `README.md` and `CLAUDE.md` before enabling them. `PADSPLIT_COLLECTION_ONLY=1` restricts action hooks, but isolated local output does not by itself disable Firestore stats uploads when credentials are present.

## Dashboard and data

- `docs/index.html`: occupancy, tasks and manual task discussion, thermostat/weather overview, notes.
- `docs/private-messages.html`, `templates.html`, `codes.html`, `vendors.html`: operational pages backed by Firestore.
- `docs/stats.html`, `kpi-history.html`: authenticated financial/KPI views using a separate Firebase auth instance.
- `docs/data/latest.json`, `occupancy.json`, `docs/thermostat/latest.json`: dashboard data snapshots. `padsplit_scraper/output/stats.json` and monthly history are private output paths; `occupancy.json` derives presence from messages/tasks, whereas listed vacancy KPIs have a different meaning.

## Firebase and Firestore

The web pages initialize Firebase JS SDK 10.12.0 with project `padsplit-scrapper`. Firebase Authentication gates some UI and Firestore rules enforce data access. Python uses Firebase Admin for selected reads/writes. `firebase.json` declares Firestore rules and a Python 3.12 functions codebase named `leak-alert`.

| Path | Purpose in source | Checked-in client rule |
|---|---|---|
| `property_codes/{slug}` | Property contact, room/lockbox codes and maintenance information | Read/write restricted to a specific authenticated UID |
| `property_codes/{slug}/code_versions/{id}` | Immutable code snapshots for history/restore | Same UID; list limited to 50; restricted create fields; no update/delete |
| `notes/{document}` | Dashboard notes, including stats/messages/templates notes | Local proposal: owner-only read/write; deployed rules previously observed public-read |
| `task_messages/{id}` | Task discussions and status | Local proposal: owner-only read/write; deployed rules previously observed public-read |
| `manual_tasks/{id}` | Manually created tasks | Local proposal: owner-only read/write; deployed rules previously observed public-read |
| `vendors/{id}` | Vendor directory | Local proposal: owner-only read/write; deployed rules previously observed public-read |
| `templates/shared` | Shared message templates, including the water-leak template | Local proposal: owner-only read/write; deployed rules previously observed public-read |
| `stats/latest`, `stats/monthly_history` | Latest and historical private stats | Read/write restricted to a separate UID |
| `leak_alert_pending/{callId}` | Authorized pending calls | Client access denied |
| `leak_alert_calls/{incident}/roles/{role}` | Processed call outcome by incident and role | Client access denied |
| `leak_alert_result_queue/{callId}` | Queued call results | Client access denied |

The source rules default-deny other paths. Admin SDK operations are not governed by client security rules. The checked-in rules may differ from deployed rules; public-read means the rules themselves allow unauthenticated reads even if a page has a login interface. `firestore_status_monitor.py` reads in-progress task messages; `persist.py` uploads stats when configured; `codes_history.py` handles code snapshots.

### Cloud Function

`functions/main.py` exports HTTPS `leak_alert_bland` in `us-central1`. It verifies Bland webhook signatures, checks pending calls, stores limited outcomes and queues results. Its `/confirm` path uses a bearer secret for the Bland dispatch tool. It binds `BLAND_WEBHOOK_SECRET` and `LEAK_ALERT_TOOL_BEARER`; `functions/leak_alert_bland.py` contains the pure validation and payload helpers. Deployment, billing, secrets and the deployed function state have not been verified. See the README before any deployment.

## Operational feature map

- `padsplit_scraper/scraper.py`: main PadSplit login, API collection and orchestration; `kpis.py`, `occupancy.py`, `persist.py` calculate and save results.
- `runtime.py`, `job_runner.py`, `process_lock.py`, `state_store.py`, `outbox.py`: execution gates, isolated collection jobs, locking and durable state/intents.
- `new_booking.py`, `frontload_webhook.py`: booking notifications and optional booking/listing change webhook.
- `discord_notifier.py`, `discord_reply_monitor.py`, `firestore_status_monitor.py`: notifications, reply tracking and Firestore task status monitoring.
- `field_mms.py`, `quo_sender.py`, `google_voice_chrome.py`: field digest and outbound SMS transports.
- `lock_codes.py`, `codes_history.py`, `lockout_reply.py`: Sifely lock-code automation, Firestore history and gated member lockout replies.
- `leak_reply.py`, `leak_alert.py`, `leak_alert_bland.py`: gated leak response, incident planning, Bland calls and result handling. Live actions require explicit gates; consult README for the full conditions.
- `seo_monthly.py`: monthly vacancy/SEO advice; `stats_freshness.py` labels stale stats.
- `thermostat/scraper.py`, `set_temps.py`, `schedule.py`: read TCC devices, change settings and manage macOS schedules.
- `smarthome/`: Midea CLI, cloud client, identity/session handling, policy and hourly watcher.
- `message_drafter.py`, `message_summarizer.py`: template/AI-assisted message drafting and summaries. `slack_task_digest.py` creates task digests; `obsidian_daily_digest.py` writes daily notes.
- `run_morning.sh`, `run_afternoon.sh`, `run_field_mms.sh`, `run_seo_monthly.sh`, `launchd/` and `.github/workflows/`: scheduling and operational execution. GitHub workflow schedules are defined in UTC and do not prove current deployment or enablement.

## Function index

The following is an automatically extracted index of Python functions and methods in non-test source files, including internal helpers. Line numbers refer to the cloned revision; function names and docstrings are source-derived. A separate index below lists explicitly named JavaScript functions in the HTML dashboard pages. Consult the named file for arguments and implementation.

### `functions/leak_alert_bland.py`

- `MemoryCallStore.get_pending` (line 73)
- `MemoryCallStore.put_pending` (line 77)
- `MemoryCallStore.incident_is_pending` (line 80)
- `MemoryCallStore.get_record` (line 83)
- `MemoryCallStore.put_record` (line 87)
- `MemoryCallStore.enqueue` (line 90)
- `MemoryCallStore.get_queue` (line 96)
- `MemoryCallStore.list_queue` (line 100)
- `MemoryCallStore.mark_queue_posted` (line 103)
- `FirestoreCallStore.__init__` (line 114)
- `FirestoreCallStore.get_pending` (line 117)
- `FirestoreCallStore.put_pending` (line 120)
- `FirestoreCallStore.incident_is_pending` (line 123)
- `FirestoreCallStore.get_record` (line 131)
- `FirestoreCallStore.put_record` (line 134)
- `FirestoreCallStore.enqueue` (line 137)
- `FirestoreCallStore.get_queue` (line 149)
- `FirestoreCallStore.list_queue` (line 152)
- `FirestoreCallStore.mark_queue_posted` (line 161)
- `safe_doc_id` (line 168)
- `_hmac_matches` (line 177)
- `canonical_json_bytes` (line 184) — Compact JSON matching JSON.stringify(parsed body). None if raw is not JSON.
- `verify_signature` (line 194) — HMAC-SHA256 hex over the raw body, then over compact re-serialized JSON.
- `confirm_bearer` (line 220) — Tool bearer, or the account signing secret when the tool bearer is unset.
- `rejected_call_fields` (line 228) — Fields named by a Bland 400 body. Only analysis_schema and tools.
- `without_rejected_fields` (line 241)
- `bearer_matches` (line 248) — Constant-time compare for the confirm_dispatch tool header.
- `header_value` (line 265)
- `parse_time` (line 275)
- `stale_timestamp` (line 300) — Reject when Bland included a timestamp and it is outside the window.
- `strip_phones` (line 323)
- `voicemail_message` (line 329) — Leave-message only. first_sentence and task stay the shared planner script.
- `task_for` (line 334)
- `confirm_tool` (line 344)
- `build_call_body` (line 376) — POST /v1/calls body. Caller must not log it: it can contain a phone and the tool secret.
- `format_result_line` (line 413)
- `classify_outcome` (line 437) — Map a Bland call object to answered, voicemail, no_answer, or failed.
- `_as_bool` (line 463)
- `_as_eta` (line 477)
- `_from_citations` (line 491)
- `extract_confirmation` (line 509)
- `_summary_text` (line 548)
- `build_record` (line 561)
- `_queue_doc` (line 596)
- `handle_webhook` (line 608) — Verify, require a pending call, write the record, enqueue one Quo line.
- `handle_confirm` (line 686) — Mid-call confirm_dispatch. Bearer secret, pending call required. No Quo post.
- `_snap_dict` (line 725)
- `_record_ref` (line 732)

### `functions/main.py`

- `leak_alert_bland` (line 48)

### `message_drafter.py`

- `load_environment` (line 56)
- `parse_dt` (line 60)
- `utc_now` (line 72)
- `iso_utc` (line 76)
- `normalize` (line 80)
- `mask_pii` (line 84)
- `load_json` (line 91)
- `save_json` (line 97)
- `sender_name` (line 102)
- `occupant_names` (line 106)
- `is_tenant_message` (line 116)
- `property_address` (line 125)
- `slugify` (line 133)
- `scope_matches` (line 138)
- `classify_message` (line 162)
- `load_template` (line 200)
- `load_property_sop` (line 204)
- `format_history` (line 217)
- `build_prompt` (line 234)
- `call_claude` (line 271)
- `template_fallback` (line 318)
- `load_state` (line 329)
- `prune_state` (line 337)
- `already_handled` (line 348)
- `select_candidates` (line 356)
- `send_critical_discord_alert` (line 395)
- `make_draft` (line 411)
- `update_state` (line 439)
- `print_stdout` (line 448)
- `main` (line 455)

### `message_summarizer.py`

- `format_room` (line 36)
- `format_address` (line 43)
- `_normalize_whitespace` (line 57)
- `occupant_names` (line 61)
- `_sender_name` (line 78)
- `_is_tenant_sender` (line 85)
- `extract_tenant_text` (line 92)
- `parse_urgent_items` (line 117)
- `render_summary` (line 142)
- `call_minimax` (line 168)
- `truncate_for_discord` (line 230)
- `send_to_discord` (line 237)
- `main` (line 266)

### `obsidian_daily_digest.py`

- `load_environment` (line 26)
- `parse_iso_datetime` (line 31)
- `format_local_timestamp` (line 43)
- `normalize_whitespace` (line 50)
- `load_json` (line 54)
- `get_daily_note_path` (line 58)
- `get_counts` (line 66)
- `format_task_address` (line 73)
- `format_task_groups` (line 82)
- `get_thread_occupant_names` (line 106)
- `select_recent_tenant_messages` (line 123)
- `format_thermostat_lines` (line 164)
- `load_drafts_payload` (line 191)
- `format_draft_replies` (line 201)
- `render_block` (line 238)
- `upsert_managed_block` (line 270)
- `write_daily_digest` (line 285)
- `main` (line 300)

### `padsplit_scraper/codes_history.py`

- `canonicalize_fields` (line 29)
- `content_hash` (line 38)
- `restore_payload` (line 43)
- `_as_dt` (line 49)
- `_latest_row` (line 59)
- `_log` (line 70)
- `catch_up` (line 74)
- `main` (line 147)

### `padsplit_scraper/discord_notifier.py`

- `_load_latest_payload` (line 13)
- `_format_task_line` (line 24)
- `_build_digest` (line 33)
- `_init_firestore_app` (line 58)
- `_load_ac_filter_dates` (line 76)
- `post_discord_message` (line 108)
- `main` (line 130)

### `padsplit_scraper/discord_reply_monitor.py`

- `_load_json` (line 63)
- `_load_latest_payload` (line 69)
- `_tokenize` (line 80)
- `_canonicalize_tokens` (line 85)
- `_canonicalize_text` (line 89)
- `_address_variants` (line 93)
- `_address_core_tokens` (line 100)
- `_build_address_matcher` (line 104)
- `_load_processed_replies` (line 142)
- `_save_processed_replies` (line 150)
- `_fetch_replies` (line 159)
- `_find_matching_tasks` (line 174)
- `_extract_completed_task_ids` (line 210)
- `main` (line 217)

### `padsplit_scraper/field_mms.py`

- `Window.id` (line 126)
- `load_environment` (line 141)
- `running_in_ci` (line 145)
- `sending_allowed` (line 149) — CI must never send. Live send is the Mac launchd job (prefer Mac).
- `normalize_phone` (line 165)
- `assert_group_recipients` (line 174)
- `assert_quo_recipients` (line 192) — Don + Dad required. Ang GV is the default third. Joe is not required.
- `resolve_quo_recipients` (line 216)
- `window_for` (line 227) — Morning 7am CT only. Before 7am is the prior day's 7am window (catch-up).
- `_parse_created` (line 236)
- `normalize_whitespace` (line 248)
- `sanitize_sms` (line 252)
- `contains_lock_code_like` (line 262)
- `format_house` (line 266)
- `format_room` (line 272)
- `_sender_name` (line 279)
- `_occupant_names` (line 287)
- `is_tenant_item` (line 305)
- `message_why` (line 330)
- `_iter_thread_messages` (line 361)
- `summarize_host_messages` (line 371)
- `extract_open_task_lines` (line 411)
- `digest_discord_open_tasks` (line 438)
- `build_mms_body` (line 449)
- `plan_send` (line 463)
- `load_sent_windows` (line 504)
- `save_sent_window` (line 520)
- `fetch_host_threads` (line 528)
- `load_latest_json_threads` (line 537)
- `collect_host_lines` (line 549)
- `fetch_discord_channel_messages` (line 563)
- `collect_task_lines` (line 583)
- `_escape_applescript` (line 596)
- `resolve_field_mms_transport` (line 600) — quo \| google_voice \| messages \| auto (default: Quo then GV then Messages).
- `resolve_quo_from_number` (line 612)
- `resolve_quo_api_key` (line 624)
- `send_via_quo` (line 631) — Send one Quo group SMS/MMS to all recipients. Never log the API key.
- `messages_chat_name` (line 677)
- `send_via_messages_chat` (line 681) — Send one group MMS via the existing Mac Messages thread (Voice as owner).
- `send_group_mms` (line 712)
- `build_launchd_plist` (line 759)
- `install_launchd` (line 772)
- `run_window` (line 787)
- `main` (line 824)

### `padsplit_scraper/firestore_status_monitor.py`

- `_load_json` (line 13)
- `_load_latest_payload` (line 19)
- `_load_processed_doc_ids` (line 30)
- `_save_processed_doc_ids` (line 38)
- `_build_task_map` (line 47)
- `_init_firestore_client` (line 59)
- `main` (line 73)

### `padsplit_scraper/frontload_webhook.py`

- `authorization_value` (line 153) — Bearer token for a Grok Bot routine webhook. Do not double-prefix.
- `webhook_enabled` (line 161)
- `dry_run_enabled` (line 165) — Default on. Explicit 0/false/no/off turns live POST on (when enabled).
- `_flag` (line 170)
- `_ci` (line 178)
- `observed_at_now` (line 184)
- `make_event_id` (line 191)
- `_date_only` (line 196)
- `_num` (line 205)
- `_house_from_thread` (line 217) — Street only. Chat title is often the member name and is never used.
- `_room_from_thread` (line 228)
- `_move_in_from_thread` (line 239)
- `_is_cancel_status` (line 246)
- `_blank_booking` (line 255)
- `_consider_booking` (line 266)
- `_iter_booking_statuses` (line 293)
- `project_bookings` (line 306) — Booking rows from messenger bookingStatus plus new_booking inbox hits.
- `_message_move_in_date` (line 354)
- `project_message_move_ins` (line 368)
- `project_listings` (line 410)
- `project_occupancy` (line 435)
- `build_projection` (line 455) — Current scrape view. ``None`` rooms/occupancy means this run did not refresh them.
- `_empty_projection` (line 471)
- `resolve_projection` (line 475) — Keep listing/occupancy sections a messages-only run did not refresh.
- `intro_rent_for` (line 494)
- `_listing_index` (line 503)
- `_rents` (line 515)
- `_payload` (line 523)
- `_booking_payload` (line 550)
- `_move_in_payload` (line 574)
- `diff_projection` (line 602) — Pure diff. ``previous`` None is the baseline caller; this returns [].
- `_booking_ids_by_room` (line 660)
- `_diff_move_ins` (line 670)
- `_diff_occupancy_move_ins` (line 702)
- `_diff_listings` (line 728)
- `_dedupe` (line 792)
- `_load_state` (line 808)
- `_empty_state` (line 826)
- `_save_state` (line 837)
- `_append_dry_run` (line 850)
- `post_payload` (line 856) — Return ``(ok\|retry\|reject, status_code)``. Never raises.
- `_default_post` (line 897)
- `_mark_sent` (line 901)
- `_queue_pending` (line 908)
- `_mark_failed` (line 915) — Stop retrying. Store status and time only — never the payload.
- `emit_for_scraper` (line 929) — Detect and emit. Returns a result dict. Does not raise.
- `_emit` (line 967)
- `_fetch_pending` (line 1071)
- `_send_block` (line 1090) — Return a skip result when this run must not send or touch the ledger.
- `_deliver` (line 1106)

### `padsplit_scraper/google_voice_chrome.py`

- `GoogleVoicePage.current_url` (line 101)
- `GoogleVoicePage.page_title` (line 102)
- `GoogleVoicePage.page_text` (line 103)
- `GoogleVoicePage.goto_messages` (line 104)
- `GoogleVoicePage.open_compose` (line 105)
- `GoogleVoicePage.add_recipient` (line 106)
- `GoogleVoicePage.recipient_chip_count` (line 107)
- `GoogleVoicePage.set_body` (line 108)
- `GoogleVoicePage.click_send` (line 109)
- `GoogleVoicePage.close` (line 110)
- `resolve_chrome_user_data_dir` (line 113)
- `resolve_chrome_profile_directory` (line 120)
- `format_voice_recipient` (line 128)
- `detect_google_voice_challenge` (line 137) — True when Voice bounced to a login wall, captcha, or account challenge.
- `raise_if_google_voice_challenge` (line 161)
- `send_on_google_voice_page` (line 170) — Compose one group message (not three 1:1s) and send it.
- `send_via_google_voice_chrome` (line 192) — Send one group SMS via voice.google.com in a persistent Chrome profile.
- `launch_playwright_voice_page` (line 216)
- `PlaywrightVoicePage.__init__` (line 268)
- `PlaywrightVoicePage.current_url` (line 273)
- `PlaywrightVoicePage.page_title` (line 279)
- `PlaywrightVoicePage.page_text` (line 285)
- `PlaywrightVoicePage.goto_messages` (line 291)
- `PlaywrightVoicePage.open_compose` (line 298)
- `PlaywrightVoicePage.add_recipient` (line 308)
- `PlaywrightVoicePage.recipient_chip_count` (line 321)
- `PlaywrightVoicePage.set_body` (line 331)
- `PlaywrightVoicePage.click_send` (line 341)
- `PlaywrightVoicePage.close` (line 349)
- `PlaywrightVoicePage._first_visible` (line 359)
- `PlaywrightVoicePage._click_first` (line 369)

### `padsplit_scraper/http_clients.py`

- `TimeoutSession.__init__` (line 15)
- `TimeoutSession.request` (line 19)
- `build_session` (line 24)

### `padsplit_scraper/job_runner.py`

- `isolate_outputs` (line 34)
- `_collection_worker` (line 40) — Real scraper entry used by the collection runner.
- `_interpret_worker_result` (line 49)
- `_result` (line 60)
- `run_collection` (line 85) — Run the scraper under a worker-lifetime lock. Never git-publishes.
- `main` (line 144)

### `padsplit_scraper/kpis.py`

- `compute_monthly_kpis` (line 10) — Approximate monthly bonuses/penalties using historical metrics that are available.
- `_parse_iso` (line 67)
- `_to_num` (line 84)
- `_find_property_key` (line 100)
- `_room_property_label` (line 118)
- `_extract_earnings_rows` (line 146)
- `_find_net_revenue` (line 154)
- `_flatten_tasks` (line 161)
- `compute_kpis` (line 172)

### `padsplit_scraper/leak_alert.py`

- `live_enabled` (line 106) — Default off. CI and collection-only never enable alerts.
- `normalize_e164` (line 113) — Return E.164 or empty. Callers must not log the result.
- `recipient_hash` (line 123)
- `leak_category` (line 127) — Short label for the script. Does not copy the member message.
- `fixed_script` (line 141)
- `tenant_body` (line 152)
- `message_has_picture` (line 164)
- `bland_voice_spec` (line 175) — Bland call shape. phone_number is applied at send time from env and is not stored.
- `quo_private_spec` (line 188) — One Quo recipient per POST. from/to are applied at send time and are not stored.
- `parse_group_e164s` (line 199) — Comma or whitespace list. Empty when unset. Never log the result.
- `quo_group_spec` (line 212) — One group POST. The participant list is applied at send time and is not stored.
- `_conversations_url` (line 224)
- `default_quo_get` (line 231) — GET helper for the dry-run conversation check. The body is not logged.
- `_participant_set` (line 257)
- `check_group_conversation` (line 266) — Dry-run only. Return matched, mismatch, not_found, or check_failed.
- `_group_entry` (line 308)
- `assert_plan_has_no_numbers` (line 375)
- `load_state` (line 383)
- `save_state` (line 400)
- `_configured_status` (line 407)
- `occupancy_pk` (line 415) — Numeric occupancy.pk. Messenger stores it inside the GraphQL id.
- `phone_from_profile_payload` (line 427) — Read phone_number from a member-phone response. Empty when absent.
- `member_phone_fetcher` (line 440) — GET the host member-phone endpoint. The number is not logged.
- `_house_member_phones` (line 464) — In-memory E.164 list for current members of this house, plus a skip count.
- `_tenant_entries` (line 507)
- `build_plan` (line 563) — Plan one incident. Returns None when there is no current member leak.
- `persist_plan` (line 667) — Record actionable keys. Missing config and over-cap results are not stored.

### `padsplit_scraper/leak_alert_bland.py`

- `dry_run_enabled` (line 79) — Default on. Live HTTP requires an explicit off value.
- `calls_live` (line 88)
- `result_post_live` (line 95) — Quo result line. Own flag, still blocked by dry-run, CI, and leak-alert enable.
- `mask_e164` (line 103) — Keep the leading plus and the last two digits. Preview output only.
- `_stamp` (line 111)
- `_append_jsonl` (line 115)
- `_voice_key` (line 123)
- `_parse_voice_key` (line 127)
- `_parse_time` (line 139)
- `default_bland_post` (line 143) — POST /v1/calls. Raises before any socket when the live gate is closed.
- `default_bland_get` (line 168) — GET /v1/calls/{id}. The id is restricted to a token so the URL cannot wander.
- `default_quo_post` (line 189) — POST one Quo group message. The payload is not logged.
- `_bearer` (line 214)
- `_read_json` (line 221)
- `_within_retry_block` (line 246)
- `place_plan_calls` (line 255) — Place Don and Tom calls, or log a dry-run line. Never stores the phone.
- `poll_open_calls` (line 368) — GET calls placed on an earlier run when no webhook record is stored.
- `post_group_result_line` (line 468) — One Quo group line. Dry-run writes the line and does not POST.
- `drain_result_queue` (line 506) — Post or dry-log each queued line once per call_id.
- `advance_plans` (line 570) — Poll earlier calls, drain result lines, then place this run's voice calls.
- `firestore_store_or_none` (line 621) — Admin client when Mac credentials exist. Never required for dry-run.
- `render_preview` (line 636) — Exact call bodies with fake numbers masked to the last two digits.
- `main` (line 678)

### `padsplit_scraper/leak_reply.py`

- `load_environment` (line 304)
- `running_in_ci` (line 308)
- `live_send_enabled` (line 312) — Default off until Mac .env sets LEAK_REPLY_ENABLE. CI must not send.
- `_log` (line 321)
- `encode_digits_for_discord` (line 325) — Spell each digit so Discord outbound stays digit-free. Cart decodes [one]…[nine].
- `decode_digits_from_discord` (line 336) — Inverse of encode_digits_for_discord. Tests / Cart pickup only.
- `street_label_for_discord` (line 346) — Drop leading house numbers. Prefer thread_ship_to for Cart.
- `_state_abbrev` (line 351)
- `thread_ship_to` (line 356) — Full ship-to of the leaking property from the PadSplit thread only.
- `is_full_ship_to` (line 399) — Need a house number plus a street name so Cart can ship.
- `assert_water_key_order_safe` (line 405) — Digit-free except the leaking property ship-to. No lock codes.
- `detect_leak` (line 418) — True only for burst / pipe / main / flooding / wall-ceiling emergencies.
- `canonicalize_water_key_youtube` (line 436) — Keep the canonical short URL; strip ?si= / utm tracking.
- `load_shared_template_fields` (line 441) — Same templates/shared doc new_booking uses for the Hirevire card.
- `pick_t5_source` (line 455)
- `load_t5_source` (line 463)
- `adapt_t5_to_member_thread` (line 475) — 1:1 tone from house-wide t5. Always add Quo. No lock codes / SSN / Amazon.
- `format_leak_body` (line 512) — PadSplit 1:1 body from live t5 (or baked t5). Quo + canonical YouTube.
- `discord_water_key_order_text` (line 527) — Cart event. Digit-free except full ship-to. Requires house + address.
- `load_state` (line 560)
- `save_state` (line 577)
- `already_sent` (line 582)
- `record_sent` (line 598)
- `host_text_handles_leak` (line 606) — Auto-reply marker, or Nest's t5 catch-up pair (phrase + water-key short).
- `same_house` (line 615) — True when both threads resolve to one house. Unknown streets do not match each other.
- `house_host_handled` (line 628) — Host shutoff note on any current thread at this house, after the member report, inside lookback.
- `host_already_sent_leak_pack` (line 655)
- `recent_member_leak` (line 664)
- `decide` (line 688)
- `post_automations_discord` (line 766) — Post WATER_KEY_ORDER. Digit-free except ship_to. Do not use lock_codes.assert on the full text.
- `process_leaks` (line 793)
- `load_host_messages` (line 991)
- `run` (line 995)
- `run_for_scraper` (line 1093)
- `main` (line 1123)

### `padsplit_scraper/lock_codes.py`

- `load_environment` (line 133)
- `running_in_ci` (line 137)
- `live_actions_enabled` (line 141) — Mac morning/afternoon only. GitHub Actions / CI must not rotate or post.
- `sifely_api_key` (line 151) — Return SIFELY_API_KEY or empty. Never invent a key. Never print it.
- `has_digit_characters` (line 156)
- `redact_for_log` (line 160) — Strip keys and digit runs so logs never contain PIN digits or tokens.
- `hash_passcode` (line 168)
- `generate_passcode` (line 172) — In-memory PIN only. Tests mock this to REDACTED. Never log the value.
<!-- Private property-specific operational details withheld. -->
<!-- Private property-specific operational details withheld. -->
- `vacancy_allows_rotate` (line 195) — Vacant alone is not enough. Require an empty/turned photo.
- `vacancy_key` (line 209)
- `pending_auto_rotate_rooms` (line 220)
- `current_member_threads` (line 232)
- `parse_sifely_share_code` (line 253) — Read an inbound #new-tenants Sifely share. Tests use REDACTED, not PINs.
<!-- Private property-specific operational details withheld. -->
- `discord_human_change_text` (line 273)
- `discord_rotated_text` (line 277)
- `need_you_missing_key_text` (line 281)
- `member_host_message` (line 285) — PadSplit host inbox may include the new code. Do not log this string.
- `assert_discord_outbound_safe` (line 293)
- `decide` (line 299) — Pure v1 decision table. Side effects stay in execute/run.
- `load_state` (line 345)
- `save_state` (line 364)
- `_empty_state` (line 369)
- `passcode_hashes_from_list` (line 379)
- `detect_human_change` (line 390)
- `sifely_headers` (line 404) — Raw sk- key. Do not prefix Bearer.
- `_unwrap_sifely` (line 412)
- `sifely_request` (line 418)
- `list_locks` (line 451)
- `list_passcodes` (line 477)
- `change_passcode` (line 494)
- `resolve_lock` (line 516)
- `resolve_keyboard_pwd_id` (line 533)
- `send_host_message` (line 554) — SEND the host message on the member thread. Reuses the scraper session.
- `post_ops_discord` (line 594)
- `fetch_new_tenants_messages` (line 611)
- `load_json_file` (line 623)
- `load_occupancy_rooms` (line 629)
- `load_host_messages` (line 640)
- `update_codes_page` (line 651) — Write the new code to Firestore property_codes (GitHub Pages codes.html).
- `_log` (line 678)
- `_date_only` (line 682)
- `_discord_text_for` (line 696)
- `_mark_vacancies_handled` (line 706)
- `run` (line 713)
- `_first_new_share` (line 870)
- `_notify_current_members` (line 896)
- `main` (line 921)

### `padsplit_scraper/lockout_reply.py`

- `load_environment` (line 288)
- `running_in_ci` (line 292)
- `live_send_enabled` (line 296) — Default off until Mac .env sets LOCKOUT_REPLY_ENABLE. CI must not send.
- `_log` (line 305)
- `parse_dt` (line 309)
- `normalize_text` (line 321)
- `sender_name` (line 325)
- `occupant_names` (line 332)
- `is_member_message` (line 342)
- `is_host_message` (line 353)
- `message_text` (line 358)
- `iter_thread_messages` (line 368)
- `detect_lockout` (line 376)
- `detect_door_fail_followup` (line 380) — True when the member says the door/code still failed after a door reply.
- `detect_entry_success` (line 385) — True when the member confirms they got in after a door-stage send.
- `detect_fresh_lockout` (line 395) — Lockout cue that is not only a door-fail follow-up phrase.
- `thread_street` (line 405)
- `thread_room` (line 410)
- `current_occupant` (line 418)
- `match_house` (line 429)
- `mentioned_room` (line 452)
- `resolve_room` (line 457)
- `score_certainty` (line 469)
- `first_filled` (line 497)
- `pick_room_lockbox` (line 505) — Prefer the lockbox/room code that matches the member room.
- `codes_from_property_doc` (line 534)
- `_fail_ladder_lines` (line 576)
- `format_door_lockout_body` (line 586) — Stage-1 PadSplit body: door codes only. Never log this string.
- `format_lockbox_lockout_body` (line 617) — Stage-2 PadSplit body: lockbox / room code + location. Never log this string.
- `format_lockout_body` (line 634) — Stage-1 door body. Lockbox is never included.
- `discord_lockout_detected_text` (line 639)
- `discord_missing_codes_text` (line 654)
<!-- Private property-specific operational details withheld. -->
- `discord_sifely_unavailable_text` (line 667)
- `load_state` (line 676)
- `save_state` (line 691)
- `_thread_state` (line 696)
- `_legacy_combined_pack` (line 701) — Old single-pack state (sent_at, no per-stage keys) counts as both stages.
- `stage_sent_at` (line 706)
- `already_sent_stage` (line 717)
- `already_sent` (line 731) — True when the door stage was already sent in the idempotency window.
- `record_sent` (line 742)
- `resolved_at` (line 762)
- `already_resolved` (line 767)
- `record_resolved` (line 780) — Persist resolved so later fail-ish noise does not re-open stage 2.
- `host_lockout_stage_messages` (line 797)
- `host_already_sent_stage` (line 813)
- `host_already_sent_lockout_pack` (line 822) — True when an old combined pack (door + lockbox line) is already on the thread.
- `latest_host_stage_at` (line 828)
- `door_reply_at` (line 839)
- `recent_member_lockout` (line 855)
- `recent_door_fail_followup` (line 884) — Newest member lockout / door-fail message strictly after the door reply.
- `recent_entry_success` (line 913) — Newest member entry-success message strictly after the door reply.
- `recent_fresh_lockout_and_fail` (line 942) — After resolved: newest fail only when a fresh lockout (not fail-only noise) exists.
- `fetch_property_codes` (line 975) — Read property_codes/{slug}. Never log field values.
<!-- Private property-specific operational details withheld. -->
- `decide` (line 1062)
- `post_automations_discord` (line 1227)
- `_discord_text` (line 1245)
- `process_lockouts` (line 1259)
- `load_host_messages` (line 1412)
- `run` (line 1416)
- `run_for_scraper` (line 1489)
- `main` (line 1515)

### `padsplit_scraper/new_booking.py`

- `iso_utc` (line 177)
- `parse_dt` (line 181)
- `load_state` (line 193)
- `save_state` (line 208)
- `load_leftover_compose_tabs` (line 213)
- `save_leftover_compose_tabs` (line 227)
- `live_send_enabled` (line 234) — Mac morning/afternoon scraper sends. GitHub Actions scrape must not.
- `parse_firestore_string_map` (line 243)
- `load_live_new_booking_template` (line 256) — Read the live **new booking request** card from docs/templates.html storage.
- `template_send_body` (line 276)
- `verify_hirevire_template` (line 284) — Require the Hirevire application owned by [redacted email].
- `parse_year_month` (line 294)
- `has_recent_eviction` (line 311) — True when occupant rental history shows any eviction in the last 7 years.
- `close_leftover_compose_tabs` (line 343) — Close leftover host compose/draft tabs for this thread before send.
- `leftover_tabs_open` (line 364)
- `require_leftover_drafts_cleared` (line 370) — HARD GATE: leftover drafts must be cleared before any host send.
- `booking_id_from_status` (line 398)
- `pending_booking_status` (line 405) — Messenger PENDING extra — join/context only, not the booking-hit source.
- `occupancy_pk_from_gid` (line 417)
- `parse_pending_inbox_payload` (line 437)
- `collect_booking_hits` (line 452) — Hits come from hostPendingBookingRequests only. Messenger is join/context.
- `host_already_sent_hirevire` (line 492)
- `already_handled` (line 510)
- `record_booking` (line 519)
- `last_initial` (line 538)
- `build_screening_snapshot` (line 543) — Safe screening snapshot for #new-tenants. No email, phone, SSN, photos, lock codes, credit.
- `format_new_tenants_pack` (line 576)
- `post_new_tenants_webhook` (line 608)
- `notify_new_tenants_pack_for_joe` (line 624) — Post Discord #new-tenants pack (@ Joe only). CI must not post.
- `send_host_message` (line 638) — SEND the host message on the member thread. Not a draft.
- `fetch_rental_history` (line 689)
- `fetch_pending_booking_requests` (line 725) — Primary booking-hit source: host pending-booking inbox.
- `fetch_chat_id_for_occupancy` (line 763)
- `send_first_host_message` (line 803) — Close leftover compose/draft tabs (hard gate), then SEND one host copy.
- `_occupancy_id_for_hit` (line 820)
- `_post_pack_for_result` (line 829)
- `process_new_bookings` (line 843) — Handle each hostPendingBookingRequests hit once. Never Approve/Reject.
- `run_for_scraper` (line 1086)

### `padsplit_scraper/occupancy.py`

- `compute_occupancy` (line 21)
- `operator_lists` (line 46) — Split occupancy rows into operator lists. Incoming is future next_move_in only.
- `_flatten_tasks` (line 77)
- `_ingest_chat` (line 90)
- `_ingest_ticket` (line 116)
- `_finalize_row` (line 145)
- `_room_state` (line 180)
- `_room_key` (line 209)
- `_normalize_street` (line 213)
- `_normalize_room` (line 220)
- `_coerce_room_number` (line 226)
- `_street_from_chat` (line 235)
- `_street_from_ticket` (line 241)
- `_room_from_obj` (line 250)
- `_user_present` (line 254)
- `_last_message_after` (line 260)
- `_message_date` (line 267)
- `_date_only` (line 276)
- `_chicago_date` (line 295)
- `_scraped_at` (line 301)
- `_is_turn_category` (line 307)
- `_ticket_photo_count` (line 311)
- `_chat_photo_count` (line 322)
- `_iter_chat_messages` (line 333)
- `_unique_ids` (line 344)
- `_room_sort_key` (line 355)

### `padsplit_scraper/outbox.py`

- `_now` (line 22)
- `load` (line 26)
- `enqueue` (line 34)
- `pending` (line 53)
- `mark_done` (line 57)

### `padsplit_scraper/persist.py`

- `_latest_output_path` (line 37)
- `_stats_output_path` (line 41)
- `_occupancy_output_path` (line 45)
- `_docs_occupancy_path` (line 49)
- `_persist_occupancy_payload` (line 53)
- `_timestamped_output_path` (line 58)
- `_monthly_history_path` (line 67)
- `_legacy_docs_monthly_history_path` (line 71)
- `seed_private_monthly_history` (line 75)
- `_write_json` (line 84)
- `_load_json_if_exists` (line 89)
- `configure_output_dirs` (line 99) — Point persist writes at an explicit tree. Used by the isolated runner.
- `snapshot_output_dirs` (line 106) — Capture persist globals so a runner can restore them after isolation.
- `restore_output_dirs` (line 114) — Restore persist globals captured by ``snapshot_output_dirs``.
- `prior_last_complete_success` (line 121) — Last complete success from a prior payload, rejecting stale run health.
- `_build_run_status` (line 152)
- `_attach_run_status` (line 195)
- `_persist_latest_payload` (line 201)
- `_load_score_history` (line 218)
- `_build_monthly_history_payload` (line 224)
- `_build_stats_payload` (line 278)
- `_firestore_client_or_none` (line 297)
- `upload_stats_to_firestore` (line 315) — Upsert owner-only stats docs. Never raises.

### `padsplit_scraper/process_lock.py`

- `LockAcquisition.__enter__` (line 56)
- `LockAcquisition.__exit__` (line 59)
- `lock_path` (line 64)
- `flock_file` (line 70) — Stable lock inode. Never unlinked for the life of the lock name.
- `_registry_key` (line 75)
- `read_lock_pid` (line 79)
- `_pid_alive` (line 98)
- `_lock_age_seconds` (line 110)
- `_flock_busy_errno` (line 117)
- `lock_is_held` (line 121) — True when this process or another process holds the flock.
- `lock_is_stale` (line 155) — True when a leftover lock name exists and no live owner holds it.
- `_write_pid` (line 176)
- `_clear_pid` (line 185)
- `_try_flock_exclusive` (line 194)
- `acquire_lock` (line 204)
- `release_lock` (line 289)
- `main` (line 316)

### `padsplit_scraper/quo_sender.py`

- `_log` (line 85)
- `dry_run_enabled` (line 95) — Default on. Only an explicit false flag allows live HTTP.
- `http_allowed` (line 104) — True only for a non-CI, non-dry-run leak_alert send.
- `mask_e164` (line 116) — Last two digits only. Preview and logs use this, never the full number.
- `_water_key_url` (line 124)
- `tenant_sms` (line 132) — Quo tenant SMS. Own copy; not the PadSplit member reply or the group text.
- `_house_room_from_planner_body` (line 160) — House and room from the planner tenant line. Empty room when it has none.
- `outbound_tenant_text` (line 173) — Build the Quo tenant SMS from a planner body. Does not edit that planner copy.
- `_assert_safe_copy` (line 181)
- `preview_call_script` (line 189) — Hook for the voice PR. This is not a Bland script and must stay empty of one.
- `_flag_label` (line 194)
- `gate_lines` (line 203)
- `render_preview` (line 224) — Exact group and tenant texts for a fake incident. Numbers are masked.
- `write_preview` (line 266)
- `error_code` (line 273) — Quo error code only. The rest of the body is not logged.
- `backoff_seconds` (line 294)
- `_raw_key` (line 298)
- `default_post` (line 305) — POST helper. Callers must not log the body or the response.
- `post_text` (line 340) — POST one SMS. 0206400, 0204403, other 4xx, and timeouts are not retried.
- `_stamp` (line 406)
- `atomic_save_state` (line 410) — Replace the state file. The payload is checked before the rename.
- `claim_key` (line 429) — Record ``sending`` on disk before POST. False when the key is already claimed.
- `_finish` (line 470)
- `resolve_from_number` (line 484)
- `resolve_group_numbers` (line 489)
- `resolve_tenant_numbers` (line 498) — Live member phones. The list is not stored.
- `build_outbounds` (line 517)
- `_invalid_reason` (line 579)
- `deliver` (line 591) — Claim each planned Quo key, then POST. Caller must already have passed the gate.
- `maybe_deliver` (line 651) — Send only when the leak_alert gate is open and dry-run is explicitly off.
- `main` (line 702)

### `padsplit_scraper/runtime.py`

- `running_in_ci` (line 51)
- `flag_value` (line 56)
- `send_enabled` (line 69) — Return True only when CI is off, collection-only is off, and a flag is on.
- `collection_only` (line 87)
- `action_hooks_enabled` (line 97)
- `git_publish_enabled` (line 106)
- `collection_output_dir` (line 113) — Isolated collection output. Never the live git-tracked docs/data tree.
- `state_dir` (line 122)
- `lock_dir` (line 130)
- `host_identity` (line 138)
- `enabled_send_actions` (line 142)

### `padsplit_scraper/scraper.py`

- `load_credentials` (line 400)
- `create_session` (line 410)
- `ScrapePhaseError.__init__` (line 427)
- `_authed_request` (line 445)
- `login` (line 466)
- `fetch_messages` (line 485)
- `fetch_thread_messages` (line 539) — Fetch the most recent `first` messages for a single chat thread.
- `fetch_tasks` (line 573) — Fetch maintenance tickets and group them by status to mirror UI buckets.
- `fetch_properties_stats` (line 616) — Fetch per-property occupancy stats used by the dashboard stats page.
- `fetch_rooms` (line 639) — Fetch all rooms with pagination.
- `fetch_earnings` (line 671) — Fetch monthly earnings history.
- `fetch_performance_history` (line 694) — Fetch portfolio-level monthly performance history from host performance endpoints.
- `update_task_status` (line 775)
- `_describe_exception` (line 797)
- `_run_phase` (line 806)
- `_enrich_recent_threads` (line 822)
- `_action_hooks_allowed` (line 850) — Hooks follow an explicit policy when one is provided.
- `_invoke_action_hook` (line 865) — Import-and-call one outbound hook. Collection-only never reaches here.
- `_run_action_hooks` (line 874)
- `_emit_frontload_webhook` (line 894) — Booking/listing webhook. Failures stay off the scrape result.
- `_apply_isolated_output` (line 926)
- `_source_health` (line 938)
- `_count_tasks` (line 956)
- `_earnings_row_count` (line 966)
- `_this_run_health` (line 974) — Build this run's health. Never reuse a prior ``run_status`` object.
- `_ok_outcome` (line 1032)
- `_degraded_outcome` (line 1036)
- `run` (line 1045)
- `main` (line 1236)

### `padsplit_scraper/seo_monthly.py`

- `load_environment` (line 136)
- `running_in_ci` (line 140)
- `posting_allowed` (line 144) — CI must never post. Live post is the Mac LaunchAgent only.
- `sanitize_text` (line 156)
- `joe_mention` (line 163)
- `normalize_price_status` (line 170)
- `_room_key` (line 175)
- `_occupancy_index` (line 183)
- `_has_cover` (line 192)
- `_cover_has_orientation` (line 200)
- `listed_or_vacant_over_14` (line 208) — Listed-status or presence-vacant rooms older than 14 Chicago days.
- `pricing_outliers` (line 286) — High / Very High Pricing Analysis only. Never a blind −$1 sweep.
- `listing_photo_gaps` (line 293)
- `_room_label` (line 314)
- `_days_phrase` (line 320)
- `build_advice_pack` (line 329)
- `timezone_utc` (line 350)
- `render_markdown` (line 356)
- `render_joe_lines` (line 436)
- `_short_street` (line 476)
- `_room_num` (line 481)
- `attach_discord_section` (line 485)
- `fetch_live_bundle` (line 491)
- `load_stale_bundle` (line 507)
- `_load_first_json` (line 531)
- `collect_bundle` (line 544)
- `report_path` (line 571)
- `write_report` (line 576)
- `post_joe_discord` (line 582)
- `build_launchd_plist` (line 606)
- `install_launchd` (line 619)
- `run_pack` (line 634)
- `main` (line 681)

### `padsplit_scraper/state_store.py`

- `state_path` (line 20)
- `load_json` (line 25)
- `save_json` (line 36)

### `padsplit_scraper/stats_freshness.py`

- `stats_freshness` (line 12)
- `_parse_ts` (line 43)

### `slack_task_digest.py`

- `_as_ct` (line 52)
- `ct_day` (line 58)
- `in_morning_window` (line 62)
- `load_data` (line 67)
- `load_ledger` (line 71)
- `already_posted_today` (line 81)
- `post_decision` (line 85) — Return a skip reason, or None when the digest should be sent.
- `write_ledger` (line 94)
- `collect_tasks` (line 107)
- `digest_counts` (line 125)
- `format_dry_run` (line 135) — Counts only. Callers must not print the composed digest in dry-run.
- `format_message` (line 147)
- `compose_message` (line 169) — Build the digest from live tasks. kpis/vacancy_rooms are ignored.
- `truncate_for_discord` (line 188)
- `send_to_discord` (line 195) — Post the digest. Never print the webhook URL or any other secret.
- `fetch_weather` (line 229)
- `run_digest` (line 265) — Compose and maybe post. Dry-run prints counts and always returns 0.
- `main` (line 307)

### `smarthome/cli.py`

- `_notify` (line 13)
- `_slot_key_for` (line 22)
- `cmd_list` (line 29)
- `cmd_set` (line 49)
- `cmd_off` (line 72)
- `cmd_status` (line 94)
- `build_parser` (line 116)
- `main` (line 131)

### `smarthome/clocks.py`

- `tokenize` (line 35)
- `find_active_slot` (line 42)
- `slot_key` (line 51)
- `match_houses` (line 55)
- `resolve_house` (line 64)
- `_load_json` (line 73)
- `_slots_from_payload` (line 79)
- `load_slots` (line 86)
- `active_cool` (line 108)

### `smarthome/cloud.py`

- `f_to_c` (line 59)
- `c_to_f` (line 63)
- `load_credentials` (line 67)
- `_is_ac` (line 76)
- `_map_cloud_error` (line 81)
- `slim_login_body` (line 91) — mill1000-style /mj/user/login body: same silo, no 2.22.0 version block.
- `_install_slim_login` (line 124)
- `_connect_slim` (line 152)
- `_connect_for_spec` (line 185)
- `connect` (line 193)
- `_use_appliance_code` (line 220)
- `_transparent_error_code` (line 227)
- `_transparent_send_envelope` (line 238)
- `_install_appliance_code_retry` (line 253) — US SmartHome send rejects applianceId and appVNum. Seed reqId so the library skips those fields.
- `list_acs` (line 296)
- `find_ac` (line 301)
- `_device` (line 311)
- `_check_online` (line 333)
- `_readback_temp_f` (line 338)
- `_readback_running` (line 348)
- `_refresh` (line 356)
- `set_temp` (line 362)
- `turn_off` (line 396)

### `smarthome/identity.py`

- `_mint` (line 39)
- `_read` (line 50)
- `save_identity` (line 72)
- `load_or_create` (line 80)
- `current_fingerprint` (line 90)
- `fingerprint_spec` (line 97)
- `select_fingerprint` (line 107)

### `smarthome/intent.py`

- `_empty` (line 13)
- `load_intent` (line 17)
- `save_intent` (line 25)
- `unit_state` (line 33)
- `record_hold` (line 38)
- `record_off` (line 47)
- `clear_hold` (line 56)
- `is_sticky_off` (line 64)

### `smarthome/policy.py`

- `in_night_off` (line 16)
- `resolve_action` (line 20)

### `smarthome/session.py`

- `session_lock` (line 19)
- `cooldown_active` (line 31)
- `start_cooldown` (line 49)

### `smarthome/watcher.py`

- `_notify` (line 27)
- `_load_streak` (line 36)
- `_save_streak` (line 44)
- `_today` (line 50)
- `_live_running` (line 54)
- `_read_live` (line 58)
- `_load_digest` (line 70)
- `_digest_due` (line 78)
- `_mark_digest` (line 87)
- `_format_digest` (line 98)
- `enforce_tick` (line 104)
- `build_plist` (line 190)
- `write_plist` (line 203)
- `main` (line 209)

### `thermostat/schedule.py`

- `temp_alert_webhook_url` (line 45) — Return the #ai-tasks-temp webhook, or None if unset.
- `_post_temp_alert` (line 55)
- `_should_send_temp_alert` (line 70) — True if no alert sent in last hour (file-based debounce, Unix timestamp).
- `_record_temp_alert` (line 83)
- `Slot.hhmm` (line 100)
- `Slot.display` (line 104)
- `parse_time` (line 110)
- `parse_slot` (line 127)
- `slot_to_dict` (line 139)
- `slot_from_dict` (line 143)
- `load_schedules` (line 152)
- `save_schedules` (line 170)
- `find_active_slot` (line 188)
- `sanitize_label` (line 197)
- `label_for` (line 205)
- `plist_path_for` (line 209)
- `log_paths_for` (line 213)
- `build_program_arguments` (line 218)
- `build_plist_dict` (line 230)
- `build_plist` (line 243)
- `enforcer_plist_path` (line 247)
- `build_enforcer_plist` (line 251)
- `install_enforcer_if_needed` (line 267)
- `launchctl` (line 281)
- `managed_plists` (line 285)
- `calendar_plists` (line 289)
- `target_plists` (line 293)
- `unload_and_remove` (line 301)
- `install_schedule` (line 317)
- `install_command` (line 329)
- `resume_schedule` (line 341)
- `uninstall_command` (line 348)
- `loaded_labels` (line 362)
- `normalize_location_name` (line 375)
- `resolve_schedule_target` (line 379)
- `setup_enforcer_logging` (line 397)
- `find_device_for_target` (line 418)
- `status_is_permanent_hold` (line 453)
- `enforce_command` (line 460)
- `load_live_snapshot` (line 557)
- `fetch_live_setpoints` (line 581)
- `heal_snapshot_cache` (line 662)
- `parse_scraped_at` (line 710)
- `format_snapshot_value` (line 719)
- `live_row_for_target` (line 727)
- `plist_summary` (line 742)
- `legacy_status_command` (line 766)
- `format_last_run` (line 809)
- `status_command` (line 816)
- `build_parser` (line 867)
- `main` (line 903)

### `thermostat/scraper.py`

- `load_credentials` (line 37)
- `create_session` (line 48)
- `login` (line 57)
- `fetch_location_names` (line 88) — Parse /portal/Locations HTML for the true location names keyed by LocationID.
- `fetch_locations_page` (line 115)
- `fetch_locations` (line 139)
- `fetch_device_uidata` (line 153) — Fetch active setpoints via CheckDataSession.
- `extract_device` (line 173)
- `print_report` (line 201)
- `build_output` (line 216)
- `fetch_fresh_output` (line 234)
- `validate_snapshot_payload` (line 256)
- `load_stale_snapshot` (line 268)
- `persist_output` (line 280)
- `notify_stale_fallback` (line 290)
- `main` (line 302)

### `thermostat/set_temps.py`

- `normalize_text` (line 44)
- `tokenize` (line 51)
- `target_matches` (line 56)
- `describe_location` (line 71)
- `candidate_strings` (line 80)
- `find_location_by_id` (line 95)
- `find_location_by_target` (line 107)
- `select_locations` (line 126)
- `submit_device_change` (line 151)
- `set_hold_payload` (line 177)
- `set_resume_schedule_payload` (line 191)
- `launchagent_paths` (line 205)
- `stop_launchagent` (line 212)
- `apply_device_changes` (line 231)
- `build_parser` (line 299)
- `main` (line 338)

## Dashboard JavaScript function index

Named function declarations and named function expressions, extracted from the HTML pages. Anonymous callbacks and dynamically assigned arrow functions are not an exhaustive callable inventory; consult the page source for those.

### `docs/codes.html`

- `showApp` (line 635)
- `showGate` (line 644)
- `roomNumberFromKey` (line 684)
- `lockboxCodeKey` (line 689)
- `lockboxLocationKey` (line 690)
- `lockboxNotesKey` (line 691)
- `roomAcFilterSizeKey` (line 692)
- `extraLockboxKeys` (line 693)
- `defaultFieldValue` (line 702)
- `resolveSavedOrDefault` (line 712)
- `canonicalizeCodeFields` (line 725)
- `hashCodeFields` (line 735)
- `updateDateOverdueState` (line 741)
- `createFieldInput` (line 751)
- `appendSectionLabel` (line 766)
- `renderLabelValueTable` (line 779)
- `renderHeaderRow` (line 800)
- `wrapTable` (line 812)
- `renderRoomsTable` (line 819)
- `renderExtraLockboxesTable` (line 888)
- `renderProperty` (line 940)
- `initCodes` (line 1002)
- `isCodesUser` (line 1004)
- `liveRef` (line 1006)
- `versionsCol` (line 1007)
- `writeSnapshot` (line 1009)
- `latestVersionHash` (line 1020)
- `collectForm` (line 1026)
- `refreshHouse` (line 1034)
- `formatWhen` (line 1046)
- `historyOpts` (line 1052)
- `appendHousesBack` (line 1058)
- `renderHousePicker` (line 1071)
- `loadHistory` (line 1092)
- `showVersion` (line 1142)
- `restoreLiveCodes` (line 1175)
- `bindHouse` (line 1208)

### `docs/index.html`

- `fmtDate` (line 299)
- `chicagoToday` (line 305)
- `escapeText` (line 314)
- `roomLabel` (line 324)
- `operatorLists` (line 331)
- `occupancyTable` (line 349)
- `renderOccupancy` (line 359)
- `getImportantIds` (line 431)
- `getMessageCreatedValue` (line 434)
- `sortTaskMessages` (line 440)
- `removeTaskMessageFromTask` (line 449)
- `buildTaskMessageRow` (line 458)
- `renderTaskMessages` (line 481)
- `upsertTaskMessage` (line 491)
- `removeTaskMessage` (line 512)
- `buildImportantBtn` (line 519)
- `buildTaskChatBox` (line 543)
- `buildManualTaskRow` (line 600)
- `updateManualBadgeCount` (line 622)
- `render` (line 627)
- `renderThermostats` (line 754)
- `renderWeather` (line 798)
- `initFirestore` (line 864)
- `initNotes` (line 878)

### `docs/kpi-history.html`

- `makeLineDataset` (line 315)
- `makeThresholdDataset` (line 328)
- `scoreColor` (line 341)
- `naira` (line 346)
- `normalizeScore` (line 353)
- `payoutForScore` (line 356)
- `formatScoreWithPayout` (line 361)
- `scoreBadge` (line 365)
- `renderHistory` (line 371)
- `snap` (line 395)
- `showApp` (line 590)
- `showGate` (line 594)
- `loadHistory` (line 598)

### `docs/private-messages.html`

- `fmtDate` (line 193)
- `renderList` (line 201)
- `initApp` (line 271)

### `docs/stats.html`

- `toNum` (line 625)
- `money` (line 630)
- `naira` (line 634)
- `pct` (line 642)
- `fmtDate` (line 646)
- `statsFreshness` (line 652)
- `applyStatsFreshness` (line 669)
- `scoreClass` (line 701)
- `normalizeScore` (line 707)
- `payoutForScore` (line 711)
- `formatScorePayout` (line 717)
- `formatScoreFormula` (line 722)
- `setManualStatus` (line 730)
- `setScoreCard` (line 736)
- `deriveManualDefaults` (line 782)
- `fillManualForm` (line 804)
- `readManualForm` (line 812)
- `computeManualKpis` (line 821)
- `applyManualInputs` (line 908)
- `resetManualInputs` (line 914)
- `setOccupancyCards` (line 921)
- `renderVacancy` (line 942)
- `renderRetention` (line 965)
- `renderFlipRooms` (line 1001)
- `renderTickets` (line 1022)
- `renderPerProperty` (line 1045)
- `renderStats` (line 1069)
- `showApp` (line 1149)
- `showGate` (line 1153)
- `loadStats` (line 1157)

### `docs/templates.html`

- `buildGrid` (line 207)
- `initApp` (line 282)

### `docs/vendors.html`

- `buildVendorCard` (line 201)
- `upsertVendorStatus` (line 285)
- `initFirestore` (line 297)

## Local verification on 2026-11-04

- Dashboard served over loopback and rendered in a browser using repository snapshots.
- Python 3.12.15 virtual environment and the three local requirements files installed successfully.
- `node test_codes_dashboard_render.mjs` passed.
<!-- Private property-specific operational details withheld. -->
- Live Firebase access, authenticated dashboard actions, fresh scraping, scheduled jobs and device controls have not been verified.

## Firebase verification update — 2026-11-04

The Firebase console shows the default Firestore database in `nam5` and the collections `notes`, `property_codes`, `stats`, `task_messages`, `templates`, and `vendors`. A read-only REST request using the dashboard configuration returned HTTP 200 for `notes/dashboard`. The console is on the Spark plan. Console access is separate from the Firebase Authentication account used by dashboard pages, and from local Admin SDK credentials.

The rules currently shown as deployed differ from `firestore.rules` in this clone: the deployed rules lack the explicit `property_codes/{slug}/code_versions/{id}` match. The default-deny rule therefore blocks client access to those history documents, so the codes history feature requires a deliberate review and deployment of suitable rules before it can work. The deployed rules also omit the explicit leak-alert rules, with those paths covered by default deny. No rules were deployed or data changed during verification. The existing public-read rules include notes, task messages, manual tasks, vendors, and templates.

A gitignored `.env` was created with empty service credentials and collection-only flags. Fill in only needed credentials locally; do not put secrets in chat. Live PadSplit scraping requires PadSplit credentials; Firestore Admin uploads require separately configured service credentials, and dashboard sign-in requires an authorized Firebase Authentication user.

## Local improvement branch update

The improvement branch introduces a shared responsive shell, a search over displayed occupancy lists, edit-safe refresh, and masked code inputs. Public snapshot projection now intentionally omits message and task detail; a local owner-restricted private snapshot reader and opt-in publisher are implemented, but require reviewed production configuration and deployment. It retains a limited occupancy projection that still includes property and room information. Existing public repository history and the deployed site are unchanged. Other tracked files, including financial snapshots, remain an exposure requiring separate remediation.

The previous test observation is superseded by the current run: 549 Python tests ran with one failure, the test that asserts historical rows in a rolling occupancy snapshot. The new-booking test was isolated from local environment flags and now exercises the opted-in hook path. The local Firestore emulator passed six rules tests using the installed Java 17 and pinned Firebase CLI; this does not verify deployed rules. Authenticated app flows, a fresh scrape, and production changes were not verified.

## Subsequent local changes

The overview now includes attention counts, distinguishes missing presence and vacancy ages from false/zero, and escapes task and discussion text. Secondary pages preserve unsaved edits against Firestore snapshot refreshes. Python snapshot writes use atomic replacement, public occupancy writes are projected on direct scraper runs, and failed occupancy collection marks a run degraded independently of successful stats. These are local branch changes, not production verification. Firebase listener lifecycle across sign-out and sign-in remains a follow-up.

The history-create rules were tightened locally to require the five fields and their top-level types. A local emulator test covers valid server timestamps and malformed writes; these changes have not been deployed.

## Authenticated private snapshots (local proposal)

The public dashboard snapshot intentionally omits tenant messages and individual task details. The overview and tenant-inbox pages can read a separate versioned Firestore snapshot after Firebase Authentication; local rules restrict reads to the designated operator and deny browser writes and listing. The reader validates the pointer, chunk count, indices and payload shape and treats missing or denied data as unavailable. It does not send tenant replies.

`padsplit_scraper/private_snapshot.py` provides a standalone, disabled-by-default Admin publisher. It is not scheduled. Its explicit process flags, single-host locking limitation, project check, IAM requirements, deployment prerequisites, and lack of automatic retention are described in `PRIVATE_SNAPSHOT_DESIGN.md`. Do not enable it until the rules, owner identity, credentials and retention process have been reviewed. No local test published production data. User-facing data remains unavailable until a reviewed deployment and authorized publication occur.

The occupancy test now uses deterministic historical inputs for its specific scenario and separately checks the live rolling snapshot against schema and classification invariants. On the latest local run, all 562 Python tests, all JavaScript checks, and seven Firestore emulator tests passed. Live authentication, private publication, history restore and device integrations remain unverified.

The local overview also provides a searchable room workspace, task search, mapped navigation and a collection-health section using reported values. Missing integration status is labelled unreported. The room detail dialog, task search/status filters and group sorting, and a two-pane read-only inbox are implemented locally. Task assignment or status changes in PadSplit are not offered because no authorized server-side update path was established.

## Dashboard continuation

The room dialog displays occupancy and only directly matched authorized task data and uniquely matched climate observations. The inbox shows private conversations and keeps unsent drafts in per-tab session storage; it has no send action and clears drafts on sign-out. Task discussion and manual-task controls operate on Firestore; task status filters and property sorting do not change PadSplit. Auth and Firestore emulator tests use fictional data; an owner browser session, expired tokens and production IAM remain unverified.

## Owner-only migration warning

Local proposed rules make operational notes, task discussions, manual tasks, vendors and templates owner-only. The deployed rules observed on 2026-11-04 allowed public reads, and have not been changed. Overview listeners are owner-session gated locally and preserve same-tab unsent drafts in memory while removing private DOM on sign-out. Codes and Stats notes now use the existing operator Auth session for subscriptions, separate from the stats-auth account. These local compatibility changes have focused mock coverage; real owner-browser verification is still required before deploying rules. A Google console login is not a dashboard Firebase Auth session.

## Local demo and password reset — 2026-11-04

From the repository root, run `venv/bin/python demo/server.py --demo --port 8770` and open `http://127.0.0.1:8770/`. This separate loopback-only server serves fictional fixtures and browser-local changes; use Reset demo data to restore fixtures. It does not initialize production Firebase or send messages or device requests. Demo interactions simulate results and are not evidence of production functionality. The server requires `--demo`, checks loopback host, and applies a restrictive content security policy. The production Pages directory does not include the demo.

The owner sign-in pages offer a Firebase password-reset form with non-enumerating confirmation and rate-limit/network feedback. Tests use mocks and send no reset email. Firebase's default email-action handler is used; any custom continue URL must be explicitly configured and authorized before production use. The console's authorized domains observed during this session did not include the GitHub Pages hostname. The actual owner-browser sign-in, recovery email, session expiry, and production authorization must still be verified.

The local Python regression suite passed 563 tests, the JavaScript checks passed, the isolated demo passed three tests, and 12 Firebase Auth/Firestore emulator tests passed. These results do not change or verify deployed rules. The demo dashboard overview was checked at 1440, 1024, 768 and 390 CSS pixels; the mobile navigation was opened and inspected. Other screens and interaction states have not all received equivalent responsive or accessibility coverage.

## Branch privacy and private occupancy continuation — 2026-11-04

The branch removes the tracked raw statistics, financial history and thermostat output files, replaces public occupancy and climate snapshots with withheld-data placeholders, and removes publication of raw thermostat readings from the branch's scheduled workflow and local run scripts. Public occupancy projection intentionally emits no room records. Existing public Git history, the deployed Pages site and deployed Firestore rules are unchanged by a branch push.

An explicitly enabled private snapshot publisher can include a separately supplied private occupancy file; the owner dashboard reads those room records only from the authenticated private snapshot and clears them on sign-out or account switch. See `PRIVATE_SNAPSHOT_DESIGN.md` for the command and prerequisites. No live publication or authenticated owner-browser verification was performed. If a private snapshot has not been published, occupancy remains unavailable. A private recovery copy of removed local files was retained outside the repository with restrictive filesystem permissions.

The production overview now has distinct views for rooms, tasks, climate and system health; section changes retain mounted inputs. The isolated fictional demo also supports list-to-conversation navigation on mobile. Contact-related automation configuration was moved to environment variables; review the environment setup and confirm intended recipients before enabling any messaging automation.

## Current continuation — 2026-11-04

The demo and production share visual tokens, navigation icons, a property-summary component, task-status filtering and a browser-local compact-density preference; their page renderers and data adapters remain separate. Vendors and templates now have search that leaves editable cards mounted. Creating a vendor uses a validated form; identity transitions clear vendor, template and inbox-note fields. Access-code restore compares and replaces inside a Firestore transaction. Discussion actions report failures and block blank posts. Result delivery now requires a durable claim and holds uncertain results for manual reconciliation. See `API_AND_WORKFLOW_AUDIT.md` for operation-by-operation evidence, remaining schema-validation gaps and deployment requirements.

The publication guard checks public snapshot shape, code defaults, selected secret patterns and optionally a private local blocklist; it cannot certify unknown secrets, binaries or Git history. Existing public history and the current production site still require owner-led containment and remediation. See `PUBLICATION_CHECKS.md`.
