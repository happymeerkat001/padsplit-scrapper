"""Owner-only notes contract; fictional data only."""
import unittest
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Forbidden, Conflict

class Repo:
 def __init__(self):self.version=0;self.text=''
 def ready(self):return True
 def identity(self,uid):return {'uid':uid,'role':'owner' if uid=='owner' else 'viewer','properties':[]}
 def note_get(self,uid,ident):
  if uid!='owner':raise Forbidden()
  return {'id':ident,'text':self.text,'version':self.version,'exists':self.version>0}
 def note_put(self,uid,ident,text,expected):
  if uid!='owner':raise Forbidden()
  if expected!=self.version:raise Conflict()
  self.version+=1;self.text=text;return {'id':ident,'version':self.version}

class NotesApiTests(unittest.TestCase):
 def test_owner_only_and_conflicts(self):
  c=TestClient(create_app(repository=Repo(),verify_token=lambda x:x,allowed_origins=['https://example.invalid']))
  owner={'Authorization':'Bearer owner'};viewer={'Authorization':'Bearer viewer'};path='/v1/notes/dashboard'
  self.assertEqual(c.get(path,headers=viewer).status_code,403)
  self.assertEqual(c.get(path,headers=owner).json()['version'],0)
  data={'text':'fictional-private-note','expected_version':0}
  self.assertEqual(c.put(path,headers=viewer,json=data).status_code,403)
  saved=c.put(path,headers=owner,json=data);self.assertEqual(saved.json()['version'],1)
  self.assertNotIn('fictional-private-note',saved.text)
  self.assertEqual(c.put(path,headers=owner,json=data).status_code,409)
  self.assertEqual(c.get(path,headers=owner).json()['text'],'fictional-private-note')
