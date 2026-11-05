"""Synthetic isolated PostgreSQL coverage of report permissions and precision."""
import os,unittest,uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Repository
from backend.storage.schema import apply_migrations

DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')
@unittest.skipUnless(DSN,'isolated PostgreSQL test database not supplied')
class ReportPostgresTests(unittest.TestCase):
 def test_reports_owner_only_and_decimal_text_preserved(self):
  u=urlsplit(DSN)
  if u.hostname not in ('127.0.0.1','localhost','::1') or not u.path.lstrip('/').startswith('padsplit_stage_'):self.fail('isolated DB required')
  schema='report_test_'+uuid.uuid4().hex[:12]
  with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  def cleanup():
   with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
  self.addCleanup(cleanup)
  dsn=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
  with psycopg.connect(dsn) as db:
   apply_migrations(db)
   for uid,role in (('owner','owner'),('viewer','viewer')):
    db.execute('INSERT INTO app_users(firebase_uid) VALUES (%s)',(uid,))
    db.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)',(uid,role))
   db.execute("INSERT INTO statistics(id,data,observed_at,source_path) VALUES ('latest',%s::jsonb,now(),'stats/latest')",('{"amount":12345678901234567890.123456789}',))
   db.execute("INSERT INTO app_users(firebase_uid) VALUES ('reporter')")
   db.execute("INSERT INTO user_roles(firebase_uid,role) VALUES ('reporter','viewer')")
   db.execute("INSERT INTO report_access(firebase_uid) VALUES ('reporter')")
  c=TestClient(create_app(repository=Repository(dsn),verify_token=lambda x:x,allowed_origins=['https://example.invalid']))
  h=lambda uid:{'Authorization':'Bearer '+uid}
  self.assertEqual(c.get('/v1/reports/latest',headers=h('viewer')).status_code,403)
  self.assertEqual(c.get('/v1/report-access',headers=h('reporter')).json()['allowed'],True)
  self.assertEqual(c.get('/v1/report-access',headers=h('viewer')).json()['allowed'],False)
  self.assertEqual(c.get('/v1/reports/latest',headers=h('reporter')).status_code,200)
  self.assertEqual(c.get('/v1/vendors',headers=h('reporter')).status_code,403)
  self.assertEqual(c.get('/v1/codes/schema',headers=h('reporter')).status_code,403)
  self.assertEqual(c.get('/v1/notes/stats',headers=h('reporter')).status_code,403)
  with psycopg.connect(dsn) as db: db.execute("DELETE FROM report_access WHERE firebase_uid='reporter'")
  self.assertEqual(c.get('/v1/reports/latest',headers=h('reporter')).status_code,403)
  with psycopg.connect(dsn) as db:db.execute("INSERT INTO report_access(firebase_uid) VALUES ('reporter')")
  role='report_role_'+uuid.uuid4().hex[:10]
  with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
  def cleanup_role():
   with psycopg.connect(DSN,autocommit=True) as db:
    db.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
    db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
  self.addCleanup(cleanup_role)
  with psycopg.connect(dsn,autocommit=True) as db:
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   for statement in ['GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}','GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}','GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}','GRANT SELECT (firebase_uid), UPDATE (auth_lock_guard) ON report_access TO {}','GRANT SELECT (id,data,observed_at) ON statistics TO {}']:
    db.execute(sql.SQL(statement).format(sql.Identifier(role)))
  limited=Repository(psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema+' -c role='+role))
  self.assertEqual(limited.report('reporter','latest')['id'],'latest')
  limited_client=TestClient(create_app(repository=limited,verify_token=lambda x:x,allowed_origins=['https://example.invalid']))
  self.assertEqual(limited_client.get('/v1/report-access',headers=h('reporter')).json()['allowed'],True)
  self.assertEqual(limited_client.get('/v1/reports/latest',headers=h('reporter')).status_code,200)
  self.assertEqual(limited_client.get('/v1/reports/latest',headers=h('viewer')).status_code,403)
  with limited.connect() as db:
   with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute("INSERT INTO report_access(firebase_uid) VALUES ('viewer')")
  with limited.connect() as db:
   self.assertTrue(Repository._can_report(db,'reporter'))
   with psycopg.connect(dsn) as other:
    other.execute("SET LOCAL lock_timeout='200ms'")
    with self.assertRaises(psycopg.errors.LockNotAvailable):other.execute("DELETE FROM report_access WHERE firebase_uid='reporter'")
  response=c.get('/v1/reports/latest',headers=h('owner'))
  self.assertEqual(response.status_code,200)
  self.assertIn('12345678901234567890.123456789',response.json()['json'])
  self.assertEqual(c.get('/v1/reports/monthly_history',headers=h('owner')).status_code,404)
