"""Shared global records must always remain owner-only."""
import unittest
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Conflict, Forbidden, NotFound

class Repo:
 def __init__(self): self.rows={};self.keys={}
 def ready(self):return True
 def identity(self,uid):return {'uid':uid,'role':'owner' if uid=='owner' else 'viewer','properties':[]}
 def shared_records(self,uid,kind,limit,offset):
  if uid!='owner':raise Forbidden()
  return [v for (k,_),v in self.rows.items() if k==kind][offset:offset+limit]
 def shared_create(self,uid,kind,data,key):
  if uid!='owner':raise Forbidden()
  if key in self.keys:
   if self.keys[key][0]!=(kind,data):raise Conflict()
   return self.keys[key][1]
  id='new-'+kind;self.rows[(kind,id)]={'id':id,**data,'version':1};result={'id':id,'version':1};self.keys[key]=((kind,data),result);return result
 def shared_update(self,uid,kind,id,data,version):
  if uid!='owner':raise Forbidden()
  row=self.rows.get((kind,id))
  if not row:raise NotFound()
  if row['version']!=version:raise Conflict()
  row.update(data);row['version']+=1;return {'id':id,'version':row['version']}
 def shared_delete(self,uid,kind,id,version):
  if uid!='owner':raise Forbidden()
  row=self.rows.get((kind,id))
  if not row:raise NotFound()
  if row['version']!=version:raise Conflict()
  del self.rows[(kind,id)];return {'ok':True}

class SharedApiTests(unittest.TestCase):
 def setUp(self):
  self.repo=Repo();self.c=TestClient(create_app(repository=self.repo,verify_token=lambda s:s,allowed_origins=['https://example.invalid']))
 def h(self,uid,key=None):
  result={'Authorization':'Bearer '+uid}
  if key:result['Idempotency-Key']=key
  return result
 def test_owner_only_create_update_delete_and_conflicts(self):
  for route,data in [('vendors',{'name':'Example','specialty':'General','contact':'','location':''}),('templates',{'name':'Example','body':'Hello'})]:
   with self.subTest(route=route):
    p='/v1/'+route;owner=self.h('owner','key-'+route)
    self.assertEqual(self.c.post(p,headers=self.h('viewer','v-'+route),json=data).status_code,403)
    created=self.c.post(p,headers=owner,json=data);self.assertEqual(created.status_code,201)
    self.assertEqual(self.c.post(p,headers=owner,json=data).json(),created.json())
    self.assertEqual(self.c.post(p,headers=owner,json={**data,'name':'Different'}).status_code,409)
    id=created.json()['id'];path=p+'/'+id
    self.assertEqual(self.c.patch(path,headers=self.h('viewer'),json={**data,'expected_version':1}).status_code,403)
    self.assertEqual(self.c.patch(path,headers=self.h('owner'),json={**data,'expected_version':1}).json()['version'],2)
    self.assertEqual(self.c.patch(path,headers=self.h('owner'),json={**data,'expected_version':1}).status_code,409)
    self.assertEqual(self.c.delete(path+'?expected_version=1',headers=self.h('owner')).status_code,409)
    self.assertEqual(self.c.delete(path+'?expected_version=2',headers=self.h('owner')).status_code,200)
 def test_template_blank_title_is_preserved(self):
  result=self.c.post('/v1/templates',headers=self.h('owner','blank-title'),json={'name':'','body':'Fictional body'})
  self.assertEqual(result.status_code,201)
 def test_vendor_patch_preserves_omitted_null_and_missing_fields(self):
  self.repo.rows[('vendors','legacy')]={'id':'legacy','name':'Before','contact':None,'unknown':'preserved','version':1}
  response=self.c.patch('/v1/vendors/legacy',headers=self.h('owner'),json={'name':'After','expected_version':1})
  self.assertEqual(response.status_code,200)
  result=self.repo.rows[('vendors','legacy')]
  self.assertIsNone(result['contact']);self.assertNotIn('specialty',result);self.assertEqual(result['unknown'],'preserved')
  self.assertEqual(self.c.patch('/v1/vendors/legacy',headers=self.h('owner'),json={'expected_version':2}).status_code,422)
  self.assertEqual(self.c.patch('/v1/vendors/legacy',headers=self.h('owner'),json={'name':None,'expected_version':2}).status_code,422)
