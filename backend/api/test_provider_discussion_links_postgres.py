"""Property-scoped reviewed provider discussion links using fictional records."""
import os,unittest,uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from backend.api.provider_discussion_links import make_provider_discussion_router
from backend.api.repository import Repository,Forbidden
from backend.storage.schema import apply_migrations
DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')
@unittest.skipUnless(DSN,'isolated staging database required')
class ProviderDiscussionApiTests(unittest.TestCase):
 def test_scope_and_freshness(self):
  url=urlsplit(DSN)
  if url.hostname not in ('127.0.0.1','localhost','::1') or not url.path.lstrip('/').startswith('padsplit_stage'):self.fail('Loopback staging database required')
  schema='provider_discussion_'+uuid.uuid4().hex[:10]
  with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  def cleanup():
   with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
  self.addCleanup(cleanup);dsn=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
  with psycopg.connect(dsn) as db:
   apply_migrations(db)
   for uid,role in [('owner','owner'),('scope','viewer'),('other','viewer')]:
    db.execute('INSERT INTO app_users(firebase_uid) VALUES (%s)',(uid,));db.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)',(uid,role))
   db.execute("INSERT INTO properties(id,name) VALUES ('a','Fixture A'),('b','Fixture B')")
   db.execute("INSERT INTO property_access(firebase_uid,property_id) VALUES ('scope','a')")
   db.execute("INSERT INTO tasks(id,property_id,title,source_path) VALUES ('target-a','a','Example','manual_tasks/example')")
   db.execute("INSERT INTO padsplit_task_observations(id,task_key,source_id,property_id,bucket,data,observed_at,source_generation) VALUES ('obs-a','key-a','123','a','Open','{}','2026-01-01','fixture')")
   db.execute("INSERT INTO reviewed_provider_discussion_links(task_observation_id,internal_task_id,property_id,source_generation,expected_task_source_path) VALUES ('obs-a','target-a','a','fixture','manual_tasks/example')")
  app=FastAPI();repo=Repository(dsn)
  def actor(authorization:str=Header(default='')):
   uid=authorization.removeprefix('Bearer ')
   if uid not in ('owner','scope','other'):raise HTTPException(401)
   return {'uid':uid}
  def run(fn):
   try:return fn()
   except Forbidden:raise HTTPException(403) from None
  app.include_router(make_provider_discussion_router(actor_dependency=actor,repository_provider=lambda:repo,run=run))
  h=lambda uid:{'Authorization':'Bearer '+uid}
  with TestClient(app) as client:
   self.assertEqual(client.get('/v1/provider-discussion-links').status_code,401)
   result=client.get('/v1/provider-discussion-links',headers=h('scope'))
   self.assertEqual(result.status_code,200);self.assertEqual(result.json()['items'],[{'task_observation_id':'obs-a','task_key':'key-a','internal_task_id':'target-a','property_id':'a'}]);self.assertEqual(result.json()['coverage'],'reviewed_links_only')
   self.assertEqual(client.get('/v1/provider-discussion-links',headers=h('other')).json()['items'],[])
   self.assertEqual(client.get('/v1/provider-discussion-links?property_id=b',headers=h('scope')).status_code,403)
   self.assertEqual(client.post('/v1/provider-discussion-links',headers=h('owner'),json={}).status_code,405)
   role='provider_links_'+uuid.uuid4().hex[:10]
   with psycopg.connect(dsn,autocommit=True) as db:
    db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    def cleanup_role():
     with psycopg.connect(DSN,autocommit=True) as admin:
      admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)));admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
    self.addCleanup(cleanup_role)
    db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    grants=['GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}','GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}','GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}','GRANT SELECT (id,task_key,property_id,source_generation,observed_at) ON padsplit_task_observations TO {}','GRANT SELECT (id,property_id,source_path) ON tasks TO {}','GRANT SELECT (task_observation_id,internal_task_id,property_id,source_generation,expected_task_source_path) ON reviewed_provider_discussion_links TO {}']
    for grant in grants:db.execute(sql.SQL(grant).format(sql.Identifier(role)))
   class Restricted(Repository):
    def connect(self):
     db=super().connect();db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));return db
   limited=FastAPI();limited.include_router(make_provider_discussion_router(actor_dependency=actor,repository_provider=lambda:Restricted(dsn),run=run))
   with TestClient(limited) as scoped:
    self.assertEqual(scoped.get('/v1/provider-discussion-links',headers=h('scope')).json()['items'],result.json()['items'])
    self.assertEqual(scoped.get('/v1/provider-discussion-links',headers=h('other')).json()['items'],[])
   with Restricted(dsn).connect() as db:
    with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute('DELETE FROM reviewed_provider_discussion_links')
   with psycopg.connect(dsn) as db:db.execute("UPDATE tasks SET property_id='b' WHERE id='target-a'")
   self.assertEqual(client.get('/v1/provider-discussion-links',headers=h('scope')).json()['items'],[])
   with psycopg.connect(dsn) as db:db.execute("UPDATE tasks SET property_id='a',source_path='manual_tasks/changed' WHERE id='target-a'")
   self.assertEqual(client.get('/v1/provider-discussion-links',headers=h('scope')).json()['items'],[])
   with psycopg.connect(dsn) as db:db.execute("UPDATE tasks SET source_path='manual_tasks/example' WHERE id='target-a'");db.execute("INSERT INTO padsplit_task_observations(id,task_key,source_id,property_id,bucket,data,observed_at,source_generation) VALUES ('obs-new','key-a','123','a','Open','{}','2026-01-02','new')")
   self.assertEqual(client.get('/v1/provider-discussion-links',headers=h('scope')).json()['items'],[])
