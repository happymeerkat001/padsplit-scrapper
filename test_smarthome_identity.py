#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from smarthome import identity


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "identity.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_file_mints_once(self) -> None:
        first = identity.load_or_create(self.path)
        self.assertTrue(self.path.exists())
        self.assertEqual(first["fingerprint"], "msmarthome")
        self.assertTrue(first["device_id"])
        self.assertTrue(first["pushToken"])
        self.assertNotEqual(first["device_id"], identity.LIBRARY_DEFAULT_DEVICE_ID)
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["device_id"], first["device_id"])
        self.assertEqual(stored["pushToken"], first["pushToken"])

    def test_existing_file_reuses_same_pair(self) -> None:
        first = identity.load_or_create(self.path)
        second = identity.load_or_create(self.path)
        self.assertEqual(second["device_id"], first["device_id"])
        self.assertEqual(second["pushToken"], first["pushToken"])
        self.assertEqual(json.loads(self.path.read_text())["device_id"], first["device_id"])

    def test_corrupt_json_is_treated_as_missing(self) -> None:
        self.path.write_text("{not-json")
        record = identity.load_or_create(self.path)
        self.assertTrue(record["device_id"])
        self.assertTrue(record["pushToken"])
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored["device_id"], record["device_id"])
        self.assertEqual(stored["pushToken"], record["pushToken"])

    def test_partial_json_is_treated_as_missing(self) -> None:
        self.path.write_text(json.dumps({"fingerprint": "msmarthome", "device_id": "abc"}))
        record = identity.load_or_create(self.path)
        self.assertTrue(record["device_id"])
        self.assertTrue(record["pushToken"])
        self.assertNotEqual(record["device_id"], "abc")
        stored = json.loads(self.path.read_text())
        self.assertIn("pushToken", stored)
        self.assertTrue(stored["pushToken"])

    def test_select_slim_reuses_ids_and_stays_in_silo(self) -> None:
        first = identity.load_or_create(self.path)
        selected = identity.select_fingerprint("msmarthome-slim", self.path)
        self.assertEqual(selected["fingerprint"], "msmarthome-slim")
        self.assertEqual(selected["device_id"], first["device_id"])
        self.assertEqual(selected["pushToken"], first["pushToken"])
        spec = identity.fingerprint_spec("msmarthome-slim")
        self.assertEqual(spec["appname"], "MSmartHome")
        self.assertEqual(spec["appid"], 1010)
        self.assertTrue(spec["apiurl"].startswith("https://mp-prod.appsmb.com"))
        self.assertNotIn("mapp.appsmb.com", spec["apiurl"])
        self.assertNotIn("medi.com", spec["apiurl"])

    def test_table_has_no_foreign_silos(self) -> None:
        appnames = {spec["appname"] for spec in identity.FINGERPRINTS.values()}
        self.assertEqual(appnames, {"MSmartHome"})
        self.assertTrue(identity.FORBIDDEN_APPNAMES.isdisjoint(appnames))
        for spec in identity.FINGERPRINTS.values():
            self.assertEqual(spec["appid"], 1010)
            self.assertIn("mp-prod.appsmb.com", spec["apiurl"])
            self.assertNotIn("mapp.appsmb.com", spec["apiurl"])
            self.assertNotIn("smartmidea.net", spec["apiurl"])

    def test_unknown_fingerprint_rejected(self) -> None:
        with self.assertRaises(identity.UnknownFingerprintError):
            identity.select_fingerprint("NetHome Plus", self.path)
        with self.assertRaises(identity.UnknownFingerprintError):
            identity.fingerprint_spec("nethome-plus")


if __name__ == "__main__":
    unittest.main()
