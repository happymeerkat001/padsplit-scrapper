#!/usr/bin/env python3
import unittest
from datetime import datetime, timezone

from padsplit_scraper import kpis


class PadSplitKpiTests(unittest.TestCase):
    def test_to_num_and_parse_iso_reject_invalid_input(self) -> None:
        self.assertEqual(kpis._to_num("1,234.5"), 1234.5)
        self.assertEqual(kpis._to_num(True), 0.0)
        self.assertEqual(kpis._to_num("not a number"), 0.0)
        self.assertEqual(kpis._parse_iso("2026-07-20T12:00:00Z"), datetime(2026, 7, 20, 12, tzinfo=timezone.utc))
        self.assertIsNone(kpis._parse_iso("not a timestamp"))

    def test_compute_monthly_kpis_applies_boundary_scores(self) -> None:
        result = kpis.compute_monthly_kpis(
            {"occupancy_pct": 90, "avg_tenure_days": 180, "avg_flip_days": 5}
        )

        self.assertEqual(result["score"], 120)
        self.assertEqual(result["penalties"], [])
        self.assertTrue(result["partial"])

    def test_compute_kpis_counts_open_ticket_age_and_property_metrics(self) -> None:
        result = kpis.compute_kpis(
            rooms=[
                {"id": 1, "property_id": 7, "detailed_status": "occupied", "days_in_current_status": 200},
                {"id": 2, "property_id": 7, "detailed_status": "listed", "days_in_current_status": 31, "base_price": "700"},
                {"id": 3, "property_id": 7, "detailed_status": "flip-room", "days_in_current_status": 6},
            ],
            properties=[
                {
                    "id": 7,
                    "address": "456 Oak St",
                    "location": "Dallas, TX",
                    "rooms": [{"id": 1}, {"id": 2}, {"id": 3}],
                    "occupied": 1,
                    "inactive": 0,
                    "vacant": 2,
                    "needs_flip": 1,
                    "move_in": 0,
                }
            ],
            earnings_payload={"results": [{"month": "2026-06-01", "net_revenue": "1,500.00"}]},
            tasks_by_bucket={"Open": [{"id": 9, "status": "accepted", "created": "2026-06-30T00:00:00Z"}]},
            now=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )

        self.assertEqual(result["occupancy_pct"], 33.33)
        self.assertEqual(result["rooms_over_30d"], 1)
        self.assertEqual(result["tickets_over_7d"], 1)
        self.assertEqual(result["tickets_over_14d"], 1)
        self.assertEqual(result["monthly_net_revenue"], 1500.0)
        self.assertEqual(result["revenue_per_room"], 500.0)
        self.assertEqual(result["per_property"][0]["property"], "456 Oak St, Dallas, TX")


if __name__ == "__main__":
    unittest.main()
