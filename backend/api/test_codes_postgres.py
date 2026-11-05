"""Encrypted codes integration test in a disposable schema of a local staging DB."""
import base64
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Repository

DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')

@unittest.skipUnless(DSN,'Set PADSPLIT_API_TEST_DATABASE_URL to an isolated local rehearsal DB')
class CodesPostgresTests(unittest.TestCase):
 def test_encryption_history_and_restore(self):
  parsed=urlsplit(DSN)
  if parsed.hostname not in ('127.0.0.1','localhost','::1') or not parsed.path.lstrip('/').startswith('padsplit_stage'):
   self.fail('Test requires an isolated local staging database')
  schema='api_codes_'+uuid.uuid4().hex[:12]
  with psycopg.connect(DSN,autocommit=True) as admin:admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  scoped=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
  try:
   with psycopg.connect(scoped) as db:
    root=Path(__file__).resolve().parent.parent/'storage'/'migrations'
    db.execute((root/'001_initial.sql').read_text());db.execute((root/'003_code_history_context.sql').read_text());db.execute((root/'007_private_codes_projection.sql').read_text())
    db.execute("INSERT INTO app_users(firebase_uid) VALUES ('fictional-owner'),('fictional-viewer')")
    db.execute("INSERT INTO user_roles(firebase_uid,role) VALUES ('fictional-owner','owner'),('fictional-viewer','viewer')")
    db.execute("INSERT INTO properties(id,name) VALUES ('sample-house','Example')")
   repo=Repository(scoped);client=TestClient(create_app(repository=repo,verify_token=lambda token:token,allowed_origins=['https://example.invalid']))
   owner={'Authorization':'Bearer fictional-owner'};viewer={'Authorization':'Bearer fictional-viewer'}
   key=base64.b64encode(b'A'*32).decode()
   with patch.dict(os.environ,{'PADSPLIT_CODES_KEY':key,'PADSPLIT_CODES_KEY_VERSION':'test-v1'},clear=False):
    path='/v1/properties/sample-house/codes'
    from backend.api.repository import Forbidden
    with self.assertRaises(Forbidden):repo.codes_schema('fictional-viewer')
    self.assertEqual(client.get('/v1/me',headers=viewer).status_code,200)
    self.assertEqual(client.get('/v1/codes/schema',headers=viewer).status_code,403)
    self.assertEqual(client.get('/v1/codes/schema',headers=owner).json(),{'properties':[],'versions':{}})
    self.assertEqual(client.get(path,headers=viewer).status_code,403)
    first={'fields':{'front':'fictional-secret-111'},'expected_version':0}
    self.assertEqual(client.put(path,headers=owner,json=first).json(),{'version':1})
    self.assertEqual(client.get('/v1/codes/schema',headers=owner).status_code,409)
    from backend.storage.secrets import encrypt_code_schema
    layout={'slug':'sample-house','address':'Example','sections':[{'label':'Main','fields':[{'key':'front','label':'Front','value':''}]}]}
    nonce,cipher=encrypt_code_schema(layout,property_id='sample-house',version=1,key_version='test-v1',key=b'A'*32)
    with psycopg.connect(scoped) as db:
     db.execute("INSERT INTO access_code_schemas(property_id,ciphertext,nonce,key_version,version,source_path) VALUES ('sample-house',%s,%s,'test-v1',1,'private_configuration/codes_schema')",(cipher,nonce))
    schema_response=client.get('/v1/codes/schema',headers=owner);self.assertEqual(schema_response.json(),{'properties':[layout],'versions':{'sample-house':1}});self.assertNotIn('fictional-secret',schema_response.text)
    orphan={'slug':'orphan-house','address':'Example orphan','sections':[{'label':'Main','fields':[]}]}
    nonce2,cipher2=encrypt_code_schema(orphan,property_id='orphan-house',version=1,key_version='test-v1',key=b'A'*32)
    with psycopg.connect(scoped) as db:
     db.execute("INSERT INTO properties(id,name) VALUES ('orphan-house','Example orphan')")
     db.execute("INSERT INTO access_code_schemas(property_id,ciphertext,nonce,key_version,version,source_path) VALUES ('orphan-house',%s,%s,'test-v1',1,'private_configuration/codes_schema')",(cipher2,nonce2))
    self.assertEqual(client.get('/v1/codes/schema',headers=owner).status_code,409)
    with psycopg.connect(scoped) as db:
     db.execute("DELETE FROM access_code_schemas WHERE property_id='orphan-house'")
     db.execute("DELETE FROM properties WHERE id='orphan-house'")
    masked=client.get(path,headers=owner)
    self.assertEqual(masked.json()['fields']['front'],'••••');self.assertNotIn('fictional-secret-111',masked.text)
    self.assertEqual(client.post(path+'/reveal',headers=owner).json()['fields']['front'],'fictional-secret-111')
    self.assertEqual(client.put(path,headers=owner,json=first).status_code,409)
    second={'fields':{'front':'fictional-secret-222'},'expected_version':1}
    self.assertEqual(client.put(path,headers=owner,json=second).json(),{'version':2})
    history=client.get(path+'/history',headers=owner).json()['items'];self.assertEqual(len(history),1)
    history_id=history[0]['id'];self.assertEqual(history[0]['source_version'],1)
    self.assertEqual(client.post(path+'/history/'+history_id+'/reveal',headers=viewer).status_code,403)
    revealed=client.post(path+'/history/'+history_id+'/reveal',headers=owner)
    self.assertEqual(revealed.status_code,200);self.assertEqual(revealed.json()['fields']['front'],'fictional-secret-111')
    self.assertNotIn('fictional-secret',client.get(path+'/history',headers=owner).text)
    self.assertEqual(client.post(path+'/history/'+history_id+'/restore',headers=owner,json={'expected_version':1}).status_code,409)
    restored=client.post(path+'/history/'+history_id+'/restore',headers=owner,json={'expected_version':2})
    self.assertEqual(restored.json(),{'version':3});self.assertNotIn('fictional-secret',restored.text)
    self.assertEqual(client.post(path+'/reveal',headers=owner).json()['fields']['front'],'fictional-secret-111')
    with psycopg.connect(scoped) as db:
     row=db.execute('SELECT ciphertext,nonce FROM access_codes').fetchone()
     self.assertNotIn(b'fictional-secret',bytes(row[0]));self.assertEqual(len(row[1]),12)
     audit=repr(db.execute('SELECT action,redacted_metadata FROM audit_events').fetchall())
     self.assertNotIn('fictional-secret',audit)
     with self.assertRaises(psycopg.Error):
      with db.transaction():db.execute('DELETE FROM code_history')
    with patch.dict(os.environ,{'PADSPLIT_CODES_KEY_VERSION':'other'}):
     self.assertEqual(client.post(path+'/reveal',headers=owner).status_code,503)
   with patch.dict(os.environ,{'PADSPLIT_CODES_KEY':''}):
    self.assertEqual(client.get('/v1/properties/sample-house/codes',headers=owner).status_code,503)
  finally:
   with psycopg.connect(DSN,autocommit=True) as admin:admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
