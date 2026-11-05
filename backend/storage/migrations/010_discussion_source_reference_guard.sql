-- CHECK expressions that evaluate NULL pass by default in PostgreSQL. Require
-- the complete source-reference shape to be explicitly true instead.
ALTER TABLE task_discussions DROP CONSTRAINT discussion_source_ref_shape;
ALTER TABLE task_discussions ADD CONSTRAINT discussion_source_ref_shape CHECK ((
  (source_task_ref_type IS NULL AND source_task_ref_string IS NULL AND source_task_ref_integer IS NULL AND source_present_fields IS NULL)
  OR (source_task_ref_type='string' AND source_task_ref_string IS NOT NULL AND source_task_ref_integer IS NULL AND source_present_fields IS NOT NULL)
  OR (source_task_ref_type='integer' AND source_task_ref_string IS NULL AND source_task_ref_integer IS NOT NULL AND source_present_fields IS NOT NULL)
) IS TRUE);
