# Function and job reference

This document describes the checked-in source. It is not evidence that any integration or scheduled job is enabled in production. Line numbers refer to the current working tree at generation time. The generated index below lists Python definitions, named JavaScript declarations and common arrow assignments. Anonymous callbacks and dynamically computed JavaScript callables are not exhaustively indexed. The index is navigation, not a substitute for reviewing control flow and side effects before executing a function.

## Execution flows and side effects

| Entry or component | Inputs and output | Callers / side effects / related tests |
|---|---|---|
| `padsplit_scraper/scraper.py:main` / `run` | Credentials/environment and optional `--messages-only`, `--isolate-output`; returns run status/exit code and JSON snapshots. | Calls login and paginated PadSplit APIs, occupancy/KPI derivation and persistence; may invoke gated action hooks and Firestore stats upload. Tests: `test_padsplit_scraper.py`, `test_padsplit_occupancy.py`, `test_padsplit_kpis.py`, `test_stats_firestore.py`. |
| `python -m padsplit_scraper.job_runner` | Optional `--messages-only`; prints JSON result. | Acquires process lock, isolates local files, passes immutable collection-only policy and never Git-publishes. Isolation alone does **not** disable Firestore uploads if Admin credentials are present. Tests: `test_job_runner.py`, `test_process_lock.py`. |
| `padsplit_scraper/persist.py` | Collector objects and paths → local snapshots and stats documents. | Writes local JSON; stats may be uploaded via Admin SDK. Tests: `test_stats_firestore.py`, `test_stats_freshness.py`. |
| `padsplit_scraper/occupancy.py`, `kpis.py` | Messages/tasks and room, earnings and property data → derived occupancy/KPIs. | Called by scraper; calculations should preserve distinction between inferred presence and listed status. Tests: `test_padsplit_occupancy.py`, `test_padsplit_kpis.py`. |
| `thermostat/scraper.py`, `set_temps.py`, `schedule.py` | TCC login, device target/setpoints or schedule slots. | Reads devices; set/schedule operations can change devices or macOS LaunchAgents. Tests: `test_thermostat_scraper.py`, `test_thermostat_set_temps.py`, `test_thermostat_schedule.py`. |
| `python -m smarthome` / `python -m smarthome.watcher` | CLI arguments/cloud credentials, or schedule/policy. | Cloud device reads and potential controls; watcher can enforce policy and notify. Tests `test_smarthome_*.py`. |
| `message_drafter.py`, `message_summarizer.py` | Templates/messages and optional AI credentials → drafts/summaries. | May call AI APIs and write local outputs; tests `test_message_summarizer.py`. Drafts are not evidence of tenant delivery. |
| `slack_task_digest.py`, `obsidian_daily_digest.py` | Scraped data, configuration → digest. | Discord posting is possible outside dry-run; Obsidian script writes a local daily note. Tests `test_slack_task_digest.py`, `test_obsidian_daily_digest.py`. |
| `field_mms.py`, `google_voice_chrome.py`, `quo_sender.py` | Daily field summary or leak-alert plan and transport configuration. | Can send SMS via Quo, Google Voice or Messages depending on gates. Tests `test_field_mms.py`, `test_quo_sender.py`. |
| `new_booking.py`, `frontload_webhook.py`, `lock_codes.py`, `lockout_reply.py`, `leak_reply.py`, `leak_alert.py`, `leak_alert_bland.py` | Scraper context/events, stored state, optional integration credentials. | Gated notifications, outbound webhooks, lock operations, tenant replies and voice/SMS. Distinct defaults and dry-run policies apply; read each module before execution. Tests named `test_padsplit_new_booking.py`, `test_frontload_webhook.py`, `test_lock_codes.py`, `test_lockout_reply.py`, `test_leak_reply.py`, `test_leak_alert.py`, `test_leak_alert_bland.py`. |
| `codes_history.py` | Admin client/current time → counts. | Reads codes and versions, writes changed snapshots and prunes old versions; skips CI. Test `test_codes_history.py`. |
| `functions/main.py:leak_alert_bland` | HTTPS request and bound secrets → JSON response/status. | `POST`-style webhook handling verifies HMAC; `/confirm` validates bearer. It reads pending records, writes outcomes/queue through Admin Firestore. It does not itself send Quo messages or initiate calls. `functions/leak_alert_bland.py` contains validation and pure helpers; tests `test_leak_alert_bland.py`. |
| Dashboard pages in `docs/` | User actions, local JSON, Firebase web auth / Firestore snapshots → DOM. | Inputs can create/update/delete real Firestore documents depending on authentication and rules. See `DATABASE_REFERENCE.md`; browser JS is not executed by GitHub Pages server-side. |

## Scheduled entry points

The following definitions are in source; timezone is UTC for GitHub cron, while macOS LaunchAgent schedule semantics depend on host configuration. Presence in the repository does not prove deployment or enablement.

| Definition | Schedule / behavior | Side effects |
|---|---|---|
| `.github/workflows/scrape.yml` | `17,47 * * * *`, plus manual dispatch | Collects PadSplit; copies `latest.json` and `occupancy.json`, may post a task digest, commits/pushes rolling snapshots; can upload private stats if credentials configured. |
| `.github/workflows/thermostat.yml` | `0 11 * * *`, plus manual dispatch | Scrapes TCC and publishes rolling thermostat snapshot. |
| `.github/workflows/slack_digest.yml` | `0 11 * * *`, plus manual dispatch | Posts task digest when conditions are met, with ledger handling. |
| `.github/workflows/summarize_messages.yml` | `17 1,10,18 * * *`, plus manual dispatch | Summarizes messages; inspect workflow and configuration for publication/notification behavior. |
| `run_morning.sh`, `run_afternoon.sh` | Existing Mac scheduling scripts; exact schedule depends on installed LaunchAgents | Scrape and Git operations. Paths are specific to the original machine; inspect before use. |
| `run_field_mms.sh`, `launchd/com.padsplit.field-mms.plist` | 07:00 host local time configuration; README specifies Chicago operations | Field SMS; source describes fallback transports. |
| `run_seo_monthly.sh`, `launchd/com.padsplit.seo-monthly.plist` | 09:00 on day 1 host local configuration; README specifies Chicago operations | Report and optional notification. |
| `smarthome.watcher`, `thermostat/schedule.py` generated agents | Configured separately on host | Can enforce device settings and/or post notifications. |

## Generated symbol index

Python entries show parameter names (including `self`), declared return type if present, source docstring summary when available, and same-file syntactic caller names. Caller matching is a simple name-based scan, so dynamic calls, aliases, callbacks and cross-module callers may be missing or ambiguous; lack of a listed caller does not mean unused. Return annotations are declarations, not runtime guarantees. Refer to the component table and source for module-level side effects and related tests.

### `functions/leak_alert_bland.py`

- **`MemoryCallStore.get_pending`** (line 73); inputs: `self, call_id`; return: `Optional[Dict[str, Any]]`; callers: `handle_confirm`, `handle_webhook`.
- **`MemoryCallStore.put_pending`** (line 77); inputs: `self, call_id, doc`; return: `None`; callers: `handle_confirm`.
- **`MemoryCallStore.incident_is_pending`** (line 80); inputs: `self, incident_id`; return: `bool`; callers: `handle_confirm`, `handle_webhook`.
- **`MemoryCallStore.get_record`** (line 83); inputs: `self, incident_id, role`; return: `Optional[Dict[str, Any]]`; callers: `handle_webhook`.
- **`MemoryCallStore.put_record`** (line 87); inputs: `self, incident_id, role, doc`; return: `None`; callers: `handle_webhook`.
- **`MemoryCallStore.enqueue`** (line 90); inputs: `self, call_id, doc`; return: `bool`; callers: `handle_webhook`.
- **`MemoryCallStore.get_queue`** (line 96); inputs: `self, call_id`; return: `Optional[Dict[str, Any]]`; callers: not resolved in file.
- **`MemoryCallStore.list_queue`** (line 100); inputs: `self`; return: `List[Dict[str, Any]]`; callers: not resolved in file.
- **`MemoryCallStore.mark_queue_posted`** (line 103); inputs: `self, call_id, posted_at`; return: `None`; callers: not resolved in file.
- **`FirestoreCallStore.__init__`** (line 114); inputs: `self, client`; return: `None`; callers: not resolved in file.
- **`FirestoreCallStore.get_pending`** (line 117); inputs: `self, call_id`; return: `Optional[Dict[str, Any]]`; callers: `handle_confirm`, `handle_webhook`.
- **`FirestoreCallStore.put_pending`** (line 120); inputs: `self, call_id, doc`; return: `None`; callers: `handle_confirm`.
- **`FirestoreCallStore.incident_is_pending`** (line 123); inputs: `self, incident_id`; return: `bool`; callers: `handle_confirm`, `handle_webhook`.
- **`FirestoreCallStore.get_record`** (line 131); inputs: `self, incident_id, role`; return: `Optional[Dict[str, Any]]`; callers: `handle_webhook`.
- **`FirestoreCallStore.put_record`** (line 134); inputs: `self, incident_id, role, doc`; return: `None`; callers: `handle_webhook`.
- **`FirestoreCallStore.enqueue`** (line 137); inputs: `self, call_id, doc`; return: `bool`; callers: `handle_webhook`.
- **`FirestoreCallStore.get_queue`** (line 149); inputs: `self, call_id`; return: `Optional[Dict[str, Any]]`; callers: not resolved in file.
- **`FirestoreCallStore.list_queue`** (line 152); inputs: `self`; return: `List[Dict[str, Any]]`; callers: not resolved in file.
- **`FirestoreCallStore.mark_queue_posted`** (line 161); inputs: `self, call_id, posted_at`; return: `None`; callers: not resolved in file.
- **`safe_doc_id`** (line 168); inputs: `value`; return: `str`; callers: `FirestoreCallStore.enqueue`, `FirestoreCallStore.get_pending`, `FirestoreCallStore.get_queue`, `FirestoreCallStore.mark_queue_posted`, `FirestoreCallStore.put_pending`, `_record_ref`.
- **`_hmac_matches`** (line 177); inputs: `secret, material, provided`; return: `bool`; callers: `verify_signature`.
- **`canonical_json_bytes`** (line 184); inputs: `raw`; return: `Optional[bytes]`; callers: `verify_signature`. Compact JSON matching JSON.stringify(parsed body). None if raw is not JSON.
- **`verify_signature`** (line 194); inputs: `secret, raw, signature`; return: `bool`; callers: `handle_webhook`. HMAC-SHA256 hex over the raw body, then over compact re-serialized JSON.
- **`confirm_bearer`** (line 220); inputs: `tool_bearer, webhook_secret`; return: `str`; callers: not resolved in file. Tool bearer, or the account signing secret when the tool bearer is unset.
- **`rejected_call_fields`** (line 228); inputs: `payload`; return: `List[str]`; callers: not resolved in file. Fields named by a Bland 400 body. Only analysis_schema and tools.
- **`without_rejected_fields`** (line 241); inputs: `body, fields`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`bearer_matches`** (line 248); inputs: `header, secret`; return: `bool`; callers: `handle_confirm`. Constant-time compare for the confirm_dispatch tool header.
- **`header_value`** (line 265); inputs: `headers, name`; return: `str`; callers: `stale_timestamp`.
- **`parse_time`** (line 275); inputs: `value`; return: `Optional[datetime]`; callers: `stale_timestamp`.
- **`stale_timestamp`** (line 300); inputs: `payload, headers, now`; return: `bool`; callers: `handle_webhook`. Reject when Bland included a timestamp and it is outside the window.
- **`strip_phones`** (line 323); inputs: `text`; return: `str`; callers: `_summary_text`.
- **`voicemail_message`** (line 329); inputs: `script`; return: `str`; callers: `build_call_body`. Leave-message only. first_sentence and task stay the shared planner script.
- **`task_for`** (line 334); inputs: `script`; return: `str`; callers: `build_call_body`.
- **`confirm_tool`** (line 344); inputs: `webhook_url, secret`; return: `Dict[str, Any]`; callers: `build_call_body`.
- **`build_call_body`** (line 376); inputs: `phone_number, script, incident_id, role, webhook_url, webhook_secret, citation_schema_id`; return: `Dict[str, Any]`; callers: not resolved in file. POST /v1/calls body. Caller must not log it: it can contain a phone and the tool secret.
- **`format_result_line`** (line 413); inputs: `role, outcome, confirmed, eta_minutes`; return: `str`; callers: `handle_webhook`.
- **`classify_outcome`** (line 437); inputs: `payload`; return: `Optional[str]`; callers: `build_record`, `handle_webhook`. Map a Bland call object to answered, voicemail, no_answer, or failed.
- **`_as_bool`** (line 463); inputs: `value`; return: `Optional[bool]`; callers: `_from_citations`, `extract_confirmation`, `handle_confirm`.
- **`_as_eta`** (line 477); inputs: `value`; return: `Optional[int]`; callers: `_from_citations`, `extract_confirmation`, `handle_confirm`.
- **`_from_citations`** (line 491); inputs: `payload`; return: `tuple[Optional[bool], Optional[int]]`; callers: `extract_confirmation`.
- **`extract_confirmation`** (line 509); inputs: `payload, pending`; return: `tuple[Optional[bool], Optional[int]]`; callers: `build_record`.
- **`_summary_text`** (line 548); inputs: `payload`; return: `str`; callers: `build_record`.
- **`build_record`** (line 561); inputs: `payload, incident_id, role, source, now, pending`; return: `Optional[Dict[str, Any]]`; callers: `handle_webhook`.
- **`_queue_doc`** (line 596); inputs: `record, line, now`; return: `Dict[str, Any]`; callers: `handle_webhook`.
- **`handle_webhook`** (line 608); inputs: `raw, signature, secret, store, now, headers, dry_run, result_log`; return: `HandleResult`; callers: not resolved in file. Verify, require a pending call, write the record, enqueue one Quo line.
- **`handle_confirm`** (line 686); inputs: `raw, authorization, secret, store, now`; return: `HandleResult`; callers: not resolved in file. Mid-call confirm_dispatch. Bearer secret, pending call required. No Quo post.
- **`_snap_dict`** (line 725); inputs: `snap`; return: `Optional[Dict[str, Any]]`; callers: `FirestoreCallStore.get_pending`, `FirestoreCallStore.get_queue`, `FirestoreCallStore.get_record`.
- **`_record_ref`** (line 732); inputs: `client, incident_id, role`; return: `Any`; callers: `FirestoreCallStore.get_record`, `FirestoreCallStore.put_record`.

### `functions/main.py`

- **`leak_alert_bland`** (line 48); inputs: `req`; return: `https_fn.Response`; callers: not resolved in file.

### `message_drafter.py`

- **`load_environment`** (line 56); inputs: `none`; return: `None`; callers: `main`.
- **`parse_dt`** (line 60); inputs: `value`; return: `Optional[datetime]`; callers: `format_history`, `prune_state`, `select_candidates`.
- **`utc_now`** (line 72); inputs: `none`; return: `datetime`; callers: `main`, `make_draft`, `select_candidates`.
- **`iso_utc`** (line 76); inputs: `dt`; return: `str`; callers: `format_history`, `main`, `make_draft`, `select_candidates`.
- **`normalize`** (line 80); inputs: `value`; return: `str`; callers: `build_prompt`, `format_history`, `make_draft`, `occupant_names`, `property_address`, `scope_matches`, `select_candidates`, `sender_name`.
- **`mask_pii`** (line 84); inputs: `value`; return: `str`; callers: `build_prompt`, `format_history`, `make_draft`.
- **`load_json`** (line 91); inputs: `path, default`; return: `unspecified`; callers: `load_state`, `main`.
- **`save_json`** (line 97); inputs: `path, payload`; return: `None`; callers: `main`.
- **`sender_name`** (line 102); inputs: `sender`; return: `str`; callers: `build_prompt`, `format_history`, `is_tenant_message`, `make_draft`, `template_fallback`.
- **`occupant_names`** (line 106); inputs: `thread`; return: `set[str]`; callers: `is_tenant_message`.
- **`is_tenant_message`** (line 116); inputs: `thread, message`; return: `bool`; callers: `format_history`, `select_candidates`.
- **`property_address`** (line 125); inputs: `thread`; return: `str`; callers: `build_prompt`, `load_property_sop`, `make_draft`, `scope_matches`.
- **`slugify`** (line 133); inputs: `value`; return: `str`; callers: `load_property_sop`, `scope_matches`.
- **`scope_matches`** (line 138); inputs: `thread, filters`; return: `bool`; callers: `select_candidates`.
- **`classify_message`** (line 162); inputs: `text, thread`; return: `Tuple[str, str]`; callers: `make_draft`.
- **`load_template`** (line 200); inputs: `path, fallback`; return: `str`; callers: `build_prompt`.
- **`load_property_sop`** (line 204); inputs: `thread`; return: `Tuple[str, str]`; callers: `build_prompt`.
- **`format_history`** (line 217); inputs: `thread`; return: `str`; callers: `build_prompt`.
- **`build_prompt`** (line 234); inputs: `thread, message, category`; return: `Tuple[str, str, str, str]`; callers: `make_draft`.
- **`call_claude`** (line 271); inputs: `system_prompt, user_prompt`; return: `str`; callers: `make_draft`.
- **`template_fallback`** (line 318); inputs: `thread, message, category`; return: `str`; callers: `make_draft`.
- **`load_state`** (line 329); inputs: `none`; return: `Dict`; callers: `main`.
- **`prune_state`** (line 337); inputs: `state, now`; return: `Dict`; callers: `main`.
- **`already_handled`** (line 348); inputs: `state, message_id, last_message_at`; return: `bool`; callers: `select_candidates`.
- **`select_candidates`** (line 356); inputs: `payload, state, scope_filter`; return: `List[Tuple[Dict, Dict, str, str]]`; callers: `main`.
- **`send_critical_discord_alert`** (line 395); inputs: `draft`; return: `None`; callers: `main`.
- **`make_draft`** (line 411); inputs: `thread, message, message_id, last_message_at, use_ai`; return: `Dict`; callers: `main`.
- **`update_state`** (line 439); inputs: `state, draft`; return: `None`; callers: `main`.
- **`print_stdout`** (line 448); inputs: `drafts`; return: `None`; callers: `main`.
- **`main`** (line 455); inputs: `none`; return: `None`; callers: not resolved in file.

### `message_summarizer.py`

- **`format_room`** (line 36); inputs: `chat`; return: `str`; callers: `render_summary`.
- **`format_address`** (line 43); inputs: `chat`; return: `str`; callers: `render_summary`.
- **`_normalize_whitespace`** (line 57); inputs: `value`; return: `str`; callers: `_sender_name`, `extract_tenant_text`, `occupant_names`.
- **`occupant_names`** (line 61); inputs: `chat`; return: `set[str]`; callers: `_is_tenant_sender`.
- **`_sender_name`** (line 78); inputs: `sender`; return: `str`; callers: `_is_tenant_sender`.
- **`_is_tenant_sender`** (line 85); inputs: `chat, sender`; return: `bool`; callers: `extract_tenant_text`.
- **`extract_tenant_text`** (line 92); inputs: `chat`; return: `str | None`; callers: `render_summary`.
- **`parse_urgent_items`** (line 117); inputs: `raw`; return: `list[dict]`; callers: `main`.
- **`render_summary`** (line 142); inputs: `urgent_items, messages_by_id`; return: `str`; callers: `main`.
- **`call_minimax`** (line 168); inputs: `prompt`; return: `str`; callers: `main`.
- **`truncate_for_discord`** (line 230); inputs: `message`; return: `str`; callers: `send_to_discord`.
- **`send_to_discord`** (line 237); inputs: `message`; return: `None`; callers: `main`.
- **`main`** (line 266); inputs: `none`; return: `None`; callers: not resolved in file.

### `obsidian_daily_digest.py`

- **`load_environment`** (line 26); inputs: `none`; return: `None`; callers: `main`.
- **`parse_iso_datetime`** (line 31); inputs: `value`; return: `Optional[datetime]`; callers: `format_local_timestamp`, `select_recent_tenant_messages`.
- **`format_local_timestamp`** (line 43); inputs: `value`; return: `str`; callers: `format_draft_replies`, `render_block`.
- **`normalize_whitespace`** (line 50); inputs: `value`; return: `str`; callers: `format_draft_replies`, `format_task_address`, `format_task_groups`, `get_thread_occupant_names`, `select_recent_tenant_messages`.
- **`load_json`** (line 54); inputs: `path`; return: `Dict`; callers: `main`.
- **`get_daily_note_path`** (line 58); inputs: `base_dir, note_date`; return: `Path`; callers: `write_daily_digest`.
- **`get_counts`** (line 66); inputs: `tasks`; return: `Tuple[int, int, int]`; callers: `render_block`.
- **`format_task_address`** (line 73); inputs: `task`; return: `str`; callers: `format_task_groups`.
- **`format_task_groups`** (line 82); inputs: `tasks`; return: `List[str]`; callers: `render_block`.
- **`get_thread_occupant_names`** (line 106); inputs: `thread`; return: `set[str]`; callers: `select_recent_tenant_messages`.
- **`select_recent_tenant_messages`** (line 123); inputs: `payload`; return: `List[str]`; callers: `render_block`.
- **`format_thermostat_lines`** (line 164); inputs: `payload`; return: `List[str]`; callers: `render_block`.
- **`load_drafts_payload`** (line 191); inputs: `path`; return: `Optional[Dict]`; callers: `main`.
- **`format_draft_replies`** (line 201); inputs: `drafts_payload`; return: `List[str]`; callers: `render_block`.
- **`render_block`** (line 238); inputs: `padsplit_payload, thermostat_payload, generated_at, drafts_payload`; return: `str`; callers: `write_daily_digest`.
- **`upsert_managed_block`** (line 270); inputs: `existing_text, block`; return: `str`; callers: `write_daily_digest`.
- **`write_daily_digest`** (line 285); inputs: `padsplit_payload, thermostat_payload, daily_notes_dir, generated_at, drafts_payload`; return: `Path`; callers: `main`.
- **`main`** (line 300); inputs: `none`; return: `None`; callers: not resolved in file.

### `padsplit_scraper/codes_history.py`

- **`canonicalize_fields`** (line 29); inputs: `data`; return: `Dict[str, str]`; callers: `catch_up`, `content_hash`, `restore_payload`.
- **`content_hash`** (line 38); inputs: `data`; return: `str`; callers: `catch_up`.
- **`restore_payload`** (line 43); inputs: `fields, updated_at`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`_as_dt`** (line 49); inputs: `value`; return: `Optional[datetime]`; callers: `_latest_row`, `catch_up`.
- **`_latest_row`** (line 59); inputs: `rows`; return: `Optional[Dict[str, Any]]`; callers: `catch_up`.
- **`_log`** (line 70); inputs: `message`; return: `None`; callers: `catch_up`.
- **`catch_up`** (line 74); inputs: `client, now, environ`; return: `Dict[str, Any]`; callers: `main`.
- **`main`** (line 147); inputs: `none`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/discord_notifier.py`

- **`_load_latest_payload`** (line 13); inputs: `base_dir`; return: `Dict`; callers: `main`.
- **`_format_task_line`** (line 24); inputs: `task`; return: `str`; callers: `_build_digest`.
- **`_build_digest`** (line 33); inputs: `payload`; return: `str`; callers: `main`.
- **`_init_firestore_app`** (line 58); inputs: `none`; return: `'firebase_admin.App'`; callers: `main`.
- **`_load_ac_filter_dates`** (line 76); inputs: `app`; return: `List[Dict]`; callers: `main`.
- **`post_discord_message`** (line 108); inputs: `text, token, channel`; return: `Dict`; callers: `main`.
- **`main`** (line 130); inputs: `none`; return: `None`; callers: not resolved in file.

### `padsplit_scraper/discord_reply_monitor.py`

- **`_load_json`** (line 63); inputs: `path, default`; return: `unspecified`; callers: `_load_processed_replies`, `main`.
- **`_load_latest_payload`** (line 69); inputs: `base_dir`; return: `Dict`; callers: `main`.
- **`_tokenize`** (line 80); inputs: `value`; return: `List[str]`; callers: `_build_address_matcher`, `_canonicalize_text`, `_find_matching_tasks`.
- **`_canonicalize_tokens`** (line 85); inputs: `tokens`; return: `List[str]`; callers: `_build_address_matcher`, `_canonicalize_text`, `_find_matching_tasks`.
- **`_canonicalize_text`** (line 89); inputs: `value`; return: `str`; callers: `_address_variants`, `_build_address_matcher`, `_find_matching_tasks`.
- **`_address_variants`** (line 93); inputs: `address`; return: `List[str]`; callers: `_build_address_matcher`.
- **`_address_core_tokens`** (line 100); inputs: `tokens`; return: `Set[str]`; callers: `_build_address_matcher`.
- **`_build_address_matcher`** (line 104); inputs: `tasks`; return: `Dict`; callers: `main`.
- **`_load_processed_replies`** (line 142); inputs: `path`; return: `Set[str]`; callers: `main`.
- **`_save_processed_replies`** (line 150); inputs: `path, processed_ts`; return: `None`; callers: `main`.
- **`_fetch_replies`** (line 159); inputs: `token, channel, message_id`; return: `List[Dict]`; callers: `main`.
- **`_find_matching_tasks`** (line 174); inputs: `text, matcher`; return: `Tuple[str, List[int]]`; callers: `_extract_completed_task_ids`.
- **`_extract_completed_task_ids`** (line 210); inputs: `text, matcher`; return: `List[int]`; callers: `main`.
- **`main`** (line 217); inputs: `none`; return: `None`; callers: not resolved in file.

### `padsplit_scraper/field_mms.py`

- **`Window.id`** (line 126); inputs: `self`; return: `str`; callers: not resolved in file.
- **`load_environment`** (line 141); inputs: `none`; return: `None`; callers: `main`.
- **`running_in_ci`** (line 145); inputs: `none`; return: `bool`; callers: `main`, `plan_send`, `sending_allowed`.
- **`sending_allowed`** (line 149); inputs: `none`; return: `bool`; callers: `send_group_mms`, `send_via_quo`. CI must never send. Live send is the Mac launchd job (prefer Mac).
- **`normalize_phone`** (line 165); inputs: `value`; return: `str`; callers: `assert_group_recipients`, `assert_quo_recipients`, `resolve_quo_from_number`, `send_group_mms`.
- **`assert_group_recipients`** (line 174); inputs: `recipients`; return: `List[str]`; callers: `send_group_mms`.
- **`assert_quo_recipients`** (line 192); inputs: `recipients`; return: `List[str]`; callers: `resolve_quo_recipients`, `send_group_mms`, `send_via_quo`. Don + Dad required. Ang GV is the default third. Joe is not required.
- **`resolve_quo_recipients`** (line 216); inputs: `none`; return: `List[str]`; callers: `plan_send`, `send_group_mms`.
- **`window_for`** (line 227); inputs: `now`; return: `Window`; callers: `run_window`. Morning 7am CT only. Before 7am is the prior day's 7am window (catch-up).
- **`_parse_created`** (line 236); inputs: `value`; return: `Optional[datetime]`; callers: `fetch_discord_channel_messages`, `summarize_host_messages`.
- **`normalize_whitespace`** (line 248); inputs: `value`; return: `str`; callers: `_occupant_names`, `_sender_name`, `format_house`, `message_why`.
- **`sanitize_sms`** (line 252); inputs: `text`; return: `str`; callers: `build_mms_body`, `extract_open_task_lines`, `summarize_host_messages`.
- **`contains_lock_code_like`** (line 262); inputs: `text`; return: `bool`; callers: `build_mms_body`, `extract_open_task_lines`, `plan_send`, `summarize_host_messages`.
- **`format_house`** (line 266); inputs: `thread`; return: `str`; callers: `summarize_host_messages`.
- **`format_room`** (line 272); inputs: `thread`; return: `str`; callers: `summarize_host_messages`.
- **`_sender_name`** (line 279); inputs: `person`; return: `str`; callers: `is_tenant_item`.
- **`_occupant_names`** (line 287); inputs: `thread`; return: `Set[str]`; callers: `is_tenant_item`.
- **`is_tenant_item`** (line 305); inputs: `thread, message`; return: `bool`; callers: `summarize_host_messages`.
- **`message_why`** (line 330); inputs: `message`; return: `str`; callers: `summarize_host_messages`.
- **`_iter_thread_messages`** (line 361); inputs: `thread`; return: `Iterable[Dict[str, Any]]`; callers: `summarize_host_messages`.
- **`summarize_host_messages`** (line 371); inputs: `threads, since`; return: `List[str]`; callers: `collect_host_lines`.
- **`extract_open_task_lines`** (line 411); inputs: `content`; return: `List[str]`; callers: `digest_discord_open_tasks`.
- **`digest_discord_open_tasks`** (line 438); inputs: `messages`; return: `List[str]`; callers: `collect_task_lines`.
- **`build_mms_body`** (line 449); inputs: `host_lines, task_lines`; return: `str`; callers: `plan_send`.
- **`plan_send`** (line 463); inputs: `host_lines, task_lines, window, sent_windows, ci`; return: `SendPlan`; callers: `run_window`.
- **`load_sent_windows`** (line 504); inputs: `path`; return: `Set[str]`; callers: `run_window`, `save_sent_window`.
- **`save_sent_window`** (line 520); inputs: `window_id, path`; return: `None`; callers: `run_window`.
- **`fetch_host_threads`** (line 528); inputs: `none`; return: `List[Dict[str, Any]]`; callers: `collect_host_lines`.
- **`load_latest_json_threads`** (line 537); inputs: `none`; return: `List[Dict[str, Any]]`; callers: `collect_host_lines`.
- **`collect_host_lines`** (line 549); inputs: `since`; return: `List[str]`; callers: not resolved in file.
- **`fetch_discord_channel_messages`** (line 563); inputs: `token, since`; return: `List[Dict[str, Any]]`; callers: `collect_task_lines`.
- **`collect_task_lines`** (line 583); inputs: `since`; return: `List[str]`; callers: not resolved in file.
- **`_escape_applescript`** (line 596); inputs: `value`; return: `str`; callers: `send_via_messages_chat`.
- **`resolve_field_mms_transport`** (line 600); inputs: `none`; return: `str`; callers: `send_group_mms`. quo \| google_voice \| messages \| auto (default: Quo then GV then Messages).
- **`resolve_quo_from_number`** (line 612); inputs: `none`; return: `str`; callers: `send_via_quo`.
- **`resolve_quo_api_key`** (line 624); inputs: `none`; return: `str`; callers: `send_via_quo`.
- **`send_via_quo`** (line 631); inputs: `body, recipients, http_post`; return: `None`; callers: `send_group_mms`. Send one Quo group SMS/MMS to all recipients. Never log the API key.
- **`messages_chat_name`** (line 677); inputs: `none`; return: `str`; callers: `send_group_mms`.
- **`send_via_messages_chat`** (line 681); inputs: `body, chat_name`; return: `None`; callers: `send_group_mms`. Send one group MMS via the existing Mac Messages thread (Voice as owner).
- **`send_group_mms`** (line 712); inputs: `body, recipients`; return: `None`; callers: not resolved in file.
- **`build_launchd_plist`** (line 759); inputs: `workspace`; return: `Dict[str, Any]`; callers: `install_launchd`.
- **`install_launchd`** (line 772); inputs: `workspace`; return: `Path`; callers: `main`.
- **`run_window`** (line 787); inputs: `now, host_fetcher, task_fetcher, sender, state_path, ci, dry_run`; return: `SendPlan`; callers: `main`.
- **`main`** (line 824); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/firestore_status_monitor.py`

- **`_load_json`** (line 13); inputs: `path, default`; return: `unspecified`; callers: `_load_processed_doc_ids`.
- **`_load_latest_payload`** (line 19); inputs: `base_dir`; return: `Dict`; callers: `main`.
- **`_load_processed_doc_ids`** (line 30); inputs: `path`; return: `Set[str]`; callers: `main`.
- **`_save_processed_doc_ids`** (line 38); inputs: `path, processed_doc_ids`; return: `None`; callers: `main`.
- **`_build_task_map`** (line 47); inputs: `payload`; return: `Dict[str, Dict]`; callers: `main`.
- **`_init_firestore_client`** (line 59); inputs: `none`; return: `firestore.Client`; callers: `main`.
- **`main`** (line 73); inputs: `none`; return: `None`; callers: not resolved in file.

### `padsplit_scraper/frontload_webhook.py`

- **`authorization_value`** (line 153); inputs: `key`; return: `str`; callers: `post_payload`. Bearer token for a Grok Bot routine webhook. Do not double-prefix.
- **`webhook_enabled`** (line 161); inputs: `environ`; return: `bool`; callers: not resolved in file.
- **`dry_run_enabled`** (line 165); inputs: `environ`; return: `bool`; callers: `_emit`. Default on. Explicit 0/false/no/off turns live POST on (when enabled).
- **`_flag`** (line 170); inputs: `name, default, environ`; return: `bool`; callers: `dry_run_enabled`, `webhook_enabled`.
- **`_ci`** (line 178); inputs: `environ`; return: `bool`; callers: `_send_block`.
- **`observed_at_now`** (line 184); inputs: `now`; return: `str`; callers: `_emit`.
- **`make_event_id`** (line 191); inputs: `event_type, *parts`; return: `str`; callers: `_booking_payload`, `_diff_listings`, `_move_in_payload`.
- **`_date_only`** (line 196); inputs: `value`; return: `Optional[str]`; callers: `_message_move_in_date`, `_move_in_from_thread`, `project_listings`, `project_occupancy`.
- **`_num`** (line 205); inputs: `value`; return: `Optional[float]`; callers: `intro_rent_for`, `project_listings`.
- **`_house_from_thread`** (line 217); inputs: `thread`; return: `Optional[str]`; callers: `project_bookings`, `project_message_move_ins`. Street only. Chat title is often the member name and is never used.
- **`_room_from_thread`** (line 228); inputs: `thread`; return: `Optional[str]`; callers: `project_bookings`, `project_message_move_ins`.
- **`_move_in_from_thread`** (line 239); inputs: `thread`; return: `Optional[str]`; callers: `project_bookings`, `project_message_move_ins`.
- **`_is_cancel_status`** (line 246); inputs: `status`; return: `bool`; callers: `diff_projection`.
- **`_blank_booking`** (line 255); inputs: `none`; return: `Dict[str, Any]`; callers: `_consider_booking`.
- **`_consider_booking`** (line 266); inputs: `bookings, booking_id, status, house, room, move_in_date, cancelled, seen_at`; return: `None`; callers: `project_bookings`.
- **`_iter_booking_statuses`** (line 293); inputs: `thread`; return: `Iterable[tuple]`; callers: `project_bookings`.
- **`project_bookings`** (line 306); inputs: `messages, pending_inbox`; return: `Dict[str, Dict[str, Any]]`; callers: `build_projection`. Booking rows from messenger bookingStatus plus new_booking inbox hits.
- **`_message_move_in_date`** (line 354); inputs: `message`; return: `Optional[str]`; callers: `project_message_move_ins`.
- **`project_message_move_ins`** (line 368); inputs: `messages`; return: `Dict[str, Dict[str, Any]]`; callers: `build_projection`.
- **`project_listings`** (line 410); inputs: `rooms`; return: `Dict[str, Dict[str, Any]]`; callers: `build_projection`.
- **`project_occupancy`** (line 435); inputs: `occupancy`; return: `Dict[str, Dict[str, Any]]`; callers: `build_projection`.
- **`build_projection`** (line 455); inputs: `messages, rooms, occupancy, pending_inbox`; return: `Dict[str, Any]`; callers: `_emit`. Current scrape view. ``None`` rooms/occupancy means this run did not refresh them.
- **`_empty_projection`** (line 471); inputs: `none`; return: `Dict[str, Any]`; callers: `_empty_state`, `_load_state`.
- **`resolve_projection`** (line 475); inputs: `previous, built`; return: `Dict[str, Any]`; callers: `_emit`. Keep listing/occupancy sections a messages-only run did not refresh.
- **`intro_rent_for`** (line 494); inputs: `base_price, promo_pct`; return: `Optional[float]`; callers: `_diff_listings`, `_rents`.
- **`_listing_index`** (line 503); inputs: `listings`; return: `Dict[tuple, Dict[str, Any]]`; callers: `_rents`.
- **`_rents`** (line 515); inputs: `house, room, listings`; return: `tuple`; callers: `_booking_payload`, `_move_in_payload`.
- **`_payload`** (line 523); inputs: `event_type, house, room, booking_id, intro, target, move_in_date, observed_at, event_id`; return: `Dict[str, Any]`; callers: `_booking_payload`, `_diff_listings`, `_move_in_payload`.
- **`_booking_payload`** (line 550); inputs: `event_type, booking_id, row, listings, observed_at`; return: `Dict[str, Any]`; callers: `diff_projection`.
- **`_move_in_payload`** (line 574); inputs: `house, room, move_in_date, booking_id, listings, observed_at`; return: `Dict[str, Any]`; callers: `_diff_listings`, `_diff_move_ins`.
- **`diff_projection`** (line 602); inputs: `previous, current, observed_at, listings_refreshed, occupancy_refreshed`; return: `List[Dict[str, Any]]`; callers: `_emit`. Pure diff. ``previous`` None is the baseline caller; this returns [].
- **`_booking_ids_by_room`** (line 660); inputs: `bookings`; return: `Dict[tuple, str]`; callers: `diff_projection`.
- **`_diff_move_ins`** (line 670); inputs: `previous, current, listings, observed_at, booking_ids`; return: `List[Dict[str, Any]]`; callers: `_diff_occupancy_move_ins`, `diff_projection`.
- **`_diff_occupancy_move_ins`** (line 702); inputs: `previous, current, listings, observed_at, booking_ids`; return: `List[Dict[str, Any]]`; callers: `diff_projection`.
- **`_diff_listings`** (line 728); inputs: `previous, current, observed_at, booking_ids`; return: `List[Dict[str, Any]]`; callers: `diff_projection`.
- **`_dedupe`** (line 792); inputs: `events`; return: `List[Dict[str, Any]]`; callers: `_emit`, `diff_projection`.
- **`_load_state`** (line 808); inputs: `path`; return: `Dict[str, Any]`; callers: `_emit`.
- **`_empty_state`** (line 826); inputs: `none`; return: `Dict[str, Any]`; callers: `_load_state`.
- **`_save_state`** (line 837); inputs: `path, state`; return: `None`; callers: `_emit`.
- **`_append_dry_run`** (line 850); inputs: `path, payload`; return: `None`; callers: `_deliver`.
- **`post_payload`** (line 856); inputs: `url, key, payload, post_fn, sleep_fn`; return: `tuple`; callers: `_deliver`. Return ``(ok\|retry\|reject, status_code)``. Never raises.
- **`_default_post`** (line 897); inputs: `url, json, headers, timeout`; return: `Any`; callers: not resolved in file.
- **`_mark_sent`** (line 901); inputs: `state, event_id`; return: `None`; callers: `_emit`.
- **`_queue_pending`** (line 908); inputs: `state, payload, attempts`; return: `None`; callers: `_emit`.
- **`_mark_failed`** (line 915); inputs: `state, event_id, status_code, at`; return: `None`; callers: `_emit`. Stop retrying. Store status and time only — never the payload.
- **`emit_for_scraper`** (line 929); inputs: `messages, rooms, occupancy, session, creds, now, state_path, dry_run_log_path, environ, post_fn, sleep_fn, fetch_pending_fn, policy`; return: `Dict[str, Any]`; callers: not resolved in file. Detect and emit. Returns a result dict. Does not raise.
- **`_emit`** (line 967); inputs: `messages, rooms, occupancy, session, creds, now, state_path, dry_run_log_path, environ, post_fn, sleep_fn, fetch_pending_fn, policy`; return: `Dict[str, Any]`; callers: `emit_for_scraper`.
- **`_fetch_pending`** (line 1071); inputs: `session, creds, fetch_pending_fn`; return: `Optional[List[Dict[str, Any]]]`; callers: `_emit`.
- **`_send_block`** (line 1090); inputs: `env, policy`; return: `Optional[Dict[str, Any]]`; callers: `_emit`. Return a skip result when this run must not send or touch the ledger.
- **`_deliver`** (line 1106); inputs: `event, dry_run, url, key, dry_run_log_path, post_fn, sleep_fn`; return: `tuple`; callers: `_emit`.

### `padsplit_scraper/google_voice_chrome.py`

- **`GoogleVoicePage.current_url`** (line 101); inputs: `self`; return: `str`; callers: `raise_if_google_voice_challenge`.
- **`GoogleVoicePage.page_title`** (line 102); inputs: `self`; return: `str`; callers: `raise_if_google_voice_challenge`.
- **`GoogleVoicePage.page_text`** (line 103); inputs: `self`; return: `str`; callers: `raise_if_google_voice_challenge`.
- **`GoogleVoicePage.goto_messages`** (line 104); inputs: `self`; return: `None`; callers: `send_on_google_voice_page`.
- **`GoogleVoicePage.open_compose`** (line 105); inputs: `self`; return: `None`; callers: `send_on_google_voice_page`.
- **`GoogleVoicePage.add_recipient`** (line 106); inputs: `self, phone`; return: `None`; callers: `send_on_google_voice_page`.
- **`GoogleVoicePage.recipient_chip_count`** (line 107); inputs: `self`; return: `int`; callers: `send_on_google_voice_page`.
- **`GoogleVoicePage.set_body`** (line 108); inputs: `self, body`; return: `None`; callers: `send_on_google_voice_page`.
- **`GoogleVoicePage.click_send`** (line 109); inputs: `self`; return: `None`; callers: `send_on_google_voice_page`.
- **`GoogleVoicePage.close`** (line 110); inputs: `self`; return: `None`; callers: `PlaywrightVoicePage.close`, `send_via_google_voice_chrome`.
- **`resolve_chrome_user_data_dir`** (line 113); inputs: `none`; return: `Path`; callers: `launch_playwright_voice_page`.
- **`resolve_chrome_profile_directory`** (line 120); inputs: `none`; return: `str`; callers: `launch_playwright_voice_page`.
- **`format_voice_recipient`** (line 128); inputs: `value`; return: `str`; callers: `PlaywrightVoicePage.add_recipient`.
- **`detect_google_voice_challenge`** (line 137); inputs: `url, title, visible_text`; return: `bool`; callers: `raise_if_google_voice_challenge`. True when Voice bounced to a login wall, captcha, or account challenge.
- **`raise_if_google_voice_challenge`** (line 161); inputs: `page`; return: `None`; callers: `PlaywrightVoicePage.open_compose`, `send_on_google_voice_page`.
- **`send_on_google_voice_page`** (line 170); inputs: `page, body, recipients`; return: `None`; callers: `send_via_google_voice_chrome`. Compose one group message (not three 1:1s) and send it.
- **`send_via_google_voice_chrome`** (line 192); inputs: `body, recipients, voice_page`; return: `None`; callers: not resolved in file. Send one group SMS via voice.google.com in a persistent Chrome profile.
- **`launch_playwright_voice_page`** (line 216); inputs: `none`; return: `GoogleVoicePage`; callers: `send_via_google_voice_chrome`.
- **`PlaywrightVoicePage.__init__`** (line 268); inputs: `self, playwright, context, page`; return: `None`; callers: not resolved in file.
- **`PlaywrightVoicePage.current_url`** (line 273); inputs: `self`; return: `str`; callers: `raise_if_google_voice_challenge`.
- **`PlaywrightVoicePage.page_title`** (line 279); inputs: `self`; return: `str`; callers: `raise_if_google_voice_challenge`.
- **`PlaywrightVoicePage.page_text`** (line 285); inputs: `self`; return: `str`; callers: `raise_if_google_voice_challenge`.
- **`PlaywrightVoicePage.goto_messages`** (line 291); inputs: `self`; return: `None`; callers: `send_on_google_voice_page`.
- **`PlaywrightVoicePage.open_compose`** (line 298); inputs: `self`; return: `None`; callers: `send_on_google_voice_page`.
- **`PlaywrightVoicePage.add_recipient`** (line 308); inputs: `self, phone`; return: `None`; callers: `send_on_google_voice_page`.
- **`PlaywrightVoicePage.recipient_chip_count`** (line 321); inputs: `self`; return: `int`; callers: `send_on_google_voice_page`.
- **`PlaywrightVoicePage.set_body`** (line 331); inputs: `self, body`; return: `None`; callers: `send_on_google_voice_page`.
- **`PlaywrightVoicePage.click_send`** (line 341); inputs: `self`; return: `None`; callers: `send_on_google_voice_page`.
- **`PlaywrightVoicePage.close`** (line 349); inputs: `self`; return: `None`; callers: `send_via_google_voice_chrome`.
- **`PlaywrightVoicePage._first_visible`** (line 359); inputs: `self, selectors, timeout_ms`; return: `Optional[Any]`; callers: `PlaywrightVoicePage._click_first`, `PlaywrightVoicePage.add_recipient`, `PlaywrightVoicePage.open_compose`, `PlaywrightVoicePage.set_body`.
- **`PlaywrightVoicePage._click_first`** (line 369); inputs: `self, selectors, timeout_ms`; return: `bool`; callers: `PlaywrightVoicePage.click_send`, `PlaywrightVoicePage.open_compose`.

### `padsplit_scraper/http_clients.py`

- **`TimeoutSession.__init__`** (line 15); inputs: `self, timeout`; return: `None`; callers: not resolved in file.
- **`TimeoutSession.request`** (line 19); inputs: `self, method, url, **kwargs`; return: `unspecified`; callers: not resolved in file.
- **`build_session`** (line 24); inputs: `timeout, user_agent`; return: `TimeoutSession`; callers: not resolved in file.

### `padsplit_scraper/job_runner.py`

- **`isolate_outputs`** (line 34); inputs: `environ`; return: `Path`; callers: `run_collection`.
- **`_collection_worker`** (line 40); inputs: `messages_only, **_kwargs`; return: `unspecified`; callers: not resolved in file. Real scraper entry used by the collection runner.
- **`_interpret_worker_result`** (line 49); inputs: `raw`; return: `tuple[str, int, Dict[str, Any]]`; callers: `run_collection`.
- **`_result`** (line 60); inputs: `action, exit_code, output_dir, hooks, run_status, reason, lock_pid`; return: `Dict[str, Any]`; callers: `run_collection`.
- **`run_collection`** (line 85); inputs: `messages_only, environ, lock_directory, timeout, run_fn`; return: `Dict[str, Any]`; callers: `main`. Run the scraper under a worker-lifetime lock. Never git-publishes.
- **`main`** (line 144); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/kpis.py`

- **`compute_monthly_kpis`** (line 10); inputs: `month_data`; return: `Dict[str, Any]`; callers: not resolved in file. Approximate monthly bonuses/penalties using historical metrics that are available.
- **`_parse_iso`** (line 67); inputs: `value`; return: `Optional[datetime]`; callers: `compute_kpis`.
- **`_to_num`** (line 84); inputs: `value`; return: `float`; callers: `_find_net_revenue`, `compute_kpis`, `compute_monthly_kpis`.
- **`_find_property_key`** (line 100); inputs: `room`; return: `Optional[str]`; callers: `compute_kpis`.
- **`_room_property_label`** (line 118); inputs: `room`; return: `str`; callers: `compute_kpis`.
- **`_extract_earnings_rows`** (line 146); inputs: `earnings_payload`; return: `List[Dict[str, Any]]`; callers: `compute_kpis`.
- **`_find_net_revenue`** (line 154); inputs: `row`; return: `float`; callers: `compute_kpis`.
- **`_flatten_tasks`** (line 161); inputs: `tasks_by_bucket`; return: `List[Dict[str, Any]]`; callers: `compute_kpis`.
- **`compute_kpis`** (line 172); inputs: `rooms, properties, earnings_payload, tasks_by_bucket, now, subjective_score`; return: `Dict[str, Any]`; callers: not resolved in file.

### `padsplit_scraper/leak_alert.py`

- **`live_enabled`** (line 106); inputs: `environ`; return: `bool`; callers: not resolved in file. Default off. CI and collection-only never enable alerts.
- **`normalize_e164`** (line 113); inputs: `value`; return: `str`; callers: `_group_entry`, `_participant_set`, `build_plan`, `parse_group_e164s`, `phone_from_profile_payload`. Return E.164 or empty. Callers must not log the result.
- **`recipient_hash`** (line 123); inputs: `e164`; return: `str`; callers: `_tenant_entries`.
- **`leak_category`** (line 127); inputs: `text`; return: `str`; callers: `build_plan`. Short label for the script. Does not copy the member message.
- **`fixed_script`** (line 141); inputs: `house, room, category`; return: `str`; callers: `build_plan`.
- **`tenant_body`** (line 152); inputs: `house, room, ask_photos`; return: `str`; callers: `_tenant_entries`.
- **`message_has_picture`** (line 164); inputs: `message`; return: `bool`; callers: `build_plan`.
- **`bland_voice_spec`** (line 175); inputs: `script`; return: `Dict[str, Any]`; callers: `build_plan`. Bland call shape. phone_number is applied at send time from env and is not stored.
- **`quo_private_spec`** (line 188); inputs: `content`; return: `Dict[str, Any]`; callers: `_tenant_entries`. One Quo recipient per POST. from/to are applied at send time and are not stored.
- **`parse_group_e164s`** (line 199); inputs: `raw`; return: `List[str]`; callers: `_group_entry`. Comma or whitespace list. Empty when unset. Never log the result.
- **`quo_group_spec`** (line 212); inputs: `content, participant_count`; return: `Dict[str, Any]`; callers: `_group_entry`. One group POST. The participant list is applied at send time and is not stored.
- **`_conversations_url`** (line 224); inputs: `page_token`; return: `str`; callers: `check_group_conversation`.
- **`default_quo_get`** (line 231); inputs: `url, headers`; return: `tuple[int, Dict[str, Any]]`; callers: not resolved in file. GET helper for the dry-run conversation check. The body is not logged.
- **`_participant_set`** (line 257); inputs: `values`; return: `set[str]`; callers: `check_group_conversation`.
- **`check_group_conversation`** (line 266); inputs: `expected, conversation_id, api_key, getter`; return: `str`; callers: `_group_entry`. Dry-run only. Return matched, mismatch, not_found, or check_failed.
- **`_group_entry`** (line 308); inputs: `incident, script, environ, quo_ready, dry_run, conversations_get`; return: `Dict[str, Any]`; callers: `build_plan`.
- **`assert_plan_has_no_numbers`** (line 375); inputs: `payload`; return: `None`; callers: `build_plan`, `load_state`, `persist_plan`, `save_state`.
- **`load_state`** (line 383); inputs: `path`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`save_state`** (line 400); inputs: `state, path`; return: `None`; callers: not resolved in file.
- **`_configured_status`** (line 407); inputs: `has_number, provider_ready`; return: `str`; callers: `build_plan`.
- **`occupancy_pk`** (line 415); inputs: `thread`; return: `Optional[str]`; callers: `_house_member_phones`. Numeric occupancy.pk. Messenger stores it inside the GraphQL id.
- **`phone_from_profile_payload`** (line 427); inputs: `payload`; return: `str`; callers: `_house_member_phones`. Read phone_number from a member-phone response. Empty when absent.
- **`member_phone_fetcher`** (line 440); inputs: `session, api_root`; return: `unspecified`; callers: not resolved in file. GET the host member-phone endpoint. The number is not logged.
- **`member_phone_fetcher.fetch`** (line 443); inputs: `pk`; return: `Dict[str, Any]`; callers: `check_group_conversation`.
- **`_house_member_phones`** (line 464); inputs: `source, house_threads, now, profile_fetcher`; return: `tuple[List[str], int]`; callers: `build_plan`. In-memory E.164 list for current members of this house, plus a skip count.
- **`_tenant_entries`** (line 507); inputs: `incident, house_label, room, ask_photos, phones, missing, quo_ready`; return: `List[Dict[str, Any]]`; callers: `build_plan`.
- **`build_plan`** (line 563); inputs: `thread, now, house_threads, environ, profile_fetcher, dry_run, conversations_get`; return: `Optional[AlertPlan]`; callers: not resolved in file. Plan one incident. Returns None when there is no current member leak.
- **`persist_plan`** (line 667); inputs: `plan, state, now`; return: `List[Dict[str, Any]]`; callers: not resolved in file. Record actionable keys. Missing config and over-cap results are not stored.

### `padsplit_scraper/leak_alert_bland.py`

- **`dry_run_enabled`** (line 79); inputs: `environ`; return: `bool`; callers: `calls_live`, `drain_result_queue`, `place_plan_calls`, `post_group_result_line`. Default on. Live HTTP requires an explicit off value.
- **`calls_live`** (line 88); inputs: `environ`; return: `bool`; callers: `advance_plans`, `default_bland_get`, `default_bland_post`, `poll_open_calls`, `result_post_live`.
- **`result_post_live`** (line 95); inputs: `environ`; return: `bool`; callers: `default_quo_post`, `drain_result_queue`, `post_group_result_line`. Quo result line. Own flag, still blocked by dry-run, CI, and leak-alert enable.
- **`mask_e164`** (line 103); inputs: `number`; return: `str`; callers: `render_preview`. Keep the leading plus and the last two digits. Preview output only.
- **`_stamp`** (line 111); inputs: `now`; return: `str`; callers: `drain_result_queue`, `place_plan_calls`, `poll_open_calls`.
- **`_append_jsonl`** (line 115); inputs: `path, row`; return: `None`; callers: `place_plan_calls`, `post_group_result_line`.
- **`_voice_key`** (line 123); inputs: `incident, role`; return: `str`; callers: `place_plan_calls`.
- **`_parse_voice_key`** (line 127); inputs: `key`; return: `Optional[tuple]`; callers: `poll_open_calls`.
- **`_parse_time`** (line 139); inputs: `value`; return: `Optional[datetime]`; callers: `_within_retry_block`.
- **`default_bland_post`** (line 143); inputs: `body, api_key, environ, opener`; return: `tuple`; callers: `place_plan_calls`. POST /v1/calls. Raises before any socket when the live gate is closed.
- **`default_bland_get`** (line 168); inputs: `call_id, api_key, environ, opener`; return: `tuple`; callers: `poll_open_calls`. GET /v1/calls/{id}. The id is restricted to a token so the URL cannot wander.
- **`default_quo_post`** (line 189); inputs: `payload, api_key, environ, opener`; return: `int`; callers: `post_group_result_line`. POST one Quo group message. The payload is not logged.
- **`_bearer`** (line 214); inputs: `api_key`; return: `str`; callers: `default_bland_get`, `default_bland_post`.
- **`_read_json`** (line 221); inputs: `request, opener`; return: `tuple`; callers: `default_bland_get`, `default_bland_post`, `default_quo_post`.
- **`_within_retry_block`** (line 246); inputs: `prior, now`; return: `bool`; callers: `place_plan_calls`.
- **`place_plan_calls`** (line 255); inputs: `plan, state, now, environ, poster, store, call_log`; return: `List[Dict[str, Any]]`; callers: `advance_plans`. Place Don and Tom calls, or log a dry-run line. Never stores the phone.
- **`poll_open_calls`** (line 368); inputs: `state, now, environ, getter, store, skip_call_ids`; return: `List[Dict[str, Any]]`; callers: `advance_plans`. GET calls placed on an earlier run when no webhook record is stored.
- **`post_group_result_line`** (line 468); inputs: `line, environ, poster, dry_run, result_log`; return: `Dict[str, Any]`; callers: `drain_result_queue`. One Quo group line. Dry-run writes the line and does not POST.
- **`drain_result_queue`** (line 506); inputs: `state, now, environ, store, quo_post, result_log`; return: `List[Dict[str, Any]]`; callers: `advance_plans`. Post or dry-log each queued line once per call_id.
- **`advance_plans`** (line 570); inputs: `plans, state, now, environ, poster, getter, quo_post, store, call_log, result_log`; return: `List[Dict[str, Any]]`; callers: not resolved in file. Poll earlier calls, drain result lines, then place this run's voice calls.
- **`firestore_store_or_none`** (line 621); inputs: `none`; return: `Any`; callers: `advance_plans`. Admin client when Mac credentials exist. Never required for dry-run.
- **`render_preview`** (line 636); inputs: `none`; return: `str`; callers: `main`. Exact call bodies with fake numbers masked to the last two digits.
- **`main`** (line 678); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/leak_reply.py`

- **`load_environment`** (line 304); inputs: `none`; return: `None`; callers: `main`, `run`.
- **`running_in_ci`** (line 308); inputs: `none`; return: `bool`; callers: `main`, `process_leaks`, `run`, `run_for_scraper`.
- **`live_send_enabled`** (line 312); inputs: `none`; return: `bool`; callers: `main`, `run`, `run_for_scraper`. Default off until Mac .env sets LEAK_REPLY_ENABLE. CI must not send.
- **`_log`** (line 321); inputs: `message`; return: `None`; callers: `load_t5_source`, `main`, `post_automations_discord`, `process_leaks`, `run`, `run_for_scraper`.
- **`encode_digits_for_discord`** (line 325); inputs: `value`; return: `str`; callers: `discord_water_key_order_text`. Spell each digit so Discord outbound stays digit-free. Cart decodes [one]…[nine].
- **`decode_digits_from_discord`** (line 336); inputs: `value`; return: `str`; callers: not resolved in file. Inverse of encode_digits_for_discord. Tests / Cart pickup only.
- **`decode_digits_from_discord._repl`** (line 340); inputs: `match`; return: `str`; callers: not resolved in file.
- **`street_label_for_discord`** (line 346); inputs: `street`; return: `str`; callers: not resolved in file. Drop leading house numbers. Prefer thread_ship_to for Cart.
- **`_state_abbrev`** (line 351); inputs: `value`; return: `str`; callers: `thread_ship_to`.
- **`thread_ship_to`** (line 356); inputs: `thread`; return: `str`; callers: `decide`. Full ship-to of the leaking property from the PadSplit thread only.
- **`is_full_ship_to`** (line 399); inputs: `address`; return: `bool`; callers: `discord_water_key_order_text`. Need a house number plus a street name so Cart can ship.
- **`assert_water_key_order_safe`** (line 405); inputs: `text, ship_to`; return: `str`; callers: `discord_water_key_order_text`, `post_automations_discord`, `run`. Digit-free except the leaking property ship-to. No lock codes.
- **`detect_leak`** (line 418); inputs: `text`; return: `bool`; callers: `recent_member_leak`. True only for burst / pipe / main / flooding / wall-ceiling emergencies.
- **`canonicalize_water_key_youtube`** (line 436); inputs: `text`; return: `str`; callers: `adapt_t5_to_member_thread`. Keep the canonical short URL; strip ?si= / utm tracking.
- **`load_shared_template_fields`** (line 441); inputs: `fetch_doc`; return: `Dict[str, str]`; callers: `load_t5_source`. Same templates/shared doc new_booking uses for the Hirevire card.
- **`load_shared_template_fields.fetch_doc`** (line 447); inputs: `none`; return: `Dict[str, Any]`; callers: `load_shared_template_fields`.
- **`pick_t5_source`** (line 455); inputs: `fields`; return: `str`; callers: `load_t5_source`.
- **`load_t5_source`** (line 463); inputs: `fetch_doc`; return: `str`; callers: `format_leak_body`.
- **`adapt_t5_to_member_thread`** (line 475); inputs: `text`; return: `str`; callers: `format_leak_body`. 1:1 tone from house-wide t5. Always add Quo. No lock codes / SSN / Amazon.
- **`format_leak_body`** (line 512); inputs: `source, fetch_doc`; return: `str`; callers: `decide`, `process_leaks`. PadSplit 1:1 body from live t5 (or baked t5). Quo + canonical YouTube.
- **`discord_water_key_order_text`** (line 527); inputs: `house_label, ship_to, street, room, chat_id`; return: `Optional[str]`; callers: `decide`. Cart event. Digit-free except full ship-to. Requires house + address.
- **`load_state`** (line 560); inputs: `path`; return: `Dict[str, Any]`; callers: `process_leaks`.
- **`save_state`** (line 577); inputs: `state, path`; return: `None`; callers: `process_leaks`.
- **`already_sent`** (line 582); inputs: `state, chat_id, now, window`; return: `bool`; callers: `decide`.
- **`record_sent`** (line 598); inputs: `state, chat_id, now`; return: `None`; callers: `process_leaks`.
- **`host_text_handles_leak`** (line 606); inputs: `text`; return: `bool`; callers: `host_already_sent_leak_pack`, `house_host_handled`. Auto-reply marker, or Nest's t5 catch-up pair (phrase + water-key short).
- **`same_house`** (line 615); inputs: `left, right`; return: `bool`; callers: `house_host_handled`. True when both threads resolve to one house. Unknown streets do not match each other.
- **`house_host_handled`** (line 628); inputs: `source, threads, report_at, now, lookback`; return: `bool`; callers: `decide`. Host shutoff note on any current thread at this house, after the member report, inside lookback.
- **`host_already_sent_leak_pack`** (line 655); inputs: `thread`; return: `bool`; callers: not resolved in file.
- **`recent_member_leak`** (line 664); inputs: `thread, now, lookback`; return: `Optional[Dict[str, Any]]`; callers: `decide`.
- **`decide`** (line 688); inputs: `thread, now, state, leak_body, house_threads`; return: `Decision`; callers: `process_leaks`.
- **`post_automations_discord`** (line 766); inputs: `text, ship_to, token, channel`; return: `Optional[Dict[str, Any]]`; callers: `process_leaks`. Post WATER_KEY_ORDER. Digit-free except ship_to. Do not use lock_codes.assert on the full text.
- **`process_leaks`** (line 793); inputs: `threads, now, state, state_path, leftover_compose_tabs, close_tabs_fn, send_fn, post_discord, send_enabled, dry_run, fetch_doc, leak_body, plan_alerts, alert_enabled, alert_state_path, alert_environ, profile_fetcher, session, conversations_get, bland_post, bland_get, quo_post, call_store`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`load_host_messages`** (line 991); inputs: `none`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`run`** (line 995); inputs: `now, dry_run, host_messages, leftover_compose_tabs, leftover_tabs_path, send_fn, post_discord, state_path, session, creds, fetch_doc, alert_state_path, alert_environ, profile_fetcher, conversations_get, bland_post, bland_get, quo_post, call_store`; return: `RunResult`; callers: `main`, `run_for_scraper`.
- **`run._send`** (line 1038); inputs: `chat_id, text`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`run_for_scraper`** (line 1093); inputs: `session, creds, messages, now, state_path, leftover_compose_tabs, leftover_tabs_path`; return: `List[Dict[str, Any]]`; callers: not resolved in file.
- **`main`** (line 1123); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/lock_codes.py`

- **`load_environment`** (line 133); inputs: `none`; return: `None`; callers: `main`, `run`.
- **`running_in_ci`** (line 137); inputs: `none`; return: `bool`; callers: `live_actions_enabled`, `main`, `run`.
- **`live_actions_enabled`** (line 141); inputs: `none`; return: `bool`; callers: `main`. Mac morning/afternoon only. GitHub Actions / CI must not rotate or post.
- **`sifely_api_key`** (line 151); inputs: `none`; return: `str`; callers: `run`. Return SIFELY_API_KEY or empty. Never invent a key. Never print it.
- **`has_digit_characters`** (line 156); inputs: `text`; return: `bool`; callers: `assert_discord_outbound_safe`.
- **`redact_for_log`** (line 160); inputs: `text`; return: `str`; callers: `_log`. Strip keys and digit runs so logs never contain PIN digits or tokens.
- **`hash_passcode`** (line 168); inputs: `code`; return: `str`; callers: `passcode_hashes_from_list`, `run`.
- **`generate_passcode`** (line 172); inputs: `none`; return: `str`; callers: not resolved in file. In-memory PIN only. Tests mock this to REDACTED. Never log the value.
- **`is_spanish_moss_address`** (line 177); inputs: `value`; return: `bool`; callers: `current_member_threads`, `vacancy_allows_rotate`.
- **`is_spanish_moss_back_lock`** (line 186); inputs: `lock`; return: `bool`; callers: `resolve_lock`.
- **`vacancy_allows_rotate`** (line 195); inputs: `room`; return: `bool`; callers: `pending_auto_rotate_rooms`. Vacant alone is not enough. Require an empty/turned photo.
- **`vacancy_key`** (line 209); inputs: `room`; return: `str`; callers: `_mark_vacancies_handled`, `pending_auto_rotate_rooms`.
- **`pending_auto_rotate_rooms`** (line 220); inputs: `rooms, rotated_keys`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`current_member_threads`** (line 232); inputs: `messages, now`; return: `List[Dict[str, Any]]`; callers: `_notify_current_members`.
- **`parse_sifely_share_code`** (line 253); inputs: `text`; return: `Optional[str]`; callers: `_first_new_share`. Read an inbound #new-tenants Sifely share. Tests use REDACTED, not PINs.
- **`_mentions_spanish_moss_lock`** (line 266); inputs: `text`; return: `bool`; callers: `parse_sifely_share_code`.
- **`discord_human_change_text`** (line 273); inputs: `none`; return: `str`; callers: `_discord_text_for`, `run`.
- **`discord_rotated_text`** (line 277); inputs: `none`; return: `str`; callers: `_discord_text_for`, `run`.
- **`need_you_missing_key_text`** (line 281); inputs: `none`; return: `str`; callers: `_discord_text_for`, `run`.
- **`member_host_message`** (line 285); inputs: `code`; return: `str`; callers: `_notify_current_members`. PadSplit host inbox may include the new code. Do not log this string.
- **`assert_discord_outbound_safe`** (line 293); inputs: `text`; return: `str`; callers: `post_ops_discord`, `run`.
- **`decide`** (line 299); inputs: `in_ci, api_key_present, api_available, human_change, pending_vacancy, inbound_share`; return: `Plan`; callers: `run`. Pure v1 decision table. Side effects stay in execute/run.
- **`load_state`** (line 345); inputs: `path`; return: `Dict[str, Any]`; callers: `run`.
- **`save_state`** (line 364); inputs: `state, path`; return: `None`; callers: `run`.
- **`_empty_state`** (line 369); inputs: `none`; return: `Dict[str, Any]`; callers: `load_state`.
- **`passcode_hashes_from_list`** (line 379); inputs: `passcodes`; return: `Dict[str, str]`; callers: `run`.
- **`detect_human_change`** (line 390); inputs: `current_hashes, previous_hashes, last_auto_rotate_hash`; return: `bool`; callers: `run`.
- **`sifely_headers`** (line 404); inputs: `api_key`; return: `Dict[str, str]`; callers: `sifely_request`. Raw sk- key. Do not prefix Bearer.
- **`_unwrap_sifely`** (line 412); inputs: `payload`; return: `Any`; callers: `sifely_request`.
- **`sifely_request`** (line 418); inputs: `method, path, api_key, params, session`; return: `Any`; callers: `change_passcode`, `list_locks`, `list_passcodes`.
- **`list_locks`** (line 451); inputs: `api_key, session`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`list_passcodes`** (line 477); inputs: `api_key, lock_id, session`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`change_passcode`** (line 494); inputs: `api_key, lock_id, keyboard_pwd_id, new_code, session`; return: `Any`; callers: `run`.
- **`resolve_lock`** (line 516); inputs: `locks`; return: `Optional[Dict[str, Any]]`; callers: `run`.
- **`resolve_keyboard_pwd_id`** (line 533); inputs: `passcodes`; return: `Optional[str]`; callers: `run`.
- **`send_host_message`** (line 554); inputs: `session, creds, chat_id, text, request_fn`; return: `Dict[str, Any]`; callers: `_notify_current_members`. SEND the host message on the member thread. Reuses the scraper session.
- **`post_ops_discord`** (line 594); inputs: `text, token, channel`; return: `Optional[Dict[str, Any]]`; callers: `run`.
- **`fetch_new_tenants_messages`** (line 611); inputs: `token`; return: `List[Dict[str, Any]]`; callers: `_first_new_share`.
- **`load_json_file`** (line 623); inputs: `path`; return: `Any`; callers: `load_host_messages`, `load_occupancy_rooms`.
- **`load_occupancy_rooms`** (line 629); inputs: `none`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`load_host_messages`** (line 640); inputs: `none`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`update_codes_page`** (line 651); inputs: `code`; return: `bool`; callers: `run`. Write the new code to Firestore property_codes (GitHub Pages codes.html).
- **`_log`** (line 678); inputs: `message`; return: `None`; callers: `_first_new_share`, `_notify_current_members`, `main`, `post_ops_discord`, `run`, `update_codes_page`.
- **`_date_only`** (line 682); inputs: `value`; return: `unspecified`; callers: `current_member_threads`.
- **`_discord_text_for`** (line 696); inputs: `kind`; return: `Optional[str]`; callers: not resolved in file.
- **`_mark_vacancies_handled`** (line 706); inputs: `state, rooms`; return: `None`; callers: `run`.
- **`run`** (line 713); inputs: `now, dry_run, sifely_session, occupancy_rooms, host_messages, inbound_messages, post_discord, update_digest, notify_members, generate_code, state_path`; return: `RunResult`; callers: `main`.
- **`_first_new_share`** (line 870); inputs: `inbound_messages, processed_ids`; return: `tuple[Optional[str], Optional[str]]`; callers: `run`.
- **`_notify_current_members`** (line 896); inputs: `code, messages, now`; return: `int`; callers: `run`.
- **`main`** (line 921); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/lockout_reply.py`

- **`load_environment`** (line 288); inputs: `none`; return: `None`; callers: `main`, `run`.
- **`running_in_ci`** (line 292); inputs: `none`; return: `bool`; callers: `main`, `run`.
- **`live_send_enabled`** (line 296); inputs: `none`; return: `bool`; callers: `main`, `run`, `run_for_scraper`. Default off until Mac .env sets LOCKOUT_REPLY_ENABLE. CI must not send.
- **`_log`** (line 305); inputs: `message`; return: `None`; callers: `fetch_property_codes`, `main`, `obtain_spanish_moss_back`, `post_automations_discord`, `process_lockouts`, `run`, `run_for_scraper`.
- **`parse_dt`** (line 309); inputs: `value`; return: `Optional[datetime]`; callers: `current_occupant`, `latest_host_stage_at`, `recent_door_fail_followup`, `recent_entry_success`, `recent_fresh_lockout_and_fail`, `recent_member_lockout`, `resolved_at`, `stage_sent_at`.
- **`normalize_text`** (line 321); inputs: `value`; return: `str`; callers: `message_text`, `occupant_names`, `sender_name`, `thread_street`.
- **`sender_name`** (line 325); inputs: `sender`; return: `str`; callers: `is_member_message`.
- **`occupant_names`** (line 332); inputs: `thread`; return: `set[str]`; callers: `is_member_message`.
- **`is_member_message`** (line 342); inputs: `thread, message`; return: `bool`; callers: `recent_door_fail_followup`, `recent_entry_success`, `recent_fresh_lockout_and_fail`, `recent_member_lockout`.
- **`is_host_message`** (line 353); inputs: `message`; return: `bool`; callers: `host_lockout_stage_messages`.
- **`message_text`** (line 358); inputs: `message`; return: `str`; callers: `decide`, `host_lockout_stage_messages`, `process_lockouts`, `recent_door_fail_followup`, `recent_entry_success`, `recent_fresh_lockout_and_fail`, `recent_member_lockout`.
- **`iter_thread_messages`** (line 368); inputs: `thread`; return: `List[Dict[str, Any]]`; callers: `host_lockout_stage_messages`, `recent_door_fail_followup`, `recent_entry_success`, `recent_fresh_lockout_and_fail`, `recent_member_lockout`.
- **`detect_lockout`** (line 376); inputs: `text`; return: `bool`; callers: `detect_fresh_lockout`, `recent_door_fail_followup`, `recent_member_lockout`.
- **`detect_door_fail_followup`** (line 380); inputs: `text`; return: `bool`; callers: `recent_door_fail_followup`, `recent_fresh_lockout_and_fail`, `recent_member_lockout`. True when the member says the door/code still failed after a door reply.
- **`detect_entry_success`** (line 385); inputs: `text`; return: `bool`; callers: `recent_entry_success`. True when the member confirms they got in after a door-stage send.
- **`detect_fresh_lockout`** (line 395); inputs: `text`; return: `bool`; callers: `recent_fresh_lockout_and_fail`. Lockout cue that is not only a door-fail follow-up phrase.
- **`thread_street`** (line 405); inputs: `thread`; return: `str`; callers: `decide`, `process_lockouts`.
- **`thread_room`** (line 410); inputs: `thread`; return: `str`; callers: `resolve_room`.
- **`current_occupant`** (line 418); inputs: `thread, now`; return: `bool`; callers: `decide`, `process_lockouts`.
- **`match_house`** (line 429); inputs: `street, extra_text`; return: `HouseHit`; callers: `decide`, `process_lockouts`.
- **`mentioned_room`** (line 452); inputs: `text`; return: `str`; callers: `resolve_room`.
- **`resolve_room`** (line 457); inputs: `thread, member_text`; return: `RoomHit`; callers: `decide`.
- **`score_certainty`** (line 469); inputs: `house, room, member_text, codes_ready`; return: `Tuple[int, str]`; callers: `decide`.
- **`first_filled`** (line 497); inputs: `doc, keys`; return: `str`; callers: `codes_from_property_doc`, `pick_room_lockbox`.
- **`pick_room_lockbox`** (line 505); inputs: `doc, room`; return: `Tuple[str, str]`; callers: `codes_from_property_doc`. Prefer the lockbox/room code that matches the member room.
- **`codes_from_property_doc`** (line 534); inputs: `slug, doc, room, sifely_back, sifely_rotated, used_inbound_share`; return: `EntryCodes`; callers: `decide`.
- **`_fail_ladder_lines`** (line 576); inputs: `none`; return: `List[str]`; callers: `format_door_lockout_body`.
- **`format_door_lockout_body`** (line 586); inputs: `house_label, room, entry`; return: `str`; callers: `decide`, `format_lockout_body`. Stage-1 PadSplit body: door codes only. Never log this string.
- **`format_lockbox_lockout_body`** (line 617); inputs: `house_label, room, entry`; return: `str`; callers: `decide`. Stage-2 PadSplit body: lockbox / room code + location. Never log this string.
- **`format_lockout_body`** (line 634); inputs: `house_label, room, entry`; return: `str`; callers: not resolved in file. Stage-1 door body. Lockbox is never included.
- **`discord_lockout_detected_text`** (line 639); inputs: `house_label, needs_tap`; return: `str`; callers: `_discord_text`.
- **`discord_missing_codes_text`** (line 654); inputs: `house_label`; return: `str`; callers: `_discord_text`.
- **`discord_spanish_moss_rotated_text`** (line 663); inputs: `none`; return: `str`; callers: `_discord_text`.
- **`discord_sifely_unavailable_text`** (line 667); inputs: `none`; return: `str`; callers: `_discord_text`.
- **`load_state`** (line 676); inputs: `path`; return: `Dict[str, Any]`; callers: `process_lockouts`.
- **`save_state`** (line 691); inputs: `state, path`; return: `None`; callers: `process_lockouts`.
- **`_thread_state`** (line 696); inputs: `state, chat_id`; return: `Dict[str, Any]`; callers: `resolved_at`, `stage_sent_at`.
- **`_legacy_combined_pack`** (line 701); inputs: `row`; return: `bool`; callers: `stage_sent_at`. Old single-pack state (sent_at, no per-stage keys) counts as both stages.
- **`stage_sent_at`** (line 706); inputs: `state, chat_id, stage`; return: `Optional[datetime]`; callers: `already_sent_stage`, `door_reply_at`.
- **`already_sent_stage`** (line 717); inputs: `state, chat_id, stage, now, window`; return: `bool`; callers: `already_sent`, `decide`.
- **`already_sent`** (line 731); inputs: `state, chat_id, now, window`; return: `bool`; callers: not resolved in file. True when the door stage was already sent in the idempotency window.
- **`record_sent`** (line 742); inputs: `state, chat_id, now, action, stage`; return: `None`; callers: `process_lockouts`.
- **`resolved_at`** (line 762); inputs: `state, chat_id`; return: `Optional[datetime]`; callers: `already_resolved`, `decide`.
- **`already_resolved`** (line 767); inputs: `state, chat_id, now, window`; return: `bool`; callers: `decide`.
- **`record_resolved`** (line 780); inputs: `state, chat_id, now, reason`; return: `None`; callers: `process_lockouts`. Persist resolved so later fail-ish noise does not re-open stage 2.
- **`host_lockout_stage_messages`** (line 797); inputs: `thread`; return: `Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]`; callers: `host_already_sent_lockout_pack`, `host_already_sent_stage`, `latest_host_stage_at`.
- **`host_already_sent_stage`** (line 813); inputs: `thread, stage`; return: `bool`; callers: `decide`.
- **`host_already_sent_lockout_pack`** (line 822); inputs: `thread`; return: `bool`; callers: not resolved in file. True when an old combined pack (door + lockbox line) is already on the thread.
- **`latest_host_stage_at`** (line 828); inputs: `thread, stage`; return: `Optional[datetime]`; callers: `door_reply_at`.
- **`door_reply_at`** (line 839); inputs: `state, thread, chat_id`; return: `Optional[datetime]`; callers: `decide`.
- **`recent_member_lockout`** (line 855); inputs: `thread, now, lookback, include_door_fail`; return: `Optional[Dict[str, Any]]`; callers: `decide`, `process_lockouts`.
- **`recent_door_fail_followup`** (line 884); inputs: `thread, now, after, lookback`; return: `Optional[Dict[str, Any]]`; callers: `decide`. Newest member lockout / door-fail message strictly after the door reply.
- **`recent_entry_success`** (line 913); inputs: `thread, now, after, lookback`; return: `Optional[Dict[str, Any]]`; callers: `decide`. Newest member entry-success message strictly after the door reply.
- **`recent_fresh_lockout_and_fail`** (line 942); inputs: `thread, now, after, lookback`; return: `Optional[Dict[str, Any]]`; callers: `decide`. After resolved: newest fail only when a fresh lockout (not fail-only noise) exists.
- **`fetch_property_codes`** (line 975); inputs: `slug`; return: `Dict[str, Any]`; callers: not resolved in file. Read property_codes/{slug}. Never log field values.
- **`obtain_spanish_moss_back`** (line 1000); inputs: `sifely_session, inbound_messages, processed_share_ids, rotate_if_needed, generate_code`; return: `Tuple[str, str]`; callers: not resolved in file. Return (code, source). Code stays in memory. Never log it.
- **`decide`** (line 1062); inputs: `thread, now, state, codes_doc, sifely_back, sifely_source`; return: `Decision`; callers: `process_lockouts`.
- **`post_automations_discord`** (line 1227); inputs: `text, token, channel`; return: `Optional[Dict[str, Any]]`; callers: `process_lockouts`.
- **`_discord_text`** (line 1245); inputs: `kind, house_label`; return: `Optional[str]`; callers: `process_lockouts`.
- **`process_lockouts`** (line 1259); inputs: `threads, now, state, state_path, leftover_compose_tabs, close_tabs_fn, send_fn, codes_fn, sifely_fn, post_discord, send_enabled, dry_run`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`load_host_messages`** (line 1412); inputs: `none`; return: `List[Dict[str, Any]]`; callers: `run`.
- **`run`** (line 1416); inputs: `now, dry_run, host_messages, leftover_compose_tabs, leftover_tabs_path, send_fn, codes_fn, sifely_fn, post_discord, state_path, session, creds`; return: `RunResult`; callers: `main`, `run_for_scraper`.
- **`run._send`** (line 1445); inputs: `chat_id, text`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`run_for_scraper`** (line 1489); inputs: `session, creds, messages, now, state_path, leftover_compose_tabs, leftover_tabs_path`; return: `List[Dict[str, Any]]`; callers: not resolved in file.
- **`main`** (line 1515); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/new_booking.py`

- **`iso_utc`** (line 177); inputs: `dt`; return: `str`; callers: `record_booking`.
- **`parse_dt`** (line 181); inputs: `value`; return: `Optional[datetime]`; callers: `has_recent_eviction`, `parse_year_month`.
- **`load_state`** (line 193); inputs: `path`; return: `Dict[str, Any]`; callers: `process_new_bookings`.
- **`save_state`** (line 208); inputs: `state, path`; return: `None`; callers: `process_new_bookings`.
- **`load_leftover_compose_tabs`** (line 213); inputs: `path`; return: `List[Dict[str, Any]]`; callers: `run_for_scraper`.
- **`save_leftover_compose_tabs`** (line 227); inputs: `tabs, path`; return: `None`; callers: `run_for_scraper`.
- **`live_send_enabled`** (line 234); inputs: `none`; return: `bool`; callers: `notify_new_tenants_pack_for_joe`, `run_for_scraper`. Mac morning/afternoon scraper sends. GitHub Actions scrape must not.
- **`parse_firestore_string_map`** (line 243); inputs: `doc`; return: `Dict[str, str]`; callers: `load_live_new_booking_template`.
- **`load_live_new_booking_template`** (line 256); inputs: `fetch_doc`; return: `Dict[str, str]`; callers: not resolved in file. Read the live **new booking request** card from docs/templates.html storage.
- **`load_live_new_booking_template.fetch_doc`** (line 261); inputs: `none`; return: `Dict[str, Any]`; callers: `load_live_new_booking_template`.
- **`template_send_body`** (line 276); inputs: `label, text`; return: `str`; callers: `load_live_new_booking_template`.
- **`verify_hirevire_template`** (line 284); inputs: `text`; return: `str`; callers: `load_live_new_booking_template`. Validate that the Hirevire template refers to the expected account.
- **`parse_year_month`** (line 294); inputs: `value`; return: `Optional[datetime]`; callers: `has_recent_eviction`.
- **`has_recent_eviction`** (line 311); inputs: `rental_history, now`; return: `bool`; callers: `process_new_bookings`. True when occupant rental history shows any eviction in the last 7 years.
- **`close_leftover_compose_tabs`** (line 343); inputs: `open_tabs, chat_id`; return: `List[Dict[str, Any]]`; callers: not resolved in file. Close leftover host compose/draft tabs for this thread before send.
- **`leftover_tabs_open`** (line 364); inputs: `open_tabs, chat_id`; return: `bool`; callers: `require_leftover_drafts_cleared`.
- **`require_leftover_drafts_cleared`** (line 370); inputs: `open_tabs, chat_id, close_tabs_fn`; return: `List[Dict[str, Any]]`; callers: `send_first_host_message`, `send_host_message`. HARD GATE: leftover drafts must be cleared before any host send.
- **`booking_id_from_status`** (line 398); inputs: `booking_status`; return: `Optional[str]`; callers: `collect_booking_hits`.
- **`pending_booking_status`** (line 405); inputs: `thread`; return: `Optional[Dict[str, Any]]`; callers: `collect_booking_hits`. Messenger PENDING extra — join/context only, not the booking-hit source.
- **`occupancy_pk_from_gid`** (line 417); inputs: `gid`; return: `Optional[int]`; callers: `fetch_chat_id_for_occupancy`.
- **`parse_pending_inbox_payload`** (line 437); inputs: `payload`; return: `List[Dict[str, Any]]`; callers: `fetch_pending_booking_requests`.
- **`collect_booking_hits`** (line 452); inputs: `pending_inbox, messages`; return: `List[Dict[str, Any]]`; callers: `process_new_bookings`, `run_for_scraper`. Hits come from hostPendingBookingRequests only. Messenger is join/context.
- **`host_already_sent_hirevire`** (line 492); inputs: `thread, template_text`; return: `bool`; callers: `process_new_bookings`.
- **`already_handled`** (line 510); inputs: `state, booking_id`; return: `bool`; callers: `process_new_bookings`.
- **`record_booking`** (line 519); inputs: `state, booking_id, chat_id, action, now, pack_posted, source`; return: `None`; callers: `process_new_bookings`.
- **`last_initial`** (line 538); inputs: `last_name`; return: `str`; callers: `build_screening_snapshot`.
- **`build_screening_snapshot`** (line 543); inputs: `booking_id, thread, pending_node, hirevire_sent`; return: `Dict[str, Any]`; callers: `process_new_bookings`. Safe screening snapshot for #new-tenants. No email, phone, SSN, photos, lock codes, credit.
- **`format_new_tenants_pack`** (line 576); inputs: `snapshot, joe_user_id`; return: `str`; callers: `notify_new_tenants_pack_for_joe`.
- **`post_new_tenants_webhook`** (line 608); inputs: `text, post_fn`; return: `Dict[str, Any]`; callers: `notify_new_tenants_pack_for_joe`.
- **`notify_new_tenants_pack_for_joe`** (line 624); inputs: `result, post_fn`; return: `Dict[str, Any]`; callers: not resolved in file. Post Discord #new-tenants pack (@ Joe only). CI must not post.
- **`send_host_message`** (line 638); inputs: `session, creds, chat_id, text, leftover_compose_tabs, close_tabs_fn, request_fn`; return: `Dict[str, Any]`; callers: `run_for_scraper.send_fn`. SEND the host message on the member thread. Not a draft.
- **`fetch_rental_history`** (line 689); inputs: `session, creds, occupancy_id, request_fn`; return: `Dict[str, Any]`; callers: `run_for_scraper.rental_history_fn`.
- **`fetch_pending_booking_requests`** (line 725); inputs: `session, creds, request_fn`; return: `List[Dict[str, Any]]`; callers: `run_for_scraper`. Primary booking-hit source: host pending-booking inbox.
- **`fetch_pending_booking_requests._post`** (line 739); inputs: `query`; return: `Dict[str, Any]`; callers: `fetch_pending_booking_requests`.
- **`fetch_chat_id_for_occupancy`** (line 763); inputs: `session, creds, occupancy_id, request_fn`; return: `Optional[str]`; callers: `run_for_scraper.resolve_chat_id_fn`.
- **`send_first_host_message`** (line 803); inputs: `chat_id, text, leftover_compose_tabs, close_tabs_fn, send_fn`; return: `Dict[str, Any]`; callers: `process_new_bookings`. Close leftover compose/draft tabs (hard gate), then SEND one host copy.
- **`_occupancy_id_for_hit`** (line 820); inputs: `hit`; return: `Optional[str]`; callers: `process_new_bookings`.
- **`_post_pack_for_result`** (line 829); inputs: `result, notify_fn`; return: `Dict[str, Any]`; callers: `process_new_bookings`.
- **`process_new_bookings`** (line 843); inputs: `messages, pending_inbox, now, state, state_path, load_template, rental_history_fn, leftover_compose_tabs, close_tabs_fn, send_fn, notify_fn, resolve_chat_id_fn, send_enabled`; return: `List[Dict[str, Any]]`; callers: `run_for_scraper`. Handle each hostPendingBookingRequests hit once. Never Approve/Reject.
- **`run_for_scraper`** (line 1086); inputs: `session, creds, messages, now, state_path, leftover_compose_tabs, leftover_tabs_path`; return: `List[Dict[str, Any]]`; callers: not resolved in file.
- **`run_for_scraper.send_fn`** (line 1116); inputs: `chat_id, text`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`run_for_scraper.rental_history_fn`** (line 1125); inputs: `thread`; return: `Dict[str, Any]`; callers: `process_new_bookings`.
- **`run_for_scraper.resolve_chat_id_fn`** (line 1131); inputs: `occupancy_id`; return: `Optional[str]`; callers: `process_new_bookings`.

### `padsplit_scraper/occupancy.py`

- **`compute_occupancy`** (line 21); inputs: `messages, tasks, now`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`operator_lists`** (line 46); inputs: `rooms, today`; return: `Dict[str, List[Dict[str, Any]]]`; callers: not resolved in file. Split occupancy rows into operator lists. Incoming is future next_move_in only.
- **`_flatten_tasks`** (line 77); inputs: `tasks_by_bucket`; return: `List[Dict[str, Any]]`; callers: `compute_occupancy`.
- **`_ingest_chat`** (line 90); inputs: `rooms, chat, today`; return: `None`; callers: `compute_occupancy`.
- **`_ingest_ticket`** (line 116); inputs: `rooms, ticket, today`; return: `None`; callers: `compute_occupancy`.
- **`_finalize_row`** (line 145); inputs: `state, today`; return: `Dict[str, Any]`; callers: `compute_occupancy`.
- **`_room_state`** (line 180); inputs: `rooms, key, street, room_number`; return: `Dict[str, Any]`; callers: `_ingest_chat`, `_ingest_ticket`.
- **`_room_key`** (line 209); inputs: `street, room_number`; return: `Tuple[str, Optional[str]]`; callers: `_ingest_chat`, `_ingest_ticket`.
- **`_normalize_street`** (line 213); inputs: `value`; return: `str`; callers: `_room_key`.
- **`_normalize_room`** (line 220); inputs: `value`; return: `Optional[str]`; callers: `_ingest_ticket`, `_room_from_obj`.
- **`_coerce_room_number`** (line 226); inputs: `value`; return: `Any`; callers: `_finalize_row`.
- **`_street_from_chat`** (line 235); inputs: `chat`; return: `str`; callers: `_ingest_chat`.
- **`_street_from_ticket`** (line 241); inputs: `ticket`; return: `str`; callers: `_ingest_ticket`.
- **`_room_from_obj`** (line 250); inputs: `room`; return: `Optional[str]`; callers: `_ingest_chat`.
- **`_user_present`** (line 254); inputs: `user`; return: `bool`; callers: `_ingest_chat`.
- **`_last_message_after`** (line 260); inputs: `last_message, listed`; return: `bool`; callers: `_ingest_chat`.
- **`_message_date`** (line 267); inputs: `value`; return: `Optional[date]`; callers: `_last_message_after`.
- **`_date_only`** (line 276); inputs: `value`; return: `Optional[date]`; callers: `_ingest_chat`, `_ingest_ticket`, `_message_date`, `operator_lists`.
- **`_chicago_date`** (line 295); inputs: `now`; return: `date`; callers: `compute_occupancy`.
- **`_scraped_at`** (line 301); inputs: `now`; return: `str`; callers: `compute_occupancy`.
- **`_is_turn_category`** (line 307); inputs: `category`; return: `bool`; callers: `_ingest_ticket`.
- **`_ticket_photo_count`** (line 311); inputs: `ticket`; return: `int`; callers: `_ingest_ticket`.
- **`_chat_photo_count`** (line 322); inputs: `chat`; return: `int`; callers: `_ingest_chat`.
- **`_iter_chat_messages`** (line 333); inputs: `chat`; return: `Iterable[Dict[str, Any]]`; callers: `_chat_photo_count`.
- **`_unique_ids`** (line 344); inputs: `values`; return: `List[Any]`; callers: `_finalize_row`.
- **`_room_sort_key`** (line 355); inputs: `value`; return: `Tuple[int, str]`; callers: `compute_occupancy`, `operator_lists`.

### `padsplit_scraper/outbox.py`

- **`_now`** (line 22); inputs: `none`; return: `str`; callers: `enqueue`, `mark_done`.
- **`load`** (line 26); inputs: `environ`; return: `Dict[str, Any]`; callers: `enqueue`, `mark_done`, `pending`.
- **`enqueue`** (line 34); inputs: `kind, payload, environ`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`pending`** (line 53); inputs: `environ`; return: `List[Dict[str, Any]]`; callers: not resolved in file.
- **`mark_done`** (line 57); inputs: `item_id, environ`; return: `bool`; callers: not resolved in file.

### `padsplit_scraper/persist.py`

- **`_latest_output_path`** (line 37); inputs: `none`; return: `Path`; callers: `_persist_latest_payload`.
- **`_stats_output_path`** (line 41); inputs: `none`; return: `Path`; callers: `_load_score_history`.
- **`_occupancy_output_path`** (line 45); inputs: `none`; return: `Path`; callers: `_persist_occupancy_payload`.
- **`_docs_occupancy_path`** (line 49); inputs: `none`; return: `Path`; callers: `_persist_occupancy_payload`.
- **`_persist_occupancy_payload`** (line 53); inputs: `payload`; return: `None`; callers: not resolved in file.
- **`_timestamped_output_path`** (line 58); inputs: `scraped_at`; return: `Path`; callers: `_persist_latest_payload`.
- **`_monthly_history_path`** (line 67); inputs: `none`; return: `Path`; callers: `_build_monthly_history_payload`, `seed_private_monthly_history`.
- **`_legacy_docs_monthly_history_path`** (line 71); inputs: `none`; return: `Path`; callers: `seed_private_monthly_history`.
- **`seed_private_monthly_history`** (line 75); inputs: `none`; return: `None`; callers: `_build_monthly_history_payload`.
- **`_write_json`** (line 84); inputs: `path, payload`; return: `None`; callers: `_persist_latest_payload`, `_persist_occupancy_payload`, `seed_private_monthly_history`.
- **`_load_json_if_exists`** (line 89); inputs: `path`; return: `Optional[Dict[str, Any]]`; callers: `_build_monthly_history_payload`, `_load_score_history`, `seed_private_monthly_history`.
- **`configure_output_dirs`** (line 99); inputs: `output_dir, docs_data_dir`; return: `None`; callers: not resolved in file. Point persist writes at an explicit tree. Used by the isolated runner.
- **`snapshot_output_dirs`** (line 106); inputs: `none`; return: `Dict[str, Path]`; callers: not resolved in file. Capture persist globals so a runner can restore them after isolation.
- **`restore_output_dirs`** (line 114); inputs: `snapshot`; return: `None`; callers: not resolved in file. Restore persist globals captured by ``snapshot_output_dirs``.
- **`prior_last_complete_success`** (line 121); inputs: `previous, this_run_scraped_at`; return: `Optional[str]`; callers: not resolved in file. Last complete success from a prior payload, rejecting stale run health.
- **`_build_run_status`** (line 152); inputs: `state, mode, run_scraped_at, failed_phase, error_type, error_message, fallback_used, sources, started_at, finished_at, host, counts, last_complete_success, omissions`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`_attach_run_status`** (line 195); inputs: `payload, run_status`; return: `Dict[str, Any]`; callers: `_persist_latest_payload`.
- **`_persist_latest_payload`** (line 201); inputs: `payload, scraped_at, run_status, write_timestamped`; return: `Path`; callers: not resolved in file.
- **`_load_score_history`** (line 218); inputs: `none`; return: `List[Dict[str, Any]]`; callers: not resolved in file.
- **`_build_monthly_history_payload`** (line 224); inputs: `performance_history, kpis, scraped_at`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`_build_stats_payload`** (line 278); inputs: `scraped_at, rooms, properties, earnings_payload, kpis, run_status`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`_firestore_client_or_none`** (line 297); inputs: `none`; return: `Any`; callers: `upload_stats_to_firestore`.
- **`upload_stats_to_firestore`** (line 315); inputs: `stats_payload, monthly_history_payload, client`; return: `bool`; callers: not resolved in file. Upsert owner-only stats docs. Never raises.

### `padsplit_scraper/process_lock.py`

- **`LockAcquisition.__enter__`** (line 56); inputs: `self`; return: `'LockAcquisition'`; callers: not resolved in file.
- **`LockAcquisition.__exit__`** (line 59); inputs: `self, exc_type, exc, tb`; return: `bool`; callers: not resolved in file.
- **`lock_path`** (line 64); inputs: `name, environ, directory`; return: `Path`; callers: `acquire_lock`, `main`.
- **`flock_file`** (line 70); inputs: `path`; return: `Path`; callers: `acquire_lock`, `lock_is_held`, `main`. Stable lock inode. Never unlinked for the life of the lock name.
- **`_registry_key`** (line 75); inputs: `path`; return: `str`; callers: `acquire_lock`, `lock_is_held`, `release_lock`.
- **`read_lock_pid`** (line 79); inputs: `path`; return: `Optional[int]`; callers: `acquire_lock`, `lock_is_stale`, `main`, `release_lock`.
- **`_pid_alive`** (line 98); inputs: `pid`; return: `bool`; callers: `acquire_lock`, `lock_is_stale`.
- **`_lock_age_seconds`** (line 110); inputs: `path`; return: `Optional[float]`; callers: `lock_is_stale`.
- **`_flock_busy_errno`** (line 117); inputs: `exc`; return: `bool`; callers: `_try_flock_exclusive`, `lock_is_held`.
- **`lock_is_held`** (line 121); inputs: `path`; return: `bool`; callers: `lock_is_stale`, `main`. True when this process or another process holds the flock.
- **`lock_is_stale`** (line 155); inputs: `path, empty_grace`; return: `bool`; callers: `main`. True when a leftover lock name exists and no live owner holds it.
- **`_write_pid`** (line 176); inputs: `path, pid`; return: `None`; callers: `acquire_lock`.
- **`_clear_pid`** (line 185); inputs: `path`; return: `None`; callers: `release_lock`.
- **`_try_flock_exclusive`** (line 194); inputs: `fd`; return: `bool`; callers: `acquire_lock`.
- **`acquire_lock`** (line 204); inputs: `name, directory, environ, timeout, poll_interval, after_mkdir`; return: `LockAcquisition`; callers: `main`.
- **`release_lock`** (line 289); inputs: `acquisition`; return: `None`; callers: `LockAcquisition.__exit__`, `acquire_lock`, `main`.
- **`main`** (line 316); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/public_snapshot.py`

- **`_timestamp`** (line 22); inputs: `value`; return: `str`; callers: `make_public_snapshot`.
- **`make_public_snapshot`** (line 34); inputs: `raw`; return: `dict[str, Any]`; callers: `write_public_snapshot`.
- **`write_public_snapshot`** (line 72); inputs: `source, destination`; return: `None`; callers: `main`.
- **`main`** (line 88); inputs: `none`; return: `None`; callers: not resolved in file.

### `padsplit_scraper/quo_sender.py`

- **`_log`** (line 85); inputs: `message`; return: `None`; callers: `deliver`, `main`, `maybe_deliver`, `post_text`.
- **`dry_run_enabled`** (line 95); inputs: `environ`; return: `bool`; callers: `http_allowed`. Default on. Only an explicit false flag allows live HTTP.
- **`http_allowed`** (line 104); inputs: `environ`; return: `bool`; callers: `maybe_deliver`. True only for a non-CI, non-dry-run leak_alert send.
- **`mask_e164`** (line 116); inputs: `number`; return: `str`; callers: `render_preview`. Last two digits only. Preview and logs use this, never the full number.
- **`_water_key_url`** (line 124); inputs: `none`; return: `str`; callers: `tenant_sms`.
- **`tenant_sms`** (line 132); inputs: `house, room, ask_photos`; return: `str`; callers: `outbound_tenant_text`, `render_preview`. Quo tenant SMS. Own copy; not the PadSplit member reply or the group text.
- **`_house_room_from_planner_body`** (line 160); inputs: `body`; return: `tuple[str, str]`; callers: `outbound_tenant_text`. House and room from the planner tenant line. Empty room when it has none.
- **`outbound_tenant_text`** (line 173); inputs: `body, ask_photos`; return: `str`; callers: `build_outbounds`. Build the Quo tenant SMS from a planner body. Does not edit that planner copy.
- **`_assert_safe_copy`** (line 181); inputs: `text`; return: `None`; callers: `render_preview`, `tenant_sms`.
- **`preview_call_script`** (line 189); inputs: `none`; return: `str`; callers: `render_preview`. Hook for the voice PR. This is not a Bland script and must stay empty of one.
- **`_flag_label`** (line 194); inputs: `environ, name`; return: `str`; callers: `gate_lines`.
- **`gate_lines`** (line 203); inputs: `environ`; return: `List[str]`; callers: `render_preview`.
- **`render_preview`** (line 224); inputs: `environ`; return: `str`; callers: `main`. Exact group and tenant texts for a fake incident. Numbers are masked.
- **`write_preview`** (line 266); inputs: `text, path`; return: `Path`; callers: `main`.
- **`error_code`** (line 273); inputs: `payload`; return: `str`; callers: `post_text`. Quo error code only. The rest of the body is not logged.
- **`backoff_seconds`** (line 294); inputs: `attempt`; return: `float`; callers: `post_text`.
- **`_raw_key`** (line 298); inputs: `api_key`; return: `str`; callers: `maybe_deliver`, `post_text`.
- **`default_post`** (line 305); inputs: `url, headers, payload, timeout`; return: `tuple[int, Dict[str, Any]]`; callers: not resolved in file. POST helper. Callers must not log the body or the response.
- **`post_text`** (line 340); inputs: `content, from_number, to_numbers, api_key, http_post, sleeper, timeout, max_attempts`; return: `Dict[str, Any]`; callers: `deliver`. POST one SMS. 0206400, 0204403, other 4xx, and timeouts are not retried.
- **`_stamp`** (line 406); inputs: `now`; return: `str`; callers: `_finish`, `claim_key`.
- **`atomic_save_state`** (line 410); inputs: `state, path`; return: `None`; callers: `claim_key`, `deliver`. Replace the state file. The payload is checked before the rename.
- **`claim_key`** (line 429); inputs: `state, key, now, kind, mode, path`; return: `bool`; callers: `deliver`. Record ``sending`` on disk before POST. False when the key is already claimed.
- **`_finish`** (line 470); inputs: `state, key, outcome, now`; return: `None`; callers: `deliver`.
- **`resolve_from_number`** (line 484); inputs: `environ`; return: `str`; callers: `maybe_deliver`.
- **`resolve_group_numbers`** (line 489); inputs: `environ, from_number`; return: `List[str]`; callers: `maybe_deliver`.
- **`resolve_tenant_numbers`** (line 498); inputs: `thread, house_threads, now, profile_fetcher`; return: `List[str]`; callers: `maybe_deliver`. Live member phones. The list is not stored.
- **`build_outbounds`** (line 517); inputs: `plan, from_number, group_numbers, tenant_numbers`; return: `List[Outbound]`; callers: `deliver`.
- **`_invalid_reason`** (line 579); inputs: `item`; return: `str`; callers: `deliver`.
- **`deliver`** (line 591); inputs: `plan, state, state_path, now, from_number, group_numbers, tenant_numbers, api_key, http_post, sleeper, timeout`; return: `List[Dict[str, Any]]`; callers: `maybe_deliver`. Claim each planned Quo key, then POST. Caller must already have passed the gate.
- **`maybe_deliver`** (line 651); inputs: `plan, state, state_path, now, environ, thread, house_threads, profile_fetcher, group_numbers, tenant_numbers, http_post, sleeper`; return: `List[Dict[str, Any]]`; callers: not resolved in file. Send only when the leak_alert gate is open and dry-run is explicitly off.
- **`main`** (line 702); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/runtime.py`

- **`running_in_ci`** (line 51); inputs: `environ`; return: `bool`; callers: `action_hooks_enabled`, `collection_only`, `git_publish_enabled`, `send_enabled`.
- **`flag_value`** (line 56); inputs: `raw`; return: `Optional[bool]`; callers: `action_hooks_enabled`, `collection_only`, `git_publish_enabled`, `send_enabled`.
- **`send_enabled`** (line 69); inputs: `action, environ`; return: `bool`; callers: `enabled_send_actions`. Return True only when CI is off, collection-only is off, and a flag is on.
- **`collection_only`** (line 87); inputs: `environ`; return: `bool`; callers: `send_enabled`.
- **`action_hooks_enabled`** (line 97); inputs: `environ`; return: `bool`; callers: `collection_only`.
- **`git_publish_enabled`** (line 106); inputs: `environ`; return: `bool`; callers: not resolved in file.
- **`collection_output_dir`** (line 113); inputs: `environ`; return: `Path`; callers: not resolved in file. Isolated collection output. Never the live git-tracked docs/data tree.
- **`state_dir`** (line 122); inputs: `environ`; return: `Path`; callers: `collection_output_dir`.
- **`lock_dir`** (line 130); inputs: `name, environ`; return: `Path`; callers: not resolved in file.
- **`host_identity`** (line 138); inputs: `none`; return: `str`; callers: not resolved in file.
- **`enabled_send_actions`** (line 142); inputs: `environ`; return: `Iterable[str]`; callers: not resolved in file.

### `padsplit_scraper/scraper.py`

- **`load_credentials`** (line 400); inputs: `none`; return: `Dict[str, str]`; callers: `run`.
- **`create_session`** (line 410); inputs: `none`; return: `requests.Session`; callers: `run`.
- **`ScrapePhaseError.__init__`** (line 427); inputs: `self, phase, message, endpoint, original`; return: `None`; callers: not resolved in file.
- **`_authed_request`** (line 445); inputs: `session, method, url, creds, login_fn, **kwargs`; return: `requests.Response`; callers: `fetch_earnings`, `fetch_messages`, `fetch_performance_history`, `fetch_properties_stats`, `fetch_rooms`, `fetch_tasks`, `fetch_thread_messages`, `update_task_status`.
- **`login`** (line 466); inputs: `session, email, password, force`; return: `None`; callers: `run`.
- **`fetch_messages`** (line 485); inputs: `session, creds, page_size`; return: `List[Dict]`; callers: `run`.
- **`fetch_thread_messages`** (line 539); inputs: `session, creds, chat_id, first`; return: `List[Dict]`; callers: `_enrich_recent_threads`. Fetch the most recent `first` messages for a single chat thread.
- **`fetch_tasks`** (line 573); inputs: `session, creds`; return: `Dict[str, List[Dict]]`; callers: `run`. Fetch maintenance tickets and group them by status to mirror UI buckets.
- **`fetch_properties_stats`** (line 616); inputs: `session, creds`; return: `List[Dict]`; callers: `run`. Fetch per-property occupancy stats used by the dashboard stats page.
- **`fetch_rooms`** (line 639); inputs: `session, creds, page_size`; return: `List[Dict]`; callers: `run`. Fetch all rooms with pagination.
- **`fetch_earnings`** (line 671); inputs: `session, creds`; return: `Dict[str, Any]`; callers: `run`. Fetch monthly earnings history.
- **`fetch_performance_history`** (line 694); inputs: `session, creds`; return: `Dict[str, Dict[str, Any]]`; callers: `run`. Fetch portfolio-level monthly performance history from host performance endpoints.
- **`update_task_status`** (line 775); inputs: `session, creds, task_id, new_status`; return: `Dict`; callers: not resolved in file.
- **`_describe_exception`** (line 797); inputs: `exc`; return: `str`; callers: `_run_phase`.
- **`_run_phase`** (line 806); inputs: `label, phase, fn`; return: `unspecified`; callers: `_enrich_recent_threads`, `run`.
- **`_enrich_recent_threads`** (line 822); inputs: `session, creds, messages`; return: `None`; callers: `run`.
- **`_action_hooks_allowed`** (line 850); inputs: `policy`; return: `bool`; callers: `_run_action_hooks`. Hooks follow an explicit policy when one is provided.
- **`_invoke_action_hook`** (line 865); inputs: `module_name, func_name, session, creds, messages`; return: `None`; callers: `_run_action_hooks`. Import-and-call one outbound hook. Collection-only never reaches here.
- **`_run_action_hooks`** (line 874); inputs: `session, creds, messages, policy`; return: `None`; callers: `run`.
- **`_emit_frontload_webhook`** (line 894); inputs: `session, creds, messages, rooms, occupancy, policy`; return: `None`; callers: `run`. Booking/listing webhook. Failures stay off the scrape result.
- **`_apply_isolated_output`** (line 926); inputs: `none`; return: `Path`; callers: `run`.
- **`_source_health`** (line 938); inputs: `messages_scraped_at, stats_state, stats_scraped_at, fallback_used`; return: `Dict[str, Any]`; callers: `run`.
- **`_count_tasks`** (line 956); inputs: `fetched`; return: `int`; callers: `_this_run_health`.
- **`_earnings_row_count`** (line 966); inputs: `earnings_payload`; return: `int`; callers: `_this_run_health`.
- **`_this_run_health`** (line 974); inputs: `state, mode, run_scraped_at, started_at, messages, previous, fetched_tasks, rooms, properties, earnings_payload, failed_phase, error_type, error_message, fallback_used, sources`; return: `Dict[str, Any]`; callers: `run`. Build this run's health. Never reuse a prior ``run_status`` object.
- **`_ok_outcome`** (line 1032); inputs: `run_status`; return: `RunOutcome`; callers: `run`.
- **`_degraded_outcome`** (line 1036); inputs: `run_status`; return: `RunOutcome`; callers: `run`.
- **`run`** (line 1045); inputs: `messages_only, isolate_output, policy`; return: `RunOutcome`; callers: `main`.
- **`main`** (line 1236); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/seo_monthly.py`

- **`load_environment`** (line 136); inputs: `none`; return: `None`; callers: `main`.
- **`running_in_ci`** (line 140); inputs: `none`; return: `bool`; callers: `main`, `posting_allowed`, `run_pack`.
- **`posting_allowed`** (line 144); inputs: `ci`; return: `bool`; callers: `run_pack`. CI must never post. Live post is the Mac LaunchAgent only.
- **`sanitize_text`** (line 156); inputs: `text`; return: `str`; callers: `attach_discord_section`, `render_joe_lines`, `render_markdown`.
- **`joe_mention`** (line 163); inputs: `none`; return: `str`; callers: `render_joe_lines`.
- **`normalize_price_status`** (line 170); inputs: `value`; return: `Optional[str]`; callers: `listed_or_vacant_over_14.add`.
- **`_room_key`** (line 175); inputs: `address, room_number`; return: `Tuple[str, Optional[str]]`; callers: `_occupancy_index`, `listed_or_vacant_over_14`, `listed_or_vacant_over_14.add`.
- **`_occupancy_index`** (line 183); inputs: `rows`; return: `Dict[Tuple[str, Optional[str]], Dict[str, Any]]`; callers: `listed_or_vacant_over_14`.
- **`_has_cover`** (line 192); inputs: `room`; return: `bool`; callers: `listed_or_vacant_over_14.add`.
- **`_cover_has_orientation`** (line 200); inputs: `room`; return: `bool`; callers: `listed_or_vacant_over_14.add`.
- **`listed_or_vacant_over_14`** (line 208); inputs: `rooms, occupancy_rooms`; return: `List[StaleRoom]`; callers: `build_advice_pack`. Listed-status or presence-vacant rooms older than 14 Chicago days.
- **`listed_or_vacant_over_14.add`** (line 217); inputs: `room, occ_row, listed, vacant`; return: `None`; callers: `listed_or_vacant_over_14`.
- **`pricing_outliers`** (line 286); inputs: `rooms`; return: `List[StaleRoom]`; callers: `build_advice_pack`. High / Very High Pricing Analysis only. Never a blind −$1 sweep.
- **`listing_photo_gaps`** (line 293); inputs: `rooms`; return: `Tuple[List[str], str]`; callers: `build_advice_pack`.
- **`_room_label`** (line 314); inputs: `row`; return: `str`; callers: `listing_photo_gaps`, `render_markdown`.
- **`_days_phrase`** (line 320); inputs: `row`; return: `str`; callers: `render_markdown`.
- **`build_advice_pack`** (line 329); inputs: `bundle, now`; return: `AdvicePack`; callers: `run_pack`.
- **`timezone_utc`** (line 350); inputs: `none`; return: `unspecified`; callers: `build_advice_pack`, `report_path`.
- **`render_markdown`** (line 356); inputs: `pack`; return: `str`; callers: `build_advice_pack`.
- **`render_joe_lines`** (line 436); inputs: `pack`; return: `List[str]`; callers: `build_advice_pack`.
- **`_short_street`** (line 476); inputs: `label`; return: `str`; callers: `render_joe_lines`.
- **`_room_num`** (line 481); inputs: `row`; return: `str`; callers: `render_joe_lines`.
- **`attach_discord_section`** (line 485); inputs: `markdown, joe_lines, posted, reason`; return: `str`; callers: `run_pack`.
- **`fetch_live_bundle`** (line 491); inputs: `now`; return: `DataBundle`; callers: not resolved in file.
- **`load_stale_bundle`** (line 507); inputs: `none`; return: `DataBundle`; callers: not resolved in file.
- **`_load_first_json`** (line 531); inputs: `paths`; return: `Optional[Dict[str, Any]]`; callers: `load_stale_bundle`.
- **`collect_bundle`** (line 544); inputs: `now, live_fetcher, stale_loader, stale_only`; return: `DataBundle`; callers: `run_pack`.
- **`report_path`** (line 571); inputs: `now, directory`; return: `Path`; callers: `run_pack`.
- **`write_report`** (line 576); inputs: `markdown, path`; return: `Path`; callers: `run_pack`.
- **`post_joe_discord`** (line 582); inputs: `lines, token, channel`; return: `None`; callers: not resolved in file.
- **`build_launchd_plist`** (line 606); inputs: `workspace`; return: `Dict[str, Any]`; callers: `install_launchd`.
- **`install_launchd`** (line 619); inputs: `workspace`; return: `Path`; callers: `main`.
- **`run_pack`** (line 634); inputs: `now, live_fetcher, stale_loader, poster, output_dir, stale_only, dry_run, ci`; return: `AdvicePack`; callers: `main`.
- **`main`** (line 681); inputs: `argv`; return: `int`; callers: not resolved in file.

### `padsplit_scraper/state_store.py`

- **`state_path`** (line 20); inputs: `name, environ`; return: `Path`; callers: `load_json`, `save_json`.
- **`load_json`** (line 25); inputs: `name, default, environ`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`save_json`** (line 36); inputs: `name, payload, environ`; return: `Path`; callers: not resolved in file.

### `padsplit_scraper/stats_freshness.py`

- **`stats_freshness`** (line 12); inputs: `payload, now`; return: `Dict[str, Any]`; callers: not resolved in file.
- **`_parse_ts`** (line 43); inputs: `value`; return: `Optional[datetime]`; callers: `stats_freshness`.

### `slack_task_digest.py`

- **`_as_ct`** (line 52); inputs: `now`; return: `datetime`; callers: `ct_day`, `in_morning_window`, `write_ledger`.
- **`ct_day`** (line 58); inputs: `now`; return: `str`; callers: `format_message`, `run_digest`.
- **`in_morning_window`** (line 62); inputs: `now`; return: `bool`; callers: `post_decision`.
- **`load_data`** (line 67); inputs: `path`; return: `Dict[str, Any]`; callers: `main`.
- **`load_ledger`** (line 71); inputs: `path`; return: `Dict[str, Any]`; callers: `run_digest`.
- **`already_posted_today`** (line 81); inputs: `ledger, day`; return: `bool`; callers: `run_digest`.
- **`post_decision`** (line 85); inputs: `already_posted, after_scrape, now`; return: `Optional[str]`; callers: `run_digest`. Return a skip reason, or None when the digest should be sent.
- **`write_ledger`** (line 94); inputs: `path, day, source, posted_at`; return: `None`; callers: `run_digest`.
- **`collect_tasks`** (line 107); inputs: `tasks`; return: `Tuple[Dict[str, List[Tuple[str, str, Optional[int]]]], int, int]`; callers: `compose_message`, `digest_counts`.
- **`digest_counts`** (line 125); inputs: `latest`; return: `Dict[str, int]`; callers: `run_digest`.
- **`format_dry_run`** (line 135); inputs: `counts`; return: `str`; callers: `run_digest`. Counts only. Callers must not print the composed digest in dry-run.
- **`format_message`** (line 147); inputs: `grouped, total_req, total_open, today`; return: `str`; callers: `compose_message`.
- **`compose_message`** (line 169); inputs: `latest, weather_block, kpis, today`; return: `str`; callers: `run_digest`. Build the digest from live tasks. kpis/vacancy_rooms are ignored.
- **`truncate_for_discord`** (line 188); inputs: `message`; return: `str`; callers: `send_to_discord`.
- **`send_to_discord`** (line 195); inputs: `message`; return: `bool`; callers: not resolved in file. Post the digest. Never print the webhook URL or any other secret.
- **`fetch_weather`** (line 229); inputs: `none`; return: `Optional[str]`; callers: not resolved in file.
- **`run_digest`** (line 265); inputs: `latest, now, ledger_path, after_scrape, dry_run, send, weather_fn`; return: `int`; callers: `main`. Compose and maybe post. Dry-run prints counts and always returns 0.
- **`main`** (line 307); inputs: `argv`; return: `int`; callers: not resolved in file.

### `smarthome/cli.py`

- **`_notify`** (line 13); inputs: `text`; return: `None`; callers: `cmd_list`, `cmd_off`, `cmd_set`.
- **`_slot_key_for`** (line 22); inputs: `name, now`; return: `Optional[str]`; callers: `cmd_set`.
- **`cmd_list`** (line 29); inputs: `_args`; return: `int`; callers: not resolved in file.
- **`cmd_set`** (line 49); inputs: `args`; return: `int`; callers: not resolved in file.
- **`cmd_off`** (line 72); inputs: `args`; return: `int`; callers: not resolved in file.
- **`cmd_status`** (line 94); inputs: `_args`; return: `int`; callers: not resolved in file.
- **`build_parser`** (line 116); inputs: `none`; return: `argparse.ArgumentParser`; callers: `main`.
- **`main`** (line 131); inputs: `argv`; return: `int`; callers: not resolved in file.

### `smarthome/clocks.py`

- **`tokenize`** (line 35); inputs: `value`; return: `List[str]`; callers: `match_houses`.
- **`find_active_slot`** (line 42); inputs: `slots, now`; return: `Slot`; callers: `active_cool`.
- **`slot_key`** (line 51); inputs: `house, slot`; return: `str`; callers: not resolved in file.
- **`match_houses`** (line 55); inputs: `name`; return: `List[str]`; callers: `resolve_house`.
- **`resolve_house`** (line 64); inputs: `name`; return: `Optional[str]`; callers: `active_cool`.
- **`_load_json`** (line 73); inputs: `path`; return: `Dict`; callers: `load_slots`.
- **`_slots_from_payload`** (line 79); inputs: `rows`; return: `List[Slot]`; callers: `load_slots`.
- **`load_slots`** (line 86); inputs: `house, schedules_path, clocks_path`; return: `Optional[List[Slot]]`; callers: `active_cool`.
- **`active_cool`** (line 108); inputs: `name, now, schedules_path, clocks_path`; return: `Optional[Tuple[str, Slot]]`; callers: not resolved in file.

### `smarthome/cloud.py`

- **`f_to_c`** (line 59); inputs: `fahrenheit`; return: `float`; callers: `set_temp`.
- **`c_to_f`** (line 63); inputs: `celsius`; return: `float`; callers: `_readback_temp_f`.
- **`load_credentials`** (line 67); inputs: `none`; return: `Dict[str, str]`; callers: `connect`.
- **`_is_ac`** (line 76); inputs: `item`; return: `bool`; callers: `list_acs`.
- **`_map_cloud_error`** (line 81); inputs: `exc`; return: `Exception`; callers: `connect`, `set_temp`, `turn_off`.
- **`slim_login_body`** (line 91); inputs: `original, device_id, pushtoken, appid, account`; return: `Dict[str, Any]`; callers: `_install_slim_login.wrapped.patched`. mill1000-style /mj/user/login body: same silo, no 2.22.0 version block.
- **`_install_slim_login`** (line 124); inputs: `client`; return: `None`; callers: `_connect_slim`.
- **`_install_slim_login.wrapped`** (line 127); inputs: `none`; return: `None`; callers: not resolved in file.
- **`_install_slim_login.wrapped.patched`** (line 130); inputs: `endpoint, *args, **kwargs`; return: `Any`; callers: not resolved in file.
- **`_connect_slim`** (line 152); inputs: `account, password, appname, device_id, pushtoken, **_kwargs`; return: `Any`; callers: not resolved in file.
- **`_connect_for_spec`** (line 185); inputs: `spec`; return: `Callable[..., Any]`; callers: `connect`.
- **`connect`** (line 193); inputs: `credentials, connect_fn, identity_path`; return: `Any`; callers: not resolved in file.
- **`_use_appliance_code`** (line 220); inputs: `endpoint, payload`; return: `Dict[str, Any]`; callers: `_install_appliance_code_retry.patched_request`.
- **`_transparent_error_code`** (line 227); inputs: `exc`; return: `Optional[int]`; callers: not resolved in file.
- **`_transparent_send_envelope`** (line 238); inputs: `cloud, req_id`; return: `Dict[str, Any]`; callers: `_install_appliance_code_retry.patched_request`.
- **`_install_appliance_code_retry`** (line 253); inputs: `cloud`; return: `None`; callers: `connect`. US SmartHome send rejects applianceId and appVNum. Seed reqId so the library skips those fields.
- **`_install_appliance_code_retry.patched_request`** (line 258); inputs: `endpoint, args, authenticate, key, data, req_id, instant`; return: `Any`; callers: not resolved in file.
- **`list_acs`** (line 296); inputs: `cloud`; return: `List[Dict[str, Any]]`; callers: `find_ac`.
- **`find_ac`** (line 301); inputs: `cloud, name`; return: `Dict[str, Any]`; callers: `set_temp`, `turn_off`.
- **`_device`** (line 311); inputs: `cloud, appliance_id, appliance_state_fn`; return: `Any`; callers: `set_temp`, `turn_off`.
- **`_check_online`** (line 333); inputs: `dev, name`; return: `None`; callers: `set_temp`, `turn_off`.
- **`_readback_temp_f`** (line 338); inputs: `dev`; return: `Optional[float]`; callers: `set_temp`.
- **`_readback_running`** (line 348); inputs: `dev`; return: `Optional[bool]`; callers: `set_temp`, `turn_off`.
- **`_refresh`** (line 356); inputs: `dev, cloud`; return: `None`; callers: `set_temp`, `turn_off`.
- **`set_temp`** (line 362); inputs: `cloud, name, fahrenheit, mode, appliance_state_fn`; return: `None`; callers: not resolved in file.
- **`turn_off`** (line 396); inputs: `cloud, name, appliance_state_fn`; return: `None`; callers: not resolved in file.

### `smarthome/identity.py`

- **`_mint`** (line 39); inputs: `none`; return: `Dict[str, str]`; callers: `load_or_create`.
- **`_read`** (line 50); inputs: `path`; return: `Optional[Dict[str, str]]`; callers: `current_fingerprint`, `load_or_create`.
- **`save_identity`** (line 72); inputs: `payload, path`; return: `None`; callers: `load_or_create`, `select_fingerprint`.
- **`load_or_create`** (line 80); inputs: `path`; return: `Dict[str, str]`; callers: `select_fingerprint`.
- **`current_fingerprint`** (line 90); inputs: `path`; return: `str`; callers: not resolved in file.
- **`fingerprint_spec`** (line 97); inputs: `name`; return: `Dict[str, Any]`; callers: `select_fingerprint`.
- **`select_fingerprint`** (line 107); inputs: `name, path`; return: `Dict[str, str]`; callers: not resolved in file.

### `smarthome/intent.py`

- **`_empty`** (line 13); inputs: `none`; return: `Dict[str, Any]`; callers: `load_intent`.
- **`load_intent`** (line 17); inputs: `path`; return: `Dict[str, Any]`; callers: `clear_hold`, `is_sticky_off`, `record_hold`, `record_off`.
- **`save_intent`** (line 25); inputs: `payload, path`; return: `None`; callers: `clear_hold`, `record_hold`, `record_off`.
- **`unit_state`** (line 33); inputs: `payload, name`; return: `Dict[str, Any]`; callers: `clear_hold`, `is_sticky_off`, `record_hold`, `record_off`.
- **`record_hold`** (line 38); inputs: `name, fahrenheit, slot_key, path`; return: `None`; callers: not resolved in file.
- **`record_off`** (line 47); inputs: `name, path`; return: `None`; callers: not resolved in file.
- **`clear_hold`** (line 56); inputs: `name, path`; return: `None`; callers: not resolved in file.
- **`is_sticky_off`** (line 64); inputs: `name, path`; return: `bool`; callers: not resolved in file.

### `smarthome/policy.py`

- **`in_night_off`** (line 16); inputs: `now`; return: `bool`; callers: `resolve_action`.
- **`resolve_action`** (line 20); inputs: `name, now, intent_path, schedules_path, clocks_path`; return: `Dict[str, Any]`; callers: not resolved in file.

### `smarthome/session.py`

- **`session_lock`** (line 19); inputs: `path`; return: `Iterator[None]`; callers: not resolved in file.
- **`cooldown_active`** (line 31); inputs: `path, now, fingerprint`; return: `bool`; callers: not resolved in file.
- **`start_cooldown`** (line 49); inputs: `path, now, fingerprint`; return: `None`; callers: not resolved in file.

### `smarthome/watcher.py`

- **`_notify`** (line 27); inputs: `text`; return: `None`; callers: not resolved in file.
- **`_load_streak`** (line 36); inputs: `path`; return: `Dict[str, Any]`; callers: `enforce_tick`.
- **`_save_streak`** (line 44); inputs: `count, day, path`; return: `None`; callers: `enforce_tick`.
- **`_today`** (line 50); inputs: `now`; return: `str`; callers: `_digest_due`, `_mark_digest`, `enforce_tick`.
- **`_live_running`** (line 54); inputs: `dev`; return: `Optional[bool]`; callers: `_read_live`.
- **`_read_live`** (line 58); inputs: `client, item, device_fn`; return: `tuple[Optional[bool], Optional[float]]`; callers: `enforce_tick`.
- **`_load_digest`** (line 70); inputs: `path`; return: `Dict[str, Any]`; callers: `_digest_due`, `_mark_digest`.
- **`_digest_due`** (line 78); inputs: `now, path`; return: `bool`; callers: `enforce_tick`.
- **`_mark_digest`** (line 87); inputs: `now, path`; return: `None`; callers: `enforce_tick`.
- **`_format_digest`** (line 98); inputs: `when, rows`; return: `str`; callers: `enforce_tick`.
- **`enforce_tick`** (line 104); inputs: `now, connect_fn, set_temp_fn, turn_off_fn, list_acs_fn, device_fn, intent_path, notify_fn`; return: `Dict[str, Any]`; callers: `main`.
- **`build_plist`** (line 190); inputs: `none`; return: `Dict[str, Any]`; callers: `write_plist`.
- **`write_plist`** (line 203); inputs: `path`; return: `Path`; callers: `main`.
- **`main`** (line 209); inputs: `argv`; return: `int`; callers: not resolved in file.

### `thermostat/schedule.py`

- **`temp_alert_webhook_url`** (line 45); inputs: `none`; return: `Optional[str]`; callers: `enforce_command`. Return the #ai-tasks-temp webhook, or None if unset.
- **`_post_temp_alert`** (line 55); inputs: `webhook_url, target, temp_f`; return: `None`; callers: not resolved in file.
- **`_should_send_temp_alert`** (line 70); inputs: `none`; return: `bool`; callers: `enforce_command`. True if no alert sent in last hour (file-based debounce, Unix timestamp).
- **`_record_temp_alert`** (line 83); inputs: `none`; return: `None`; callers: `enforce_command`.
- **`Slot.hhmm`** (line 100); inputs: `self`; return: `str`; callers: not resolved in file.
- **`Slot.display`** (line 104); inputs: `self`; return: `str`; callers: not resolved in file.
- **`parse_time`** (line 110); inputs: `value`; return: `Tuple[int, int]`; callers: `parse_slot`.
- **`parse_slot`** (line 127); inputs: `values`; return: `Slot`; callers: `install_command`.
- **`slot_to_dict`** (line 139); inputs: `slot`; return: `Dict[str, int]`; callers: `save_schedules`.
- **`slot_from_dict`** (line 143); inputs: `value`; return: `Slot`; callers: `load_schedules`.
- **`load_schedules`** (line 152); inputs: `none`; return: `Dict[str, List[Slot]]`; callers: `enforce_command`, `save_schedules`, `status_command`.
- **`save_schedules`** (line 170); inputs: `data`; return: `None`; callers: `install_command`.
- **`find_active_slot`** (line 188); inputs: `slots, now`; return: `Slot`; callers: `enforce_command`, `status_command`.
- **`sanitize_label`** (line 197); inputs: `name`; return: `str`; callers: `install_command`, `uninstall_command`.
- **`label_for`** (line 205); inputs: `slug, slot`; return: `str`; callers: `build_plist_dict`, `plist_path_for`.
- **`plist_path_for`** (line 209); inputs: `slug, slot`; return: `Path`; callers: `install_schedule`.
- **`log_paths_for`** (line 213); inputs: `slug, slot`; return: `Tuple[Path, Path]`; callers: `build_plist_dict`.
- **`build_program_arguments`** (line 218); inputs: `slot, target_args`; return: `List[str]`; callers: `build_plist_dict`.
- **`build_plist_dict`** (line 230); inputs: `slug, slot, target_args`; return: `Dict`; callers: `build_plist`.
- **`build_plist`** (line 243); inputs: `slug, slot, target_args`; return: `str`; callers: `install_schedule`.
- **`enforcer_plist_path`** (line 247); inputs: `none`; return: `Path`; callers: `install_enforcer_if_needed`.
- **`build_enforcer_plist`** (line 251); inputs: `none`; return: `Dict`; callers: `install_enforcer_if_needed`.
- **`install_enforcer_if_needed`** (line 267); inputs: `none`; return: `None`; callers: `install_command`.
- **`launchctl`** (line 281); inputs: `args`; return: `subprocess.CompletedProcess`; callers: `install_enforcer_if_needed`, `install_schedule`, `loaded_labels`, `unload_and_remove`.
- **`managed_plists`** (line 285); inputs: `none`; return: `List[Path]`; callers: `calendar_plists`, `uninstall_command`.
- **`calendar_plists`** (line 289); inputs: `none`; return: `List[Path]`; callers: `legacy_status_command`, `status_command`.
- **`target_plists`** (line 293); inputs: `slug`; return: `List[Path]`; callers: `install_command`, `install_schedule`, `uninstall_command`.
- **`unload_and_remove`** (line 301); inputs: `paths`; return: `None`; callers: `install_command`, `install_schedule`, `uninstall_command`.
- **`install_schedule`** (line 317); inputs: `target_slug, target_args, slots`; return: `None`; callers: not resolved in file.
- **`install_command`** (line 329); inputs: `args`; return: `int`; callers: `main`.
- **`resume_schedule`** (line 341); inputs: `target_args`; return: `None`; callers: `uninstall_command`.
- **`uninstall_command`** (line 348); inputs: `args`; return: `int`; callers: `main`.
- **`loaded_labels`** (line 362); inputs: `none`; return: `set[str]`; callers: `install_enforcer_if_needed`, `legacy_status_command`, `status_command`.
- **`normalize_location_name`** (line 375); inputs: `value`; return: `str`; callers: `fetch_live_setpoints.worker`, `find_device_for_target`, `heal_snapshot_cache`, `install_command`, `live_row_for_target`, `load_live_snapshot`, `resolve_schedule_target`, `status_command`.
- **`resolve_schedule_target`** (line 379); inputs: `target, schedules`; return: `str`; callers: `status_command`.
- **`setup_enforcer_logging`** (line 397); inputs: `none`; return: `logging.Logger`; callers: `enforce_command`.
- **`find_device_for_target`** (line 418); inputs: `norm_target, raw_locations, location_names`; return: `Tuple[Optional[int], Optional[Dict]]`; callers: `enforce_command`.
- **`status_is_permanent_hold`** (line 453); inputs: `value`; return: `bool`; callers: `enforce_command`.
- **`enforce_command`** (line 460); inputs: `none`; return: `int`; callers: `main`.
- **`load_live_snapshot`** (line 557); inputs: `none`; return: `Tuple[Dict[str, Dict[str, object]], Optional[str]]`; callers: `legacy_status_command`.
- **`fetch_live_setpoints`** (line 581); inputs: `none`; return: `Optional[Dict[str, Dict[str, object]]]`; callers: `legacy_status_command`.
- **`fetch_live_setpoints.worker`** (line 588); inputs: `none`; return: `None`; callers: not resolved in file.
- **`heal_snapshot_cache`** (line 662); inputs: `live_data`; return: `None`; callers: `legacy_status_command`.
- **`parse_scraped_at`** (line 710); inputs: `value`; return: `Optional[datetime]`; callers: `heal_snapshot_cache`, `legacy_status_command`.
- **`format_snapshot_value`** (line 719); inputs: `value`; return: `str`; callers: `live_row_for_target`.
- **`live_row_for_target`** (line 727); inputs: `target, live_snapshot`; return: `Dict[str, str]`; callers: `legacy_status_command`.
- **`plist_summary`** (line 742); inputs: `path`; return: `Dict[str, str]`; callers: `legacy_status_command`, `status_command`.
- **`legacy_status_command`** (line 766); inputs: `paths`; return: `int`; callers: `status_command`.
- **`format_last_run`** (line 809); inputs: `path`; return: `str`; callers: `status_command`.
- **`status_command`** (line 816); inputs: `args`; return: `int`; callers: `main`.
- **`build_parser`** (line 867); inputs: `none`; return: `argparse.ArgumentParser`; callers: `main`.
- **`main`** (line 903); inputs: `argv`; return: `int`; callers: not resolved in file.

### `thermostat/scraper.py`

- **`load_credentials`** (line 37); inputs: `none`; return: `Dict[str, str]`; callers: `main`.
- **`create_session`** (line 48); inputs: `none`; return: `requests.Session`; callers: `main`.
- **`login`** (line 57); inputs: `session, email, password`; return: `None`; callers: `main`.
- **`fetch_location_names`** (line 88); inputs: `session`; return: `Dict[int, str]`; callers: `fetch_fresh_output`. Parse /portal/Locations HTML for the true location names keyed by LocationID.
- **`fetch_locations_page`** (line 115); inputs: `session, page`; return: `List[Dict]`; callers: `fetch_locations`.
- **`fetch_locations`** (line 139); inputs: `session`; return: `List[Dict]`; callers: `fetch_fresh_output`.
- **`fetch_device_uidata`** (line 153); inputs: `session, device_id`; return: `Dict`; callers: `fetch_fresh_output`. Fetch active setpoints via CheckDataSession.
- **`extract_device`** (line 173); inputs: `dev`; return: `Dict`; callers: `build_output`.
- **`extract_device.first_present`** (line 176); inputs: `keys`; return: `Optional[float]`; callers: `extract_device`.
- **`print_report`** (line 201); inputs: `locations`; return: `None`; callers: `main`.
- **`build_output`** (line 216); inputs: `raw_locations, location_names`; return: `Dict`; callers: `fetch_fresh_output`.
- **`fetch_fresh_output`** (line 234); inputs: `session`; return: `Dict`; callers: `main`.
- **`validate_snapshot_payload`** (line 256); inputs: `payload, source`; return: `Dict`; callers: `load_stale_snapshot`.
- **`load_stale_snapshot`** (line 268); inputs: `path`; return: `Dict`; callers: `main`.
- **`persist_output`** (line 280); inputs: `output, out_dir`; return: `None`; callers: `main`.
- **`notify_stale_fallback`** (line 290); inputs: `scraped_at`; return: `None`; callers: `main`.
- **`main`** (line 302); inputs: `none`; return: `int`; callers: not resolved in file.

### `thermostat/set_temps.py`

- **`normalize_text`** (line 44); inputs: `value`; return: `str`; callers: `tokenize`.
- **`tokenize`** (line 51); inputs: `value`; return: `List[str]`; callers: `target_matches`.
- **`target_matches`** (line 56); inputs: `target, candidate_strings`; return: `bool`; callers: `find_location_by_target`.
- **`describe_location`** (line 71); inputs: `loc, location_names`; return: `str`; callers: `apply_device_changes`, `find_location_by_id`, `find_location_by_target`.
- **`candidate_strings`** (line 80); inputs: `loc, location_names`; return: `List[str]`; callers: `find_location_by_target`.
- **`find_location_by_id`** (line 95); inputs: `locations, location_names, location_id`; return: `Dict`; callers: `select_locations`.
- **`find_location_by_target`** (line 107); inputs: `locations, location_names, target`; return: `Dict`; callers: `select_locations`.
- **`select_locations`** (line 126); inputs: `locations, location_names, targets, location_ids, allow_all`; return: `List[Dict]`; callers: `apply_device_changes`.
- **`submit_device_change`** (line 151); inputs: `session, device_id, payload, action_label`; return: `bool`; callers: `apply_device_changes`.
- **`set_hold_payload`** (line 177); inputs: `device_id, cool_setpoint, heat_setpoint`; return: `Dict`; callers: `apply_device_changes`.
- **`set_resume_schedule_payload`** (line 191); inputs: `device_id`; return: `Dict`; callers: `apply_device_changes`.
- **`launchagent_paths`** (line 205); inputs: `none`; return: `List[Path]`; callers: `stop_launchagent`.
- **`stop_launchagent`** (line 212); inputs: `none`; return: `None`; callers: `apply_device_changes`.
- **`apply_device_changes`** (line 231); inputs: `cool_setpoint, heat_setpoint, targets, location_ids, allow_all, resume_schedule, stop_scheduled_job`; return: `None`; callers: `main`.
- **`build_parser`** (line 299); inputs: `none`; return: `unspecified`; callers: `main`.
- **`main`** (line 338); inputs: `argv`; return: `int`; callers: not resolved in file.

## JavaScript symbols in dashboard pages

Static text scan of named declarations and common arrow assignments. Input/output and side effects require source review; the scan is not a JavaScript parser.

### `docs/codes.html`

- `showApp` (line 651)
- `showGate` (line 660)
- `roomNumberFromKey` (line 700)
- `lockboxCodeKey` (line 705)
- `lockboxLocationKey` (line 706)
- `lockboxNotesKey` (line 707)
- `roomAcFilterSizeKey` (line 708)
- `extraLockboxKeys` (line 709)
- `defaultFieldValue` (line 718)
- `resolveSavedOrDefault` (line 728)
- `canonicalizeCodeFields` (line 741)
- `hashCodeFields` (line 751)
- `updateDateOverdueState` (line 757)
- `createFieldInput` (line 767)
- `appendSectionLabel` (line 784)
- `renderLabelValueTable` (line 797)
- `renderHeaderRow` (line 818)
- `wrapTable` (line 830)
- `renderRoomsTable` (line 837)
- `renderExtraLockboxesTable` (line 906)
- `renderProperty` (line 958)
- `initCodes` (line 1020)
- `isCodesUser` (line 1022)
- `liveRef` (line 1024)
- `versionsCol` (line 1025)
- `writeSnapshot` (line 1027)
- `latestVersionHash` (line 1038)
- `collectForm` (line 1044)
- `refreshHouse` (line 1052)
- `formatWhen` (line 1064)
- `historyOpts` (line 1070)
- `appendHousesBack` (line 1076)
- `renderHousePicker` (line 1089)
- `loadHistory` (line 1110)
- `showVersion` (line 1160)
- `restoreLiveCodes` (line 1193)
- `bindHouse` (line 1226)

### `docs/index.html`

- `fmtDate` (line 311)
- `chicagoToday` (line 317)
- `escapeText` (line 326)
- `roomLabel` (line 336)
- `operatorLists` (line 343)
- `occupancyTable` (line 361)
- `renderOccupancy` (line 371)
- `getImportantIds` (line 443)
- `getMessageCreatedValue` (line 446)
- `sortTaskMessages` (line 452)
- `removeTaskMessageFromTask` (line 461)
- `buildTaskMessageRow` (line 470)
- `renderTaskMessages` (line 493)
- `upsertTaskMessage` (line 503)
- `removeTaskMessage` (line 524)
- `buildImportantBtn` (line 531)
- `buildTaskChatBox` (line 555)
- `buildManualTaskRow` (line 612)
- `updateManualBadgeCount` (line 634)
- `render` (line 639)
- `renderThermostats` (line 766)
- `renderWeather` (line 810)
- `initFirestore` (line 876)
- `initNotes` (line 890)

### `docs/kpi-history.html`

- `makeLineDataset` (line 324)
- `makeThresholdDataset` (line 337)
- `scoreColor` (line 350)
- `naira` (line 355)
- `normalizeScore` (line 362)
- `payoutForScore` (line 365)
- `formatScoreWithPayout` (line 370)
- `scoreBadge` (line 374)
- `renderHistory` (line 380)
- `snap` (line 404)
- `showApp` (line 599)
- `showGate` (line 603)
- `loadHistory` (line 607)

### `docs/private-messages.html`

- `fmtDate` (line 200)
- `renderList` (line 208)
- `initApp` (line 278)

### `docs/stats.html`

- `toNum` (line 633)
- `money` (line 638)
- `naira` (line 642)
- `pct` (line 650)
- `fmtDate` (line 654)
- `statsFreshness` (line 660)
- `applyStatsFreshness` (line 677)
- `scoreClass` (line 709)
- `normalizeScore` (line 715)
- `payoutForScore` (line 719)
- `formatScorePayout` (line 725)
- `formatScoreFormula` (line 730)
- `setManualStatus` (line 738)
- `setScoreCard` (line 744)
- `deriveManualDefaults` (line 790)
- `fillManualForm` (line 812)
- `readManualForm` (line 820)
- `computeManualKpis` (line 829)
- `applyManualInputs` (line 916)
- `resetManualInputs` (line 922)
- `setOccupancyCards` (line 929)
- `renderVacancy` (line 950)
- `renderRetention` (line 973)
- `renderFlipRooms` (line 1009)
- `renderTickets` (line 1030)
- `renderPerProperty` (line 1053)
- `renderStats` (line 1077)
- `showApp` (line 1157)
- `showGate` (line 1161)
- `loadStats` (line 1165)

### `docs/templates.html`

- `buildGrid` (line 214)
- `initApp` (line 289)

### `docs/vendors.html`

- `buildVendorCard` (line 207)
- `upsertVendorStatus` (line 291)
- `initFirestore` (line 303)

## Private snapshot and UI helpers added locally

- `padsplit_scraper/private_snapshot.py`: `_write_private_snapshot` writes bounded versioned chunks then a pointer; `run_publisher` checks explicit gates, CI, local locking and the Firebase project; `main` provides the standalone CLI. Calls may write private Firestore data only when explicitly enabled and configured. Tests: `test_private_snapshot.py` and Firestore rules emulator tests. No automatic scheduled caller is installed.
- `docs/private-snapshot.js`: `readPrivateSnapshot(readDocument)` validates metadata, reads exact chunks, checks consistency and returns the assembled messages/tasks or an unavailable error. Called by the overview and private-messages pages. Test: `test_private_reader.mjs`.
- `docs/auth-listeners.js`: `createAuthListeners()` manages subscription cleanup and reattachment on auth changes; `staleListenerIds` supports list reconciliation. Test: `test_auth_listeners.mjs`.
- `docs/listener-status.js`, `docs/system-health.js`, `docs/occupancy-view.js`, and `docs/navigation.js` provide listener states, reported collection health, occupancy display labels, and existing-page navigation respectively. They do not invoke device controls or external messages.

## Dashboard detail and inbox continuation

- `docs/property-detail.js`: `propertyDetailData` conservatively joins room data to directly identified tasks and uniquely matching climate records; `attachPropertyDetail` opens an accessible dialog, clears private content and restores focus. Tests: `test_property_detail.mjs`, `test_property_dialog.mjs`.
- `docs/task-filter.js`: search/status predicates, draft clearing guard and discussion scroll tracking; UI filters and sorts rendered rows without issuing PadSplit assignment changes. Test: `test_task_filter.mjs`.
- `docs/private-messages.html`: two-pane private inbox and per-tab draft storage; reads the private snapshot but has no outgoing message action. Tests: `test_inbox_view.mjs`, `test_private_messages_render.mjs`.

## Demo and shared authentication additions — 2026-11-04

`demo/server.py` starts the explicit loopback demo server and serves only its allowlisted demo files and session endpoint. `demo/demo.js` implements fictional navigation and browser-local simulated workflows. `docs/password-reset.js` mounts the Firebase password-reset form and handles validation and generic feedback. `docs/owner-notes.js` manages owner-only note subscriptions, cleanup and unsent drafts. See the source for exact interfaces and side effects.
