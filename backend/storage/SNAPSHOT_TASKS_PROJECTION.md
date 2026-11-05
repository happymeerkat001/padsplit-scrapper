# Private PadSplit task snapshot projection

`backend.storage.project_snapshot_tasks` reconstructs the complete owner-only private dashboard snapshot from the authenticated encrypted export. It stages provider ticket observations separately from editable internal/manual tasks. It never sends status changes to PadSplit. The original complete ticket, including any unfamiliar fields, remains in the encrypted Firestore source record. The SQL read model contains only the source task ID, bucket, explicitly scoped target property, observed time and selected task fields: status, category, details and room number. The scoped API performs a second allowlist pass and does not return structured access-code fields. Free-text details can still contain sensitive information and are protected by property authorization.

Every source task needs an owner-reviewed binding. The file must be 0600 in a 0700 directory outside the repository. This is fictional input:

```json
{"version":1,"bindings":[{"source_id":123,"source_property_id":456,"source_property_present":true,"target_property_id":"fixture-property","expected_property_name":"Fixture property"}]}
```

Source IDs and source property identifiers match exactly by type and value. `source_property_present` distinguishes an absent source property field from an explicit null. A `target_property_id` and expected name are checked against an existing property, so names, source addresses and external IDs do not automatically grant scope. For a deliberately owner-only task use `"target_property_id":null,"expected_property_name":null`. Duplicate, missing, changed or unused bindings fail the entire transaction. The same source ID can be preserved in distinct source buckets under one reviewed binding; a duplicate within a single bucket is ambiguous and rejected. The mapping controls private data access and must be reviewed by an authorized operator.

After importing and verifying the encrypted source and setting `PADSPLIT_MIGRATION_KEY` as documented in the migration runbook, run these commands against a disposable local `padsplit_stage_*` database:

```sh
python -m backend.storage.project_snapshot_tasks --input /private/export.enc --bindings /private/task-map.json
python -m backend.storage.project_snapshot_tasks --input /private/export.enc --bindings /private/task-map.json --apply
```

The first command is a dry run. Apply is transactional and idempotent; differences at the same source observation time stop for review. Missing tickets are not treated as deletions. This is not a live sync, incremental catch-up, source write pause or production cutover. The production export may not contain a private snapshot at all; no actual source or bindings have been verified for this projection.

`backend.api.snapshot_tasks.make_snapshot_tasks_router` exposes the latest stored observation of each provider ticket at `GET /v1/provider-tasks`, with property authorization, bounded pagination and an explicit `ingestion: staging_only` status. Owner-only records are hidden from scoped users. It has no write routes or provider sends. It is mounted in the local API through the `limited` actor and error-redacting `run` wrapper; live identity and migration still require separate verification. A reviewed DBA can consider the following additional restricted-role grant, alongside the existing auth-lock grants:

```sql
GRANT SELECT (id,task_key,source_id,property_id,bucket,data,observed_at) ON padsplit_task_observations TO :"api_role";
```

Synthetic tests check that this role cannot change property assignments. No live grants have been made. A complete overview still needs live snapshot ingestion, validated freshness, correct task-to-room linking and all existing workflows before release mapping.
