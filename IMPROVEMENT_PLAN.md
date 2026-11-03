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

## Completion checklist — current continuation

Implementation and verification are tracked separately. A checkmark means evidence exists for the stated scope, not that production has been deployed.

| Page or workflow | Implementation | Verification still required |
| --- | --- | --- |
| Overview and source health | Summary and dedicated views exist | Cross-viewport and authorized owner session |
| Properties and rooms | Search and property detail exist; occupancy uses private snapshots | Real owner session, private snapshot freshness and identifier matching |
| Tasks | Search, filters, internal tasks and discussion exist | Authenticated create/edit failure and concurrency behavior; provider assignment is not integrated |
| Tenant inbox | Two-pane private reader and drafts exist; production delivery unavailable | Mobile layout, account-switch cleanup and owner session |
| Climate | Observational display exists | Fresh private source, actual device status and authorized commands |
| Reports | Existing authenticated statistics and history retained | Formula regression and authorized browser session |
| Vendors | Firestore list and edits exist | Search and owner CRUD verification |
| Templates | Firestore edits and draft guards exist | Search and owner CRUD verification |
| Access codes | Authenticated editing, masking and history exist | Owner reveal, copy, restore and deployed rules |
| System health | Source observations and errors exist | Stale, partial and failure states against real collection |
| Settings and account | Sign-in, sign-out and reset exist; no general provider settings | Session expiry, live reset and supported persistent settings |
| Demo | Isolated fictional adapter and local workflows exist separately | Shared presentation with production; every view, viewport and interaction |
| Privacy and publication | Local public projections omit private records | Tree and reachable history audit; historical remediation decision |
| Backend and integrations | Existing gated collectors and actions preserved | Workflow audit, relevant failure modes and controlled integration tests |
| Documentation | Guides and design reference exist | Refresh references, add action/API audit and verify claims |

Baseline at continuation: 569 Python tests and the JavaScript checks pass. This is a starting point, not verification of all items above.

## Continuation results and open items

- Implemented shared visual tokens, outline icon component and task-status matching across the demo and production shell; the two applications still have separate page renderers and data adapters. Production settings persist a browser-local density preference.
- Implemented search for mounted vendor/template cards, vendor-name validation and a guarded vendor create form. Account transitions now clear vendor, template and inbox-note DOM state. These paths have local unit coverage, not an authenticated owner-browser run.
- Implemented transaction-based code restore, discussion validation/error feedback, safer provider retry policy, webhook queue recovery and public-output checks. See `API_AND_WORKFLOW_AUDIT.md` and `PUBLICATION_CHECKS.md` for test scope and unresolved risks.
- Verified all 11 demo routes render and avoid page-level overflow at 1440, 1024, 768 and 390 CSS pixels. This is not a complete visual, keyboard or interaction audit of every page/state. The overview live accessibility scan reported no violations after fixing warning-badge contrast. Screenshot artifacts have not been saved.
- Existing public branch ancestry contains exposed tenant messages and access values. A clean root on a separate local branch is a candidate for owner review; it does not remove the currently public refs. Do not rewrite shared history or force-push without explicit authorization. Existing deployed site and rules remain unchanged.

## Clean-publication continuation

The user authorized a separate clean-history branch. The publication candidate must use a new root commit containing the reviewed current tree, with no parents from the existing repository history. Before pushing it, inspect private-data findings, run the publication guard with a private local blocklist, verify the candidate index and root commit, and run the relevant regression and emulator suites. Push the new branch only; do not alter the existing shared refs, site or deployed rules. Existing public history still requires separate owner-led remediation.

The property-status summary is now shared by the demo and authorized production occupancy view. It groups only valid property IDs, distinguishes unknown readiness and clears on sign-out. A durable claim now prevents automatic repeats of uncertain result notifications; legacy and ambiguous queue entries require manual review before rollout. See the API audit for the required operational precautions. Overall page rendering remains separate and real-owner workflows are not verified.

## Enterprise operations continuation

The local implementation uses a shared grouped navigation and visual tokens, a property overview with observed occupancy categories, private owner-only room details, an unsent production inbox, display preferences, and an integration collection-status view. The team role model is being exercised separately in Auth/Firestore emulators because the legacy private snapshot is all-property; it is not wired to production or to the existing snapshot. `FEATURE_STATUS.md` records per-page implementation and verification boundaries. No Vercel deployment or production rules change is authorized by this continuation.
