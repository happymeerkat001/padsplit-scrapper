# Staging climate projection

The projection imports a private thermostat snapshot into an isolated PostgreSQL staging database. It does not contact a provider, control devices, publish a snapshot, or establish that a reading is current. The source timestamp may belong to a stale scraper fallback. The API labels observations as staging data; source collection status is unknown. Device-to-property scope is explicitly reviewed, never inferred from a name.

The snapshot must match the scraper's `scraped_at` and `locations` output, including every emitted device field. Unknown or missing fields, invalid numbers, duplicate identities, unmapped locations, changed device scope, and conflicting observations fail closed. Explicit nulls and zero values remain distinct. A canonical SHA-256 digest is retained with each observation as provenance, not authentication of the source. Existing rows are never deleted by an import.

Store both JSON inputs in an owner-only directory outside the repository, with directory mode `0700` and file mode `0600`. Keep backups encrypted off-server, with encryption keys separate. Use fictional values when testing. Review source IDs and target property names against the intended staging database before running. Example binding format:

```json
{"version":1,"bindings":[{"source_location_id":12,"target_property_id":"property-example","expected_property_name":"Fictional property"}]}
```

The source location ID must match both value and type. Every location must be mapped, and every mapping used. The target ID and expected property name must match an existing staging property. A device already associated with another property is rejected for manual reconciliation.

Set `DATABASE_URL` to the isolated local staging PostgreSQL database; the existing `staging_connect` guard rejects non-loopback destinations. Apply versioned migrations through the existing schema tooling, then validate and import:

```sh
python -m backend.storage.project_climate --input /private/staging/climate.json --bindings /private/staging/climate-bindings.json
python -m backend.storage.project_climate --input /private/staging/climate.json --bindings /private/staging/climate-bindings.json --apply
```

The first command is a dry run. An identical repeat is idempotent; changed content at the same device and timestamp is rejected. Output reports only the count and generic errors, not source records. Use the documented least-privilege API database role with `SELECT` on `id`, `device_key`, `property_id`, `observed_at`, and `data` of `climate_observations`; the API does not need the source fingerprint or write access. Verify migrations and privileges in staging before enabling the endpoint. This projection is not an automated collector or production cutover procedure.
