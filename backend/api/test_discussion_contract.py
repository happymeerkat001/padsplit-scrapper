"""API validation for scoped internal discussion controls."""
import unittest
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.test_api import FakeRepository
from backend.api.repository import Forbidden, NotFound


class DiscussionRepo(FakeRepository):
    def __init__(self):
        super().__init__()
        self.created = None
        self.deleted = False

    def discussion_create(self, uid, task_id, body, key, role_label=None, status_label=None):
        self.discussion_list(uid, task_id, 10, 0)
        if self.users[uid]['role'] == 'viewer': raise Forbidden()
        self.created = (body, role_label, status_label)
        return {'id': 'discussion-id'}

    def discussion_delete(self, uid, task_id, discussion_id):
        self.discussion_list(uid, task_id, 10, 0)
        if self.users[uid]['role'] == 'viewer': raise Forbidden()
        if self.deleted or discussion_id != 'discussion-id': raise NotFound()
        self.deleted = True
        return {'ok': True}


class DiscussionContractTests(unittest.TestCase):
    def setUp(self):
        self.repo = DiscussionRepo()
        self.client = TestClient(create_app(repository=self.repo, verify_token=lambda t: t if t in self.repo.users else None,
                                            allowed_origins=['https://example.invalid'], rate_limit=100))

    def headers(self, uid): return {'Authorization': 'Bearer ' + uid, 'Idempotency-Key': 'fixture-key'}

    def test_label_validation_and_scoped_delete(self):
        path = '/v1/tasks/task-a/discussion'
        payload = {'body': 'Fictional update', 'role_label': 'Repair', 'status_label': 'In Process'}
        self.assertEqual(self.client.post(path, headers=self.headers('operator'), json=payload).status_code, 201)
        self.assertEqual(self.repo.created, ('Fictional update', 'Repair', 'In Process'))
        self.assertEqual(self.client.post(path, headers=self.headers('operator'), json={**payload, 'role_label': 'x' * 101}).status_code, 422)
        self.assertEqual(self.client.post(path, headers=self.headers('operator'), json={**payload, 'role_label': ''}).status_code, 422)
        self.assertEqual(self.client.post(path, headers=self.headers('viewer'), json=payload).status_code, 403)
        item = path + '/discussion-id'
        self.assertEqual(self.client.delete(item, headers=self.headers('viewer')).status_code, 403)
        self.assertEqual(self.client.delete('/v1/tasks/task-b/discussion/discussion-id', headers=self.headers('operator')).status_code, 403)
        self.assertEqual(self.client.delete(item, headers=self.headers('operator')).status_code, 200)
        self.assertEqual(self.client.delete(item, headers=self.headers('operator')).status_code, 404)


if __name__ == '__main__': unittest.main()
