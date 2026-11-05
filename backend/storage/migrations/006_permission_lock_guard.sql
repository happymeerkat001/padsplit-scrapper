-- PostgreSQL SELECT ... FOR SHARE requires UPDATE privilege on at least one
-- column of each locked table. These immutable-value columns permit a narrow
-- column grant without granting changes to identity, role, or scope fields.
ALTER TABLE app_users ADD COLUMN auth_lock_guard BOOLEAN NOT NULL DEFAULT FALSE CHECK(auth_lock_guard IS FALSE);
ALTER TABLE user_roles ADD COLUMN auth_lock_guard BOOLEAN NOT NULL DEFAULT FALSE CHECK(auth_lock_guard IS FALSE);
ALTER TABLE property_access ADD COLUMN auth_lock_guard BOOLEAN NOT NULL DEFAULT FALSE CHECK(auth_lock_guard IS FALSE);
