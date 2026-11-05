-- NULL means unread state was not observed, not that the conversation is read.
ALTER TABLE conversations ADD COLUMN source_unread BOOLEAN;
