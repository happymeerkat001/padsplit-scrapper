#!/usr/bin/env python3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from smarthome import intent, policy


class IntentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "intent.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_hold_clears_off(self) -> None:
        intent.record_off("Unit A", path=self.path)
        intent.record_hold("Unit A", 72, "1404 pioneer:08:00", path=self.path)
        state = intent.unit_state(intent.load_intent(self.path), "Unit A")
        self.assertFalse(state["sticky_off"])
        self.assertEqual(state["hold_f"], 72)

    def test_failed_ack_does_not_write(self) -> None:
        intent.record_off("Unit A", path=self.path)
        # Caller only writes after ACK; simulate failed set by not calling record_hold.
        self.assertTrue(intent.is_sticky_off("Unit A", path=self.path))

    def test_empty_file_is_readable(self) -> None:
        payload = intent.load_intent(self.path)
        self.assertEqual(payload["units"], {})


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.intent_path = Path(self.tmp.name) / "intent.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sticky_off_beats_clock(self) -> None:
        intent.record_off("1404 Pioneer window", path=self.intent_path)
        action = policy.resolve_action(
            "1404 Pioneer window",
            datetime(2026, 8, 28, 12, 0),
            intent_path=self.intent_path,
        )
        self.assertEqual(action["kind"], "off")

    def test_day_is_floor(self) -> None:
        action = policy.resolve_action(
            "Sylvia rm 6",
            datetime(2026, 8, 29, 10, 0),
            intent_path=self.intent_path,
        )
        self.assertEqual(action["kind"], "floor")
        self.assertEqual(action["f"], 74)

    def test_night_off(self) -> None:
        action = policy.resolve_action(
            "Broken crest",
            datetime(2026, 8, 29, 2, 0),
            intent_path=self.intent_path,
        )
        self.assertEqual(action["kind"], "night_off")


if __name__ == "__main__":
    unittest.main()
