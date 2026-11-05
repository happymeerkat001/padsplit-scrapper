# Access-code history staging projection

`backend.storage.project_code_history` imports supported Firestore `property_codes/{property}/code_versions/{id}` records into a **disposable local staging database only**. Source backup authentication, source-document reconciliation, verified current-code and private-schema projection, and separately managed backup and code encryption keys are required first. It never reads the live provider or Firestore, and does not print property IDs, history IDs, values, or keys.

The current projector now recognizes validated historical descendants while retaining its fail-closed checks for unknown descendants. Run the existing encrypted export/import and schema migration sequence in `MIGRATION_RUNBOOK.md`, then project and verify the current codes with `backend.storage.project_codes`. With the same private `DATABASE_URL`, `PADSPLIT_MIGRATION_KEY`, `PADSPLIT_CODES_KEY`, and `PADSPLIT_CODES_KEY_VERSION`, run:

```sh
python -m backend.storage.project_code_history --input /secure/export.enc
python -m backend.storage.project_code_history --input /secure/export.enc --apply
python -m backend.storage.project_code_history --input /secure/export.enc --verify
```

The first command is a dry run. Apply and verify only after reviewing the output and confirming this is isolated staging. Every version must have exactly the known `fields`, `contentHash`, `createdAt`, `expireAt`, and `source` fields, a verified hash, supported timestamp encodings and origin, and a corresponding successfully verified current property/schema projection. Unsupported or mismatched source data stops the transaction for private review rather than being coerced or dropped.

Each historical code map is encrypted with the separately managed server code key; original Firestore document paths and IDs remain in `source_path`, and timestamp nanoseconds, expiry and origin are stored as metadata. The numeric `source_version=1` is solely the authenticated-encryption context used by the existing API and **does not represent an original chronological version**. History order uses original `createdAt`; equal timestamps use deterministic IDs. The original complete source document, including its hash, remains in the encrypted source records. Repeated identical imports are idempotent. Conflicts do not overwrite immutable history. A synthetic owner test restores a projected version using the existing API and checks that ordinary responses do not contain codes.

The original Firestore history catch-up job applies retention. This projection does not delete expired rows or schedule a retention job. The source hash implementations use language-specific JSON serialization; any unsupported or mismatched serialization stops for explicit review rather than guessing. Complete source comparison, off-server backup and real owner authorization remain mandatory before cutover. No real history export or production write was performed by this development work.
