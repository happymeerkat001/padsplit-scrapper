-- Allow a narrowly granted SELECT FOR SHARE without allowing property reassignment.
ALTER TABLE conversations ADD COLUMN auth_lock_guard BOOLEAN NOT NULL DEFAULT FALSE CHECK(auth_lock_guard IS FALSE);
