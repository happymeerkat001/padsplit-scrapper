"""Synthetic-only schema readiness checks; never target a remote database."""
import os,unittest,uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Repository
from backend.storage.schema import apply_migrations

DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')

@unittest.skipUnless(DSN,'Disposable local staging database required')
class ReadinessSchemaTests(unittest.TestCase):
 def test_missing_table_column_and_restricted_role(self):
  parsed=urlsplit(DSN)
  if parsed.hostname not in ('127.0.0.1','localhost','::1') or not parsed.path.lstrip('/').startswith('padsplit_stage'):
   self.fail('Synthetic local staging database required')
  name='ready_test_'+uuid.uuid4().hex[:12]; role='ready_role_'+uuid.uuid4().hex[:12]
  with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
  def cleanup():
   with psycopg.connect(DSN,autocommit=True) as db:
    db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
    db.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
    db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
  scoped=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+name)
  with psycopg.connect(scoped,autocommit=True) as db:
   db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(name),sql.Identifier(role)))
  self.addCleanup(cleanup)
  repository=Repository(scoped)
  app=create_app(repository=repository,verify_token=lambda _:None,allowed_origins=['http://localhost:9999'])
  with TestClient(app) as client:self.assertEqual(client.get('/health/ready').status_code,503)
  with psycopg.connect(scoped) as db:apply_migrations(db)
  self.assertTrue(repository.ready())
  class Restricted(Repository):
   def connect(self):
    conn=super().connect();conn.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));return conn
  # Catalog inspection needs schema USAGE, not application data access.
  self.assertTrue(Restricted(scoped).ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE messages DROP COLUMN source_attachments')
  self.assertFalse(repository.ready())
  with TestClient(app) as client:self.assertEqual(client.get('/health/ready').status_code,503)
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE messages ADD COLUMN source_attachments JSONB')
  self.assertTrue(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE conversations DROP COLUMN source_unread')
  self.assertFalse(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE conversations ADD COLUMN source_unread BOOLEAN')
  self.assertTrue(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE conversations DROP COLUMN source_display')
  self.assertFalse(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE conversations ADD COLUMN source_display JSONB')
  self.assertTrue(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE climate_observations DROP COLUMN data')
  self.assertFalse(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE climate_observations ADD COLUMN data JSONB')
  self.assertTrue(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE task_discussions DROP COLUMN deleted_at')
  self.assertFalse(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE task_discussions ADD COLUMN deleted_at TIMESTAMPTZ')
  self.assertTrue(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE reviewed_provider_discussion_links DROP COLUMN expected_task_source_path')
  self.assertFalse(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE reviewed_provider_discussion_links ADD COLUMN expected_task_source_path TEXT')
  self.assertTrue(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE tasks DROP COLUMN source_path')
  self.assertFalse(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('ALTER TABLE tasks ADD COLUMN source_path TEXT')
  self.assertTrue(repository.ready())
  with psycopg.connect(scoped) as db:db.execute('DROP TABLE reviewed_room_task_links')
  self.assertFalse(repository.ready())
