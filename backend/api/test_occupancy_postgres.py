"""Synthetic occupancy API authorization checks in isolated PostgreSQL."""
import os, unittest, uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from backend.api.occupancy import make_occupancy_router
from backend.api.repository import Repository, Forbidden, NotFound
from backend.storage.schema import apply_migrations

DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')
@unittest.skipUnless(DSN,'Set an isolated staging database')
class OccupancyApiTests(unittest.TestCase):
 def test_property_scope_latest_and_denied_filters(self):
  parsed=urlsplit(DSN)
  if parsed.hostname not in ('127.0.0.1','localhost','::1') or not parsed.path.lstrip('/').startswith('padsplit_stage'):self.fail('Synthetic loopback staging database required')
  schema='occupancy_test_'+uuid.uuid4().hex[:12]
  with psycopg.connect(DSN,autocommit=True) as conn:conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  def cleanup():
   with psycopg.connect(DSN,autocommit=True) as conn:conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
  self.addCleanup(cleanup)
  dsn=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
  with psycopg.connect(dsn) as db:
   apply_migrations(db)
   for uid,role in [('owner','owner'),('scoped','viewer'),('empty','viewer')]:
    db.execute('INSERT INTO app_users(firebase_uid) VALUES (%s)',(uid,));db.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)',(uid,role))
   db.execute("INSERT INTO properties(id,name) VALUES ('a','Fixture A'),('b','Fixture B')")
   db.execute("INSERT INTO property_access(firebase_uid,property_id) VALUES ('scoped','a')")
   db.execute("INSERT INTO rooms(id,property_id,label) VALUES ('room-a','a','1'),('room-b','b','1')")
   db.execute("INSERT INTO occupancy_observations(room_id,observed_at,occupant_present,rent_ready,source_payload) VALUES ('room-a','2026-01-01',false,true,'{\"state\":\"old\"}'),('room-a','2026-01-02',true,false,'{\"state\":\"new\"}'),('room-b','2026-01-02',true,false,'{\"state\":\"private\"}')")
  repo=Repository(dsn);app=FastAPI()
  def actor(authorization: str=Header(default='')):
   uid=authorization.removeprefix('Bearer ')
   if uid not in ('owner','scoped','empty'):raise HTTPException(401)
   return {'uid':uid}
  def run(fn):
   try:return fn()
   except Forbidden:raise HTTPException(403) from None
   except NotFound:raise HTTPException(404) from None
  app.include_router(make_occupancy_router(actor_dependency=actor,repository_provider=lambda:repo,run=run))
  with TestClient(app) as client:
   h=lambda uid:{'Authorization':'Bearer '+uid}
   self.assertEqual(client.get('/v1/occupancy').status_code,401)
   scoped=client.get('/v1/occupancy',headers=h('scoped'));self.assertEqual(scoped.status_code,200)
   self.assertEqual(len(scoped.json()['items']),1);self.assertEqual(scoped.json()['items'][0]['data']['state'],'new')
   self.assertRegex(scoped.json()['items'][0]['observation_id'],r'^\d+$')
   self.assertEqual(client.get('/v1/occupancy?property_id=b',headers=h('scoped')).status_code,403)
   self.assertEqual(client.get('/v1/occupancy',headers=h('empty')).json()['items'],[])
   self.assertEqual(len(client.get('/v1/occupancy',headers=h('owner')).json()['items']),2)
   self.assertEqual(len(client.get('/v1/occupancy?limit=1',headers=h('owner')).json()['items']),1)
  role='occupancy_role_'+uuid.uuid4().hex[:10]
  with psycopg.connect(dsn,autocommit=True) as db:
   db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
   def cleanup_role():
    with psycopg.connect(DSN,autocommit=True) as admin:
     admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)));admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
   self.addCleanup(cleanup_role)
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   for statement in ['GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}','GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}','GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}','GRANT SELECT (id,property_id,label) ON rooms TO {}','GRANT SELECT (id,name) ON properties TO {}','GRANT SELECT (id,room_id,observed_at,source_payload) ON occupancy_observations TO {}']:
    db.execute(sql.SQL(statement).format(sql.Identifier(role)))
  class Restricted(Repository):
   def connect(self):
    db=super().connect();db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));return db
  restricted=Restricted(dsn);scoped_app=FastAPI()
  scoped_app.include_router(make_occupancy_router(actor_dependency=actor,repository_provider=lambda:restricted,run=run))
  with TestClient(scoped_app) as client:
   self.assertEqual(client.get('/v1/occupancy',headers=h('scoped')).status_code,200)
   self.assertEqual(client.get('/v1/occupancy?property_id=b',headers=h('scoped')).status_code,403)
  with restricted.connect() as db:
   with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute("UPDATE rooms SET property_id='b' WHERE id='room-a'")
