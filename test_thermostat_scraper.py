#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import thermostat.scraper as scraper


class DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def sample_output(scraped_at: str = "2026-04-27T12:16:16Z") -> dict:
    return {
        "scraped_at": scraped_at,
        "locations": [
            {
                "id": 7715771,
                "name": "10235 Ridge Oak",
                "devices": [
                    {
                        "id": 9766835,
                        "name": "RIDGE OAK",
                        "temp": 76.0,
                        "heat_setpoint": 68.0,
                        "cool_setpoint": 78.0,
                        "humidity": 59.0,
                        "outdoor_temp": 74.0,
                        "mode": None,
                        "equipment_status": None,
                    }
                ],
            }
        ],
    }


class ThermostatScraperTests(unittest.TestCase):
    def test_successful_fresh_scrape_writes_current_output_and_skips_slack(self) -> None:
        fresh_output = sample_output("2026-05-04T11:00:00Z")
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            with (
                patch.object(scraper, "OUTPUT_DIR", out_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login") as login_mock,
                patch.object(scraper, "fetch_fresh_output", return_value=fresh_output),
                patch.object(scraper, "post_slack_message") as slack_mock,
                patch.object(scraper, "print_report"),
            ):
                exit_code = scraper.main()

            self.assertEqual(exit_code, 0)
            login_mock.assert_called_once()
            slack_mock.assert_not_called()
            latest_path = out_dir / "latest.json"
            timestamped_path = out_dir / "2026-05-04T11-00-00Z.json"
            self.assertEqual(json.loads(latest_path.read_text()), fresh_output)
            self.assertEqual(json.loads(timestamped_path.read_text()), fresh_output)

    def test_timeout_after_login_uses_stale_snapshot_and_sends_one_slack_alert(self) -> None:
        stale_output = sample_output("2026-04-27T12:16:16Z")
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            latest_path = out_dir / "latest.json"
            latest_path.write_text(json.dumps(stale_output, indent=2))

            with (
                patch.object(scraper, "OUTPUT_DIR", out_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login") as login_mock,
                patch.object(
                    scraper,
                    "fetch_fresh_output",
                    side_effect=requests.exceptions.Timeout("vendor timeout"),
                ),
                patch.object(scraper, "post_slack_message") as slack_mock,
                patch.object(scraper, "print_report"),
            ):
                original_latest = latest_path.read_text()
                exit_code = scraper.main()

            self.assertEqual(exit_code, 0)
            login_mock.assert_called_once()
            slack_mock.assert_called_once_with(
                "⚠️ Thermostat API Alert: Timeout detected. Using stale data from 2026-04-27T12:16:16Z. "
                "Morning pipeline continuing."
            )
            self.assertEqual(latest_path.read_text(), original_latest)

    def test_missing_or_invalid_stale_snapshot_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            with (
                patch.object(scraper, "OUTPUT_DIR", out_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(
                    scraper,
                    "fetch_fresh_output",
                    side_effect=requests.exceptions.Timeout("vendor timeout"),
                ),
                patch.object(scraper, "post_slack_message") as slack_mock,
                patch.object(scraper, "print_report"),
            ):
                with self.assertRaises(RuntimeError):
                    scraper.main()
                slack_mock.assert_not_called()

            (out_dir / "latest.json").write_text("{bad json")
            with (
                patch.object(scraper, "OUTPUT_DIR", out_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(
                    scraper,
                    "fetch_fresh_output",
                    side_effect=requests.exceptions.Timeout("vendor timeout"),
                ),
                patch.object(scraper, "post_slack_message") as slack_mock,
                patch.object(scraper, "print_report"),
            ):
                with self.assertRaises(RuntimeError):
                    scraper.main()
                slack_mock.assert_not_called()

    def test_login_failure_does_not_use_stale_fallback_or_send_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            (out_dir / "latest.json").write_text(json.dumps(sample_output(), indent=2))

            with (
                patch.object(scraper, "OUTPUT_DIR", out_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login", side_effect=RuntimeError("bad credentials")),
                patch.object(scraper, "post_slack_message") as slack_mock,
                patch.object(scraper, "print_report"),
            ):
                with self.assertRaises(RuntimeError):
                    scraper.main()

                slack_mock.assert_not_called()

    def test_slack_alert_uses_stale_snapshot_timestamp(self) -> None:
        stale_output = sample_output("2026-05-01T06:15:00Z")
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            (out_dir / "latest.json").write_text(json.dumps(stale_output, indent=2))

            with (
                patch.object(scraper, "OUTPUT_DIR", out_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(
                    scraper,
                    "fetch_fresh_output",
                    side_effect=requests.exceptions.ConnectionError("socket hangup"),
                ),
                patch.object(scraper, "post_slack_message") as slack_mock,
                patch.object(scraper, "print_report"),
            ):
                exit_code = scraper.main()

            self.assertEqual(exit_code, 0)
            alert_message = slack_mock.call_args.args[0]
            self.assertIn("2026-05-01T06:15:00Z", alert_message)


if __name__ == "__main__":
    unittest.main()
