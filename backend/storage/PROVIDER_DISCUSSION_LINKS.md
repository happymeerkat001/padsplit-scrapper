# Reviewed provider discussion bindings — staging only

A provider task observation and an internal task are separate records. This tool never infers that they represent the same thing from ticket numbers, labels, names or addresses. An authorized operator must independently review authoritative private evidence and bind the exact provider observation to an existing internal task. The task and observation must belong to the same explicitly scoped property and the observation must come from the authenticated staged source generation. Unscoped tasks remain owner-only and cannot be linked by this projector.

Store the mapping JSON outside the repository in a directory owned by the operator with mode `0700`; the file must be mode `0600`. Never place actual task IDs, private paths or source records in chat, logs or Git. Example (fictional values only):

```json
{"version":1,"bindings":[{"task_observation_id":"provider_observation_example","internal_task_id":"internal_example","expected_property_id":"property_example","expected_generation":"private_dashboard_runs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","expected_task_source_path":"manual_tasks/example"}]}
```

`expected_task_source_path` must exactly match the target task's source path, or be JSON `null` for a reviewed task with no source path. First import and verify the authenticated encrypted export and project the provider observations and internal tasks. Then use the original migration key and an isolated local `padsplit_stage_*` database:

```sh
python -m backend.storage.project_provider_discussion_links --input /private/export.enc --bindings /private/bindings.json
python -m backend.storage.project_provider_discussion_links --input /private/export.enc --bindings /private/bindings.json --apply
```

The first command is a dry run. Identical imports are idempotent; conflicting IDs, source paths, source generations, properties and duplicates fail closed. The importer re-verifies the complete authenticated source and never fetches or modifies an external provider. Its output contains only counts or generic errors. No production migration or schedule is enabled.

`GET /v1/provider-discussion-links` returns only reviewed links whose exact observation remains the latest for its provider task and whose task still has the reviewed source path and property. It applies the current account's property permissions and identifies its coverage as `reviewed_links_only` and ingestion as `staging_only`. A newly collected observation invalidates an earlier review until checked again. A missing link never means there is no corresponding discussion. The dashboard connects a link only if its observation ID, task key and property ID exactly match the displayed observation. It opens the scoped internal task workspace, which reauthorizes direct access. No association or source identity is inferred.

The API principal needs the identity/property lock grants documented in `MIGRATION_RUNBOOK.md` plus the following least-privilege read grants (review these for the target staging environment):

```sql
GRANT SELECT (id,task_key,property_id,source_generation,observed_at) ON padsplit_task_observations TO :"api_role";
GRANT SELECT (id,property_id,source_path) ON tasks TO :"api_role";
GRANT SELECT (task_observation_id,internal_task_id,property_id,source_generation,expected_task_source_path) ON reviewed_provider_discussion_links TO :"api_role";
```

The restricted role cannot mutate reviewed links. The API's task discussion operations are separate; they use verified author identities and current task property authorization. Binding existing historical discussion records is still a separate source reconciliation step. This support does not establish complete historical coverage, live source ingestion, authorization of any real mapping, or release parity.
