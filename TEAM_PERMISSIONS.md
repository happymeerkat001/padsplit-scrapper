# Team permissions: local emulator prototype

This is a **local emulator prototype**, not an enabled production feature. The
review-only Firestore rules use separate trusted owner and statistics claims
instead of hardcoded account identifiers. Existing codes, notes, tasks, messages, vendors, templates and the
all-property private snapshot remain owner-only; no member gains access to
them by receiving a new role.

The prototype operates only on the distinct `property_workspace/{id}/tasks`
collection. Membership, invitations and audit events are in `team_members`,
`team_invitations` and `team_admin_audit`. All client writes to these paths are
denied by the proposed rules, including writes by the owner. Reads of a scoped
property's tasks require the owner or an active member assigned to that exact
property. A member can read only their own membership and invitation; only the
owner can list the roster or read administrative audit entries.

| Capability | Configured owner | Manager | Operator | Viewer |
| --- | --- | --- | --- | --- |
| Invite, update or deactivate a member | Yes | No | No | No |
| Accept an invitation for oneself | Not applicable | Yes | Yes | Yes |
| Read assigned scoped-property tasks | All properties | Assigned only | Assigned only | Assigned only |
| Create or edit scoped tasks via trusted service | All properties | Assigned only | Assigned only | No |
| Delete scoped tasks via trusted service | All properties | Assigned only | No | No |
| Read administrative audit | Yes | No | No | No |

The owner is immutable and cannot be invited, demoted or deactivated through
the prototype. There is no second owner role; this preserves the existing
authorization model and guarantees that the last authorized owner cannot be
removed by these actions. Only that owner can provision roles. Invitation
targets must already exist in the Auth emulator; no email invitation is sent.
Accepted roles and property lists come from server-controlled invitation
documents, not a submitted role claim. The service verifies the caller's
Firebase ID token with revocation checking and authorizes the verified UID
against trusted state. Task edits use an expected version and return a conflict
on mismatch. Task and administration changes create a redacted success audit
event in the same Firestore transaction; task text and tokens are not included.
Failed attempts are not persisted as audit events by this prototype.

`functions/team_admin.py` refuses to run unless both Auth and Firestore hosts
are explicit loopback emulators and the project is `demo-padsplit`; it also
requires an explicit `PADSPLIT_LEGACY_OWNER_UID` server environment value.
Missing or invalid owner configuration fails closed. The optional
`functions/team_admin_http.py` adapter binds to IPv4 loopback and refuses requests
with forwarded headers, an unexpected Host or Origin, oversized/non-JSON bodies,
or missing/invalid Firebase ID tokens. It never logs request headers or bodies.
It also serves a small emulator-only interface at `/team`; that interface is
not wired to the production dashboard. The Auth emulator
accepts emulator-issued tokens and is not a production authorization test. A
production implementation needs a reviewed trusted endpoint, deployed rules,
property-scoped data migration, retention/administrative audit policy and real
identity provisioning. In particular, the legacy private snapshot must not be
reused to provide a restricted user with a partial view: it contains all
properties. No changes in this prototype have been deployed.

Run the local test suite with `cd tests/firestore && npm test` (Java 17+ and the
repository Python virtual environment are required). The suite exercises the
real Admin SDK against both emulators, verifies owner versus non-owner changes,
invitation acceptance, role and property checks, version conflicts, deactivation,
attempts to forge membership/audit or access another property's task, and the
HTTP adapter's origin, host, forwarded-header and token rejection paths.

To run the optional adapter for a local integration, first start the Auth and
Firestore emulators using `firebase.emulator-test.json`, then in another shell:

```sh
GCLOUD_PROJECT=demo-padsplit \
FIRESTORE_EMULATOR_HOST=127.0.0.1:8185 \
FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9198 \
PADSPLIT_LEGACY_OWNER_UID=fictional-owner-uid \
venv/bin/python -m functions.team_admin_http --emulator --port 8788 --origin http://127.0.0.1:8765
```

Open `http://127.0.0.1:8788/team`. The page signs in directly against the local
Auth emulator; its token exists only in page memory and is cleared on sign-out.
Sign-out does not revoke an already-issued emulator token. The service never
stores a password or logs request bodies or headers. The interface supports
invitations, acceptance, role/scope changes, deactivation and scoped task
create/edit/delete; it does not yet display a roster or audit history.

For manual use, create fictional Auth emulator identities before inviting
them. The following interactive helper requires both emulator hosts, the demo
project, and an `example.invalid` email. It prompts for a new emulator-only
password without echoing it and refuses to replace an existing account:

```sh
GCLOUD_PROJECT=demo-padsplit FIRESTORE_EMULATOR_HOST=127.0.0.1:8185 \
FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9198 \
venv/bin/python -m functions.team_seed_emulator --emulator --uid fictional-owner-uid
```

Use that fictional UID as the configured emulator owner. Creating the account
does not itself grant a Firestore claim. Repeat
with a different fictional UID to create an invitee, sign in as the emulator
owner and invite that UID with the desired role and property IDs, then sign in
as the invitee and select **Accept my invitation**. Do not use actual account
credentials. The helper cannot create or change production accounts.

## Review-only Firestore claim bootstrap

The checked-in rules are a **local review candidate and must not be deployed to
production in their present state**. A trusted Firebase Admin operator must
first assign boolean custom claims `padsplit_legacy_owner` and
`padsplit_legacy_stats` to the separately verified owner and reporting accounts,
respectively. Never accept these roles from browser input, document contents
controlled by clients, unverified email addresses, or user-supplied claims.
Absent, false, or non-boolean claims are denied. Owner and report access stay
separate unless the trusted operator explicitly grants both.

For isolated staging, provision fictional accounts and claims using a trusted
Admin SDK session for the separate staging Firebase project. Inspect and
preserve any existing claims when updating an account, because the Admin SDK
claim setter replaces the complete custom-claims map. Refresh each ID token,
then test owner/report allowed paths, missing/false/string claims and
unauthorized accounts. The Node rules tests use emulator-injected claims;
the Auth emulator account seeding helper does not grant claims automatically.
Record account-to-role review and rollback procedures privately. Review the
actual staging rules and verify these cases before contemplating any rules
deployment; production traffic, current live rules and Firebase accounts are
unchanged by this repository change. PostgreSQL API authorization continues to
use verified Firebase identity and server-side role/property mappings.

The only operation endpoint is `POST http://127.0.0.1:8788/team/action` with
`Content-Type: application/json`, either the explicitly configured `Origin` or
the service's own loopback origin, and an emulator
Firebase ID token in `Authorization: Bearer ...`. Request bodies contain only
the action's specific fields; unknown fields, including a client-supplied actor
or admin flag, are rejected. The action shapes are:

- `invite`: `uid`, `role`, `properties` (owner only).
- `accept_invitation`: `uid` matching the signed-in identity.
- `update_access`: `uid`, `role`, `properties` (owner only).
- `deactivate`: `uid` (owner only; cannot target the fixed owner).
- `create_task`: `property`, `task`, `title`, `status`.
- `update_task`: the create fields plus `expected_version`.
- `delete_task`: `property`, `task`, `expected_version`.

Each body also includes its `action` string. A task status is `open`,
`in_progress` or `done`. Responses contain only a success flag or a generic
error; no token or task body is echoed. This prototype intentionally does not
send invitations, expose live data, accept production tokens, or provide a
production administration endpoint.
