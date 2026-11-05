"""Real PostgreSQL privilege and concurrent revocation checks (synthetic data)."""
import os
import secrets
import threading
import time
import unittest
import uuid
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from backend.storage.schema import apply_migrations
from backend.storage.migrate import staging_connect

DSN=os.environ.get('DATABASE_URL')

@unittest.skipUnless(DSN,'isolated staging PostgreSQL not supplied')
class PermissionLockTests(unittest.TestCase):
    def setUp(self):
        self.role='padsplit_lock_'+uuid.uuid4().hex[:12]
        self.password=secrets.token_urlsafe(32)
        self.admin=staging_connect();apply_migrations(self.admin)
        with self.admin.transaction():
            self.admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(self.role),sql.Literal(self.password)))
            self.admin.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(self.role)))
            for table,columns in (('app_users','firebase_uid,active'),('user_roles','firebase_uid,role'),('property_access','firebase_uid,property_id')):
                self.admin.execute(sql.SQL('GRANT SELECT ({}) ON {} TO {}').format(sql.SQL(columns),sql.Identifier(table),sql.Identifier(self.role)))
            self.admin.execute(sql.SQL('GRANT SELECT ON tasks TO {}').format(sql.Identifier(self.role)))
            for table in ('app_users','user_roles','property_access'):
                self.admin.execute(sql.SQL('GRANT UPDATE (auth_lock_guard) ON {} TO {}').format(sql.Identifier(table),sql.Identifier(self.role)))
            self.admin.execute(sql.SQL('GRANT UPDATE (title) ON tasks TO {}').format(sql.Identifier(self.role)))
            self.admin.execute("INSERT INTO app_users(firebase_uid) VALUES ('fixture-operator')")
            self.admin.execute("INSERT INTO user_roles(firebase_uid,role) VALUES ('fixture-operator','operator')")
            self.admin.execute("INSERT INTO properties(id,name) VALUES ('fixture-property','Fixture')")
            self.admin.execute("INSERT INTO property_access(firebase_uid,property_id) VALUES ('fixture-operator','fixture-property')")
            self.admin.execute("INSERT INTO tasks(id,property_id,title) VALUES ('fixture-task','fixture-property','before')")
        self.api_dsn=make_conninfo(DSN,user=self.role,password=self.password)

    def tearDown(self):
        self.admin.rollback()
        with self.admin.transaction():
            self.admin.execute("DELETE FROM tasks WHERE id='fixture-task'")
            self.admin.execute("DELETE FROM property_access WHERE firebase_uid='fixture-operator'")
            self.admin.execute("DELETE FROM user_roles WHERE firebase_uid='fixture-operator'")
            self.admin.execute("DELETE FROM app_users WHERE firebase_uid='fixture-operator'")
            self.admin.execute("DELETE FROM properties WHERE id='fixture-property'")
            self.admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(self.role)))
            self.admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(self.role)))
        self.admin.close()

    def test_api_cannot_change_roles_scopes_or_active_status(self):
        with psycopg.connect(self.api_dsn) as api:
            for statement in ("UPDATE app_users SET active=false WHERE firebase_uid='fixture-operator'",
                              "UPDATE user_roles SET role='owner' WHERE firebase_uid='fixture-operator'",
                              "UPDATE property_access SET property_id='other' WHERE firebase_uid='fixture-operator'",
                              "DELETE FROM property_access WHERE firebase_uid='fixture-operator'",
                              "INSERT INTO user_roles(firebase_uid,role) VALUES ('other','owner')"):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with api.transaction():api.execute(statement)
            with self.assertRaises(psycopg.errors.CheckViolation):
                with api.transaction():api.execute("UPDATE user_roles SET auth_lock_guard=true WHERE firebase_uid='fixture-operator'")
            with api.transaction():
                api.execute("UPDATE user_roles SET auth_lock_guard=false WHERE firebase_uid='fixture-operator'")

    def test_revocation_waits_for_authorized_write_and_then_blocks_new_scope(self):
        locked=threading.Event();finish=threading.Event();revoking=threading.Event();failures=[];revoke_pid=[]
        def writer():
            try:
                with psycopg.connect(self.api_dsn) as api:
                    api.execute("SELECT u.firebase_uid,r.role FROM app_users u JOIN user_roles r ON u.firebase_uid=r.firebase_uid WHERE u.firebase_uid='fixture-operator' AND u.active=true FOR SHARE OF u,r")
                    scope=api.execute("SELECT property_id FROM property_access WHERE firebase_uid='fixture-operator' FOR SHARE").fetchall()
                    if scope!=[('fixture-property',)]:raise AssertionError('scope mismatch')
                    locked.set()
                    if not finish.wait(5):raise AssertionError('writer release timeout')
                    api.execute("UPDATE tasks SET title='authorized before revocation' WHERE id='fixture-task'")
            except Exception as e:failures.append(type(e).__name__);locked.set()
        def revoke():
            try:
                with staging_connect() as db:
                    revoke_pid.append(db.execute('select pg_backend_pid()').fetchone()[0]);revoking.set()
                    db.execute("DELETE FROM property_access WHERE firebase_uid='fixture-operator' AND property_id='fixture-property'")
            except Exception as e:failures.append(type(e).__name__);revoking.set()
        w=threading.Thread(target=writer);w.start();self.assertTrue(locked.wait(5));self.assertEqual(failures,[])
        r=threading.Thread(target=revoke);r.start();self.assertTrue(revoking.wait(5))
        try:
            deadline=time.monotonic()+5;blocked=False
            while time.monotonic()<deadline:
                status=self.admin.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s',(revoke_pid[0],)).fetchone();self.admin.rollback()
                if status and status[0]=='Lock':blocked=True;break
                time.sleep(.05)
            self.assertTrue(blocked,'revocation did not wait on permission lock')
        finally:
            finish.set();w.join(5);r.join(5)
        self.assertFalse(w.is_alive());self.assertFalse(r.is_alive());self.assertEqual(failures,[])
        with psycopg.connect(self.api_dsn) as api:
            self.assertEqual(api.execute("SELECT property_id FROM property_access WHERE firebase_uid='fixture-operator'").fetchall(),[])
            self.assertEqual(api.execute("SELECT title FROM tasks WHERE id='fixture-task'").fetchone()[0],'authorized before revocation')
