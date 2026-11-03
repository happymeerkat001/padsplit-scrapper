#!/usr/bin/env python3
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

from smarthome import clocks


SLOTS = [
    {"hour": 8, "minute": 0, "cool": 76, "heat": 62},
    {"hour": 14, "minute": 0, "cool": 77, "heat": 62},
    {"hour": 17, "minute": 30, "cool": 77, "heat": 62},
    {"hour": 19, "minute": 0, "cool": 76, "heat": 62},
]
FAKE_ALIASES = ((('maple',), '101 maple', None), (('cedar','view'), '102 cedar view', None),
                (('willow',), '103 willow', None), (('sample','fictional17'), None, '104 sample fictional17'))


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.schedules = self.root / 'schedules.json'
        self.local = self.root / 'clocks.json'
        self.schedules.write_text(json.dumps({'101 maple': SLOTS, '102 cedar view': SLOTS, '103 willow': SLOTS}))
        self.local.write_text(json.dumps({'104 sample fictional17': {'enabled': False, 'slots': SLOTS}}))
        patcher = patch.object(clocks, 'ALLOW_LIST', FAKE_ALIASES)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_afternoon_schedule(self):
        house, slot = clocks.active_cool('101 Maple window', datetime(2026, 8, 28, 15, 0),
                                         schedules_path=self.schedules, clocks_path=self.local)
        self.assertEqual(house, '101 maple'); self.assertEqual(slot.cool, 77)

    def test_midnight_wrap(self):
        slots = [clocks.Slot(8, 0, 76), clocks.Slot(19, 0, 74)]
        self.assertEqual(clocks.find_active_slot(slots, datetime(2026, 8, 28, 2, 0)).cool, 74)

    def test_unmapped_name(self):
        self.assertIsNone(clocks.resolve_house('Living room'))
        self.assertIsNone(clocks.active_cool('Living room', datetime(2026, 8, 28, 15, 0),
                                            schedules_path=self.schedules, clocks_path=self.local))

    def test_missing_schedule_does_not_invent_clock(self):
        self.schedules.write_text('{}')
        self.assertIsNone(clocks.active_cool('101 Maple window', datetime(2026, 8, 28, 15, 0),
                                            schedules_path=self.schedules, clocks_path=self.local))

    def test_ambiguous_name(self):
        with self.assertRaises(clocks.AmbiguousHouseError): clocks.resolve_house('Maple and Willow combo')

    def test_private_clock_disabled(self):
        self.assertEqual(clocks.resolve_house('104 Sample Fictional17'), '104 sample fictional17')
        self.assertIsNone(clocks.active_cool('104 Sample Fictional17', datetime(2026, 8, 28, 15, 0),
                                             schedules_path=self.schedules, clocks_path=self.local))

    def test_private_clock_enabled(self):
        self.local.write_text(json.dumps({'104 sample fictional17': {'enabled': True, 'slots': SLOTS}}))
        house, slot = clocks.active_cool('104 Sample Fictional17', datetime(2026, 8, 28, 15, 0),
                                         schedules_path=self.schedules, clocks_path=self.local)
        self.assertEqual(house, '104 sample fictional17'); self.assertEqual(slot.cool, 77)


if __name__ == '__main__': unittest.main()
