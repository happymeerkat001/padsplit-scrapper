-- Preserve the precision and origin of imported Firestore history without
-- changing the meaning of source_version, which is an encryption context.
ALTER TABLE code_history ADD COLUMN source_created_nanos INTEGER CHECK(source_created_nanos BETWEEN 0 AND 999999999);
ALTER TABLE code_history ADD COLUMN source_expire_at TIMESTAMPTZ;
ALTER TABLE code_history ADD COLUMN source_expire_nanos INTEGER CHECK(source_expire_nanos BETWEEN 0 AND 999999999);
ALTER TABLE code_history ADD COLUMN source_origin TEXT CHECK(source_origin IN ('page','catchup'));
