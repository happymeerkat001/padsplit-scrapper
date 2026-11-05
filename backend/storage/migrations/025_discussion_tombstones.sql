-- Preserve source and audit history when a discussion is removed from the UI.
ALTER TABLE task_discussions ADD COLUMN deleted_at TIMESTAMPTZ;
