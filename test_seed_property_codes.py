#!/usr/bin/env python3
"""Seed-script tests. Uses placeholders only and never prints field values."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import seed_property_codes as seed  # noqa: E402


PLACEHOLDER = "placeholder-not-a-code"
DIGIT_PLACEHOLDER = "9999"


class SeedPropertyCodesTests(unittest.TestCase):
    def test_dry_run_prints_counts_and_keys_only(self):
        payload = seed.normalize_payload(
            {
                "example_house": {
                    "front_door": PLACEHOLDER,
                    "r1": DIGIT_PLACEHOLDER,
                    "updatedAt": "ignore-me",
                },
                "spanish_moss": {"back_door": ""},
            }
        )
        report = seed.dry_run_report(payload)
        self.assertIn("properties: 2", report)
        self.assertIn("example_house: 2 keys", report)
        self.assertIn("keys: front_door, r1", report)
        self.assertIn("spanish_moss: 1 keys", report)
        self.assertIn("keys: back_door", report)
        self.assertNotIn(PLACEHOLDER, report)
        self.assertNotIn(DIGIT_PLACEHOLDER, report)
        self.assertNotIn("ignore-me", report)

    def test_cli_dry_run_does_not_echo_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "property_codes.local.json"
            path.write_text(
                json.dumps(
                    {
                        "example_house": {
                            "wifi_primary": PLACEHOLDER,
                            "lockbox_1": DIGIT_PLACEHOLDER,
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "seed_property_codes.py"), "--dry-run", "--file", str(path)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("properties: 1", result.stdout)
        self.assertIn("keys: lockbox_1, wifi_primary", result.stdout)
        self.assertNotIn(PLACEHOLDER, result.stdout)
        self.assertNotIn(PLACEHOLDER, result.stderr)
        self.assertNotIn(DIGIT_PLACEHOLDER, result.stdout)
        self.assertNotIn(DIGIT_PLACEHOLDER, result.stderr)

    def test_invalid_json_does_not_echo_file_text(self):
        secret = "LEAK-ME-9999"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text('{ "example_house": "' + secret, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "seed_property_codes.py"), "--dry-run", "--file", str(path)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not valid JSON", result.stderr)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)

    def test_non_string_value_is_rejected_without_echoing_it(self):
        with self.assertRaises(ValueError) as raised:
            seed.normalize_payload({"example_house": {"front_door": 987654}})
        message = str(raised.exception)
        self.assertIn("front_door", message)
        self.assertIn("must be a string", message)
        self.assertNotIn("987654", message)

    def test_refuses_codes_html(self):
        with self.assertRaises(ValueError) as raised:
            seed.load_local_payload(ROOT / "docs" / "codes.html")
        self.assertIn("refusing", str(raised.exception))

    def test_missing_file(self):
        with self.assertRaises(ValueError) as raised:
            seed.load_local_payload(ROOT / "property_codes.local.json")
        self.assertIn("missing", str(raised.exception))

    def test_write_merges_and_does_not_log_values(self):
        writes = []

        class _Doc:
            def __init__(self, slug):
                self.slug = slug

            def set(self, data, merge=False):
                writes.append((self.slug, data, merge))

        class _Client:
            def collection(self, name):
                if name != "property_codes":
                    raise AssertionError("unexpected collection")
                return self

            def document(self, slug):
                return _Doc(slug)

        payload = {"example_house": {"front_door": PLACEHOLDER}}
        count = seed.write_payload(_Client(), payload)
        self.assertEqual(count, 1)
        slug, data, merge = writes[0]
        self.assertEqual(slug, "example_house")
        self.assertTrue(merge)
        self.assertEqual(data["front_door"], PLACEHOLDER)
        self.assertIn("updatedAt", data)
        report = seed.dry_run_report(payload)
        self.assertNotIn(PLACEHOLDER, report)

    def test_script_does_not_read_repo_codes_page(self):
        source = (ROOT / "scripts" / "seed_property_codes.py").read_text(encoding="utf-8")
        self.assertIn("refusing to read docs/codes.html", source)
        self.assertNotIn("read_text", source.split("def load_local_payload", 1)[0])


if __name__ == "__main__":
    unittest.main()
