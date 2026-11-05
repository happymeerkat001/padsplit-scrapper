# Staging occupancy projection

`backend.storage.project_occupancy` reads only an authenticated encrypted Firestore export and requires a complete `private_dashboard/current` pointer and its exact chunks. It requires an `occupancy` payload produced by the existing collector and preserves the original row fields and observation timestamp. It never turns absent rooms into deletions or infers property membership from an address, room number or PadSplit property identifier. A source with unknown fields, duplicate source identities, ambiguous scopes or altered values fails closed.

Before running, an authorized operator must create a private JSON mapping outside the repository in a 0700 directory with a 0600 file. Example uses invented data:

```json
{"version":1,"bindings":[{"property_id":123,"address":"Example Street","room_number":1,"target_property_id":"property-example","expected_property_name":"Fictional property","room_label":"1"}]}
```

The source-side `property_id`, `address` and `room_number` must exactly match one source row, including types and null values. The target property must already exist, and the expected name and target room label must be reviewed. Each source identity and target property/room label must be unique. No extra or missing binding is accepted. Verify these associations outside the tool before use; the mapping controls access to sensitive operational data. A collector row whose address or room is missing cannot safely be projected automatically.

With a distinct private database role and loopback-only `DATABASE_URL` naming a disposable `padsplit_stage_*` database, first import and verify the full export with the migration runbook. Set `PADSPLIT_MIGRATION_KEY` securely as documented there. Then run:

```sh
python -m backend.storage.project_occupancy --input /private/export.enc --bindings /private/occupancy-map.json
python -m backend.storage.project_occupancy --input /private/export.enc --bindings /private/occupancy-map.json --apply
```

The first command is a dry run. Apply and reapply are transactional and detect mismatches rather than overwriting them. A later snapshot containing the same room and timestamp but changed content or generation stops for review. Removing a row in a later source snapshot never deletes previously projected data. The tool is not a production sync, change feed, cleanup service or cutover; operators must still reconcile stale or removed rooms and implement a supported regular collector before the dashboard can rely on live freshness. The real exported snapshot may lack occupancy entirely, and then this tool deliberately stops.

`backend.api.occupancy.make_occupancy_router` supplies a read-only, paginated `/v1/occupancy` for the latest available observation per room. It derives identity and property scopes through the existing API actor and database authorization checks; nonowners cannot filter another property, and returned rows may contain private operational details. The router is mounted in the local app with `limited`, `repository_provider` and `run`, but must still be verified with real identity; it is not deployed. It reports `ingestion: staging_only` and does not claim current data. Suggested additional restricted-role grants (subject to DBA review):

```sql
GRANT SELECT (id, property_id, label) ON rooms TO :"api_role";
GRANT SELECT (id, room_id, observed_at, source_payload) ON occupancy_observations TO :"api_role";
```

These supplement the narrowly scoped authorization grants in `MIGRATION_RUNBOOK.md`. The synthetic PostgreSQL test also confirms that this role cannot change room property assignments. No production grants were made.
