"""Property authorization and freshness of explicitly reviewed links."""
import os,unittest,uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from backend.api.reviewed_links import make_reviewed_links_router
from backend.api.repository import Repository,Forbidden
from backend.storage.schema import apply_migrations
DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')
@unittest.skipUnless(DSN,'isolated PostgreSQL required')
class ReviewedLinkApiTests(unittest.TestCase):
 def test_scoped_links_disappear_when_observations_change(self):
  url=urlsplit(DSN)
  if url.hostname not in ('127.0.0.1','localhost','::1') or not url.path.lstrip('/').startswith('padsplit_stage'):self.fail('Loopback staging DB required')
  schema='link_api_'+uuid.uuid4().hex[:12]
  with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  def cleanup():
   with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
  self.addCleanup(cleanup);dsn=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
  with psycopg.connect(dsn) as db:
   apply_migrations(db)
   for uid,role in [('owner','owner'),('scoped','viewer'),('other','viewer')]:db.execute('INSERT INTO app_users(firebase_uid) VALUES (%s)',(uid,));db.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)',(uid,role))
   db.execute("INSERT INTO properties(id,name) VALUES ('a','Fixture A'),('b','Fixture B')");db.execute("INSERT INTO property_access(firebase_uid,property_id) VALUES ('scoped','a')")
   db.execute("INSERT INTO rooms(id,property_id,label) VALUES ('room-a','a','1'),('room-b','b','1')")
   obs=db.execute("INSERT INTO occupancy_observations(room_id,observed_at,source_payload,source_generation) VALUES ('room-a','2026-01-01','{}','fixture') RETURNING id").fetchone()[0]
   db.execute("INSERT INTO padsplit_task_observations(id,task_key,source_id,property_id,bucket,data,observed_at,source_generation) VALUES ('obs-a','task-a','123','a','Open','{}','2026-01-01','fixture')")
   db.execute("INSERT INTO reviewed_room_task_links(task_observation_id,occupancy_observation_id,property_id,source_generation) VALUES ('obs-a',%s,'a','fixture')",(obs,))
  app=FastAPI();repo=Repository(dsn)
  def actor(authorization:str=Header(default='')):
   uid=authorization.removeprefix('Bearer ')
   if uid not in ('owner','scoped','other'):raise HTTPException(401)
   return {'uid':uid}
  def run(fn):
   try:return fn()
   except Forbidden:raise HTTPException(403) from None
  app.include_router(make_reviewed_links_router(actor_dependency=actor,repository_provider=lambda:repo,run=run))
  h=lambda uid:{'Authorization':'Bearer '+uid}
  with TestClient(app) as client:
   self.assertEqual(client.get('/v1/room-task-links').status_code,401)
   response=client.get('/v1/room-task-links',headers=h('scoped'));self.assertEqual(response.status_code,200)
   expected=[{'room_id':'room-a','task_key':'task-a','occupancy_observation_id':str(obs),'task_observation_id':'obs-a'}]
   self.assertEqual(response.json()['items'],expected);self.assertEqual(response.json()['coverage'],'reviewed_links_only')
   self.assertEqual(client.get('/v1/room-task-links',headers=h('other')).json()['items'],[])
   self.assertEqual(client.get('/v1/room-task-links?property_id=b',headers=h('scoped')).status_code,403)
   self.assertEqual(client.post('/v1/room-task-links',headers=h('owner'),json={}).status_code,405)
   role='links_role_'+uuid.uuid4().hex[:10]
   with psycopg.connect(dsn,autocommit=True) as db:
    db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
    def cleanup_role():
     with psycopg.connect(DSN,autocommit=True) as admin:
      admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)));admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
    self.addCleanup(cleanup_role)
    db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
    for grant in ['GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}','GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}','GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}','GRANT SELECT (id,property_id) ON rooms TO {}','GRANT SELECT (id,room_id,observed_at,source_payload,source_generation) ON occupancy_observations TO {}','GRANT SELECT (id,task_key,property_id,source_generation,observed_at) ON padsplit_task_observations TO {}','GRANT SELECT (task_observation_id,occupancy_observation_id,property_id,source_generation) ON reviewed_room_task_links TO {}']:
     db.execute(sql.SQL(grant).format(sql.Identifier(role)))
   class Restricted(Repository):
    def connect(self):
     db=super().connect();db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));return db
   limited_app=FastAPI();limited_app.include_router(make_reviewed_links_router(actor_dependency=actor,repository_provider=lambda:Restricted(dsn),run=run))
   with TestClient(limited_app) as limited:
    self.assertEqual(limited.get('/v1/room-task-links',headers=h('scoped')).json()['items'],expected)
    self.assertEqual(limited.get('/v1/room-task-links',headers=h('other')).json()['items'],[])
   with Restricted(dsn).connect() as restricted:
    with self.assertRaises(psycopg.errors.InsufficientPrivilege):restricted.execute("DELETE FROM reviewed_room_task_links")
   with psycopg.connect(dsn) as db:db.execute("UPDATE padsplit_task_observations SET property_id='b' WHERE id='obs-a'")
   self.assertEqual(client.get('/v1/room-task-links',headers=h('scoped')).json()['items'],[])
   with psycopg.connect(dsn) as db:db.execute("UPDATE padsplit_task_observations SET property_id='a',source_generation='different' WHERE id='obs-a'")
   self.assertEqual(client.get('/v1/room-task-links',headers=h('scoped')).json()['items'],[])
   with psycopg.connect(dsn) as db:db.execute("UPDATE padsplit_task_observations SET source_generation='fixture' WHERE id='obs-a'")
   with psycopg.connect(dsn) as db:db.execute("INSERT INTO occupancy_observations(room_id,observed_at,source_payload,source_generation) VALUES ('room-a','2026-01-02','{}','new')")
   self.assertEqual(client.get('/v1/room-task-links',headers=h('scoped')).json()['items'],[])
