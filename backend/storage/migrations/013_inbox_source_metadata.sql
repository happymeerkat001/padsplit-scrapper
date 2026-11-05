-- The private source export remains authoritative. These fields describe only
-- the observed, potentially incomplete recent-message slice.
ALTER TABLE conversations ADD COLUMN source_title TEXT;
ALTER TABLE conversations ADD COLUMN source_observed_at TIMESTAMPTZ;
ALTER TABLE messages ADD COLUMN sender_label TEXT;
ALTER TABLE messages ADD COLUMN source_deleted BOOLEAN NOT NULL DEFAULT FALSE;
