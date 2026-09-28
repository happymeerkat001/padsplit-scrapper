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
        self.assertNotIn("stats-auth", STATS)
        self.assertNotIn("TXSU0LOpmDWNBbbv0x3uHHBnZb12", STATS)
        self.assertNotIn("pcJSHjdXeDfOeGRQMgso11Gvlxh2", STATS)
        self.assertNotIn("const STATS_UID = \"8jOJNgLoxpfyseZ0RY1PDZ1DXbi2\"", STATS)
        self.assertNotIn("user.uid === STATS_UID", STATS)
        self.assertNotIn('"stats", "latest"', STATS)
        self.assertIn("isApprovedCodesUser", STATS)
        self.assertIn("function approvedCodesUid()", STATS)
        self.assertIn("signInWithEmailAndPassword", STATS)
        self.assertIn('doc(db, "private_pages", pageId)', STATS)
        self.assertIn("getDoc", STATS)
        self.assertNotIn("./data/stats.json", STATS)
        self.assertNotIn("fetch(", STATS)
        self.assertIn("statsFreshness", STATS)
        self.assertIn("Listed-status summary", STATS)

    def test_history_page_uses_same_gate(self) -> None:
        self.assertIn('id="gate-wrap"', HISTORY)
        self.assertIn("stats-auth", HISTORY)
        self.assertNotIn("TXSU0LOpmDWNBbbv0x3uHHBnZb12", HISTORY)
        self.assertNotIn("pcJSHjdXeDfOeGRQMgso11Gvlxh2", HISTORY)
        self.assertNotIn("const STATS_UID", HISTORY)
        self.assertNotIn("user.uid === STATS_UID", HISTORY)
        self.assertIn("function isApprovedCodesUser", HISTORY)
        self.assertIn("isApprovedCodesUser(user)", HISTORY)
        self.assertIn("await signOut(auth)", HISTORY)
        self.assertIn('"stats", "monthly_history"', HISTORY)
        self.assertNotIn("./data/monthly_history.json", HISTORY)
        self.assertIn("./stats.html", HISTORY)
        self.assertIn("./kpi-history.html", STATS)

    def test_occupancy_dashboard_stays_public(self) -> None:
        self.assertIn("occupancy.json", INDEX)
        self.assertIn("Incoming move-ins", INDEX)
        self.assertNotIn("vacancy_rooms", INDEX)


if __name__ == "__main__":
    unittest.main()
