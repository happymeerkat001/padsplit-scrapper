CREATE TABLE notes(id TEXT PRIMARY KEY, text TEXT NOT NULL, version BIGINT NOT NULL DEFAULT 1 CHECK(version > 0), source_path TEXT UNIQUE);
ALTER TABLE templates DROP CONSTRAINT templates_source_path_key;
ALTER TABLE templates ADD COLUMN source_field_key TEXT;
ALTER TABLE templates ADD CONSTRAINT templates_source_pair_unique UNIQUE(source_path, source_field_key);
