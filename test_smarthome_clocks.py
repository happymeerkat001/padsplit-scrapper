#!/usr/bin/env python3
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from smarthome import clocks


PIONEER_SLOTS = [
    {"hour": 8, "minute": 0, "cool": 76, "heat": 62},
    {"hour": 14, "minute": 0, "cool": 77, "heat": 62},
    {"hour": 17, "minute": 30, "cool": 77, "heat": 62},
    {"hour": 19, "minute": 0, "cool": 76, "heat": 62},
]


class ClockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.schedules = self.root / "schedules.json"
        self.local = self.root / "clocks.json"
        self.schedules.write_text(
            json.dumps(
                {
                    "1404 pioneer": PIONEER_SLOTS,
                    "3406 green hill": PIONEER_SLOTS,
                    "3414 pebbleshores": PIONEER_SLOTS,
                    "5509 burton": PIONEER_SLOTS,
                    "6623 leanna": PIONEER_SLOTS,
                }
            )
        )
        self.local.write_text(
            json.dumps({"1025 broken crest": {"enabled": False, "slots": PIONEER_SLOTS}})
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_afternoon_pioneer_cool(self) -> None:
        house, slot = clocks.active_cool(
            "1404 Pioneer window",
            datetime(2026, 8, 28, 15, 0),
            schedules_path=self.schedules,
            clocks_path=self.local,
        )
        self.assertEqual(house, "1404 pioneer")
        self.assertEqual(slot.cool, 77)

    def test_midnight_wrap(self) -> None:
        slots = [clocks.Slot(8, 0, 76), clocks.Slot(19, 0, 74)]
        active = clocks.find_active_slot(slots, datetime(2026, 8, 28, 2, 0))
        self.assertEqual(active.cool, 74)

    def test_pebbleshores_has_no_clock(self) -> None:
        self.assertIsNone(clocks.resolve_house("3414 Pebbleshores AC"))
        self.assertIsNone(
            clocks.active_cool(
                "3414 Pebbleshores AC",
                datetime(2026, 8, 28, 15, 0),
                schedules_path=self.schedules,
                clocks_path=self.local,
            )
        )

    def test_unmapped_name(self) -> None:
        self.assertIsNone(clocks.resolve_house("Living room"))

    def test_ambiguous_name(self) -> None:
        with self.assertRaises(clocks.AmbiguousHouseError):
            clocks.resolve_house("Pioneer and Leanna combo")

    def test_broken_crest_disabled(self) -> None:
        self.assertEqual(clocks.resolve_house("1025 Broken Crest"), "1025 broken crest")
        self.assertIsNone(
            clocks.active_cool(
                "1025 Broken Crest",
                datetime(2026, 8, 28, 15, 0),
                schedules_path=self.schedules,
                clocks_path=self.local,
            )
        )

    def test_broken_crest_enabled(self) -> None:
        self.local.write_text(
            json.dumps({"1025 broken crest": {"enabled": True, "slots": PIONEER_SLOTS}})
        )
        house, slot = clocks.active_cool(
            "1025 Broken Crest",
            datetime(2026, 8, 28, 15, 0),
            schedules_path=self.schedules,
            clocks_path=self.local,
        )
        self.assertEqual(house, "1025 broken crest")
        self.assertEqual(slot.cool, 77)

    def test_repo_schedules_have_no_broken_crest(self) -> None:
        text = clocks.SCHEDULES_PATH.read_text()
        self.assertNotIn("broken crest", text.lower())


if __name__ == "__main__":
    unittest.main()
