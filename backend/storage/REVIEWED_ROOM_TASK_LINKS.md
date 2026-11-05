# Reviewed room/task associations — staging only

The PadSplit collector derives its occupancy groups from normalized address and room labels. Those labels and the ticket ID lists derived from them do **not** establish a trustworthy room/task relationship. This projection never infers a link from those fields. An authorized operator must independently verify each association using authoritative source evidence and select the exact already-staged task and occupancy observation IDs.

The private mapping is a JSON file outside the repository, in a 0700 directory with mode 0600. Example values below are fictional; the occupancy observation ID is the positive PostgreSQL integer, not a property or room number:

```json
{"version":1,"bindings":[{"task_observation_id":"provider_observation_example","occupancy_observation_id":1,"expected_property_id":"property-example","expected_generation":"private_dashboard_runs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}
```

Apply migrations and first import and verify the same authenticated encrypted export, then project the reviewed scoped occupancy rows and provider task observations. Determine the exact staged observation IDs and source generation through an authorized private staging connection; do not place them in chat, logs, or the repository. With the original migration key and a disposable loopback-only `padsplit_stage_*` database, run:

```sh
python -m backend.storage.project_reviewed_links --input /private/export.enc --bindings /private/reviewed-links.json
python -m backend.storage.project_reviewed_links --input /private/export.enc --bindings /private/reviewed-links.json --apply
```

The first command is a dry run. The projection authenticates and verifies the staged source, requires the complete private snapshot, requires both exact observations to belong to the same reviewed property and source generation, and rejects a conflicting prior association. Repeating the same input is idempotent. It does not alter or delete existing associations or infer missing ones. An empty or partial mapping does not establish that other rooms have no associated tasks.

The local authenticated `GET /v1/room-task-links` route returns only reviewed links whose referenced observations remain the latest for both the room and the task and whose property and source generation still agree. It applies property authorization and pagination, and labels coverage `reviewed_links_only` and ingestion `staging_only`. The frontend additionally checks the exact observation IDs before displaying a link. New observations require review again; concurrent API fetches cannot silently attach a newer link to an older loaded observation. No production deployment, real association, source mapping or continuous synchronization is implied.

The API's restricted role needs the existing identity/property lock grants plus read-only access to the specific columns below; these grants are illustrative and require independent DBA review and staging verification:

```sql
GRANT SELECT (id, property_id) ON rooms TO :"api_role";
GRANT SELECT (id, room_id, observed_at, source_payload, source_generation) ON occupancy_observations TO :"api_role";
GRANT SELECT (id, task_key, property_id, source_generation, observed_at) ON padsplit_task_observations TO :"api_role";
GRANT SELECT (task_observation_id, occupancy_observation_id, property_id, source_generation) ON reviewed_room_task_links TO :"api_role";
```

Synthetic restricted-role tests deny link mutation and cross-property reads and verify that newer observations invalidate prior links. No real mappings or live grants were made.
