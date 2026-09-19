#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATS = (ROOT / "docs" / "stats.html").read_text()
HISTORY = (ROOT / "docs" / "kpi-history.html").read_text()
INDEX = (ROOT / "docs" / "index.html").read_text()


class StatsGateTests(unittest.TestCase):
    def test_stats_page_has_owner_gate_and_firestore_read(self) -> None:
        self.assertIn('id="gate-wrap"', STATS)
        self.assertIn('id="app-wrap"', STATS)
        self.assertIn("stats-auth", STATS)
        self.assertIn("8jOJNgLoxpfyseZ0RY1PDZ1DXbi2", STATS)
        self.assertIn('getDoc', STATS)
        self.assertIn('"stats", "latest"', STATS)
        self.assertIn("signInWithEmailAndPassword", STATS)
        self.assertIn("user.uid === OWNER_UID", STATS)
        self.assertNotIn("./data/stats.json", STATS)
        self.assertIn("statsFreshness", STATS)
        self.assertIn("Listed-status summary", STATS)

    def test_history_page_uses_same_gate(self) -> None:
        self.assertIn('id="gate-wrap"', HISTORY)
        self.assertIn("stats-auth", HISTORY)
        self.assertIn("8jOJNgLoxpfyseZ0RY1PDZ1DXbi2", HISTORY)
        self.assertIn('"stats", "monthly_history"', HISTORY)
        self.assertIn("user.uid === OWNER_UID", HISTORY)
        self.assertNotIn("./data/monthly_history.json", HISTORY)
        self.assertIn("./stats.html", HISTORY)
        self.assertIn("./kpi-history.html", STATS)

    def test_occupancy_dashboard_stays_public(self) -> None:
        self.assertIn("occupancy.json", INDEX)
        self.assertIn("Incoming move-ins", INDEX)
        self.assertNotIn("vacancy_rooms", INDEX)


if __name__ == "__main__":
    unittest.main()
