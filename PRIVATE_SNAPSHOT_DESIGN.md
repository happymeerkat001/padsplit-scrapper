# Private dashboard snapshot proposal

This is local, opt-in infrastructure; no scheduled job calls it, no credentials are created and no Firestore rules have been deployed. It is not a replacement for production exposure remediation. Existing public site and Git history remain unchanged.

The standalone publisher requires explicit `PADSPLIT_PRIVATE_SNAPSHOT_ENABLE=1` and `PADSPLIT_COLLECTION_ONLY=0` in its process environment. It refuses CI and an immutable collection-only policy, and does not require or enable action hooks. The explicit collection-only override applies to this standalone command, which can write sensitive live data; do not use it as part of a collection-only job. It acquires a local interprocess lock before reading the snapshot or initializing the Admin SDK, and refuses a client whose project is not `padsplit-scrapper`. It writes only `scraped_at`, `messages` and `tasks` from the passed payload to versioned chunks; it does not automatically run as part of any scrape or scheduled job. It writes chunks first and updates the pointer last. If a chunk write fails, the old pointer is not changed. A failure or timeout while writing the pointer can have an uncertain outcome and must be checked by an operator.

Reader contract: fetch document `private_dashboard/current`. Accept only `schema_version: 1`, a 32-character lowercase hex `run_id`, integer `chunk_count` from 1 through 100, and a valid timezone-aware `scraped_at`. Fetch each document `private_dashboard_runs/{run_id}/chunks/{index}` by exact index from zero to `chunk_count - 1`; require matching integer `index` and string `data` no longer than 400,000 ASCII bytes. Join the strings in index order, parse JSON and validate that the result has the matching `scraped_at`, an array `messages`, and an object `tasks` whose buckets are arrays. Any missing chunk, invalid field, permission error or mismatch must produce an unavailable/error state, not empty data or a fallback to a public raw snapshot. Neither list queries nor client writes are required.

The proposed local Firestore rules allow document reads only for the same designated operator UID used for property codes, and deny client writes and collection listing. Admin SDK access is governed by IAM and bypasses rules. Tests use an isolated demo Firestore emulator and in-memory fake, not production.

Before enabling an integration, independently review the operator identity, authenticated UI, deployment, IAM, data size, data residency, privacy and retention. Concurrent publishers must be serialized by the caller; the writer does not compare timestamps or prevent an older run overwriting a newer pointer. Each successful publication creates a generation; failed chunk writes may leave an incomplete generation. No cleanup or TTL is implemented. Design and test safe retention/garbage collection before operational use, preserving the current generation and any generation being read. Automatic retries, upload triggers, rollback, and concurrency control are not implemented. A newly deployed reader will show unavailable until an authorized Admin job publishes a snapshot.

## Explicit activation after review

This command can write sensitive data to the real Firestore project. Only run after the owner has reviewed and deployed the matching rules, configured appropriately scoped Admin SDK credentials, reviewed the source and retention plan, and established a single publishing host. The command is deliberately not included in scheduled scripts or workflows. It reads process environment, not `.env`.

```sh
PADSPLIT_PRIVATE_SNAPSHOT_ENABLE=1 PADSPLIT_COLLECTION_ONLY=0 \
  venv/bin/python -m padsplit_scraper.private_snapshot --source /path/to/private/latest.json
```

The CLI's Admin client uses the existing `FIREBASE_SERVICE_ACCOUNT_JSON` or `GOOGLE_APPLICATION_CREDENTIALS` configuration. CI and immutable collection-only policies remain blocked. The local lock serializes processes using this clone on this host; it does not coordinate separate hosts, containers, or clones, so a single-publisher deployment is required. Never print source payloads, credentials, or the `.env` file.

## Optional owner-only occupancy

The local publisher accepts `--occupancy-source /path/to/private/occupancy.json` in addition to `--source`. The input must contain a timezone-aware `scraped_at` and a `rooms` array of objects. Occupancy is an optional part of the existing private snapshot payload; old snapshots without it remain readable. It is validated before any writes, serialized in the same bounded chunks, and covered by the same owner-only Firestore read rules. The flag is optional and does not enable publication or relax any runtime gate. The overview no longer fetches the public occupancy file: it shows room metrics only after the designated owner successfully loads a private snapshot containing occupancy. On sign-out or account change it clears room data, summary values and any open property detail. Missing occupancy is shown as unavailable, not zero.

The source must be a full private occupancy file generated and stored outside the public artifacts; the redacted public placeholder is not a substitute. Publication remains a separate, deliberate owner-reviewed step, and no production data was uploaded during this change.

After the production prerequisites above have been independently reviewed, an authorized operator can explicitly provide both sources:

```sh
PADSPLIT_PRIVATE_SNAPSHOT_ENABLE=1 PADSPLIT_COLLECTION_ONLY=0 \
  venv/bin/python -m padsplit_scraper.private_snapshot \
  --source /private/path/latest.json --occupancy-source /private/path/occupancy.json
```

This command writes to Firestore and was not executed as part of local testing. A current private snapshot must be published after compatible rules are reviewed and deployed before the owner can see room data. The checked-in UI cannot verify owner access or recover a private occupancy file that was never retained.
