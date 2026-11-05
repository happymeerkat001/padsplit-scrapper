# PostgreSQL team administration — staging preparation

`backend/api/team.py` is a local trusted service and router factory, wired into the main app behind an explicit flag and not deployed. Migration 009 creates append-only redacted administrative audit and deferred last-active-owner safeguards; migration 011 adds an optimistic membership version. These migrations are additive and must pass the complete staging migration and cutover checks before use.

## Boundary and integration

The main API mounts the router only when `PADSPLIT_TEAM_ADMIN_ENABLE=1` and requires the separate secret `TEAM_DATABASE_URL`. The existing API supplies its rate-limited verified actor and server-side Firebase target lookup. The request-size bound, redacted handlers, `Cache-Control: no-store`, and exact-origin CORS apply. Never accept an actor or administrator flag from the request. Failed lookup, a different UID, or enabling a disabled identity rejects the mutation. Configure bounded upstream timeouts and never log identity tokens or lookup responses. Real Firebase IAM and production service configuration are not verified.

The restricted application database repository and trusted team database repository are mandatory for writes. They must connect using different non-elevated PostgreSQL **session users** and current roles to the same configured host, port, server instance, database, and resolved authorization tables. Equivalent hostname aliases deliberately fail closed. The ordinary application principal and its session user may not modify identity, role or scope fields, may not delete, truncate or add triggers on authorization tables, and may not be members of or assume either team principal. The service checks these properties, but a DBA must additionally review all inherited roles, `SECURITY DEFINER` functions, ownership and database grants: the checks cannot prove the absence of every privilege escalation path. Do not reuse a superuser or bootstrap database role for either connection. Storing the trusted connection credential in the same application process does not isolate it from a full process compromise; use a separately protected service/process if that threat boundary is required.

Only an already active owner can list or read member data or change roles and scopes. Nonowners receive a generic denial. The roster is paginated and returns UID, role, active state, property IDs and version; it does not return emails. `PUT /v1/team/members/{uid}` uses strict JSON `{role, active, properties, expected_version}` and rejects unknown fields, including a client-supplied actor. Use expected version 0 only for a new role; read the current version before editing an existing member. A stale version receives 409. Scope replacement and redacted audit insertion happen in the same transaction. Last-owner safeguards are serialized across administrators; local concurrent service and direct-SQL deactivation/demotion tests passed. The server role must not permit arbitrary membership updates by any untrusted service.

The separate audit table records action, server-derived actor UID, target UID, final role, active flag, count of properties, and timestamp; it excludes property IDs, access codes, credentials and message bodies. Database triggers reject UPDATE, DELETE and TRUNCATE, and normal roles must not own the table or have trigger-management privileges. A database owner or superuser can bypass such controls, so authoritative retention/export and administrator audit policy are still required. No endpoint currently provides audit viewing.

Initial owner bootstrap is an explicitly reviewed operator-controlled database operation after Firebase identity verification; it is not a public endpoint. No invitation email is sent. Production user provisioning, domain ownership, rate limits, IAM, SMTP invitations, deactivation and token-revocation policies, database backup, and a cutover/rollback plan remain external work.

## Tested SQL grant shape

The local tests used two distinct non-superuser LOGIN roles. The restricted API role received only the authorization-column grants listed in `MIGRATION_RUNBOOK.md`, including `SELECT user_roles.team_access_version`, plus the domain-specific grants needed by its endpoints. The separate team-admin role received only the following team-specific subset; an actual DBA must substitute safely quoted established role names and review default privileges, `PUBLIC`, role memberships, schema ownership and all other grants before applying. These examples do not create users or credentials:

```sql
GRANT USAGE ON SCHEMA public TO :"team_role";
GRANT SELECT (firebase_uid, active) ON app_users TO :"team_role";
GRANT SELECT (firebase_uid, role, team_access_version) ON user_roles TO :"team_role";
GRANT SELECT (firebase_uid, property_id) ON property_access TO :"team_role";
GRANT SELECT (id) ON properties TO :"team_role";
GRANT INSERT (firebase_uid, active), UPDATE (active) ON app_users TO :"team_role";
GRANT INSERT (firebase_uid, role), UPDATE (role, team_access_version) ON user_roles TO :"team_role";
GRANT INSERT (firebase_uid, property_id), DELETE ON property_access TO :"team_role";
GRANT INSERT (actor_uid, target_uid, action, role, active, property_count) ON team_admin_audit TO :"team_role";
GRANT USAGE ON SEQUENCE team_admin_audit_id_seq TO :"team_role";
```

The team role needs an appropriate database CONNECT grant. The API principal must not inherit the team role or receive any write grant above the constrained `auth_lock_guard` columns. The trusted team role must not receive role membership-management, table ownership, schema CREATE, email UPDATE, user/role DELETE, audit UPDATE/DELETE/TRUNCATE, or administrative PostgreSQL flags. Scope DELETE is required for atomic revocation; route it only through the trusted authenticated service. Tests verify no direct API-role role update, rejected same users/servers/schema mismatch/escalating memberships, and absence of team-role email changes, user/role deletion and audit modification. These local grants are a tested pattern, not a production grant application.

Run the isolated tests using a Python environment with the API requirements and PostgreSQL tools:

```sh
python -m unittest backend.api.test_team_postgres -q
```

They create disposable databases and two Unix-socket-only PostgreSQL clusters with fictional identities, exercise service and HTTP authorization, inspect audit metadata, test `UPDATE`/`DELETE`/`TRUNCATE` rejection, race owner removal and stale versions, and demonstrate scope revocation waiting on in-flight permission checks. They do not contact Firebase, a real VPS, or production data.
