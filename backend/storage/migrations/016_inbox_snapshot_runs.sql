-- Authenticated, encrypted evidence for explicit staging catch-up rehearsals.
-- Operators must retain the original encrypted export and its separately held key.
CREATE TABLE inbox_snapshot_runs (
    run_id TEXT PRIMARY KEY CHECK(run_id ~ '^[a-f0-9]{32}$'),
    observed_at TIMESTAMPTZ NOT NULL UNIQUE,
    ciphertext BYTEA NOT NULL,
    nonce BYTEA NOT NULL CHECK(octet_length(nonce)=12),
    fingerprint BYTEA NOT NULL CHECK(octet_length(fingerprint)=32),
    key_version TEXT NOT NULL CHECK(key_version='v1'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
