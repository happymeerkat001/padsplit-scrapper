#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from smarthome import cli, cloud, identity, intent


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.intent_path = Path(self.tmp.name) / "intent.json"
        self.lock = Path(self.tmp.name) / "lock"
        self.identity_path = Path(self.tmp.name) / "identity.json"
        self.cooldown_path = Path(self.tmp.name) / "cooldown.json"
        self.intent_patch = patch.object(intent, "INTENT_PATH", self.intent_path)
        self.lock_patch = patch("smarthome.session.LOCK_PATH", self.lock)
        self.identity_patch = patch.object(identity, "IDENTITY_PATH", self.identity_path)
        self.cool_path_patch = patch("smarthome.session.COOLDOWN_PATH", self.cooldown_path)
        self.intent_patch.start()
        self.lock_patch.start()
        self.identity_patch.start()
        self.cool_path_patch.start()

    def tearDown(self) -> None:
        self.cool_path_patch.stop()
        self.identity_patch.stop()
        self.lock_patch.stop()
        self.intent_patch.stop()
        self.tmp.cleanup()

    def test_set_writes_hold_after_ack(self) -> None:
        with (
            patch.object(cli.session, "cooldown_active", return_value=False),
            patch.object(cli.cloud, "connect", return_value=object()),
            patch.object(cli.cloud, "set_temp") as set_temp,
            patch.object(cli, "_slot_key_for", return_value="1404 pioneer:08:00"),
        ):
            rc = cli.main(["set", "1404 Pioneer window", "72"])
        self.assertEqual(rc, 0)
        set_temp.assert_called_once()
        state = intent.unit_state(intent.load_intent(self.intent_path), "1404 Pioneer window")
        self.assertEqual(state["hold_f"], 72)
        self.assertFalse(state["sticky_off"])

    def test_off_writes_sticky_after_ack(self) -> None:
        with (
            patch.object(cli.session, "cooldown_active", return_value=False),
            patch.object(cli.cloud, "connect", return_value=object()),
            patch.object(cli.cloud, "turn_off"),
        ):
            rc = cli.main(["off", "Mystery unit"])
        self.assertEqual(rc, 0)
        self.assertTrue(intent.is_sticky_off("Mystery unit", path=self.intent_path))

    def test_failed_set_does_not_write_intent(self) -> None:
        def boom(*_a, **_k):
            raise cloud.UnknownNameError("nope")

        notify = MagicMock()
        with (
            patch.object(cli.session, "cooldown_active", return_value=False),
            patch.object(cli.cloud, "connect", return_value=object()),
            patch.object(cli.cloud, "set_temp", side_effect=boom),
            patch.object(cli, "_notify", notify),
        ):
            rc = cli.main(["set", "nope", "72"])
        self.assertEqual(rc, 1)
        self.assertEqual(intent.load_intent(self.intent_path)["units"], {})
        notify.assert_called()

    def test_status_empty(self) -> None:
        rc = cli.main(["status"])
        self.assertEqual(rc, 0)

    def test_unmapped_set_still_works(self) -> None:
        with (
            patch.object(cli.session, "cooldown_active", return_value=False),
            patch.object(cli.cloud, "connect", return_value=object()),
            patch.object(cli.cloud, "set_temp"),
            patch.object(cli, "_slot_key_for", return_value=None),
        ):
            rc = cli.main(["set", "Living room", "72"])
        self.assertEqual(rc, 0)
        state = intent.unit_state(intent.load_intent(self.intent_path), "Living room")
        self.assertEqual(state["hold_f"], 72)
        self.assertIsNone(state["hold_slot_key"])

    def test_list_session_limit_no_discord_no_intent(self) -> None:
        notify = MagicMock()
        connect = MagicMock(side_effect=cloud.SmartHomeSessionLimitError("设备数量超限"))
        with (
            patch.object(cli.cloud, "connect", connect),
            patch.object(cli, "_notify", notify),
        ):
            rc = cli.main(["list"])
        self.assertEqual(rc, 1)
        notify.assert_not_called()
        self.assertEqual(intent.load_intent(self.intent_path)["units"], {})
        self.assertTrue(cli.session.cooldown_active(fingerprint="msmarthome"))

    def test_set_session_limit_leaves_intent_unchanged(self) -> None:
        notify = MagicMock()
        with (
            patch.object(cli.session, "cooldown_active", return_value=False),
            patch.object(
                cli.cloud,
                "connect",
                side_effect=cloud.SmartHomeSessionLimitError("设备数量超限"),
            ),
            patch.object(cli, "_notify", notify),
        ):
            rc = cli.main(["set", "Broken Crest A", "74"])
        self.assertEqual(rc, 1)
        notify.assert_not_called()
        self.assertEqual(intent.load_intent(self.intent_path)["units"], {})

    def test_cooldown_blocks_same_fingerprint_connect(self) -> None:
        identity.load_or_create(self.identity_path)
        cli.session.start_cooldown(path=self.cooldown_path, fingerprint="msmarthome")
        connect = MagicMock()
        notify = MagicMock()
        with (
            patch.object(cli.cloud, "connect", connect),
            patch.object(cli, "_notify", notify),
        ):
            rc = cli.main(["list"])
        self.assertEqual(rc, 2)
        connect.assert_not_called()
        notify.assert_not_called()
        self.assertEqual(intent.load_intent(self.intent_path)["units"], {})

    def test_fingerprint_change_bypasses_cooldown_once(self) -> None:
        identity.select_fingerprint("msmarthome-slim", self.identity_path)
        cli.session.start_cooldown(path=self.cooldown_path, fingerprint="msmarthome")
        connect = MagicMock(return_value=object())
        with (
            patch.object(cli.cloud, "connect", connect),
            patch.object(cli.cloud, "list_acs", return_value=[]),
            patch.object(cli, "_notify", MagicMock()),
        ):
            rc = cli.main(["list"])
        self.assertEqual(rc, 0)
        connect.assert_called_once()

    def test_spike_sets_listed_broken_crest_and_sylvia(self) -> None:
        set_temp = MagicMock()
        names = ["Broken Crest living", "Broken Crest bedroom", "Sylvia window"]
        with (
            patch.object(cli.session, "cooldown_active", return_value=False),
            patch.object(cli.cloud, "connect", return_value=object()),
            patch.object(cli.cloud, "set_temp", set_temp),
            patch.object(cli, "_slot_key_for", return_value=None),
        ):
            for name in names:
                self.assertEqual(cli.main(["set", name, "74"]), 0)
        self.assertEqual(
            [(call.args[1], call.args[2]) for call in set_temp.call_args_list],
            [(name, 74) for name in names],
        )


if __name__ == "__main__":
    unittest.main()
