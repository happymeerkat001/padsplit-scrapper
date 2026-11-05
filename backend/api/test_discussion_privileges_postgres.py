"""Verify discussion deletion with a restricted, isolated PostgreSQL role."""
import os
import unittest
import uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from backend.api.repository import Repository, Forbidden, NotFound
from backend.storage.schema import apply_migrations

DSN = os.getenv('PADSPLIT_API_TEST_DATABASE_URL', '')

@unittest.skipUnless(DSN, 'isolated staging PostgreSQL required')
class DiscussionPrivilegeTests(unittest.TestCase):
    def test_scoped_tombstone_does_not_grant_body_writes(self):
        parsed = urlsplit(DSN)
        if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or not parsed.path.lstrip('/').startswith('padsplit_stage'):
            self.fail('isolated staging database required')
        suffix=uuid.uuid4().hex[:12]; schema='disc_test_'+suffix; role='disc_role_'+suffix
        with psycopg.connect(DSN,autocommit=True) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        def cleanup():
            with psycopg.connect(DSN,autocommit=True) as db:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
                db.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
        self.addCleanup(cleanup)
        scoped=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
        with psycopg.connect(scoped) as db:
            apply_migrations(db)
            db.execute("INSERT INTO app_users(firebase_uid) VALUES ('writer'),('reader')")
            db.execute("INSERT INTO user_roles(firebase_uid,role) VALUES ('writer','operator'),('reader','viewer')")
            db.execute("INSERT INTO properties(id,name) VALUES ('a','Example A'),('b','Example B')")
            db.execute("INSERT INTO property_access(firebase_uid,property_id) VALUES ('writer','a'),('reader','a')")
            db.execute("INSERT INTO tasks(id,property_id,title,status) VALUES ('task-a','a','Example','open'),('task-b','b','Example','open')")
            db.execute("INSERT INTO task_discussions(id,task_id,body) VALUES ('event-a','task-a','fictional text'),('event-b','task-b','fictional text')")
        with psycopg.connect(scoped,autocommit=True) as db:
            db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
            grants=['GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}',
                    'GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}',
                    'GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}',
                    'GRANT SELECT (id,property_id,version), UPDATE (status) ON tasks TO {}',
                    'GRANT SELECT (id,task_id,deleted_at), UPDATE (deleted_at) ON task_discussions TO {}',
                    'GRANT INSERT (actor_uid,action,target_type,target_id,redacted_metadata) ON audit_events TO {}']
            for grant in grants:db.execute(sql.SQL(grant).format(sql.Identifier(role)))
        class Restricted(Repository):
            def connect(self):
                conn=super().connect();conn.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));return conn
        repo=Restricted(scoped)
        with self.assertRaises(Forbidden):repo.discussion_delete('reader','task-a','event-a')
        with self.assertRaises(Forbidden):repo.discussion_delete('writer','task-b','event-b')
        self.assertEqual(repo.discussion_delete('writer','task-a','event-a'),{'ok':True})
        with self.assertRaises(NotFound):repo.discussion_delete('writer','task-a','event-a')
        with repo.connect() as db:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute("UPDATE task_discussions SET body='changed' WHERE id='event-a'")
        with psycopg.connect(scoped) as db:
            body,deleted=db.execute("SELECT body,deleted_at FROM task_discussions WHERE id='event-a'").fetchone()
            self.assertEqual(body,'fictional text');self.assertIsNotNone(deleted)

if __name__=='__main__':unittest.main()
