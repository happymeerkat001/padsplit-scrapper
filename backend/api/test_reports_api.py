"""Owner-only reports contract with fictional records."""
import unittest
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Forbidden,NotFound

class Repo:
 def identity(self,uid):return {'uid':uid,'role':'owner' if uid=='owner' else 'viewer','properties':[]}
 def report(self,uid,ident):
  if uid!='owner':raise Forbidden()
  if ident!='latest':raise NotFound()
  return {'id':'latest','json':'{"kpis":{"rooms":4}}','observed_at':'2026-01-01T00:00:00Z'}

class ReportsTests(unittest.TestCase):
 def test_owner_only_and_missing_report(self):
  c=TestClient(create_app(repository=Repo(),verify_token=lambda x:x,allowed_origins=['https://example.invalid']))
  h=lambda user:{'Authorization':'Bearer '+user}
  self.assertEqual(c.get('/v1/reports/latest').status_code,401)
  self.assertEqual(c.get('/v1/reports/latest',headers=h('viewer')).status_code,403)
  response=c.get('/v1/reports/latest',headers=h('owner'))
  self.assertEqual(response.status_code,200)
  self.assertEqual(response.json()['json'],'{"kpis":{"rooms":4}}')
  self.assertEqual(response.headers['cache-control'],'no-store')
  self.assertEqual(c.get('/v1/reports/monthly_history',headers=h('owner')).status_code,404)
  self.assertEqual(c.get('/v1/reports/arbitrary',headers=h('owner')).status_code,404)
