-- A source version is needed to authenticate the encrypted history payload.
-- Null means legacy/unknown context and must not be decrypted without review.
ALTER TABLE code_history ADD COLUMN source_version BIGINT CHECK(source_version > 0);
