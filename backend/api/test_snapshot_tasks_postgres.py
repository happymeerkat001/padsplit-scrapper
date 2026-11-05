"""Synthetic scoped provider task API tests."""
import os,unittest,uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from backend.api.snapshot_tasks import make_snapshot_tasks_router
from backend.api.repository import Repository,Forbidden
from backend.storage.schema import apply_migrations
DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')
@unittest.skipUnless(DSN,'isolated PostgreSQL required')
class SnapshotTaskTests(unittest.TestCase):
 def test_scope_and_no_raw_code_fields(self):
  url=urlsplit(DSN)
  if url.hostname not in ('127.0.0.1','localhost','::1') or not url.path.lstrip('/').startswith('padsplit_stage'):self.fail('Loopback staging DB required')
  schema='task_source_'+uuid.uuid4().hex[:12]
  with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  def cleanup():
   with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
  self.addCleanup(cleanup);dsn=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
  with psycopg.connect(dsn) as db:
   apply_migrations(db)
   for uid,role in [('owner','owner'),('scoped','viewer'),('other','viewer')]:db.execute('INSERT INTO app_users(firebase_uid) VALUES (%s)',(uid,));db.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)',(uid,role))
   db.execute("INSERT INTO properties(id,name) VALUES ('a','Fixture A'),('b','Fixture B')");db.execute("INSERT INTO property_access(firebase_uid,property_id) VALUES ('scoped','a')")
   db.execute("INSERT INTO padsplit_task_observations(id,task_key,source_id,property_id,bucket,data,observed_at,source_generation) VALUES ('old','task-a','123','a','Open','{\"details\":\"old\"}','2026-01-01','fixture'),('new','task-a','123','a','Open','{\"details\":\"new\",\"room_code\":\"fixture-secret\"}','2026-01-02','fixture'),('private','task-b','456','b','Open','{\"details\":\"private\"}','2026-01-02','fixture'),('owner-only','task-c','789',NULL,'Open','{\"details\":\"owner only\"}','2026-01-02','fixture')")
  repo=Repository(dsn);app=FastAPI()
  def actor(authorization:str=Header(default='')):
   uid=authorization.removeprefix('Bearer ')
   if uid not in ('owner','scoped','other'):raise HTTPException(401)
   return {'uid':uid}
  def run(fn):
   try:return fn()
   except Forbidden:raise HTTPException(403) from None
  app.include_router(make_snapshot_tasks_router(actor_dependency=actor,repository_provider=lambda:repo,run=run))
  h=lambda uid:{'Authorization':'Bearer '+uid}
  with TestClient(app) as client:
   self.assertEqual(client.get('/v1/provider-tasks').status_code,401)
   result=client.get('/v1/provider-tasks',headers=h('scoped'));self.assertEqual(result.status_code,200);self.assertEqual(len(result.json()['items']),1);self.assertEqual(result.json()['items'][0]['data']['details'],'new')
   self.assertEqual(result.json()['items'][0]['observation_id'],'new')
   self.assertNotIn('fixture-secret',result.text);self.assertNotIn('room_code',result.text)
   self.assertEqual(client.get('/v1/provider-tasks?property_id=b',headers=h('scoped')).status_code,403)
   self.assertEqual(len(client.get('/v1/provider-tasks',headers=h('owner')).json()['items']),3)
   self.assertEqual(client.get('/v1/provider-tasks',headers=h('other')).json()['items'],[])
   self.assertEqual(client.post('/v1/provider-tasks',headers=h('owner'),json={}).status_code,405)
  role='task_src_role_'+uuid.uuid4().hex[:10]
  with psycopg.connect(dsn,autocommit=True) as db:
   db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
   def cleanup_role():
    with psycopg.connect(DSN,autocommit=True) as admin:
     admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)));admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
   self.addCleanup(cleanup_role)
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   for grant in ['GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}','GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}','GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}','GRANT SELECT (id,task_key,source_id,property_id,bucket,data,observed_at) ON padsplit_task_observations TO {}']:
    db.execute(sql.SQL(grant).format(sql.Identifier(role)))
  class Restricted(Repository):
   def connect(self):
    db=super().connect();db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));return db
  restricted=Restricted(dsn);limited_app=FastAPI();limited_app.include_router(make_snapshot_tasks_router(actor_dependency=actor,repository_provider=lambda:restricted,run=run))
  with TestClient(limited_app) as client:
   self.assertEqual(client.get('/v1/provider-tasks',headers=h('scoped')).status_code,200)
   self.assertEqual(client.get('/v1/provider-tasks?property_id=b',headers=h('scoped')).status_code,403)
  with restricted.connect() as db:
   with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute("UPDATE padsplit_task_observations SET property_id='b' WHERE id='new'")
