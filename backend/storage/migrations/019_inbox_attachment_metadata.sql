-- Null means attachment metadata was not observed, not an empty attachment set.
-- No provider URL, authorization token, or attachment bytes belong here.
ALTER TABLE messages ADD COLUMN source_attachments JSONB
  CHECK (source_attachments IS NULL OR jsonb_typeof(source_attachments) = 'array');
