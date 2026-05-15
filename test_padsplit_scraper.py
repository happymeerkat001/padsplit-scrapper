#!/usr/bin/env python3
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

import requests

import padsplit_scraper.scraper as scraper


class DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def recent_chat() -> list[dict]:
    return [
        {
            "id": "chat-1",
            "lastMessage": {
                "created": datetime.now(timezone.utc).isoformat(),
                "text": "Need help",
            },
        }
    ]


def sample_kpis(score: int = 92) -> dict:
    return {
        "score": score,
        "avg_flip_days": 2.0,
        "occupancy_pct": 95.0,
        "avg_tenure_days": 180.0,
        "bonuses": [{"label": "occupancy >= 90%", "points": 20}],
        "penalties": [],
    }


class PadSplitScraperTests(unittest.TestCase):
    def test_compute_kpis_includes_rooms_over_30d(self) -> None:
        now = datetime(2026, 5, 15, tzinfo=timezone.utc)
        rooms = [
            {
                "id": 1,
                "detailed_status": "listed",
                "days_in_current_status": 31,
                "room_number": 1,
                "base_price": 720,
                "property_id": 100,
                "property_name": "456 Oak St, Dallas, TX",
            },
            {
                "id": 2,
                "detailed_status": "listed",
                "days_in_current_status": 14,
                "room_number": 2,
                "base_price": 800,
                "property_id": 100,
                "property_name": "456 Oak St, Dallas, TX",
            },
            {
                "id": 3,
                "detailed_status": "occupied",
                "days_in_current_status": 200,
                "room_number": 3,
                "property_id": 100,
                "property_name": "456 Oak St, Dallas, TX",
            },
        ]
        properties = [
            {
                "id": 100,
                "address": "456 Oak St",
                "location": "Dallas, TX",
                "rooms": [{"id": 1}, {"id": 2}, {"id": 3}],
                "occupied": 1,
                "vacant": 2,
                "inactive": 0,
                "needs_flip": 0,
                "move_in": 0,
            }
        ]

        kpis = scraper.compute_kpis(
            rooms=rooms,
            properties=properties,
            earnings_payload={"results": []},
            tasks_by_bucket={},
            now=now,
        )

        self.assertIn("rooms_over_30d", kpis)
        self.assertEqual(kpis["rooms_over_30d"], 1)

    def test_full_success_writes_latest_stats_and_monthly_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "output"
            docs_data_dir = root / "docs" / "data"

            with (
                patch.object(scraper, "OUTPUT_DIR", output_dir),
                patch.object(scraper, "DOCS_DATA_DIR", docs_data_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login") as login_mock,
                patch.object(scraper, "fetch_messages", return_value=recent_chat()),
                patch.object(scraper, "fetch_thread_messages", return_value=[{"id": "message-1"}]) as thread_mock,
                patch.object(scraper, "fetch_tasks", return_value={"Requests": [{"id": 1, "status": "submitted"}]}),
                patch.object(scraper, "fetch_rooms", return_value=[{"id": 1}]),
                patch.object(scraper, "fetch_properties_stats", return_value=[{"id": 9}]),
                patch.object(scraper, "fetch_earnings", return_value={"results": []}),
                patch.object(scraper, "compute_kpis", return_value=sample_kpis(92)),
                patch.object(
                    scraper,
                    "fetch_performance_history",
                    return_value={"2026-05": {"avg_flip_days": 2.0, "occupancy_pct": 95.0, "avg_tenure_days": 180.0}},
                ),
            ):
                exit_code = scraper.main([])

            self.assertEqual(exit_code, 0)
            login_mock.assert_called_once_with(unittest.mock.ANY, "user", "pw", force=False)
            thread_mock.assert_called_once()

            latest_payload = json.loads((output_dir / "latest.json").read_text())
            stats_payload = json.loads((output_dir / "stats.json").read_text())
            monthly_history = json.loads((docs_data_dir / "monthly_history.json").read_text())

            self.assertEqual(latest_payload["run_status"]["state"], "ok")
            self.assertEqual(latest_payload["run_status"]["mode"], "full")
            self.assertIn("tasks", latest_payload)
            self.assertEqual(stats_payload["run_status"]["state"], "ok")
            self.assertEqual(stats_payload["kpis"]["score"], 92)
            self.assertEqual(monthly_history["months"][-1]["month"], latest_payload["scraped_at"][:7])

            timestamped_files = [path for path in output_dir.glob("*.json") if path.name not in {"latest.json", "stats.json"}]
            self.assertEqual(len(timestamped_files), 1)

    def test_messages_only_skips_tasks_and_stats_fetches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "output"
            docs_data_dir = root / "docs" / "data"

            with (
                patch.object(scraper, "OUTPUT_DIR", output_dir),
                patch.object(scraper, "DOCS_DATA_DIR", docs_data_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(scraper, "fetch_messages", return_value=recent_chat()),
                patch.object(scraper, "fetch_thread_messages", return_value=[{"id": "message-1"}]),
                patch.object(scraper, "fetch_tasks", side_effect=AssertionError("tasks should not be fetched")),
                patch.object(scraper, "fetch_rooms", side_effect=AssertionError("rooms should not be fetched")),
                patch.object(scraper, "fetch_properties_stats", side_effect=AssertionError("properties should not be fetched")),
                patch.object(scraper, "fetch_earnings", side_effect=AssertionError("earnings should not be fetched")),
                patch.object(
                    scraper,
                    "fetch_performance_history",
                    side_effect=AssertionError("performance history should not be fetched"),
                ),
            ):
                exit_code = scraper.main(["--messages-only"])

            self.assertEqual(exit_code, 0)
            latest_payload = json.loads((output_dir / "latest.json").read_text())
            self.assertEqual(latest_payload["run_status"]["state"], "ok")
            self.assertEqual(latest_payload["run_status"]["mode"], "messages_only")
            self.assertNotIn("tasks", latest_payload)
            self.assertFalse((output_dir / "stats.json").exists())
            self.assertFalse((docs_data_dir / "monthly_history.json").exists())

    def test_stats_failure_reuses_prior_stats_and_marks_run_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "output"
            docs_data_dir = root / "docs" / "data"
            output_dir.mkdir(parents=True, exist_ok=True)
            docs_data_dir.mkdir(parents=True, exist_ok=True)

            prior_stats = {
                "scraped_at": "2026-05-01T12:00:00Z",
                "rooms": [{"id": 88}],
                "properties": [{"id": 99}],
                "earnings": [{"month": "2026-05", "net_amount": 1234}],
                "kpis": {"score": 77},
            }
            prior_monthly_history = {
                "updated_at": "2026-05-01T12:00:00Z",
                "months": [{"month": "2026-05", "score": 77}],
            }
            (output_dir / "stats.json").write_text(json.dumps(prior_stats, indent=2))
            monthly_path = docs_data_dir / "monthly_history.json"
            monthly_path.write_text(json.dumps(prior_monthly_history, indent=2))

            with (
                patch.object(scraper, "OUTPUT_DIR", output_dir),
                patch.object(scraper, "DOCS_DATA_DIR", docs_data_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(scraper, "fetch_messages", return_value=recent_chat()),
                patch.object(scraper, "fetch_thread_messages", return_value=[{"id": "message-1"}]),
                patch.object(scraper, "fetch_tasks", return_value={"Requests": [{"id": 1, "status": "submitted"}]}),
                patch.object(
                    scraper,
                    "fetch_rooms",
                    side_effect=requests.exceptions.ConnectionError("socket hangup"),
                ),
            ):
                exit_code = scraper.main([])

            self.assertEqual(exit_code, 0)
            latest_payload = json.loads((output_dir / "latest.json").read_text())
            stats_payload = json.loads((output_dir / "stats.json").read_text())
            self.assertEqual(latest_payload["run_status"]["state"], "degraded")
            self.assertEqual(latest_payload["run_status"]["failed_phase"], "room_stats")
            self.assertTrue(latest_payload["run_status"]["fallback_used"])
            self.assertIn("tasks", latest_payload)
            self.assertEqual(stats_payload["scraped_at"], "2026-05-01T12:00:00Z")
            self.assertEqual(stats_payload["kpis"]["score"], 77)
            self.assertEqual(stats_payload["run_status"]["state"], "degraded")
            self.assertEqual(json.loads(monthly_path.read_text()), prior_monthly_history)

    def test_auth_refresh_uses_forced_login_only_after_second_auth_failure(self) -> None:
        session = Mock()
        session.request = Mock(side_effect=[FakeResponse(403), FakeResponse(403), FakeResponse(200)])
        login_mock = Mock()

        response = scraper._authed_request(
            session,
            "GET",
            "https://example.test/endpoint",
            creds={"email": "user", "password": "pw"},
            login_fn=login_mock,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            login_mock.call_args_list,
            [
                call(session, "user", "pw", force=False),
                call(session, "user", "pw", force=True),
            ],
        )


if __name__ == "__main__":
    unittest.main()
