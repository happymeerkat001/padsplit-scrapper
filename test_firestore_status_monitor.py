#!/usr/bin/env python3
"""Firestore status monitor credentials. No network and no key material."""

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import firestore_status_monitor as monitor


FAKE_INFO = {"type": "service_account", "project_id": "demo-project"}
FAKE_PATH = "/tmp/padsplit-test-not-a-key.json"
MISSING = "Missing FIREBASE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS"
_CRED_KEYS = ("FIREBASE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS")


@contextmanager
def firebase_env(extra: dict):
    """Isolate credential env vars and stub the Admin SDK. Never opens a key file."""
    cleaned = {key: value for key, value in os.environ.items() if key not in _CRED_KEYS}
    cleaned.update(extra)
    fake_client = object()
    with patch.dict(os.environ, cleaned, clear=True), patch("firebase_admin._apps", {}), patch(
        "firebase_admin.credentials.Certificate", return_value="cred-sentinel"
    ) as cert, patch("firebase_admin.initialize_app") as init_app, patch(
        "firebase_admin.firestore.client", return_value=fake_client
    ) as client:
        yield cert, init_app, client, fake_client


class EnvLoadTests(unittest.TestCase):
    def test_load_environment_does_not_override_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "FIREBASE_SERVICE_ACCOUNT_JSON=from-dotenv\n"
                f"GOOGLE_APPLICATION_CREDENTIALS={FAKE_PATH}\n"
            )
            with patch.object(monitor, "ENV_PATH", env_path):
                with patch.dict(
                    os.environ,
                    {"FIREBASE_SERVICE_ACCOUNT_JSON": "from-process"},
                    clear=False,
                ):
                    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
                    monitor.load_environment()
                    self.assertEqual(os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"], "from-process")
                    self.assertEqual(os.environ["GOOGLE_APPLICATION_CREDENTIALS"], FAKE_PATH)

    def test_main_loads_environment_first(self) -> None:
        order: list[str] = []

        def load() -> None:
            order.append("load")

        def payload(_base):
            order.append("payload")
            raise FileNotFoundError("stop")

        with patch.object(monitor, "load_environment", side_effect=load):
            with patch.object(monitor, "_load_latest_payload", side_effect=payload):
                with self.assertRaises(FileNotFoundError):
                    monitor.main()
        self.assertEqual(order, ["load", "payload"])


class CredentialResolutionTests(unittest.TestCase):
    def test_service_account_json_wins_over_file_path(self) -> None:
        env = {
            "FIREBASE_SERVICE_ACCOUNT_JSON": json.dumps(FAKE_INFO),
            "GOOGLE_APPLICATION_CREDENTIALS": FAKE_PATH,
        }
        with firebase_env(env) as (cert, init_app, client, fake_client):
            result = monitor._init_firestore_client()
        self.assertIs(result, fake_client)
        cert.assert_called_once_with(FAKE_INFO)
        init_app.assert_called_once_with("cred-sentinel")
        client.assert_called_once_with()

    def test_google_application_credentials_is_fallback(self) -> None:
        with firebase_env({"GOOGLE_APPLICATION_CREDENTIALS": FAKE_PATH}) as (
            cert,
            init_app,
            client,
            fake_client,
        ):
            result = monitor._init_firestore_client()
        self.assertIs(result, fake_client)
        cert.assert_called_once_with(FAKE_PATH)
        init_app.assert_called_once_with("cred-sentinel")
        client.assert_called_once_with()

    def test_missing_both_credentials_raises_clear_error(self) -> None:
        with firebase_env({}) as (cert, init_app, _client, _fake):
            with self.assertRaises(RuntimeError) as ctx:
                monitor._init_firestore_client()
        self.assertEqual(str(ctx.exception), MISSING)
        cert.assert_not_called()
        init_app.assert_not_called()

    def test_present_credentials_but_no_client_is_a_different_error(self) -> None:
        with firebase_env({"GOOGLE_APPLICATION_CREDENTIALS": FAKE_PATH}):
            with patch.object(monitor.persist, "_firestore_client_or_none", return_value=None):
                with self.assertRaises(RuntimeError) as ctx:
                    monitor._init_firestore_client()
        self.assertNotEqual(str(ctx.exception), MISSING)
        self.assertIn("Firestore client unavailable", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
