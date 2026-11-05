"""No real codes, Firebase tokens, or service connections."""
import unittest
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Conflict, Forbidden

class Fake:
 def ready(self):return True
 def identity(self,uid):return {'uid':uid,'role':'owner' if uid=='owner' else 'viewer','properties':['sample']}
 def codes_schema(self,uid):
  if uid!='owner':raise Forbidden()
  return {'properties':[{'slug':'sample','address':'Example','sections':[{'label':'Main','fields':[]}]}],'versions':{'sample':1}}
 def codes_metadata(self,uid,p):
  if uid!='owner':raise Forbidden()
  return {'exists':True,'version':1,'fields':{'front':'••••'}}
 def codes_reveal(self,uid,p):
  if uid!='owner':raise Forbidden()
  return {'version':1,'fields':{'front':'fictional-code'}}
 def codes_save(self,uid,p,fields,expected):
  if uid!='owner':raise Forbidden()
  if expected!=1:raise Conflict()
  return {'version':2}
 def codes_history(self,uid,p,limit,offset):
  if uid!='owner':raise Forbidden()
  return [{'id':'history-1','source_version':1}]
 def codes_history_reveal(self,uid,p,h):
  if uid!='owner':raise Forbidden()
  return {'fields':{'front':'fictional-historical-code'}}
 def codes_restore(self,uid,p,h,expected):
  if uid!='owner':raise Forbidden()
  if expected!=2:raise Conflict()
  return {'version':3}

class CodesApiTests(unittest.TestCase):
 def setUp(self):
  self.c=TestClient(create_app(repository=Fake(),verify_token=lambda t:t,allowed_origins=['https://example.invalid']))
 def h(self,uid):return {'Authorization':'Bearer '+uid}
 def test_masked_owner_only_and_reveal_explicit(self):
  self.assertEqual(self.c.get('/v1/codes/schema',headers=self.h('viewer')).status_code,403)
  self.assertEqual(self.c.get('/v1/codes/schema',headers=self.h('owner')).json()['properties'][0]['slug'],'sample')
  path='/v1/properties/sample/codes'
  self.assertEqual(self.c.get(path,headers=self.h('viewer')).status_code,403)
  masked=self.c.get(path,headers=self.h('owner'))
  self.assertNotIn('fictional-code',masked.text)
  self.assertEqual(masked.headers['cache-control'],'no-store')
  self.assertEqual(self.c.post(path+'/reveal',headers=self.h('owner')).json()['fields']['front'],'fictional-code')
 def test_save_and_restore_use_versions_and_redacted_responses(self):
  path='/v1/properties/sample/codes'
  b={'fields':{'front':'fictional-code'},'expected_version':1}
  saved=self.c.put(path,headers=self.h('owner'),json=b)
  self.assertEqual(saved.status_code,200);self.assertNotIn('fictional-code',saved.text)
  self.assertEqual(self.c.put(path,headers=self.h('viewer'),json=b).status_code,403)
  self.assertEqual(self.c.put(path,headers=self.h('owner'),json={**b,'expected_version':0}).status_code,409)
  self.assertEqual(self.c.put(path,headers=self.h('owner'),json={'fields':{'front':2},'expected_version':1}).status_code,422)
  self.assertEqual(self.c.get(path+'/history',headers=self.h('viewer')).status_code,403)
  self.assertNotIn('fictional-historical-code',self.c.get(path+'/history',headers=self.h('owner')).text)
  self.assertEqual(self.c.post(path+'/history/history-1/reveal',headers=self.h('viewer')).status_code,403)
  revealed=self.c.post(path+'/history/history-1/reveal',headers=self.h('owner'))
  self.assertEqual(revealed.json()['fields']['front'],'fictional-historical-code')
  self.assertEqual(revealed.headers['cache-control'],'no-store')
  self.assertEqual(self.c.post(path+'/history/history-1/restore',headers=self.h('owner'),json={'expected_version':2}).json(),{'version':3})
  self.assertEqual(self.c.post(path+'/history/history-1/restore',headers=self.h('owner'),json={'expected_version':1}).status_code,409)
