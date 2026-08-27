#!/usr/bin/env python3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEX = (ROOT / "docs" / "index.html").read_text()
STATS = (ROOT / "docs" / "stats.html").read_text()
DIGEST = (ROOT / "slack_task_digest.py").read_text()


class DashboardOccupancyUiTests(unittest.TestCase):
    def test_dashboard_reads_live_occupancy_not_stats_vacancy(self) -> None:
        self.assertIn("occupancy.json", INDEX)
        self.assertIn("Incoming move-ins", INDEX)
        self.assertIn("Occupied after listed move-out", INDEX)
        self.assertIn("Rent-ready", INDEX)
        self.assertIn("operatorLists", INDEX)
        self.assertNotIn("vacancy_rooms", INDEX)
        self.assertNotIn("room_code", INDEX)

    def test_stats_page_labels_stale_listed_status(self) -> None:
        self.assertIn("statsFreshness", STATS)
        self.assertIn("Stats are stale", STATS)
        self.assertIn("Listed-status summary", STATS)
        self.assertIn("Listed rooms (not live occupancy)", STATS)
        self.assertIn("index.html#occupancy-section", STATS)
        self.assertNotIn("Occupancy Summary", STATS)
        self.assertNotIn("room_code", STATS)

    def test_digest_does_not_publish_vacancy_rooms_as_occupancy(self) -> None:
        self.assertNotIn("format_vacancy_alert", DIGEST)
        self.assertNotIn('merged["kpis"]', DIGEST)
        self.assertNotIn("room_code", DIGEST)


if __name__ == "__main__":
    unittest.main()
