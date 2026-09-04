#!/usr/bin/env python3
import unittest
from datetime import datetime, timezone

from padsplit_scraper import stats_freshness


NOW = datetime(2026, 8, 27, 16, 0, tzinfo=timezone.utc)


class StatsFreshnessTests(unittest.TestCase):
    def test_degraded_fallback_is_stale_even_if_rewritten_today(self) -> None:
        info = stats_freshness.stats_freshness(
            {
                "scraped_at": "2026-05-15T21:44:07Z",
                "run_status": {
                    "state": "degraded",
                    "failed_phase": "earnings_stats",
                    "fallback_used": True,
                    "run_scraped_at": "2026-08-27T16:33:18Z",
                },
            },
            NOW,
        )
        self.assertTrue(info["stale"])
        self.assertTrue(info["degraded"])
        self.assertEqual(info["failed_phase"], "earnings_stats")
        self.assertEqual(info["scraped_at"], "2026-05-15T21:44:07Z")

    def test_fresh_ok_stats_are_not_stale(self) -> None:
        info = stats_freshness.stats_freshness(
            {
                "scraped_at": "2026-08-27T16:00:00Z",
                "run_status": {"state": "ok", "fallback_used": False},
            },
            NOW,
        )
        self.assertFalse(info["stale"])
        self.assertFalse(info["degraded"])

    def test_old_ok_stats_are_stale(self) -> None:
        info = stats_freshness.stats_freshness(
            {
                "scraped_at": "2026-08-24T16:00:00Z",
                "run_status": {"state": "ok", "fallback_used": False},
            },
            NOW,
        )
        self.assertTrue(info["stale"])

    def test_missing_payload_is_stale(self) -> None:
        info = stats_freshness.stats_freshness(None, NOW)
        self.assertTrue(info["stale"])
        self.assertTrue(info["missing_scraped_at"])


if __name__ == "__main__":
    unittest.main()
