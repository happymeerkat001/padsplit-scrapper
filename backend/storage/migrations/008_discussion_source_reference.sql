-- Exact source reference type/value and field presence preserve legacy records.
ALTER TABLE task_discussions ADD COLUMN source_task_ref_type TEXT CHECK(source_task_ref_type IN ('string','integer'));
ALTER TABLE task_discussions ADD COLUMN source_task_ref_string TEXT;
ALTER TABLE task_discussions ADD COLUMN source_task_ref_integer BIGINT;
ALTER TABLE task_discussions ADD COLUMN source_present_fields TEXT[];
ALTER TABLE task_discussions ADD CONSTRAINT discussion_source_ref_shape CHECK (
  (source_task_ref_type IS NULL AND source_task_ref_string IS NULL AND source_task_ref_integer IS NULL)
  OR (source_task_ref_type='string' AND source_task_ref_string IS NOT NULL AND source_task_ref_integer IS NULL)
  OR (source_task_ref_type='integer' AND source_task_ref_string IS NULL AND source_task_ref_integer IS NOT NULL)
);
