# Operations and administration feature inventory

Status describes this local branch, not deployed services. “Implemented; locally verified” means the named code is covered by local tests or a demo check. Production owner access, deployed rules, provider integrations and real data remain separate verification tasks.

| Page or action | Current status | Evidence and boundary |
| --- | --- | --- |
| Overview | Implemented; local verification in progress | Owner-only private occupancy drives a property summary, scoped drill-down, room attention and upcoming activity; public source-health snapshot is separate. |
| Properties and rooms | Implemented; local verification in progress | Search, occupancy categories and detail dialog; related data joined only when identifiable. |
| Tasks | Implemented, production owner verification pending | Search, filters and internal discussions/tasks; PadSplit assignment/status mutation is not implemented. |
| Tenant inbox | Implemented, production owner verification pending | Private snapshot, list/reading pane and local drafts; no external-send integration. |
| Climate | Implemented with source unavailable in public preview | Public thermostat data is withheld; device operations are not presented as confirmed. |
| Reports | Implemented, account verification pending | Firestore stats uses its distinct established identity; no fabricated figures or export. |
| Vendors | Implemented, owner verification pending | Search, validated create, edit and delete with private Firestore; local tests exercise validation and drafts. |
| Templates | Implemented, owner verification pending | Search and editing of fixed templates; local tests cover filtering and draft guard. |
| Team and permissions | Emulator prototype implemented; production integration pending | Verified emulator identities, trusted owner-only provisioning, assigned-property task boundary and immutable admin audit; existing owner/stats identities and all-property snapshot remain unchanged. |
| Access codes | Private configuration migration required before release | Masked by default; restore uses a transaction. Property schema is no longer embedded in public assets; it must be provisioned privately and the rules coordinated before the page can load it. See `PRIVATE_SCHEMA_MIGRATION.md`. |
| Integrations | Read-only status view implemented | Shows last reported collection observations while labeling configuration and connectivity as unknown/unverified; no provider secrets or live checks. |
| Activity and audit | Emulator prototype for trusted administration only | Authoritative success events are written server-side in emulator transactions; production UI and migration remain pending. |
| System health | Implemented, live status verification pending | Collection state and observation timestamps only; connection claims are not invented. |
| Settings | Implemented locally for display density, timezone and date format | Preferences affect browser presentation; business scheduling and occupancy calculations retain their configured timezone. |
| Authentication and password reset | Implemented; live owner/browser verification pending | Firebase Auth, guarded listeners, generic mock-tested recovery; no reset email sent. |
| Demo | Implemented with isolated fictional adapter; further sharing in progress | Explicit localhost server, strict allowed assets, browser-local data; no production Firebase. |

See `API_AND_WORKFLOW_AUDIT.md` for action → authorization → persistence → feedback and `PUBLICATION_CHECKS.md` for the privacy and history boundary.
