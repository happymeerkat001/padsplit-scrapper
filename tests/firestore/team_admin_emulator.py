"""End-to-end team-service checks against demo Firebase emulators only."""
import os
import unittest
import uuid
import threading

import firebase_admin
from firebase_admin import auth, credentials, firestore
from google.auth.credentials import AnonymousCredentials
import requests

from functions import team_admin, team_admin_http, team_seed_emulator


class AnonymousAdminCredentials(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


class TeamAdminEmulatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        team_admin.require_emulators()
        cls.app = firebase_admin.initialize_app(AnonymousAdminCredentials(), {'projectId': 'demo-padsplit'}, name='team-emulator-test')
        cls.db = firestore.client(cls.app)
        cls.password = 'fictional-test-password-123'
        cls.uids = [team_admin.OWNER_UID, 'manager_test', 'operator_test', 'viewer_test', 'other_test']
        cls.tokens = {}
        host = os.environ['FIREBASE_AUTH_EMULATOR_HOST']
        for uid in cls.uids:
            email = f'{uid}@example.invalid'
            try:
                auth.create_user(uid=uid, email=email, password=cls.password, app=cls.app)
            except auth.UidAlreadyExistsError:
                auth.update_user(uid, email=email, password=cls.password, disabled=False, app=cls.app)
            response = requests.post(f'http://{host}/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=demo',
                                     json={'email': email, 'password': cls.password, 'returnSecureToken': True}, timeout=5)
            response.raise_for_status()
            cls.tokens[uid] = response.json()['idToken']

    @classmethod
    def tearDownClass(cls):
        firebase_admin.delete_app(cls.app)

    def run_action(self, actor_uid, **command):
        return team_admin.run_action(self.tokens[actor_uid], command, db=self.db, app=self.app)

    def test_authorized_team_and_scoped_tasks(self):
        owner = team_admin.OWNER_UID
        with self.assertRaises(PermissionError):
            self.run_action('other_test', action='invite', uid='manager_test', role='manager', properties=['house_a'])
        with self.assertRaises(ValueError):
            self.run_action(owner, action='invite', uid='manager_test', role='owner', properties=['house_a'])
        with self.assertRaises(ValueError):
            self.run_action(owner, action='deactivate', uid=owner)
        for uid, role in [('manager_test','manager'),('operator_test','operator'),('viewer_test','viewer')]:
            self.run_action(owner, action='invite', uid=uid, role=role, properties=['house_a'])
            with self.assertRaises(PermissionError):
                self.run_action('other_test', action='accept_invitation', uid=uid)
            self.run_action(uid, action='accept_invitation', uid=uid)
        with self.assertRaises(PermissionError):
            self.run_action('manager_test', action='update_access', uid='manager_test', role='owner', properties=['house_a'])
        self.run_action('operator_test', action='create_task', property='house_a', task='task_one', title='Check fixture', status='open')
        with self.assertRaises(PermissionError):
            self.run_action('viewer_test', action='create_task', property='house_a', task='task_two', title='No', status='open')
        with self.assertRaises(PermissionError):
            self.run_action('operator_test', action='create_task', property='house_b', task='task_two', title='No', status='open')
        with self.assertRaises(ValueError):
            self.run_action('operator_test', action='create_task', property='house_a', task='task_two', title=' ', status='open')
        with self.assertRaises(ValueError):
            self.run_action('operator_test', action='update_task', property='house_a', task='task_one', title='Changed', status='done', expected_version=9)
        self.run_action('manager_test', action='update_task', property='house_a', task='task_one', title='Changed', status='done', expected_version=1)
        with self.assertRaises(PermissionError):
            self.run_action('operator_test', action='delete_task', property='house_a', task='task_one', expected_version=2)
        self.run_action('manager_test', action='delete_task', property='house_a', task='task_one', expected_version=2)
        self.run_action(owner, action='deactivate', uid='operator_test')
        with self.assertRaises(PermissionError):
            self.run_action('operator_test', action='create_task', property='house_a', task='task_three', title='No', status='open')
        events = list(self.db.collection('team_admin_audit').stream())
        self.assertGreaterEqual(len(events), 10)
        for event in events:
            payload = event.to_dict()
            self.assertEqual(payload['outcome'], 'success')
            self.assertNotIn('title', payload)
            self.assertNotIn('token', payload)

    def test_invalid_token_is_rejected(self):
        with self.assertRaises(Exception):
            team_admin.run_action('invalid-token', {'action':'deactivate','uid':'manager_test'}, db=self.db, app=self.app)

    def test_emulator_seed_refuses_to_replace_existing_identity(self):
        with self.assertRaises(auth.UidAlreadyExistsError):
            team_seed_emulator.seed(team_admin.OWNER_UID, 'new@example.invalid', 'fictional-password-456', app=self.app)

    def test_loopback_http_boundary_verifies_token_and_origin(self):
        server = team_admin_http.make_server(0, 'http://127.0.0.1:8765', db=self.db, app=self.app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/team/action'
            base = f'http://127.0.0.1:{server.server_port}'
            owner_token = self.tokens[team_admin.OWNER_UID]
            viewer_token = self.tokens['viewer_test']
            headers = {'Origin':'http://127.0.0.1:8765', 'Authorization':f'Bearer {owner_token}'}
            body = {'action':'create_task','property':'house_a','task':'http_task','title':'Fixture','status':'open'}
            page = requests.get(base+'/team', timeout=5)
            self.assertEqual(page.status_code, 200)
            self.assertIn("default-src 'none'", page.headers['Content-Security-Policy'])
            self.assertIn('LOCAL EMULATOR ONLY', page.text)
            self.assertEqual(requests.get(base+'/team/app.js', timeout=5).status_code, 200)
            self.assertEqual(requests.get(base+'/team/logic.js', timeout=5).status_code, 200)
            self.assertEqual(requests.get(base+'/team/style.css', timeout=5).status_code, 200)
            self.assertEqual(requests.get(base+'/team/config', timeout=5).json()['authOrigin'], 'http://'+os.environ['FIREBASE_AUTH_EMULATOR_HOST'])
            self.assertEqual(requests.get(base+'/team/unknown', timeout=5).status_code, 404)
            preflight = requests.options(url, headers={'Origin':'http://127.0.0.1:8765','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'authorization,content-type'}, timeout=5)
            self.assertEqual(preflight.status_code, 204)
            self.assertEqual(preflight.headers['Access-Control-Allow-Origin'], 'http://127.0.0.1:8765')
            denied_preflight = requests.options(url, headers={'Origin':'http://evil.invalid','Access-Control-Request-Method':'POST'}, timeout=5)
            self.assertEqual(denied_preflight.status_code, 403)
            self.assertNotIn('Access-Control-Allow-Origin', denied_preflight.headers)
            self.assertEqual(requests.post(url, json=body, timeout=5).status_code, 403)
            self.assertEqual(requests.post(url, json=body, headers={**headers,'Origin':'http://evil.invalid'}, timeout=5).status_code, 403)
            self.assertEqual(requests.post(url, json=body, headers={**headers,'Host':'evil.invalid'}, timeout=5).status_code, 403)
            self.assertEqual(requests.post(url, json=body, headers={**headers,'X-Forwarded-For':'127.0.0.1'}, timeout=5).status_code, 400)
            self.assertEqual(requests.post(url, json=body, headers={**headers,'Authorization':'Bearer invalid'}, timeout=5).status_code, 401)
            escalate = {'action':'update_access','uid':'viewer_test','role':'manager','properties':['house_a']}
            denied = requests.post(url, json=escalate, headers={**headers,'Authorization':f'Bearer {viewer_token}'}, timeout=5)
            self.assertEqual(denied.status_code, 403)
            self.assertNotIn(viewer_token, denied.text)
            self.assertEqual(requests.post(url, json={**body,'actor':team_admin.OWNER_UID}, headers=headers, timeout=5).status_code, 400)
            response = requests.post(url, json=body, headers=headers, timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(owner_token, response.text)
            self.assertEqual(requests.post(url, json=body, headers=headers, timeout=5).status_code, 400)
            self_origin = f'http://127.0.0.1:{server.server_port}'
            self.assertEqual(requests.post(url, json={**body,'task':'http_task_self'}, headers={**headers,'Origin':self_origin}, timeout=5).status_code, 200)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
