# Operations improvement plan

Work is local on the improvement branch. Do not publish, deploy, send communications, operate devices, or change production data during verification.

1. Audit source, runtime boundaries, baseline and Firebase rules; create the application, database and function references. Verify documents against source and baseline tests.
2. Create a consistent accessible operations shell and responsive design. Check existing page behavior and viewport overflow.
3. Improve overview, occupancy and tasks without changing occupancy calculation or outbound behavior. Verify filters and stale/missing state.
4. Improve secondary pages incrementally while retaining authentication and edit flows. Check representative desktop, tablet and phone layouts.
5. Investigate baseline failures and implement evidence-backed security and reliability fixes with regression tests; inspect generated public outputs and checked-in/deployed rules separately.
6. Run relevant automated tests and visual checks, update documentation, report outstanding limitations.

## Initial findings

- `firestore.rules` includes code-version access rules absent from the deployed rules observed in the Firebase console on 2026-11-04. History is blocked in production. Review and deploy rules separately, never automatically. Acceptance: emulator tests demonstrate authorized history and denied unauthorized access before production deployment.
- `run_morning.sh` and `run_afternoon.sh` use a machine-specific path and push snapshots. This clone cannot safely assume these are portable. Acceptance: document configuration and keep verification local.
- `docs/data/` contains operational data served publicly; audit fields and publication boundaries before publishing more. Acceptance: tests enforce explicitly permitted fields and protect the existing consumers.
- Baseline suite previously reported two failures; reproduce and determine cause before changing behavior or fixtures.

## Implemented locally

- Shared navigation and responsive styling, labelled sign-in controls, masked code inputs/history, room search, explicit snapshot times and refresh protection for drafts. Acceptance verified for representative overview viewport and selected script tests; authenticated workflows still require end-to-end checks.
- Public snapshot projection strips free-form messages, task records and unrecognized fields. It preserves aggregate task counts and a limited occupancy schema. The dashboard explicitly says when details are unavailable; a private data source is needed to restore those views without public disclosure.
- Existing public HTML fallback code values were removed locally. Published site and Git history are unchanged; affected credentials and access codes must be treated as exposed and rotated by their owner. Publicly tracked financial snapshots and historical data remain outstanding.
- Local shell paths now resolve relative to the repository; selected publication paths are staged and committed explicitly. No schedules were executed.
- New-booking test opts into the path it tests, independent of local `.env`; the separate occupancy test still assumes specific rows in a rolling live snapshot. Its failure is not repaired by changing the data.

## Remaining work and acceptance criteria

- P0: Review and address tracked financial data, other public operational fields, existing site and repository history; rotate exposed access codes and credentials. Acceptance: owner-approved production remediation and independent exposure review.
- P0: Review Firestore public reads and prepare least-privilege migrations for existing dashboard clients. Acceptance: emulator tests for allowed/denied reads and writes, then deliberate deployment. Six local emulator checks now pass against the proposed repository rules; deployed rules remain unchanged and the broader least-privilege migration remains open.
- P1: Replace public task/message views with an authenticated data source. Acceptance: authorized access, denied anonymous access, editable drafts preserved, no sensitive public snapshots.
- P1: Replace rolling-snapshot-dependent occupancy test with stable, representative regression coverage without altering the existing snapshot to force a pass. Verify unknown versus zero and stale/partial cases.
- P1: Complete per-page responsive and accessibility review, search/filter and status/error improvements, and authenticated integration tests. Verify tenant messaging, restore confirmation, task discussions and device views with test accounts and emulators.
- P2: Continue backend audit of retries, pagination, session expiry, atomic writes, listener lifecycle, and failure paths. Acceptance: targeted failure-mode tests and no live side effects.

## Continuation: occupancy and editing reliability

- `docs/index.html` now presents attention counts and distinguishes unknown presence and missing vacancy age from false/zero. Task discussion and task labels escape untrusted text before rendering. These changes do not infer vacancy from a past move-out.
- Shared secondary-page draft guards retain unsaved text when Firestore snapshots arrive, including edits made during a pending save. Vendor create/delete controls prevent repeated submissions while pending. Authenticated browser flows and listener reconnection after sign-out remain to verify.
- `padsplit_scraper/persist.py` now writes JSON using atomic replacement and applies the public occupancy projection on direct scraper writes. `padsplit_scraper/scraper.py` reports occupancy derivation or persistence failure as degraded instead of an all-clear while retaining separately successful stats.
- The existing occupancy test still asserts historical entries in a rolling snapshot and remains failing. The test and snapshot were not altered to make it pass.

- Firestore history creation now requires the expected keys and types in local rules. Six emulator tests pass, including the UI-style server timestamp write and malformed-document rejection; a production security review and deliberate deployment are still needed.

## Current local verification and remaining scope

- The formerly failing occupancy test is deterministic without altering the rolling snapshot; its historical assertions and separate published-data invariants pass. The complete Python suite ran 562 tests successfully.
- The proposed private snapshot writer, readers and owner-only local rules are implemented. Writer gates, fake-client tests and seven emulator rules tests pass. The publisher is standalone and disabled by default; no real data was uploaded. One publishing host and a reviewed retention/cleanup design are required before operational use.
- Secondary page listeners now detach and reattach across auth changes, and the main overview reports loading and permission errors. Emulator rules and mock/unit tests do not prove an actual authorized browser session, session expiration behavior, or production IAM.
- The dashboard now has mapped navigation, a searchable room table, source-health reporting and attention counts. The requested contextual property detail view, full task filtering/detail experience, two-pane inbox and comprehensive all-page visual/accessibility review remain unfinished. Replies are not implemented and no fake send is offered.
- Production exposure remediation remains open: public repository history, tracked financial snapshots and live deployed files are unchanged. Existing public-read Firestore collections and operational occupancy information require owner-reviewed access and data migration; do not broaden rules. Rules and app changes have not been deployed.

- Searchable tasks filter already-rendered rows without rebuilding drafts. Overview navigation links to existing property, task, climate, report and health sections; the health display reports only available collection state. Remaining work includes a contextual property detail view, supported task sorting and assignment changes, a two-pane inbox, and comprehensive authenticated browser and accessibility checks.

## Latest dashboard continuation

The local room detail dialog, task search/status filtering and group sorting, and two-pane read-only inbox are implemented. They preserve discussion/draft input and display missing links instead of inferring property matches. The Firebase Auth and Firestore emulator suite now includes fictional SDK sign-in/sign-out, denied non-owner access, simulated owner access, task discussion/manual-task mutations, snapshot chunk reads and listener reattachment. Remaining: live owner-browser session restoration/expiry and production IAM verification; no deployed data or rules were changed. PadSplit assignment/status mutation and tenant reply delivery remain unavailable because no authorized server-side update or delivery path has been established. Review existing public exposures and plan private snapshot retention before production rollout.

## Demo and reset update — 2026-11-04

The fictional, loopback-only demo and shared Firebase password-reset form are implemented locally. Overview has been checked at four requested widths and the mobile drawer was opened. The demo is a separate prototype; production still lacks some requested dedicated views and fully verified workflows, and the requested complete screenshot set and full responsive/accessibility review remain outstanding. Owner sign-in, production password-reset delivery, production rules, Firebase IAM and snapshot retention require verification before release. Existing public site and repository history still expose operational data and access values; an administrator must contain the exposure and coordinate rotation and repository-history remediation. A branch push alone does not remediate it.

## Branch cleanup continuation — 2026-11-04

Raw tracked statistics, financial history and thermostat readings were removed from the branch tree; public occupancy now contains no room rows, and the owner dashboard can read optional occupancy from the existing gated private snapshot. The thermostat publication workflow and local scripts no longer stage raw readings. Contact numbers now require private configuration. Existing public Git history, Pages deployment, credentials and access codes remain an owner-led containment and rotation task; a branch push does not remediate those prior exposures. Actual owner login, live private snapshot publication, reset-email delivery and production IAM remain unverified. Public property metadata remains in existing operational configuration.
