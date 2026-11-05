ALTER TABLE task_discussions ADD COLUMN source_role TEXT;
ALTER TABLE task_discussions ADD COLUMN source_status TEXT;
ALTER TABLE task_discussions ADD COLUMN source_created_nanos INTEGER CHECK(source_created_nanos BETWEEN 0 AND 999999999);
