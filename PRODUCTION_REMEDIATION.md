# Production exposure and private rollout — review draft

Status checked 2026-11-04 (Asia/Manila). No production configuration, records, credentials, or deployments were changed during this review. Do not include real values, screenshots containing codes, or credentials in issues, tickets, or chat.

## Confirmed exposure

- `happymeerkat001/padsplit-scrapper` is public; GitHub Pages is enabled and publishes `main:/docs`. The current GitHub account has push but not admin or maintain permission. An administrator is needed to change repository visibility or disable Pages.
- Unauthenticated requests to the live `docs/data/latest.json` Pages output returned message and task records, including free text and code-related fields. The live `docs/codes.html` contains embedded non-empty access-related defaults. The live `docs/data/occupancy.json` exposes room-level property, presence and timing information. The local branch removes code defaults and projects selected fields, but it has not been deployed and does not erase public history.
- Unauthenticated GitHub raw-file checks also confirmed financial snapshots in `padsplit_scraper/output/stats.json` and `padsplit_scraper/output/monthly_history.json`, messages and tasks in `padsplit_scraper/docs/data/latest.json`, and climate readings in `thermostat/output/latest.json`. GitHub raw-file access and Pages access are separate exposure paths.
- The deployed Firestore rules visible in the Firebase console permit unauthenticated reads of `notes`, `task_messages`, `manual_tasks`, `vendors`, and `templates`. The local branch's owner-only proposal must be reviewed and tested with the application before any deployment; otherwise existing clients could lose access.
- Public Git history and any clones, forks or caches can retain information even after a current file is replaced. GitHub states that Pages is publicly accessible even when a repository is private, unless a separately supported access-control feature applies. [GitHub Pages publishing documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site). [GitHub sensitive-data removal guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

## Containment requiring the repository administrator

1. Coordinate an immediate maintenance window. Disable the public Pages site and restrict repository access using an authorized administrator account. These are service-affecting actions; do not assume making the repository private alone protects an existing Pages site. Independently verify that unauthenticated access is no longer available, including raw files and relevant GitHub endpoints.
2. Treat previously exposed door, lockbox, Wi-Fi and account credentials as compromised. The owner must arrange controlled rotation with the property team so authorized occupants and emergency responders are not locked out. Do not perform rotations automatically from this repository. Account for backups and other exposed identifiers separately.
3. Preserve a restricted, access-controlled backup and an incident inventory. Review current tracked snapshots and full Git history before deciding on a cleanup strategy. Coordinate clones, forks, pull requests, and any GitHub Support cache-removal request. Rewriting history and force-pushing can disrupt collaborators and will not remove external copies; prepare and review a concrete plan before that action.

## Review before re-opening the dashboard

- Verify the actual Firebase Authentication owner account maps to the intended existing owner UID. Google Cloud console login alone does not prove dashboard authorization. Keep the separate statistics identity until reviewed.
- Run local Firestore and Authentication emulator tests against the proposed rules and the updated application. Review all affected clients before tightening production rules. Deploy deliberately only after access and rollback procedures are reviewed; verify authorized and unauthorized reads independently.
- Review the entire publishable tree and Git history, not just the latest snapshot. Public occupancy still contains address, room and inferred presence data; decide whether this information may be public. Existing tracked financial files also need a separate disposition. Keep tenant content, codes, financial data and credentials out of Pages and a public repo.
- The private-snapshot publisher is standalone and disabled by default. It needs a reviewed service identity with appropriately scoped IAM, a fresh private source, the proposed rules, a single publishing host, and a retention/garbage-collection design. Its lock is local to one clone on one host. See `PRIVATE_SNAPSHOT_DESIGN.md`. Do not publish a private snapshot as a test against production.
- Verify the real owner browser session, account switch/sign-out, permission failure and the private data path with a controlled non-production project. Confirm device and messaging integrations separately; no live sends, calls, device changes or schedules are needed for this review.
- Re-enable public hosting only when all remaining public content is approved for public access, the history/exposure response is complete, access rules and private data flows are verified, and the owner approves the rollout.

## Current blockers

The active GitHub account lacks administrative permissions. The dashboard owner sign-in has not yet been verified. No PadSplit credentials, Firebase Admin credential configuration, or fresh private source snapshot were found in this clone's local configuration. Authentication to the Firebase console is separate from the dashboard's Firebase Authentication and the Admin SDK's identity. None of these observations requires disclosing secrets in chat.
