#!/usr/bin/env python3
import inspect
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import smarthome.watcher as watcher
from smarthome import intent


class WatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.intent_path = Path(self.tmp.name) / "intent.json"
        self.streak = Path(self.tmp.name) / "streak.json"
        self.digest = Path(self.tmp.name) / "digest.json"
        self.lock = Path(self.tmp.name) / "lock"
        self.cooldown_path = Path(self.tmp.name) / "cooldown.json"
        self.identity_path = Path(self.tmp.name) / "identity.json"
        self.intent_patch = patch.object(intent, "INTENT_PATH", self.intent_path)
        self.streak_patch = patch.object(watcher, "STREAK_PATH", self.streak)
        self.digest_patch = patch.object(watcher, "DIGEST_PATH", self.digest)
        self.lock_patch = patch("smarthome.session.LOCK_PATH", self.lock)
        self.cool_path_patch = patch("smarthome.session.COOLDOWN_PATH", self.cooldown_path)
        self.identity_patch = patch("smarthome.identity.IDENTITY_PATH", self.identity_path)
        self.cool_patch = patch("smarthome.session.cooldown_active", return_value=False)
        self.intent_patch.start()
        self.streak_patch.start()
        self.digest_patch.start()
        self.lock_patch.start()
        self.cool_path_patch.start()
        self.identity_patch.start()
        self.cool_patch.start()

    def tearDown(self) -> None:
        for patcher in (
            self.cool_patch,
            self.identity_patch,
            self.cool_path_patch,
            self.lock_patch,
            self.digest_patch,
            self.streak_patch,
            self.intent_patch,
        ):
            patcher.stop()
        self.tmp.cleanup()

    def test_does_not_import_honeywell_writers(self) -> None:
        source = inspect.getsource(watcher)
        self.assertNotIn("set_temps", source)
        self.assertNotIn("thermostat.schedule", source)
        self.assertNotIn("mytotalconnectcomfort", source)

    def _dev(self, *, running: bool, celsius: float) -> SimpleNamespace:
        return SimpleNamespace(state=SimpleNamespace(running=running, target_temperature=celsius))

    def test_floor_leaves_76_and_raises_low(self) -> None:
        intent.record_off("Off Unit", path=self.intent_path)
        set_temp = MagicMock()
        turn_off = MagicMock()
        devices = {
            "1": self._dev(running=True, celsius=23.3),
            "2": self._dev(running=True, celsius=24.4),
            "3": self._dev(running=True, celsius=21.1),
        }
        units = [
            {"name": "Off Unit", "id": "1", "type": "0xac"},
            {"name": "Broken crest", "id": "2", "type": "0xac"},
            {"name": "Sylvia rm 6", "id": "3", "type": "0xac"},
        ]
        result = watcher.enforce_tick(
            now=datetime(2026, 8, 29, 10, 0),
            connect_fn=lambda: object(),
            set_temp_fn=set_temp,
            turn_off_fn=turn_off,
            list_acs_fn=lambda _c: units,
            device_fn=lambda _c, aid: devices[str(aid)],
            intent_path=self.intent_path,
            notify_fn=lambda _t: None,
        )
        self.assertEqual(result["failed"], [])
        turn_off.assert_called_once_with(ANY, "Off Unit")
        set_names = [call.args[1] for call in set_temp.call_args_list]
        self.assertEqual(set_names, ["Sylvia rm 6"])
        self.assertEqual(set_temp.call_args.args[2], 74)

    def test_partial_failure_notifies(self) -> None:
        notes = []

        def set_temp(_c, name, _f):
            if "Green Hill" in name:
                raise RuntimeError("nope")

        result = watcher.enforce_tick(
            now=datetime(2026, 8, 28, 10, 0),
            connect_fn=lambda: object(),
            set_temp_fn=set_temp,
            turn_off_fn=lambda *_a, **_k: None,
            list_acs_fn=lambda _c: [
                {"name": "1404 Pioneer window", "id": "1", "type": "0xac"},
                {"name": "3406 Green Hill window", "id": "2", "type": "0xac"},
            ],
            device_fn=lambda *_a, **_k: self._dev(running=True, celsius=21.1),
            intent_path=self.intent_path,
            notify_fn=notes.append,
        )
        self.assertEqual(result["failed"], ["3406 Green Hill window"])
        self.assertTrue(any("Failed: 3406 Green Hill window" in note for note in notes))

    def test_auth_failure_does_not_flip_intent(self) -> None:
        intent.record_off("Off Unit", path=self.intent_path)

        def boom():
            raise watcher.cloud.SmartHomeAuthError("nope")

        watcher.enforce_tick(
            now=datetime(2026, 8, 28, 10, 0),
            connect_fn=boom,
            intent_path=self.intent_path,
            notify_fn=lambda _t: None,
        )
        self.assertTrue(intent.is_sticky_off("Off Unit", path=self.intent_path))

    def test_night_turns_on_units_off(self) -> None:
        turn_off = MagicMock()
        set_temp = MagicMock()
        watcher.enforce_tick(
            now=datetime(2026, 8, 29, 2, 0),
            connect_fn=lambda: object(),
            set_temp_fn=set_temp,
            turn_off_fn=turn_off,
            list_acs_fn=lambda _c: [{"name": "Broken crest", "id": "1", "type": "0xac"}],
            device_fn=lambda *_a, **_k: self._dev(running=True, celsius=24.4),
            intent_path=self.intent_path,
            notify_fn=lambda _t: None,
        )
        turn_off.assert_called_once_with(ANY, "Broken crest")
        set_temp.assert_not_called()

    def test_digest_posts_at_scheduled_hour(self) -> None:
        notes = []
        watcher.enforce_tick(
            now=datetime(2026, 8, 29, 14, 5),
            connect_fn=lambda: object(),
            set_temp_fn=lambda *_a, **_k: None,
            list_acs_fn=lambda _c: [{"name": "Sylvia rm 6", "id": "1", "type": "0xac"}],
            device_fn=lambda *_a, **_k: self._dev(running=True, celsius=24.4),
            intent_path=self.intent_path,
            notify_fn=notes.append,
        )
        self.assertTrue(any(note.startswith("SmartHome 14:05") for note in notes))
        notes.clear()
        watcher.enforce_tick(
            now=datetime(2026, 8, 29, 14, 40),
            connect_fn=lambda: object(),
            set_temp_fn=lambda *_a, **_k: None,
            list_acs_fn=lambda _c: [{"name": "Sylvia rm 6", "id": "1", "type": "0xac"}],
            device_fn=lambda *_a, **_k: self._dev(running=True, celsius=24.4),
            intent_path=self.intent_path,
            notify_fn=notes.append,
        )
        self.assertFalse(any(note.startswith("SmartHome ") for note in notes))

    def test_plist_shape(self) -> None:
        payload = watcher.build_plist()
        self.assertEqual(payload["Label"], "com.padsplit.smarthome.watcher")
        self.assertEqual(payload["StartInterval"], 3600)
        self.assertNotIn("SMARTHOME_PASSWORD", str(payload))
        self.assertTrue(str(payload["ProgramArguments"][1]).endswith("smarthome.watcher") or "-m" in payload["ProgramArguments"])

    def test_session_limit_notify_is_english_label(self) -> None:
        notes = []

        def boom():
            raise watcher.cloud.SmartHomeSessionLimitError("设备数量超限 65027")

        result = watcher.enforce_tick(
            now=datetime(2026, 8, 29, 10, 0),
            connect_fn=boom,
            intent_path=self.intent_path,
            notify_fn=notes.append,
        )
        self.assertTrue(result["skipped"])
        self.assertEqual(notes, ["SmartHome watcher: session limit"])
        self.assertNotIn("设备数量超限", notes[0])
        self.assertNotIn("65027", notes[0])
        self.assertNotIn("SMARTHOME_PASSWORD", notes[0])


if __name__ == "__main__":
    unittest.main()
