# Firestore to PostgreSQL migration: staging preparation

## Actual read-only rehearsal — 4 November 2026

A read-only traversal of the production Firestore project produced an authenticated encrypted local backup of 35 documents. A first export exposed a timestamp encoding inconsistency: some timestamps contained microseconds but an incorrect zero nanosecond tag. The exporter was corrected and regression-tested, and a second read-only export of 35 documents was used for the successful rehearsal below. The first backup remains encrypted but must not be treated as a verified migration source. Each encryption key is in a separate, access-restricted local directory outside the repository. Neither backup is off-server and neither traversal was a cross-collection point-in-time snapshot. No Firestore data was changed or removed.

The corrected encrypted backup was imported twice into a disposable, password-authenticated, loopback-only PostgreSQL 16 database, and all 35 encrypted staging records matched the backup field by field. Manual-task, shared-record, statistics and access-code projections passed dry runs and were applied twice to that disposable database. The nine code documents and private layouts were encrypted with a separate temporary key and verified field by field, including legacy fields outside the presentation schema. The discussion dry run failed closed because nine source records need authoritative task mappings. The mapper now preserves integer/string reference types and empty-text status entries, but it will not invent missing tasks or relationships. The isolated database was stopped and removed; the encrypted source backups remain. No domain completeness, relationship reconciliation, off-server recovery, or production cutover is claimed.

The real export contained no `manual_tasks` documents, so that projection's real-source pass covered zero tasks; its nonempty behavior was tested separately using fictional records. Current records may have changed since the non-atomic export.

A further local API probe used a synthetic owner identity against that temporary projected database. Anonymous and viewer schema reads were denied; the owner saw masked field names and explicit reveal responses matched the nine source code maps in memory. No code values or identifiers were printed. This verifies the staging API/data path with a simulated identity, not a real Firebase sign-in or a production deployment.

A read-only comparison found none of the nine discussion references in the older public snapshot's task IDs. That snapshot is degraded and cannot establish a safe replacement mapping. Obtain and reconcile the authoritative task records before assigning these discussions; retain their encrypted source records in the meantime.

## Current status and hard boundary

The current tooling is a **staging rehearsal**, not a production cutover. It encrypts a read-only Firestore export and copies source documents into a private PostgreSQL staging table. Supported projections include manual tasks and explicitly linked discussions, shared records, statistics, current codes and validated code histories, plus privately mapped inbox, occupancy and provider-task snapshots where those sources exist. Stats JSON is parsed without binary-float conversion and written to PostgreSQL JSONB from the original number text; duplicate keys, non-finite numbers, unknown fields and invalid timestamps stop the projection. Full relationship/business-rule comparisons, continuous synchronization, attachment content and an approved API cutover remain incomplete. Do not retire Firebase, delete source data, or treat a successful staging import as permission to cut over.

The collector enumerates root collections and recursively traverses nested subcollections, including descendants of absent parent documents. It retains each document's full path and tagged field representation, including nulls, timestamps and nanoseconds, byte strings, references, geopoints, map keys and float identity. Unsupported values stop extraction rather than being coerced. Export reads occur over time, not at a single consistent read timestamp; concurrent writes and deletions therefore require a write pause and a final verified export/diff before any future cutover. The production read-only rehearsal above used the signed-in Firebase CLI account; repeatable operator execution should be reviewed with the intended service account's read permissions.

The backup is AES-256-GCM encrypted per frame, created exclusively with mode `0600`, and has an authenticated count/digest manifest; missing, damaged, truncated, reordered or wrong-key files fail verification before intake. The key must be managed separately from the backup and database. Backups must be stored outside the repository, in a directory accessible only to the operator, and subsequently copied to an independently controlled encrypted off-server store with a separately tested recovery process. Merely creating a local file is not an off-server backup.

The staging import stores encrypted canonical documents, original paths and source update times; stable paths provide source-to-target mappings. The importer verifies the entire authenticated file first, checks for duplicate paths, compares existing source fingerprints, and writes in bounded transactions. Rerunning the same input is safe; changed documents or concurrent conflicts stop for reconciliation instead of silently overwriting. A failure after a completed batch leaves completed batches in staging; retry the same file. Staging verification compares total source/target document counts, exact decrypted tagged fields, and stored collection path, document ID, key version and source update time. The timestamp metadata uses PostgreSQL's microsecond precision; the exact original timestamp remains in the authenticated encrypted document. Verification does not prove entity relationships, final UI behavior, or financial/business equivalence.

Loopback binding alone does not protect a database from other users on the same computer. Use a dedicated password-authenticated PostgreSQL staging cluster, restrict its data and socket directories to the operating-system account, and never import real source records into a trust-authenticated test server. Source paths and projected business fields can be sensitive even though the raw source payloads and code values are encrypted. A short-lived local trust-auth rehearsal briefly held encrypted source rows and plaintext source-path identifiers before its database was dropped, its server stopped and its private test-cluster directory removed. Filesystem-level recovery of deleted data has not been assessed. No evidence of access by another local user was observed.

The staging connector forces libpq's numeric connection address to loopback and rejects a conflicting explicit `hostaddr`; this prevents `PGHOSTADDR` or a DSN host-address override from bypassing the staging host check. This network restriction is not a substitute for private sockets, password authentication or checking which local server is running.

Migration 025 adds `task_discussions.deleted_at` for scoped soft deletion. The API role requires `SELECT (deleted_at)` and `UPDATE (deleted_at)` on `task_discussions`, in addition to its existing discussion and task grants. Deletion retains the original record, hides it from ordinary discussion reads, and produces a redacted audit event. Verify grants with a restricted staging role before rollout; do not use unrestricted table writes as a shortcut.

The manual-task projection preserves the source path, notes, who/source labels and creation timestamp; original nanoseconds stay in a separate field. It uses a deterministic opaque ID. `property_id` is NULL because no property association can be safely inferred; access must remain owner-only. A present manual-task document is treated as open because the current UI deletes it to remove it and has no stored status. Missing/invalid fields or differing target rows stop the projection. The original encrypted document remains available for review.

The discussion projection runs only after the corresponding manual tasks are projected. It resolves source task IDs through exact `manual_tasks/{id}` mappings; it does not guess matches for scraped tasks, numeric IDs, or orphaned discussions. Role and status labels are preserved as source labels, never promoted into user accounts or permissions. Original timestamp nanoseconds and source paths are retained. Unknown fields, invalid timestamps, unsupported target types and mismatched targets stop the transaction for review.

The shared-record projection maps notes with an explicit string `text`; vendors with a nonempty string `name` and optional string/null `specialty`, `contact` and `location`; and complete indexed `n0`/`t0` through `n8`/`t8` template pairs. Missing vendor fields remain absent and explicit null stays null. It rejects unknown fields, other template documents, nested descendants, partial template pairs and invalid types instead of silently dropping them. This strictness may require source review because the existing interface can have partial template pairs. The full original records remain encrypted in staging. These global records are owner-only until a reviewed authorization design assigns a narrower scope.

Access-code values must use `backend.storage.secrets`, a server-only AES-GCM primitive with property, version and key version authenticated as associated data. Store its separate 32-byte `PADSPLIT_CODES_KEY` outside PostgreSQL and backups. A local owner-only history API and staging projection exist, but production history migration, retention and a key rotation workflow have not been completed.

## Isolated staging commands

Install the listed dependencies in an isolated Python environment. Initialize an empty UTF-8 PostgreSQL database on loopback with a name beginning `padsplit_stage_`. The tools intentionally refuse non-loopback hosts and other database names. Set `DATABASE_URL` in the shell without recording its value in command history or logs. Set `PADSPLIT_MIGRATION_KEY` to a securely generated base64-encoded 32-byte key obtained from an approved secret manager; do not reuse the access-code key. Do not put either secret in source, frontend variables, or screenshots.

```sh
python -m backend.storage.schema
python -m backend.storage.schema   # must report zero newly applied
# After a separately reviewed, read-only source setup and with private directory permissions 0700:
python -m backend.storage.migrate export --project padsplit-scrapper --read-live --output /private/off-repo/export.enc
python -m backend.storage.migrate import --input /private/off-repo/export.enc            # dry run
python -m backend.storage.migrate import --input /private/off-repo/export.enc --apply
python -m backend.storage.migrate import --input /private/off-repo/export.enc --apply   # idempotency
python -m backend.storage.migrate verify --input /private/off-repo/export.enc
python -m backend.storage.project_manual_tasks --input /private/off-repo/export.enc        # dry run
python -m backend.storage.project_manual_tasks --input /private/off-repo/export.enc --apply
python -m backend.storage.project_discussions --input /private/off-repo/export.enc         # dry run
python -m backend.storage.project_discussions --input /private/off-repo/export.enc --apply
python -m backend.storage.project_shared --input /private/off-repo/export.enc              # dry run
python -m backend.storage.project_shared --input /private/off-repo/export.enc --apply
python -m backend.storage.project_stats --input /private/off-repo/export.enc               # dry run
python -m backend.storage.project_stats --input /private/off-repo/export.enc --apply
```

`--read-live` reads all visible source documents and may contain sensitive data; the exported values are never printed. Verify IAM and quota with the operator before running it. If any step reports a conflict, unsupported value or mismatch, stop and reconcile it explicitly. A changed document or deleted document requires a new, separately designed incremental catch-up; the current importer intentionally refuses to overwrite it.

To assess drift between two authenticated backups without a database connection, set `PADSPLIT_MIGRATION_KEY` to the baseline key and, if different, `PADSPLIT_COMPARE_KEY` to the candidate key. Then run:

```sh
python -m backend.storage.migrate compare --baseline /private/off-repo/baseline.enc --candidate /private/off-repo/candidate.enc
```

The command authenticates both complete files, rejects duplicate source paths, and prints only counts of added, changed, deleted and unchanged whole documents (including their update timestamps). It does not print identifiers or values. This is an offline drift indicator, not an incremental importer, a field-level report, or proof of a consistent Firestore snapshot. Investigate and reconcile every change and deletion before any cutover; two individually non-atomic exports cannot prove that no intermediate writes occurred.

## Restore and future cutover

1. For a staging restore, create another empty local UTF-8 `padsplit_stage_*` database, apply migrations, restore the key from a separate secure source, run the import and verification commands above, and compare the verified counts with the export manifest. Test wrong-key and truncation rejection as well as correct recovery. An encrypted fictional fixture was restored and compared in two separate local staging databases; off-server transfer and restoration were not performed.
2. Before proposing production cutover, implement typed transforms for every in-scope collection and descendant, preserve all source IDs and types, resolve missing/orphan records, validate relationships and compare all fields and KPI/occupancy behavior. Complete production authorization and cross-property tests. Resolve failed and in-flight outbox actions manually.
3. Define a source write pause covering UI, scraper, workers, callbacks, scheduled jobs and external writers; verify the pause. Capture a final encrypted export, including deletion reconciliation, and compare against target. Alternatively, design and test a complete ordered incremental catch-up including deletes. The current tools do neither automatically.
4. Present the reviewed endpoint, infrastructure, database, role bootstrap, complete comparison report, backup/restore evidence, downtime window and rollback plan for explicit production cutover approval. Keep Firestore and its backups intact.
5. Roll back by routing traffic to the previous verified stack while writes remain paused. Any writes accepted by the new system must be reconciled back before resuming the old one; no automatic reverse synchronization exists. Never simply reopen old writes after a partially completed cutover.

## UTF-8 and authorization row locks

Schema migration checks `server_encoding` before creating a migration table and rejects non-UTF8 databases. A local SQL_ASCII rehearsal was rejected before any schema was created; create an empty UTF8 database for a new rehearsal rather than attempting in-place conversion.

`SELECT ... FOR SHARE` requires PostgreSQL UPDATE privilege on at least one column in each locked table. Migration 006 adds `auth_lock_guard` columns constrained to the single value false. A separately provisioned, narrowly scoped API database role can be granted UPDATE on these guard columns only, while changes to `active`, role values and property assignments remain unavailable to it. The following illustrates only the authorization-table portion of an eventual reviewed grant; the API needs additional narrowly scoped permissions for its domain tables. An administrator must substitute the established API role with `psql` variable quoting; do not apply this blindly to an existing deployment.

```sql
GRANT SELECT (firebase_uid, active) ON app_users TO :"api_role";
GRANT SELECT (firebase_uid, role, team_access_version) ON user_roles TO :"api_role";
GRANT SELECT (firebase_uid, property_id) ON property_access TO :"api_role";
GRANT UPDATE (auth_lock_guard) ON app_users TO :"api_role";
GRANT UPDATE (auth_lock_guard) ON user_roles TO :"api_role";
GRANT UPDATE (auth_lock_guard) ON property_access TO :"api_role";
```

Local PostgreSQL tests using a separate restricted LOGIN role confirmed that updating active status, roles or property assignments, inserting/deleting authorization rows, and setting the guard to true are denied. In a concurrent rehearsal, revocation waited for a transaction holding the permission lock to finish its authorized task update; subsequent scope reads saw the revocation. The guard columns do not replace server-side authorization, audit, tested grants for the other tables, or a separate trusted membership administration process. No live role was granted privileges.

The local read-only inbox additionally locks a conversation while checking its property and reading messages. Migration 012 adds the same constrained guard to `conversations`. The synthetic restricted-role test used these additional grants, alongside the authorization grants above:

```sql
GRANT SELECT (id, property_id, updated_at, source_title, source_observed_at, source_unread, source_display), UPDATE (auth_lock_guard) ON conversations TO :"api_role";
GRANT SELECT (id, name) ON properties TO :"api_role";
GRANT SELECT (id, conversation_id, body, sent_at, sender_label, source_deleted, source_attachments) ON messages TO :"api_role";
```

The test denied updating `conversations.property_id`; no inbox message writes or production grants were applied. These grants require DBA review against the staging role before use.

The reports endpoints require the restricted API role to have `SELECT (id, data, observed_at) ON statistics` and `SELECT (firebase_uid), UPDATE (auth_lock_guard) ON report_access`. No statistics write or capability-grant permission is given to the API role. Migration 017 creates an initially empty explicit report capability table. A trusted operator must verify the existing Firebase UID, ensure it is an active minimally scoped application user, and provision or revoke its report access separately. Report-only users cannot use owner-only notes, codes, or team routes; any property assignments remain separately scoped. The locked capability serializes revocation with reads. A DBA must review and test these grants against the actual staging role; none has been applied to a live server. The API-only report view includes a temporary unsaved score calculator; an API-only dedicated history view is mapped to its existing URL. Real owner and report-account verification remains outstanding.

## Access-code schema and values: staging-only projection

Migration 007 adds encrypted per-property access-code schema layouts and source metadata for encrypted live code maps. The projection requires one `private_configuration/codes_schema` document and an exact match between its property slugs and the `property_codes` document IDs. Every source code field must be a string and each `updatedAt` must be a consistent tagged timestamp. Extra legacy/shared string fields are preserved in full even if absent from the layout. Supported nested history has a separate authenticated staging projection described in `CODES_HISTORY_MIGRATION.md`; unknown descendants, non-string fields, missing/mismatched IDs, invalid layout, divergent timestamps, wrong keys and existing target conflicts fail closed. No code values or layout bodies are written unencrypted to PostgreSQL; property identity and the layout's property display name are stored as private database metadata. Original documents remain separately encrypted in the staging source table.

Set a distinct random 32-byte base64 `PADSPLIT_CODES_KEY` in the shell and a nonsecret `PADSPLIT_CODES_KEY_VERSION`, for example `v1`, as well as `PADSPLIT_MIGRATION_KEY`. Store the codes key separately from backups and databases; never use it as the export key. With an authenticated backup already imported and verified into an isolated UTF-8 staging database:

```sh
python -m backend.storage.project_codes --input /private/off-repo/export.enc          # dry run
python -m backend.storage.project_codes --input /private/off-repo/export.enc --apply
python -m backend.storage.project_codes --input /private/off-repo/export.enc --apply  # repeat check
python -m backend.storage.project_codes --input /private/off-repo/export.enc --verify
```

The projection is atomic for all properties and uses the same version-1 code encryption context as the API. Verification decrypts both layout and values inside the trusted process and compares them with source, without printing them. Do not use the preparation keys for production or expose them to browsers. Code history import remains unsupported and must be implemented and reviewed before any cutover involving histories. This tool does not perform production import.

An earlier rehearsal export recorded some timestamp nanosecond tags as zero even when the ISO timestamp had nonzero microseconds, because the source client exposed an inconsistent zero property. The exporter now derives the nanoseconds from the actual microseconds in that case and rejects other inconsistencies; the earlier encrypted backup remains unchanged. The corrected second export and a fresh staging database were verified as described above. Do not silently rewrite or accept the inconsistent records in the first backup.

## Discussion references and legacy status-only entries

Migration 008 adds explicit source reference type/value and field-presence metadata. It supersedes the earlier manual-only limitation: exact existing manual-task mappings still work, and an external integer or string task reference can now be matched **only** through a private operator-supplied mapping. The target task must already exist in staging; the mapping must name its exact task ID, expected property scope, and expected source path. A changed/missing target, ambiguous manual match, duplicate or unused binding, unsupported reference type, missing/null text, or unknown source field stops the transaction. Integer `42` and string `"42"` are distinct references. Legacy status-only records with `text: ""` remain empty rather than being discarded or given invented text. Original fields remain in encrypted staging; source role and status are labels, not authorization roles.

Create the mapping as a UTF-8 JSON file outside the repository in an operator-owned directory with permissions `0700` or stricter, and set file permissions to `0600` or stricter. Never commit it, print it, or place it in deployment artifacts. Every binding must be reviewed against the actual target task and property; the code cannot determine the correct match. This example is fictional:

```json
{
  "version": 1,
  "bindings": [
    {
      "source_type": "integer",
      "source_value": 42,
      "target_task_id": "example-task",
      "expected_property_id": "example-property",
      "expected_source_path": "external/example-task"
    }
  ]
}
```

`source_type` is `integer` or `string`; both expected values may be `null` only when the existing target row has the corresponding null. Duplicate JSON keys, duplicate typed references, symlinks, insecure file/directory permissions, files under the repository and unused bindings are rejected. After importing and verifying the source, projecting manual tasks and explicitly preparing any external target tasks:

```sh
python -m backend.storage.project_discussions --input /private/off-repo/export.enc --mapping /private/mapping/tasks.json
python -m backend.storage.project_discussions --input /private/off-repo/export.enc --mapping /private/mapping/tasks.json --apply
python -m backend.storage.project_discussions --input /private/off-repo/export.enc --mapping /private/mapping/tasks.json --verify
```

The first command validates and locks while checking, but does not write. Apply uses one transaction; reruns compare every mapped field and reject changed target records. Verify also compares discussion-source counts. These are staging reconciliation checks, not approval of a production cutover. The API and UI must explicitly present status-only history, and real external mappings require independent review before deployment.

Migration 010 tightens the 008 database constraint to require a complete typed reference and field-presence shape even when SQL expressions contain NULL. Older application-created discussions with no source reference remain allowed; partially populated source reference metadata is rejected.
