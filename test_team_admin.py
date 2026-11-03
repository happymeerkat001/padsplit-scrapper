import os
import unittest
from unittest.mock import patch

from functions import team_admin, team_seed_emulator


class TeamAdminBoundaryTests(unittest.TestCase):
    def test_refuses_missing_emulators_or_non_demo_project(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError): team_admin.require_emulators()
        with patch.dict(os.environ, {'GCLOUD_PROJECT':'real-project', 'FIRESTORE_EMULATOR_HOST':'127.0.0.1:8185', 'FIREBASE_AUTH_EMULATOR_HOST':'127.0.0.1:9198'}, clear=True):
            with self.assertRaises(RuntimeError): team_admin.require_emulators()

    def test_refuses_non_loopback_or_malformed_hosts(self):
        for host in ['example.invalid:8185', '127.0.0.1:abc', '127.0.0.1:8185/path', '']:
            with self.subTest(host=host), patch.dict(os.environ, {'GCLOUD_PROJECT':'demo-padsplit','FIRESTORE_EMULATOR_HOST':host,'FIREBASE_AUTH_EMULATOR_HOST':'127.0.0.1:9198'},clear=True):
                with self.assertRaises(RuntimeError): team_admin.require_emulators()

    def test_accepts_only_explicit_loopback_demo_hosts(self):
        with patch.dict(os.environ, {'GCLOUD_PROJECT':'demo-padsplit','FIRESTORE_EMULATOR_HOST':'127.0.0.1:8185','FIREBASE_AUTH_EMULATOR_HOST':'localhost:9198'},clear=True):
            team_admin.require_emulators()

    def test_rejects_role_escalation_and_invalid_scopes(self):
        for command in [{'role':'owner','properties':['one']}, {'role':['owner'],'properties':['one']}, {'role':'manager','properties':[]},
                        {'role':'operator','properties':['one','one']}, {'role':'viewer','properties':['../other']}]:
            with self.subTest(command=command):
                with self.assertRaises(ValueError): team_admin._team_fields(command)

    def test_seed_refuses_real_addresses_and_does_not_reset_existing_users(self):
        fake_app=type('App',(),{'project_id':'demo-padsplit'})()
        env={'GCLOUD_PROJECT':'demo-padsplit','FIRESTORE_EMULATOR_HOST':'127.0.0.1:8185','FIREBASE_AUTH_EMULATOR_HOST':'127.0.0.1:9198'}
        with patch.dict(os.environ,env,clear=True), patch.object(team_seed_emulator.auth,'create_user') as create:
            with self.assertRaises(ValueError): team_seed_emulator.seed('owner','person@example.com','fictional-password',app=fake_app)
            with self.assertRaises(ValueError): team_seed_emulator.seed('owner','person@example.invalid','short',app=fake_app)
            create.assert_not_called()
            team_seed_emulator.seed('owner','person@example.invalid','fictional-password',app=fake_app)
            create.assert_called_once()


if __name__ == '__main__':
    unittest.main()
