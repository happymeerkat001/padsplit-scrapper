-- Explicit report-only access for a verified existing Firebase identity.
-- Operators provision and revoke these rows separately after verification.
CREATE TABLE report_access (
    firebase_uid TEXT PRIMARY KEY REFERENCES app_users(firebase_uid),
    auth_lock_guard BOOLEAN NOT NULL DEFAULT FALSE CHECK(auth_lock_guard IS FALSE)
);
