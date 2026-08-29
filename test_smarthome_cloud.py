#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from midea_beautiful.exceptions import CloudAuthenticationError, CloudError

from smarthome import cloud, identity


class DummyDevice:
    def __init__(self, *, running=True, target_c=22.2, online=True):
        self.online = online
        self.state = SimpleNamespace(running=running, target_temperature=target_c)
        self.set_state = MagicMock()

    def refresh(self, _cloud):
        return None


class CloudHelperTests(unittest.TestCase):
    def test_f_to_c_pins(self) -> None:
        self.assertEqual(cloud.f_to_c(72), 22.2)
        self.assertEqual(cloud.f_to_c(75), 23.9)
        self.assertEqual(cloud.f_to_c(74), 23.3)

    def test_list_and_find(self) -> None:
        client = SimpleNamespace(
            list_appliances=lambda: [
                {"name": "1025 Broken Crest", "id": "1", "type": "0xac"},
                {"name": "Washer", "id": "2", "type": "0xa1"},
            ]
        )
        acs = cloud.list_acs(client)
        self.assertEqual([a["name"] for a in acs], ["1025 Broken Crest"])
        found = cloud.find_ac(client, "1025 Broken Crest")
        self.assertEqual(found["id"], "1")

    def test_unknown_name(self) -> None:
        client = SimpleNamespace(list_appliances=lambda: [])
        with self.assertRaises(cloud.UnknownNameError):
            cloud.find_ac(client, "nope")

    def test_duplicate_name(self) -> None:
        client = SimpleNamespace(
            list_appliances=lambda: [
                {"name": "Room", "id": "1", "type": "0xac"},
                {"name": "Room", "id": "2", "type": "0xac"},
            ]
        )
        with self.assertRaises(cloud.AmbiguousNameError):
            cloud.find_ac(client, "Room")

    def test_set_temp_sends_auto_celsius(self) -> None:
        client = SimpleNamespace(
            list_appliances=lambda: [{"name": "Pioneer 3", "id": "9", "type": "0xac"}]
        )
        device = DummyDevice(running=True, target_c=22.2)
        cloud.set_temp(client, "Pioneer 3", 72, appliance_state_fn=lambda **_: device)
        device.set_state.assert_called_once()
        kwargs = device.set_state.call_args.kwargs
        self.assertEqual(kwargs["running"], True)
        self.assertEqual(kwargs["mode"], cloud.AUTO_MODE)
        self.assertEqual(kwargs["target_temperature"], 22.2)

    def test_set_temp_rejects_range(self) -> None:
        client = SimpleNamespace(list_appliances=lambda: [{"name": "A", "id": "1", "type": "0xac"}])
        with self.assertRaises(cloud.SetpointRangeError):
            cloud.set_temp(client, "A", 50)

    def test_offline_is_failure(self) -> None:
        client = SimpleNamespace(list_appliances=lambda: [{"name": "A", "id": "1", "type": "0xac"}])
        device = DummyDevice(online=False)
        with self.assertRaises(cloud.OfflineError):
            cloud.set_temp(client, "A", 72, appliance_state_fn=lambda **_: device)

    def test_auth_error_maps(self) -> None:
        def boom(**_kwargs):
            raise CloudAuthenticationError(3101, "bad", "user")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            with self.assertRaises(cloud.SmartHomeAuthError):
                cloud.connect(
                    credentials={"email": "a", "password": "b"},
                    connect_fn=boom,
                    identity_path=path,
                )

    def test_session_limit_maps(self) -> None:
        def boom(**_kwargs):
            raise CloudError(65027, "limit")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            with self.assertRaises(cloud.SmartHomeSessionLimitError):
                cloud.connect(
                    credentials={"email": "a", "password": "b"},
                    connect_fn=boom,
                    identity_path=path,
                )

    def test_connect_passes_minted_identity(self) -> None:
        seen = []

        def fake_connect(**kwargs):
            seen.append(kwargs)
            return object()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            cloud.connect(
                credentials={"email": "a", "password": "b"},
                connect_fn=fake_connect,
                identity_path=path,
            )
            stored = json.loads(path.read_text())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["appname"], "MSmartHome")
        self.assertEqual(seen[0]["device_id"], stored["device_id"])
        self.assertEqual(seen[0]["pushtoken"], stored["pushToken"])
        self.assertNotEqual(seen[0]["device_id"], identity.LIBRARY_DEFAULT_DEVICE_ID)

    def test_connect_reuses_persisted_identity(self) -> None:
        seen = []

        def fake_connect(**kwargs):
            seen.append(kwargs)
            return object()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            cloud.connect(
                credentials={"email": "a", "password": "b"},
                connect_fn=fake_connect,
                identity_path=path,
            )
            first = dict(seen[0])
            cloud.connect(
                credentials={"email": "a", "password": "b"},
                connect_fn=fake_connect,
                identity_path=path,
            )
            stored = json.loads(path.read_text())
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1]["device_id"], first["device_id"])
        self.assertEqual(seen[1]["pushtoken"], first["pushtoken"])
        self.assertEqual(stored["device_id"], first["device_id"])
        self.assertEqual(stored["pushToken"], first["pushtoken"])

    def test_session_limit_leaves_identity_unchanged(self) -> None:
        def boom(**_kwargs):
            raise CloudError(65027, "limit")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            path.write_text(
                json.dumps(
                    {
                        "fingerprint": "msmarthome",
                        "device_id": "aabbccddeeff0011",
                        "pushToken": "keep-me",
                    }
                )
            )
            before = path.read_text()
            with self.assertRaises(cloud.SmartHomeSessionLimitError):
                cloud.connect(
                    credentials={"email": "a", "password": "b"},
                    connect_fn=boom,
                    identity_path=path,
                )
            self.assertEqual(path.read_text(), before)

    def test_auth_error_leaves_identity_unchanged(self) -> None:
        def boom(**_kwargs):
            raise CloudAuthenticationError(3101, "bad", "user")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            path.write_text(
                json.dumps(
                    {
                        "fingerprint": "msmarthome",
                        "device_id": "aabbccddeeff0011",
                        "pushToken": "keep-me",
                    }
                )
            )
            before = path.read_text()
            with self.assertRaises(cloud.SmartHomeAuthError):
                cloud.connect(
                    credentials={"email": "a", "password": "b"},
                    connect_fn=boom,
                    identity_path=path,
                )
            self.assertEqual(path.read_text(), before)

    def test_default_fingerprint_is_msmarthome(self) -> None:
        seen = []

        def fake_connect(**kwargs):
            seen.append(kwargs)
            return object()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            cloud.connect(
                credentials={"email": "a", "password": "b"},
                connect_fn=fake_connect,
                identity_path=path,
            )
            stored = json.loads(path.read_text())
        self.assertEqual(len(seen), 1)
        self.assertEqual(stored["fingerprint"], "msmarthome")
        self.assertEqual(seen[0]["appname"], "MSmartHome")

    def test_slim_fingerprint_uses_slim_connector_once(self) -> None:
        slim = MagicMock(return_value=object())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            identity.select_fingerprint("msmarthome-slim", path)
            with patch.object(cloud, "_connect_slim", slim):
                cloud.connect(
                    credentials={"email": "a", "password": "b"},
                    identity_path=path,
                )
            stored = json.loads(path.read_text())
        slim.assert_called_once()
        kwargs = slim.call_args.kwargs
        self.assertEqual(kwargs["appname"], "MSmartHome")
        self.assertEqual(kwargs["device_id"], stored["device_id"])
        self.assertEqual(kwargs["pushtoken"], stored["pushToken"])
        self.assertEqual(stored["fingerprint"], "msmarthome-slim")

    def test_unknown_fingerprint_does_not_call_cloud(self) -> None:
        seen = []

        def fake_connect(**kwargs):
            seen.append(kwargs)
            return object()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            path.write_text(
                json.dumps(
                    {
                        "fingerprint": "nethome-plus",
                        "device_id": "aabbccddeeff0011",
                        "pushToken": "keep-me",
                    }
                )
            )
            with self.assertRaises(identity.UnknownFingerprintError):
                cloud.connect(
                    credentials={"email": "a", "password": "b"},
                    connect_fn=fake_connect,
                    identity_path=path,
                )
        self.assertEqual(seen, [])

    def test_session_limit_does_not_advance_fingerprint(self) -> None:
        def boom(**_kwargs):
            raise CloudError(65027, "limit")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "identity.json"
            identity.load_or_create(path)
            with self.assertRaises(cloud.SmartHomeSessionLimitError):
                cloud.connect(
                    credentials={"email": "a", "password": "b"},
                    connect_fn=boom,
                    identity_path=path,
                )
            stored = json.loads(path.read_text())
        self.assertEqual(stored["fingerprint"], "msmarthome")

    def test_slim_login_body_omits_version_block(self) -> None:
        body = cloud.slim_login_body(
            {
                "data": {
                    "appKey": "x",
                    "appVersion": "2.22.0",
                    "osVersion": "8.1.0",
                    "deviceId": "old",
                    "platform": "2",
                },
                "iotData": {
                    "appId": "1010",
                    "appVNum": "2.22.0",
                    "appVersion": "2.22.0",
                    "clientVersion": "2.22.0",
                    "iampwd": "iam",
                    "password": "pw",
                    "loginAccount": "a",
                    "reqId": "req",
                    "stamp": "20260101000000",
                },
                "reqId": "req",
                "stamp": "20260101000000",
            },
            device_id="aabbccddeeff0011",
            pushtoken="token",
            appid="1010",
            account="user@example.com",
        )
        dumped = json.dumps(body)
        self.assertNotIn("2.22.0", dumped)
        self.assertNotIn("appVersion", dumped)
        self.assertNotIn("osVersion", dumped)
        self.assertNotIn("appVNum", dumped)
        self.assertNotIn("clientVersion", dumped)
        self.assertEqual(body["data"]["deviceId"], "aabbccddeeff0011")
        self.assertEqual(body["iotData"]["pushToken"], "token")
        self.assertEqual(body["iotData"]["appId"], "1010")
        self.assertEqual(body["iotData"]["loginAccount"], "user@example.com")

    def test_transparent_send_rewrites_appliance_id(self) -> None:
        seen = []

        def api_request(endpoint, args=None, **kwargs):
            seen.append((endpoint, dict(args or {}), kwargs.get("data")))
            return {"ok": True}

        client = SimpleNamespace(api_request=api_request, _appid=1010, _device_id="dev")
        cloud._install_appliance_code_retry(client)
        client.api_request("/v1/appliance/transparent/send", {"applianceId": "99", "funId": "0000"})
        endpoint, args, data = seen[0]
        self.assertIn("transparent/send", endpoint)
        self.assertEqual(args["applianceCode"], "99")
        self.assertNotIn("applianceId", args)
        self.assertIn("reqId", data)
        self.assertNotIn("appVNum", data)

    def test_off_readback(self) -> None:
        client = SimpleNamespace(list_appliances=lambda: [{"name": "A", "id": "1", "type": "0xac"}])
        device = DummyDevice(running=False, target_c=22.2)
        cloud.turn_off(client, "A", appliance_state_fn=lambda **_: device)
        device.set_state.assert_called_once()
        self.assertEqual(device.set_state.call_args.kwargs["running"], False)


if __name__ == "__main__":
    unittest.main()
