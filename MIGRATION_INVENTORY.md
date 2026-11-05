# Firestore-to-PostgreSQL inventory

This is a code inventory plus a read-only production check, not a guarantee that unknown historical fields or subcollections have been found. Extraction must preserve source IDs and unknown fields until reconciliation is complete.

## Firestore paths

| Source | Current consumers and writers | Migration concern |
| --- | --- | --- |
| `notes/{id}` | Dashboard, codes, inbox, reports, templates listeners and merge writes | Stable note ID; drafts and optimistic concurrency |
| `task_messages/{id}` | Overview discussion listener/create/delete; status monitor | Source ID, task association, timestamps, internal-only content |
| `manual_tasks/{id}` | Overview listener/create/delete | Existing fields differ from a normalized task; preserve raw source |
| `vendors/{id}` | Vendor page listener/create/update/delete | Contact data private; source ID and conflict handling |
| `templates/{id}` | Templates UI and leak-response reader | Arbitrary named fields, not only a fixed version |
| `property_codes/{slug}` and `code_versions/{id}` | Codes UI, history maintenance, lockout and Sifely readers/writers | Encrypt recoverable values and historical values; immutable history; source IDs; code-specific optimistic conflict detection |
| `stats/latest`, `stats/monthly_history` | Scraper Admin writer; stats and KPI history readers | Nested JSON string, money precision, distinct Firebase stats identity |
| `private_configuration/codes_schema` | Owner codes UI, explicit Admin publisher | Private property identifiers and presentation schema; no access values |
| `private_dashboard/current`, `private_dashboard_runs/{run}/chunks/{id}` | Explicit private snapshot publisher and authenticated overview/inbox readers | Generation pointer switched only after complete bounded chunks; raw messages, occupancy and tasks private |
| `team_members`, `team_invitations`, `team_admin_audit`, `property_workspace/{property}/tasks` | Emulator-only team service and scoped rules | Trusted role provisioning, property scope, immutable/redacted audit; not live production team management |
| `leak_alert_pending`, `leak_alert_calls/{incident}/roles`, `leak_alert_result_queue`, `leak_alert_result_claims` | Signed provider callback, poller and result sender | Durable claims, source IDs, idempotency and reconciliation; never automatically replay ambiguous sends |

On 4 November 2026, a read-only privileged top-level inventory found `notes`, `private_configuration`, `property_codes`, `stats`, `task_messages`, `templates`, and `vendors`. Nine existing code documents had no subcollections. Other document subcollections and historical fields have not been exhaustively surveyed; source extraction must discover them rather than rely on this list. Missing top-level paths above may be created before migration.

The subsequent corrected encrypted read-only traversal recorded 35 documents: four notes, one private configuration document, nine property-code documents, two stats documents, nine task discussions, one templates document and nine vendors. No nested documents appeared in that export. It traversed subcollections visible to the signed-in account but was not atomic across the source; newly created, concurrently deleted or permission-hidden paths require further review at cutover.

## Authentication and production state

Firebase Authentication remains the identity provider. Existing production Firestore containment rules restrict operational collections and the private schema to the fixed owner, while statistics use a separate existing identity. The emulator team model has owner, manager, operator and viewer roles, but its trusted endpoint is deliberately disabled for production. No new PostgreSQL role assignment should be trusted from browser input. Recent changes published the owner-only codes schema; a private snapshot pointer was absent when checked. Owner browser authentication is not yet verified.

## Data flow and jobs

The frontend currently has Firestore SDK reads, listeners and writes in `docs/index.html`, `codes.html`, `private-messages.html`, `vendors.html`, `templates.html`, `stats.html` and `kpi-history.html`. `private-snapshot.js` joins a pointer and chunks. Replacing only UI rendering is insufficient: writes, notes, code history, scoped authorization, stats identity, listener cleanup and drafts must also move behind the API. An explicitly selected API mode now uses the API for overview shared notes, vendors and templates with version checks and draft-preserving polling; overview task and occupancy flows remain unconnected in that mode. Legacy mode remains Firestore-backed. The optional API adapter is not yet wired to all pages, and API mode must not be released while those direct reads remain.

`padsplit_scraper/scraper.py` gathers PadSplit messages, tasks, rooms, occupancy and partner KPIs; `persist.py`, `public_snapshot.py`, `private_snapshot.py`, task digests and GitHub workflows publish or write outputs. Public projections intentionally withhold individual rooms and tenant content on the development branch. Existing live Pages and shared Git history still contain known private data and require separate owner-led containment and remediation. No new artifact should include real source documents.

External integrations include PadSplit, Firebase, Total Connect Comfort, Midea SmartHome, Sifely, Discord, Quo, Bland callbacks, and optional frontload webhooks. `runtime.py` controls collection-only and outbound gates. Do not enable messages, calls, lock rotation, device control, or schedules during migration. Existing source-level process locking and claim/outbox semantics must survive any multi-host move.

## Linux boundary and VPS inspection

`launchd`, `launchctl`, Apple Messages automation and macOS Keychain/browser-profile flows are not portable to Linux as written. Google Voice automation depends on a signed-in Chrome profile and can encounter interactive challenges. `thermostat/schedule.py`, `thermostat/set_temps.py`, `smarthome/watcher.py`, `padsplit_scraper/field_mms.py`, `seo_monthly.py` and `google_voice_chrome.py` contain platform-specific behavior; merely placing them on Linux does not establish readiness.

The user supplied a read-only inventory from the target Ubuntu VPS on 4 November 2026. Docker runs n8n, its separate PostgreSQL container, Caddy, and AgentForge. Caddy already publishes ports 80 and 443; another service listens on loopback port 3000. The n8n PostgreSQL port is not host-published in the reported container list. The machine reports 3.8 GiB RAM, 2.7 GiB available, no swap, and 39 GiB free on its root filesystem. Preserve these containers, their data and Caddy configuration. Do not assume an API port, reverse-proxy integration, database volume, backup destination or resource headroom. Key-only SSH from this workspace was denied, and the user supplied the command output from an existing password-authenticated session; no VPS changes have been made.

## Continuation — 4 November 2026

A read-only PostgreSQL inbox API and local two-pane interface have been added with property-scoped authorization, but no process yet projects or ingests the private conversation snapshot into those tables. A partial owner-only PostgreSQL reports interface exists for local review; it is not a replacement for every existing report workflow. These interfaces remain excluded from release navigation. API-mode tasks and browser-local settings are available locally, and an API-only vendor source is selected for release. The old Firestore pages remain in the repository. The latest terminal snapshot confirms the earlier VPS inventory but the SSH session has since disconnected; the Caddy network and mounts remain uninspected.

## Latest staging continuation — 4 November 2026

Authenticated staging projections now exist for supported encrypted access-code histories, explicitly scoped private inbox snapshots, occupancy rows and provider-task buckets. Inbox catch-up stores encrypted candidate evidence and does not infer deletions; it is not a continuous collector. The captured real backup had no verified private snapshot, so source bindings, complete chat history and current room/task data have not been established. API-only release sources are prepared for codes, vendors, templates, integration observations, reports and KPI history. Overview and private inbox still have legacy release sources and continue to block the build. The reports capability table is empty until a trusted operator verifies and grants the existing account.

## Staging continuation — 5 November 2026

The saved corrected export was rechecked: it contains nine discussions with eight distinct task references, no manual-task documents, and no private snapshot pointer. A separate redacted reconciliation table and private owner-only label key were prepared outside the checkout; no relationships were inferred. The existing GraphQL source exposes cursor pagination and attachment metadata, but its queries filter message types and have not been exercised against a live account in this continuation, so history coverage and attachment access remain unknown. Local migration 019 stores safe attachment metadata without locations, and migration 020 stores explicitly reviewed same-generation room/task observation links. A manual staging collector and host-local worker lock are implemented but no production scheduler, ingestion service, VPS network or live grant was configured.
