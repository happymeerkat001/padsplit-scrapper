import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.api.app import create_app

class FakeRepository:
    def identity(self,uid):return {'uid':uid,'role':'owner','properties':[]}
    def ready(self):return True

class TeamMountTests(unittest.TestCase):
    def app(self,**kwargs):return create_app(repository=FakeRepository(),verify_token=lambda _: 'fixture-owner',allowed_origins=['http://localhost:8888'],**kwargs)
    def test_disabled_by_default(self):
        with patch.dict(os.environ,{},clear=True):
            with TestClient(self.app()) as client:
                self.assertEqual(client.get('/v1/team/members',headers={'Authorization':'Bearer fake'}).status_code,404)
    def test_enabled_requires_explicit_trusted_connection(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(RuntimeError):
                with TestClient(self.app(team_enabled=True)):pass
    def test_enabled_mounts_routes_with_explicit_dependencies(self):
        app=self.app(team_enabled=True,team_repository=FakeRepository(),verify_team_target=lambda _:None)
        with TestClient(app) as client:
            self.assertNotEqual(client.get('/v1/team/members',headers={'Authorization':'Bearer fake'}).status_code,404)
