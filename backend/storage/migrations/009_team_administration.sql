-- The trusted service serializes every membership mutation using this same
-- transaction-scoped advisory lock. Deferred constraints also defend against
-- direct updates by another authorized database writer.
CREATE FUNCTION prevent_last_active_owner() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'user_roles' THEN
    IF OLD.role <> 'owner' THEN RETURN NULL; END IF;
  ELSE
    IF NOT OLD.active OR NOT EXISTS (SELECT 1 FROM user_roles WHERE firebase_uid = OLD.firebase_uid AND role = 'owner') THEN
      RETURN NULL;
    END IF;
  END IF;
  PERFORM pg_advisory_xact_lock(731905609);
  IF NOT EXISTS (SELECT 1 FROM app_users u JOIN user_roles r USING (firebase_uid)
                 WHERE u.active IS TRUE AND r.role = 'owner') THEN
    RAISE EXCEPTION 'At least one active owner is required' USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER team_last_owner_role
  AFTER UPDATE OR DELETE ON user_roles DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION prevent_last_active_owner();
CREATE CONSTRAINT TRIGGER team_last_owner_user
  AFTER UPDATE OR DELETE ON app_users DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION prevent_last_active_owner();

-- Administrative events contain identifiers and bounded metadata, never
-- access codes, message bodies, credentials, or property lists.
CREATE TABLE team_admin_audit (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_uid TEXT NOT NULL REFERENCES app_users(firebase_uid),
  target_uid TEXT NOT NULL REFERENCES app_users(firebase_uid),
  action TEXT NOT NULL CHECK(action = 'member.set_access'),
  role TEXT NOT NULL CHECK(role IN ('owner','manager','operator','viewer')),
  active BOOLEAN NOT NULL,
  property_count INTEGER NOT NULL CHECK(property_count BETWEEN 0 AND 50),
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE FUNCTION reject_team_audit_mutation() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Administrative audit is immutable' USING ERRCODE = '23514'; END $$;
CREATE TRIGGER team_admin_audit_immutable BEFORE UPDATE OR DELETE ON team_admin_audit
  FOR EACH ROW EXECUTE FUNCTION reject_team_audit_mutation();
CREATE TRIGGER team_admin_audit_no_truncate BEFORE TRUNCATE ON team_admin_audit
  FOR EACH STATEMENT EXECUTE FUNCTION reject_team_audit_mutation();
