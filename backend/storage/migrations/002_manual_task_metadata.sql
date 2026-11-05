ALTER TABLE tasks ADD COLUMN assigned_label TEXT;
ALTER TABLE tasks ADD COLUMN source_type TEXT;
ALTER TABLE tasks ADD COLUMN source_created_nanos INTEGER CHECK(source_created_nanos BETWEEN 0 AND 999999999);
