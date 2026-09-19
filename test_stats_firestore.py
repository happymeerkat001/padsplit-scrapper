#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from padsplit_scraper import persist


ROOT = Path(__file__).resolve().parent
RULES = (ROOT / "firestore.rules").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "scrape.yml").read_text()
MORNING = (ROOT / "run_morning.sh").read_text()
AFTERNOON = (ROOT / "run_afternoon.sh").read_text()
SEO = (ROOT / "padsplit_scraper" / "seo_monthly.py").read_text()


class _FakeDoc:
    def __init__(self) -> None:
        self.payload = None

    def set(self, payload):
        self.payload = payload


class _FakeCollection:
    def __init__(self) -> None:
        self.docs = {}

    def document(self, name):
        self.docs.setdefault(name, _FakeDoc())
        return self.docs[name]


class _FakeClient:
    def __init__(self) -> None:
        self.collections = {}

    def collection(self, name):
        self.collections.setdefault(name, _FakeCollection())
        return self.collections[name]


class StatsFirestoreTests(unittest.TestCase):
    def test_upload_writes_json_string_docs(self) -> None:
        client = _FakeClient()
        stats = {"scraped_at": "2026-09-19T12:00:00Z", "kpis": {"score": 90}, "earnings": [1]}
        history = {"updated_at": "2026-09-19T12:00:00Z", "months": [{"month": "2026-09", "score": 90}]}
        self.assertTrue(persist.upload_stats_to_firestore(stats, history, client=client))
        latest = client.collections["stats"].docs["latest"].payload
        monthly = client.collections["stats"].docs["monthly_history"].payload
        self.assertEqual(json.loads(latest["json"]), stats)
        self.assertEqual(json.loads(monthly["json"]), history)
        self.assertEqual(latest["updated_at"], "2026-09-19T12:00:00Z")

    def test_missing_credentials_skips_and_keeps_disk_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            with patch.object(persist, "OUTPUT_DIR", output_dir), patch.dict(os.environ, {}, clear=True):
                persist._write_json(persist._stats_output_path(), {"scraped_at": "x"})
                with patch.object(persist, "_firestore_client_or_none", return_value=None):
                    self.assertFalse(persist.upload_stats_to_firestore({"a": 1}, {"b": 2}))
                self.assertTrue((output_dir / "stats.json").exists())

    def test_firestore_set_failure_does_not_raise(self) -> None:
        client = MagicMock()
        client.collection.return_value.document.return_value.set.side_effect = RuntimeError("boom")
        self.assertFalse(persist.upload_stats_to_firestore({"a": 1}, {"b": 2}, client=client))

    def test_rules_allowlist_stats_and_keep_default_deny(self) -> None:
        self.assertIn("match /stats/{document}", RULES)
        self.assertIn("8jOJNgLoxpfyseZ0RY1PDZ1DXbi2", RULES)
        self.assertIn("allow read, write: if false", RULES)

    def test_pages_no_longer_publishes_financial_json(self) -> None:
        self.assertNotIn("docs/data/stats.json", WORKFLOW)
        self.assertNotIn("docs/data/monthly_history.json", WORKFLOW)
        self.assertIn("docs/data/occupancy.json", WORKFLOW)
        self.assertIn("docs/data/latest.json", WORKFLOW)
        self.assertNotIn("docs/data/stats.json", MORNING)
        self.assertNotIn("docs/data/monthly_history.json", MORNING)
        self.assertIn("docs/data/occupancy.json", MORNING)
        self.assertNotIn("docs/data/stats.json", AFTERNOON)
        self.assertNotIn("docs/data/monthly_history.json", AFTERNOON)
        self.assertNotIn('ROOT_DIR / "docs" / "data" / "stats.json"', SEO)

    def test_seed_copies_legacy_docs_history_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            docs_dir = Path(tmpdir) / "docs" / "data"
            docs_dir.mkdir(parents=True)
            (docs_dir / "monthly_history.json").write_text(json.dumps({"months": [{"month": "2026-04"}]}))
            with patch.object(persist, "OUTPUT_DIR", output_dir), patch.object(persist, "DOCS_DATA_DIR", docs_dir):
                persist.seed_private_monthly_history()
                seeded = json.loads((output_dir / "monthly_history.json").read_text())
                self.assertEqual(seeded["months"][0]["month"], "2026-04")
                persist.seed_private_monthly_history()
                self.assertEqual(json.loads((output_dir / "monthly_history.json").read_text()), seeded)


if __name__ == "__main__":
    unittest.main()
