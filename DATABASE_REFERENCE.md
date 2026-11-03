# Database reference

This describes Firestore paths found in the source, not an exhaustive survey of the live database. Field definitions are inferred from writes and reads, not enforced schemas: Firestore documents can contain additional or historical fields. Examples below are placeholders. No tenant records, phone numbers or access codes are reproduced.

## Connection and access boundaries

- Project `padsplit-scrapper`, default Firestore database; `firebase.json` points to `firestore.rules` and the `leak-alert` Python 3.12 Cloud Functions codebase. Web pages use Firebase web SDK 10.12.0 and Firebase Authentication; Python uses the Admin SDK. Server code in `padsplit_scraper/persist.py` accepts `FIREBASE_SERVICE_ACCOUNT_JSON` or `GOOGLE_APPLICATION_CREDENTIALS`; individual scripts may require a narrower configuration.
- In this session's prior console inspection on 2026-11-04, the default database was visible in `nam5`; a read-only request using the dashboard's project configuration returned `notes/dashboard` successfully. This is not a test of privileged dashboard login, authenticated writes, or Admin SDK credentials. Deployed state may have changed since the inspection.
- UI sign-in and Google Cloud console sign-in are distinct. The Admin SDK bypasses client rules and is controlled by IAM; a successful public read is not evidence of authenticated access.
- Local proposed rules restrict reads and writes of `notes`, `task_messages`, `manual_tasks`, `vendors`, and `templates` to the existing operator UID, with a separate UID for stats. The deployed rules observed on 2026-11-04 allowed public reads of those collections; the local change is not deployed. Default access is denied. See `firestore.rules` for the identities.
- The deployed rules observed in the console on 2026-11-04 did **not** include the repository's explicit `property_codes/{slug}/code_versions/{id}` rules. Under the observed default-deny rule, browser history access was denied. The checked-in explicit leak-alert denials were also absent, but the observed default-deny covered those paths. Treat deployed state as a dated observation and recheck before relying on it; no rule change was deployed.

## Paths, fields and consumers

| Path | Fields and type as used by code | Readers / writers and checked-in client rules |
|---|---|---|
| `notes/{document}` (`dashboard`, `codes`, `messages`, `stats`, `templates`) | `text`: string; merge writes | Corresponding `docs/*.html` page listens and saves. Local proposal: owner-only client read and write. |
| `task_messages/{id}` | `taskId`, `role`, `status`, `text`: strings; `created`: server timestamp | `docs/index.html` orders by `created`, listens, adds/deletes; `padsplit_scraper/firestore_status_monitor.py` queries status `In Progress`. Local proposal: owner-only read and write. `taskId` associates a discussion with a scraped or manual task; no Firestore foreign-key enforcement. |
| `manual_tasks/{id}` | `who`, `source`, `notes`: strings; `created`: server timestamp | `docs/index.html` orders by `created`, listens, adds/deletes. Local proposal: owner-only read and write. |
| `vendors/{id}` | `name`, `specialty`, `contact`, `location`: strings | `docs/vendors.html` orders by name, creates, merges edits, deletes. Local proposal: owner-only read and write. Contact may contain personal details. |
| `templates/shared` | `n0`, `n1`, … labels and `t0`, `t1`, … message bodies: strings | `docs/templates.html` reads/listens and merges edits; `padsplit_scraper/leak_reply.py` uses the water-leak template with fallback. Local proposal: owner-only read and write. |
| `property_codes/{slug}` | Dynamic named strings for contact, house and room/lockbox/maintenance fields; `updatedAt` can be Firestore timestamp from browser or ISO string from Sifely sync. Fields may contain access codes. | `docs/codes.html` reads/merges; `padsplit_scraper/lockout_reply.py` and `discord_notifier.py` read; `lock_codes.py` merges an update via Admin. Owner UID client read/write. |
| `property_codes/{slug}/code_versions/{id}` | `fields`: map of canonicalized code strings; `contentHash`: SHA-256 string; `createdAt`, `expireAt`: timestamps; `source`: string (`page` or `catchup`) | `docs/codes.html` reads ordered by `createdAt`, writes snapshots and restores; `padsplit_scraper/codes_history.py` catches up and prunes via Admin. Checked-in rules: owner get, list only with query limit ≤50, create only with specified keys, no client update/delete. Local proposed rules require all five keys and validate their top-level types; they do not validate every map value or content hash. The local rules have not been deployed. |
| `stats/latest`, `stats/monthly_history` | `json`: serialized JSON string; `updated_at`: ISO timestamp string | `padsplit_scraper/persist.py` overwrites through Admin; `docs/stats.html` and `docs/kpi-history.html` parse their respective documents using separate stats auth. Only designated stats UID client read/write. |
| `leak_alert_pending/{callId}` | `call_id`, `incident_id`, `role`, `placed_at`: strings; confirm handler may add confirmation fields and `confirmed_at` | Mac sender and `functions/leak_alert_bland.py` Admin adapter read/write. Checked-in rules deny clients. |
| `leak_alert_calls/{incident}/roles/{role}` | `outcome`, `summary`, `call_id`, `role`, `incident_id`, `source`, `recorded_at`: strings; `confirmed`: boolean or null; `eta_minutes`: number or null | Webhook/polling Admin code records outcomes, consumers inspect them; client access denied. Incident ID joins pending, role and queue records. |
| `leak_alert_result_queue/{callId}` | `call_id`, `incident_id`, `role`, `outcome`, `line`, `enqueued_at`: strings; `posted`: boolean; `posted_at`: optional string | Webhook/poller creates once, Mac result processor lists/marks posted; client access denied. |

## History, indexes and retention

The codes page snapshots canonical live fields and hash. Restore first snapshots the current document, checks that live fields did not change since it started, then replaces live fields with the selected version and an update timestamp. The hash check is not a Firestore transaction, so it should not be described as an atomic compare-and-swap. Snapshots are immutable to browser clients under checked-in rules; Admin SDK is outside those rules.

`expireAt` is set to 90 days after snapshot creation. `codes_history.catch_up()` separately removes older snapshots while retaining the latest; it skips CI and needs an Admin client. TTL configuration in the live Firestore database has not been verified: merely writing `expireAt` does not establish that automatic deletion is configured.

Queries observed: task and manual messages ordered by `created`; vendors ordered by `name`; history ordered by `createdAt` with a maximum page of 50 and cursor pagination; pending calls filtered by `incident_id`. No index configuration file is referenced by `firebase.json`; deployed index state and whether any composite index is required were not independently verified.

## Exposure and safeguards

`docs/data/*.json` and `docs/thermostat/latest.json` are dashboard inputs published by the repository's GitHub Pages workflow. They are separate from Firestore authorization; anyone able to access the published site can request published files. The deployed rules observed on 2026-11-04 allowed unauthenticated direct reads, even when a page displayed a sign-in gate. Local owner-only rules are not deployed. See `IMPROVEMENT_PLAN.md` and output-generation code for the separate exposure audit and remediation status. Do not put real values in documentation or tests, and do not widen rules to make an integration work.

## Local emulator verification

On 2026-11-04, six tests passed against the local demo Firestore emulator, covering owner and non-owner history access, the 50-document list limit, immutable versions, restoration, public notes, separate stats access, denied internal paths, and rejection of missing or wrongly typed history fields. The dashboard-style server timestamp create also passed. These results apply to the checked-in rules only, not the deployed rules. See `tests/firestore/README.md`.

## Proposed private dashboard storage

`private_dashboard/current` contains `schema_version` (integer 1), `run_id` (32 lowercase hex characters), `chunk_count` (integer 1–100), and timezone-aware ISO `scraped_at` (string). The versioned `private_dashboard_runs/{run_id}/chunks/{index}` documents contain `index` (integer) and bounded `data` (ASCII JSON fragment, string). Together the chunks encode a snapshot with `scraped_at`, `messages` (array) and `tasks` (map of arrays). The owner UID may get exact documents; client list and writes are denied in the proposed rules. Only the Admin publisher writes; IAM controls it. A pointer is replaced only after all chunks are written. An interrupted run can leave old or incomplete generations, and no TTL or cleanup is implemented. No production changes were performed. See `PRIVATE_SNAPSHOT_DESIGN.md` for configuration and limitations.

## Authenticated workflow verification

The isolated demo suite exercises the real private snapshot reader against Firestore rules, owner and denied identities, missing chunks, discussion and manual-task mutations, listener teardown, and Firebase Authentication emulator sign-up/sign-in/sign-out for a fictional non-owner. Owner identity in rules tests is simulated. The suite does not prove real owner-browser session restoration, token expiry or production IAM. No production data was written.

## Local owner-only migration compatibility

The local overview now subscribes to operational notes, discussions and manual tasks only while the designated operator UID is signed in, and clears private DOM data on sign-out or account switch. Same-tab unsent drafts are retained in memory for that UID. Codes notes now subscribes only in the operator session, and Stats notes uses the default operator Auth session separately from the named stats-auth session. Same-tab unsent notes and code edits are retained in memory for the same operator when authentication changes. These compatibility changes have focused mock coverage but require real owner-browser verification before deployment. Templates, vendors and inbox also use auth-managed listeners. These local changes do not change the public deployed access.

## Local demo isolation — 2026-11-04

The separate `demo/` server provides fictional fixtures and browser-local persistence, with no Firestore or production adapters. Resetting demo data resets only that browser's demo state. The current 12-test Auth/Firestore emulator suite validates local proposed rules, including denied non-owner access; it does not verify the rules currently deployed to the live project.

## Private occupancy continuation — 2026-11-04

The local private snapshot format accepts optional `occupancy` containing a timestamp and room-object array. Its publisher must be explicitly given a private occupancy source. The owner dashboard reads this through the existing owner-only private snapshot and clears room state on sign-out. Public occupancy contains no room details. Neither this branch's rules nor a private snapshot have been deployed; existing production access and exposed Git history are unchanged.
