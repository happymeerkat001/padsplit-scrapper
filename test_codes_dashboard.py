#!/usr/bin/env python3
"""Structure-only tests for the codes dashboard. Never print field values."""

import re
import subprocess
import unittest
from pathlib import Path


HTML_PATH = Path(__file__).resolve().parent / "docs" / "codes.html"
OCCUPANCY_PATH = Path(__file__).resolve().parent / "docs" / "data" / "occupancy.json"

HOUSE_SLUGS = [
    "leana_6623",
    "sylvia_2516",
    "ridge_oak_10235",
    "pebbleshores_3414",
    "greenhill_3406",
    "parker_4351",
    "pioneer_1404",
    "burton_5509",
    "broken_crest_1025",
]

ROOM_COUNTS = {
    "leana_6623": 5,
    "sylvia_2516": 6,
    "ridge_oak_10235": 6,
    "pebbleshores_3414": 6,
    "greenhill_3406": 7,
    "parker_4351": 8,
    "pioneer_1404": 7,
    "burton_5509": 7,
    "broken_crest_1025": 9,
}

LEGACY_LOCKBOX_COUNTS = {
    "parker_4351": 8,
    "burton_5509": 7,
}

CONTACT_OPS_KEYS = (
    "ac_filter_date",
    "ac_filter_size",
    "dryer_lint_date",
    "dryer_lint_notes",
)

EXTRA_LOCKBOX_COUNT = 2

SLUG_RE = re.compile(r'slug:\s*"([a-z0-9_]+)"')
FIELD_KEY_RE = re.compile(r'key:\s*"([a-z0-9_]+)"')
KEY_VALUE_RE = re.compile(r'key:\s*"([a-z0-9_]+)"[^\n]*value:\s*"([^"]*)"')


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _slugs(html: str) -> list[str]:
    return SLUG_RE.findall(html)


def _defaults_block(html: str) -> str:
    start = html.index("const DEFAULTS = [")
    end = html.index("];", start)
    return html[start:end]


def _house_blocks(html: str) -> dict[str, str]:
    block = _defaults_block(html)
    parts = re.split(r'slug:\s*"', block)
    houses = {}
    for part in parts[1:]:
        slug, rest = part.split('"', 1)
        houses[slug] = rest
    return houses


def _field_keys(block: str) -> list[str]:
    return FIELD_KEY_RE.findall(block)


def _key_values(block: str) -> dict[str, str]:
    return dict(KEY_VALUE_RE.findall(block))


def room_ops_keys(room_count: int) -> list[str]:
    keys = []
    for n in range(1, room_count + 1):
        keys.extend(
            [
                f"r{n}",
                f"lockbox_{n}",
                f"lockbox_{n}_location",
                f"lockbox_{n}_notes",
                f"r{n}_ac_filter_size",
            ]
        )
    return keys


def extra_lockbox_keys(n: int) -> list[str]:
    return [
        f"extra_lockbox_{n}_name",
        f"extra_lockbox_{n}_code",
        f"extra_lockbox_{n}_location",
        f"extra_lockbox_{n}_notes",
    ]


def expected_save_ops_keys(room_count: int) -> set[str]:
    keys = set(room_ops_keys(room_count))
    keys.update(CONTACT_OPS_KEYS)
    for n in range(1, EXTRA_LOCKBOX_COUNT + 1):
        keys.update(extra_lockbox_keys(n))
    return keys


class CodesDashboardStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _html()
        cls.defaults = _defaults_block(cls.html)
        cls.houses = _house_blocks(cls.html)

    def test_exactly_nine_known_houses(self):
        slugs = _slugs(self.html)
        self.assertEqual(slugs, HOUSE_SLUGS)

    def test_room_counts_match_defaults(self):
        for slug, count in ROOM_COUNTS.items():
            keys = _field_keys(self.houses[slug])
            room_keys = [k for k in keys if re.fullmatch(r"r\d+", k)]
            self.assertEqual(
                room_keys,
                [f"r{n}" for n in range(1, count + 1)],
                msg=f"{slug} room keys",
            )

    def test_contact_ops_keys_on_every_house(self):
        for slug, block in self.houses.items():
            keys = set(_field_keys(block))
            for key in CONTACT_OPS_KEYS:
                self.assertIn(key, keys, msg=f"{slug} missing {key}")
            self.assertIn('label: "AC filter date"', block)
            self.assertIn('placeholder: "16x25x1"', block)

    def test_new_ops_fields_have_empty_defaults(self):
        for slug, block in self.houses.items():
            values = _key_values(block)
            for key in ("ac_filter_size", "dryer_lint_date", "dryer_lint_notes"):
                self.assertEqual(values.get(key), "", msg=f"{slug} {key} must be empty")

    def test_rooms_table_columns_exist(self):
        for header in (
            "Room",
            "Room code",
            "Lockbox code",
            "Lockbox location",
            "Lockbox notes",
            "AC filter size",
        ):
            self.assertIn(f"'{header}'", self.html)
        self.assertIn("function lockboxCodeKey(n) { return `lockbox_${n}`; }", self.html)
        self.assertIn("function lockboxLocationKey(n) { return `lockbox_${n}_location`; }", self.html)
        self.assertIn("function lockboxNotesKey(n) { return `lockbox_${n}_notes`; }", self.html)
        self.assertIn("function roomAcFilterSizeKey(n) { return `r${n}_ac_filter_size`; }", self.html)

    def test_save_payload_includes_new_ops_keys(self):
        for slug, count in ROOM_COUNTS.items():
            expected = expected_save_ops_keys(count)
            self.assertIn("r${n}_ac_filter_size", self.html)
            self.assertIn("lockbox_${n}_location", self.html)
            self.assertIn("lockbox_${n}_notes", self.html)
            self.assertIn("extra_lockbox_${n}_name", self.html)
            self.assertIn("extra_lockbox_${n}_code", self.html)
            self.assertTrue(expected.issuperset({"r1", "lockbox_1", "ac_filter_size", "dryer_lint_date"}))
            self.assertIn(f"lockbox_{count}", expected)
            self.assertIn(f"r{count}_ac_filter_size", expected)

    def test_legacy_lockbox_n_keys_still_in_defaults(self):
        for slug, count in LEGACY_LOCKBOX_COUNTS.items():
            keys = _field_keys(self.houses[slug])
            for n in range(1, count + 1):
                self.assertIn(f"lockbox_{n}", keys, msg=f"{slug} missing lockbox_{n}")

    def test_lockbox_n_still_maps_as_code_field(self):
        self.assertIn("const lockboxKey = lockboxCodeKey(n);", self.html)
        self.assertIn("defaultFieldValue(property, lockboxKey, '')", self.html)
        self.assertIn("if (section.label === 'Lockboxes') return;", self.html)

    def test_extra_lockboxes_on_every_house(self):
        self.assertIn("const EXTRA_LOCKBOX_COUNT = 2;", self.html)
        self.assertIn("Extra Lockboxes", self.html)
        for n in range(1, EXTRA_LOCKBOX_COUNT + 1):
            for key in extra_lockbox_keys(n):
                template = key.replace(str(n), "${n}")
                self.assertIn(template, self.html)

    def test_overdue_windows(self):
        self.assertIn("ac_filter_date: 90", self.html)
        self.assertIn("dryer_lint_date: 30", self.html)

    def test_firestore_merge_and_gate_unchanged(self):
        self.assertIn("collection(db, 'property_codes')", self.html)
        self.assertIn("doc(db, 'property_codes', slug)", self.html)
        self.assertIn("{ merge: true }", self.html)
        self.assertIn("signInWithEmailAndPassword", self.html)
        self.assertIn("doc(db, 'notes', 'codes')", self.html)
        self.assertIn('id="gate-wrap"', self.html)

    def test_occupancy_json_has_no_codes_or_filter_sizes(self):
        occupancy = OCCUPANCY_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "ac_filter_size",
            "dryer_lint",
            "lockbox_",
            "room_code",
            "wifi_",
        ):
            self.assertNotIn(forbidden, occupancy)

    def test_renderer_payload_keys_with_dummy_house(self):
        result = subprocess.run(
            ["node", str(Path(__file__).resolve().parent / "test_codes_dashboard_render.mjs")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg="renderer structure test failed")
        self.assertIn("codes dashboard renderer structure ok", result.stdout)

    def test_existing_default_values_unchanged(self):
        main_html = None
        for ref in ("origin/main", "main"):
            result = subprocess.run(
                ["git", "show", f"{ref}:docs/codes.html"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                main_html = result.stdout
                break
        if main_html is None:
            self.skipTest("main codes.html not available for comparison")
        main_houses = _house_blocks(main_html)
        for slug, main_block in main_houses.items():
            current = _key_values(self.houses[slug])
            previous = _key_values(main_block)
            for key, old_value in previous.items():
                self.assertIn(key, current, msg=f"{slug} dropped key {key}")
                self.assertEqual(current[key], old_value, msg=f"{slug} changed default for {key}")


if __name__ == "__main__":
    unittest.main()
