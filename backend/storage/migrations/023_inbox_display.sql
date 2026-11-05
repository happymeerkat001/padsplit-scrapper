-- Display fields are allowlisted and bounded by the importer. The full source
-- remains encrypted separately; NULL means no display fields were observed.
ALTER TABLE conversations ADD COLUMN source_display JSONB;
