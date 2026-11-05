import unittest
from fastapi.testclient import TestClient
from backend.api.app import create_app

class Repo:
    def identity(self, uid): return {'uid':uid,'role':'viewer','properties':[]}
    def ready(self): return True

class OccupancyMountTests(unittest.TestCase):
    def test_route_is_authenticated_and_mounted(self):
        app=create_app(repository=Repo(),verify_token=lambda _: 'fixture',allowed_origins=['http://localhost:9999'])
        with TestClient(app) as client:
            self.assertEqual(client.get('/v1/occupancy').status_code,401)
            # Fake repository has no DB connection: generic 503 proves the
            # authorized request reached the handler without exposing details.
            self.assertEqual(client.get('/v1/occupancy',headers={'Authorization':'Bearer fixture'}).status_code,503)
    def test_provider_tasks_are_authenticated_and_mounted(self):
        app=create_app(repository=Repo(),verify_token=lambda _: 'fixture',allowed_origins=['http://localhost:9999'])
        with TestClient(app) as client:
            self.assertEqual(client.get('/v1/provider-tasks').status_code,401)
            self.assertEqual(client.get('/v1/provider-tasks',headers={'Authorization':'Bearer fixture'}).status_code,503)
    def test_reviewed_links_are_authenticated_and_mounted(self):
        app=create_app(repository=Repo(),verify_token=lambda _: 'fixture',allowed_origins=['http://localhost:9999'])
        with TestClient(app) as client:
            self.assertEqual(client.get('/v1/room-task-links').status_code,401)
            self.assertEqual(client.get('/v1/room-task-links',headers={'Authorization':'Bearer fixture'}).status_code,503)
    def test_climate_is_authenticated_and_mounted(self):
        app=create_app(repository=Repo(),verify_token=lambda _: 'fixture',allowed_origins=['http://localhost:9999'])
        with TestClient(app) as client:
            self.assertEqual(client.get('/v1/climate').status_code,401)
            self.assertEqual(client.get('/v1/climate',headers={'Authorization':'Bearer fixture'}).status_code,503)
    def test_provider_discussion_links_are_authenticated_and_mounted(self):
        app=create_app(repository=Repo(),verify_token=lambda _: 'fixture',allowed_origins=['http://localhost:9999'])
        with TestClient(app) as client:
            self.assertEqual(client.get('/v1/provider-discussion-links').status_code,401)
            self.assertEqual(client.get('/v1/provider-discussion-links',headers={'Authorization':'Bearer fixture'}).status_code,503)
