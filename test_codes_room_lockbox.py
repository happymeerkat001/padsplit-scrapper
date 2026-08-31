#!/usr/bin/env python3
"""Spec for Codes tab room/lockbox pairing and conflict merge."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

CODES_HTML = Path(__file__).resolve().parent / "docs" / "codes.html"

PAIRING = {
    "leana_6623": [
        ("R1", "r1", "2721", "lockbox_1", "1010"),
        ("R2", "r2", "5410", "lockbox_2", "1320"),
        ("R3", "r3", "1069", "lockbox_3", "2121"),
        ("R4", "r4", "3414", "lockbox_4", "5011"),
        ("R5", "r5", "7520", "lockbox_5", "3002"),
        ("R6", "r6", "", "lockbox_6", "4003"),
    ],
    "sylvia_2516": [
        ("R1", "r1", "7653", "lockbox_1", "1212"),
        ("R2", "r2", "6512", "lockbox_2", "3112"),
        ("R3", "r3", "1069", "lockbox_3", "5132"),
        ("R4", "r4", "1304", "lockbox_4", "2100"),
        ("R5", "r5", "5410", "lockbox_5", "4245"),
        ("R6", "r6", "2024", "lockbox_6", "4011"),
    ],
    "ridge_oak_10235": [
        ("R1", "r1", "6510", "lockbox_1", "2011"),
        ("R2", "r2", "1304", "lockbox_2", "2400"),
        ("R3", "r3", "7653", "lockbox_3", "1211"),
        ("R4", "r4", "5410", "lockbox_4", "5168"),
        ("R5", "r5", "1069", "lockbox_5", "3311"),
        ("R6", "r6", "3414", "lockbox_6", ""),
    ],
    "pebbleshores_3414": [
        ("R1", "r1", "7845 (or 5613?)", "lockbox_1", "2100"),
        ("R2", "r2", "4590", "lockbox_2", "4132"),
        ("R3", "r3", "3506", "lockbox_3", "5111"),
        ("R4", "r4", "1314", "lockbox_4", "1111"),
        ("R5", "r5", "2398", "lockbox_5", "4019"),
        ("R6", "r6", "6510", "lockbox_6", "5168"),
    ],
    "greenhill_3406": [
        ("R1", "r1", "1604", "lockbox_1", "8002"),
        ("R2", "r2", "2406", "lockbox_2", "2011"),
        ("R3", "r3", "3604", "lockbox_3", "1111"),
        ("R4", "r4", "4406", "lockbox_4", "9001"),
        ("R5", "r5", "5604", "lockbox_5", "5500"),
        ("R6", "r6", "5006", "lockbox_6", "1968"),
        ("R7", "r7", "7503", "lockbox_7", "7777"),
    ],
    "parker_4351": [
        ("R1", "r1", "1587", "lockbox_1", "1244"),
        ("R2", "r2", "0411", "lockbox_2", "3344"),
        ("R3", "r3", "9239", "lockbox_3", "4011"),
        ("R4", "r4", "3454", "lockbox_4", "5312"),
        ("R5", "r5", "2523", "lockbox_5", "7233"),
        ("R6", "r6", "6584", "lockbox_6", "5168"),
        ("R7", "r7", "9012", "lockbox_7", "0228"),
        ("R8", "r8", "0629", "lockbox_8", "3211"),
    ],
    "pioneer_1404": [
        ("R1", "r1", "3450", "keys_1", "1111"),
        ("R2", "r2", "9011", "keys_2", "2010"),
        ("R3", "r3", "7712", "keys_3", "3111"),
        ("R4", "r4", "0328 (or 1755?)", "keys_4", "0400"),
        ("R5", "r5", "2846", "keys_5", "5115"),
        ("R6", "r6", "8950", "keys_6", "6330"),
        ("R7", "r7", "5252", "keys_7", "7222"),
    ],
    "burton_5509": [
        ("R1", "r1", "232323", "lockbox_1", "1100"),
        ("R2", "r2", "242424", "lockbox_2", "2110"),
        ("R3", "r3", "141414", "lockbox_3", "3000"),
        ("R4", "r4", "456090", "lockbox_4", "4321"),
        ("R5", "r5", "504530", "lockbox_5", "5220"),
        ("R6", "r6", "313131", "lockbox_6", "6111"),
        ("R7", "r7", "121212", "lockbox_7", "7222"),
    ],
    "broken_crest_1025": [
        ("R1", "r1", "9119 (lockbox)", "lockbox_1", ""),
        ("R2", "r2", "0079 (lockbox)", "lockbox_2", ""),
        ("R3", "r3", "7633 (lockbox)", "lockbox_3", ""),
        ("R4", "r4", "1717", "lockbox_4", ""),
        ("R5", "r5", "6964", "lockbox_5", ""),
        ("R6", "r6", "5376", "lockbox_6", ""),
        ("R7", "r7", "2573", "lockbox_7", ""),
        ("R8", "r8", "6553", "lockbox_8", ""),
        ("R9", "r9", "4554", "lockbox_9", ""),
    ],
}

ROOM_FIELD_RE = re.compile(
    r'\{ label: "(R\d+)", door: \{ key: "([^"]+)", value: "([^"]*)" \}, '
    r'lockbox: \{ key: "([^"]+)", value: "([^"]*)" \} \}'
)


def merge_code_values(existing: str, incoming: str) -> str:
    current = (existing or "").strip()
    added = (incoming or "").strip()
    if not added:
        return current
    if not current:
        return added
    if added == current or added in current:
        return current
    return f"{current} (or {added}?)"


def parse_room_defaults(html: str) -> dict[str, list[tuple[str, str, str, str, str]]]:
    parsed: dict[str, list[tuple[str, str, str, str, str]]] = {}
    for block in re.split(r'\n\s*\{\s*\n\s*slug:', html):
        slug_match = re.match(r'\s*"([^"]+)"', block)
        if not slug_match:
            continue
        slug = slug_match.group(1)
        rooms_match = re.search(r'\{ label: "Rooms", fields: \[(.*?)\]\s*\}', block, re.S)
        if not rooms_match:
            continue
        parsed[slug] = [
            (label, door_key, door_val, box_key, box_val)
            for label, door_key, door_val, box_key, box_val in ROOM_FIELD_RE.findall(rooms_match.group(1))
        ]
    return parsed


class MergeCodeValuesTests(unittest.TestCase):
    def test_keeps_existing_conflict_when_incoming_already_present(self) -> None:
        self.assertEqual(merge_code_values("0961 (or 8338?)", "0961"), "0961 (or 8338?)")

    def test_identical_values_stay_single(self) -> None:
        self.assertEqual(merge_code_values("2721", "2721"), "2721")

    def test_empty_existing_takes_incoming(self) -> None:
        self.assertEqual(merge_code_values("", "4003"), "4003")

    def test_keeps_richer_conflict_string(self) -> None:
        self.assertEqual(merge_code_values("0328 (or 1755?)", "0328"), "0328 (or 1755?)")

    def test_appends_distinct_incoming(self) -> None:
        self.assertEqual(merge_code_values("1000", "2000"), "1000 (or 2000?)")


class CodesDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = CODES_HTML.read_text()
        cls.rooms = parse_room_defaults(cls.html)

    def test_pebbleshores_front_back_keeps_both_values(self) -> None:
        self.assertIn('{ key: "front_back", label: "Front/Back Door", value: "0961 (or 8338?)" }', self.html)

    def test_no_standalone_lockbox_or_keys_sections(self) -> None:
        self.assertNotIn('{ label: "Lockboxes"', self.html)
        self.assertNotIn('{ label: "Other"', self.html)

    def test_rooms_use_paired_table_renderer(self) -> None:
        self.assertIn('section.label === "Rooms"', self.html)
        self.assertIn("room-table", self.html)
        self.assertIn("Door code", self.html)
        self.assertIn("Lockbox", self.html)

    def test_pairing_table_matches_defaults(self) -> None:
        self.assertEqual(set(self.rooms), set(PAIRING))
        for slug, expected in PAIRING.items():
            self.assertEqual(self.rooms[slug], expected, slug)


if __name__ == "__main__":
    unittest.main()
