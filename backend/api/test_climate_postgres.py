"""Synthetic staging-only climate scope and least-privilege checks."""
import os,unittest,uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi import FastAPI,Header,HTTPException
from fastapi.testclient import TestClient
from backend.api.climate import make_climate_router
from backend.api.repository import Repository,Forbidden
from backend.storage.schema import apply_migrations

DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')
@unittest.skipUnless(DSN,'isolated PostgreSQL required')
class ClimateApiTests(unittest.TestCase):
 def test_scope_latest_and_denied_mutation(self):
  p=urlsplit(DSN)
  if p.hostname not in ('127.0.0.1','localhost','::1') or not p.path.lstrip('/').startswith('padsplit_stage'):self.fail('Staging required')
  schema='climate_test_'+uuid.uuid4().hex[:12]
  with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  self.addCleanup(lambda:self.drop_schema(schema))
  dsn=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
  with psycopg.connect(dsn) as db:
   apply_migrations(db)
   for uid,role in [('owner','owner'),('viewer','viewer'),('empty','viewer')]:
    db.execute('INSERT INTO app_users(firebase_uid) VALUES (%s)',(uid,));db.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)',(uid,role))
   db.execute("INSERT INTO properties(id,name) VALUES ('a','Example A'),('b','Example B')")
   db.execute("INSERT INTO property_access(firebase_uid,property_id) VALUES ('viewer','a')")
   db.execute("INSERT INTO climate_observations(id,device_key,property_id,observed_at,data,source_fingerprint) VALUES ('a1','device-a','a','2026-01-01','{\"temp\":0}',repeat('a',64)),('a2','device-a','a','2026-01-02','{\"temp\":1}',repeat('b',64)),('b1','device-b','b','2026-01-02','{\"temp\":2}',repeat('b',64))")
  repo=Repository(dsn);app=FastAPI()
  def actor(authorization:str=Header(default='')):
   uid=authorization.removeprefix('Bearer ')
   if uid not in ('owner','viewer','empty'):raise HTTPException(401)
   return {'uid':uid}
  def run(fn):
   try:return fn()
   except Forbidden:raise HTTPException(403) from None
  app.include_router(make_climate_router(actor_dependency=actor,repository_provider=lambda:repo,run=run))
  with TestClient(app) as client:
   h=lambda uid:{'Authorization':'Bearer '+uid}
   self.assertEqual(client.get('/v1/climate').status_code,401)
   result=client.get('/v1/climate',headers=h('viewer'));self.assertEqual(result.status_code,200);self.assertEqual(len(result.json()['items']),1);self.assertEqual(result.json()['items'][0]['data']['temp'],1)
   self.assertEqual(client.get('/v1/climate?property_id=b',headers=h('viewer')).status_code,403)
   self.assertEqual(client.get('/v1/climate',headers=h('empty')).json()['items'],[])
   self.assertEqual(len(client.get('/v1/climate',headers=h('owner')).json()['items']),2)
   self.assertEqual(client.post('/v1/climate',headers=h('owner')).status_code,405)
  role='climate_role_'+uuid.uuid4().hex[:12]
  with psycopg.connect(dsn,autocommit=True) as db:
   db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
   self.addCleanup(lambda:self.drop_role(role))
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   for statement in ['GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}','GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}','GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}','GRANT SELECT (id,name) ON properties TO {}','GRANT SELECT (id,device_key,property_id,observed_at,data) ON climate_observations TO {}']:
    db.execute(sql.SQL(statement).format(sql.Identifier(role)))
  class Restricted(Repository):
   def connect(self):
    db=super().connect();db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));return db
  limited=Restricted(dsn);app=FastAPI();app.include_router(make_climate_router(actor_dependency=actor,repository_provider=lambda:limited,run=run))
  with TestClient(app) as client:
   self.assertEqual(client.get('/v1/climate',headers=h('viewer')).status_code,200)
   self.assertEqual(client.get('/v1/climate?property_id=b',headers=h('viewer')).status_code,403)
  with limited.connect() as db:
   with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute("UPDATE climate_observations SET property_id='b' WHERE id='a1'")
 def drop_schema(self,schema):
  with psycopg.connect(DSN,autocommit=True) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
 def drop_role(self,role):
  with psycopg.connect(DSN,autocommit=True) as db:
   db.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)));db.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
