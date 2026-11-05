-- Concurrent owner changes use explicit versions rather than silently replacing
-- one another. Existing memberships begin at version 1; absence is version 0.
ALTER TABLE user_roles ADD COLUMN team_access_version BIGINT NOT NULL DEFAULT 1
  CHECK (team_access_version >= 1);
