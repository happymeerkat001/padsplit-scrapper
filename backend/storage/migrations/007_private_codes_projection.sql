-- Layout and values remain encrypted with distinct AAD contexts; source paths
-- identify Firestore origins but are not secrets or source field contents.
CREATE TABLE access_code_schemas(
 property_id TEXT PRIMARY KEY REFERENCES properties(id),
 ciphertext BYTEA NOT NULL,
 nonce BYTEA NOT NULL CHECK(octet_length(nonce)=12),
 key_version TEXT NOT NULL,
 version BIGINT NOT NULL DEFAULT 1 CHECK(version>0),
 source_path TEXT NOT NULL
);
ALTER TABLE access_codes ADD COLUMN source_path TEXT UNIQUE;
ALTER TABLE access_codes ADD COLUMN source_updated_nanos INTEGER CHECK(source_updated_nanos BETWEEN 0 AND 999999999);
