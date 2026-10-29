#!/usr/bin/env python3
import io
import json
import os
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from zoneinfo import ZoneInfo

import slack_task_digest


CT = ZoneInfo("America/Chicago")
WEBHOOK = "https://discord.com/api/webhooks/123/super-secret-token"


def _latest() -> dict:
    return {
        "tasks": {
            "Requests": [
                {
                    "property_address": {"street1": "1025 Broken Crest"},
                    "details": "Window stuck for Ada Lovelace",
                    "room_number": 2,
                }
            ],
            "Open": [
                {
                    "property_address": {"street1": "88 Cedar Row"},
                    "details": "Heater out code 2846 amount $174",
                    "room_number": 4,
                }
            ],
            "Eviction": [
                {
                    "property_address": {"street1": "9 Evict Lane"},
                    "details": "eviction case gamma",
                    "room_number": 1,
                }
            ],
            "Other": [
                {
                    "property_address": {"street1": "4 Hold Street"},
                    "details": "hold ticket delta",
                    "room_number": 3,
                }
            ],
            "In Progress": [
                {
                    "property_address": {"street1": "5 Progress Ave"},
                    "details": "work in progress epsilon",
                }
            ],
            "Complete": [
                {
                    "property_address": {"street1": "6 Done Road"},
                    "details": "finished zeta",
                }
            ],
        }
    }


class SlackTaskDigestTests(unittest.TestCase):
    def test_compose_message_does_not_treat_vacancy_rooms_as_occupancy(self) -> None:
        latest = {
            "tasks": {
                "Requests": [
                    {
                        "property_address": {"street1": "1025 Broken Crest"},
                        "details": "Window stuck",
                        "room_number": 2,
                    }
                ],
                "Open": [],
            }
        }
        stale_kpis = {
            "vacancy_rooms": [
                {
                    "property": "1025 Broken Crest, DeSoto, TX",
                    "room_number": 3,
                    "days_listed": 51,
                    "base_price": 174,
                }
            ]
        }
        message = slack_task_digest.compose_message(latest, kpis=stale_kpis)
        self.assertIn("1025 Broken Crest", message)
        self.assertIn("Window stuck", message)
        self.assertNotIn("Vacancy Alert", message)
        self.assertNotIn("51 days", message)
        self.assertNotIn("listed $174", message)

    def test_load_data_returns_latest_without_merging_stats_kpis(self) -> None:
        source = slack_task_digest.load_data.__code__.co_names
        self.assertNotIn("STATS_PATH", source)
        digest_source = Path(slack_task_digest.__file__).read_text()
        self.assertNotIn("format_vacancy_alert", digest_source)
        self.assertNotIn('merged["kpis"]', digest_source)

    def test_compose_includes_requests_and_open_only(self) -> None:
        self.assertEqual(slack_task_digest.TASK_BUCKETS, ("Requests", "Open"))
        message = slack_task_digest.compose_message(_latest(), today="2026-10-29")
        self.assertIn("Window stuck for Ada Lovelace", message)
        self.assertIn("Heater out code 2846 amount $174", message)
        self.assertIn("[Requests]", message)
        self.assertIn("[Open]", message)
        self.assertNotIn("eviction case gamma", message)
        self.assertNotIn("hold ticket delta", message)
        self.assertNotIn("work in progress epsilon", message)
        self.assertNotIn("finished zeta", message)
        self.assertNotIn("9 Evict Lane", message)
        self.assertEqual(
            slack_task_digest.digest_counts(_latest()),
            {"requests": 1, "open": 1, "properties": 2},
        )

    def test_dry_run_prints_counts_and_hides_composed_text(self) -> None:
        secret_body = "SECRET Ada Lovelace code 2846 amount $174 1025 Broken Crest"
        composed = {"called": False}

        def fake_compose(latest, **kwargs):
            del latest, kwargs
            composed["called"] = True
            return secret_body

        def fail_send(message: str) -> bool:
            raise AssertionError(message)

        def fail_weather() -> str:
            raise AssertionError("weather fetched")

        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            buffer = io.StringIO()
            with mock.patch.object(slack_task_digest, "compose_message", fake_compose):
                with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_TASKS": WEBHOOK}):
                    with redirect_stdout(buffer):
                        code = slack_task_digest.run_digest(
                            latest=_latest(),
                            now=datetime(2026, 10, 29, 6, 17, tzinfo=CT),
                            ledger_path=ledger,
                            after_scrape=True,
                            dry_run=True,
                            send=fail_send,
                            weather_fn=fail_weather,
                        )
            output = buffer.getvalue()
            self.assertEqual(code, 0)
            self.assertTrue(composed["called"])
            self.assertEqual(output.strip(), "dry-run\nrequests: 1\nopen: 1\nproperties: 2")
            self.assertNotIn("Ada", output)
            self.assertNotIn("2846", output)
            self.assertNotIn("174", output)
            self.assertNotIn("Broken Crest", output)
            self.assertNotIn("super-secret-token", output)
            self.assertNotIn("discord.com", output)
            self.assertFalse(ledger.exists())

    def test_cli_dry_run_reads_latest_and_skips_post(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest_path = root / "latest.json"
            ledger_path = root / "ledger.json"
            latest_path.write_text(json.dumps(_latest()))
            buffer = io.StringIO()
            with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_TASKS": WEBHOOK}):
                with redirect_stdout(buffer):
                    code = slack_task_digest.main(
                        ["--dry-run", "--latest", str(latest_path), "--ledger", str(ledger_path)]
                    )
            output = buffer.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("requests: 1", output)
            self.assertIn("open: 1", output)
            self.assertIn("properties: 2", output)
            self.assertNotIn("Ada Lovelace", output)
            self.assertNotIn("2846", output)
            self.assertNotIn("$174", output)
            self.assertNotIn("super-secret-token", output)
            self.assertFalse(ledger_path.exists())

    def test_same_day_dedupe_posts_once(self) -> None:
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        morning = datetime(2026, 10, 29, 6, 17, tzinfo=CT)
        late_backup = datetime(2026, 10, 29, 11, 49, tzinfo=CT)
        next_morning = datetime(2026, 10, 30, 5, 17, tzinfo=CT)
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "task_digest_ledger.json"
            sink = io.StringIO()
            with redirect_stdout(sink):
                first = slack_task_digest.run_digest(
                    latest=_latest(),
                    now=morning,
                    ledger_path=ledger,
                    after_scrape=True,
                    send=send,
                    weather_fn=lambda: None,
                )
                second = slack_task_digest.run_digest(
                    latest=_latest(),
                    now=datetime(2026, 10, 29, 6, 47, tzinfo=CT),
                    ledger_path=ledger,
                    after_scrape=True,
                    send=send,
                    weather_fn=lambda: None,
                )
                backup = slack_task_digest.run_digest(
                    latest=_latest(),
                    now=late_backup,
                    ledger_path=ledger,
                    after_scrape=False,
                    send=send,
                    weather_fn=lambda: None,
                )
            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            self.assertEqual(backup, 0)
            self.assertEqual(len(sent), 1)
            self.assertIn("Window stuck for Ada Lovelace", sent[0])
            stored = json.loads(ledger.read_text())
            self.assertEqual(set(stored), set(slack_task_digest.LEDGER_KEYS))
            self.assertEqual(stored["date"], "2026-10-29")
            self.assertTrue(stored["posted"])
            self.assertEqual(stored["source"], "scrape")
            self.assertNotIn("Ada", json.dumps(stored))
            self.assertNotIn("2846", json.dumps(stored))
            self.assertNotIn("174", json.dumps(stored))

            with redirect_stdout(sink):
                third = slack_task_digest.run_digest(
                    latest=_latest(),
                    now=next_morning,
                    ledger_path=ledger,
                    after_scrape=True,
                    send=send,
                    weather_fn=lambda: None,
                )
            self.assertEqual(third, 0)
            self.assertEqual(len(sent), 2)
            self.assertEqual(json.loads(ledger.read_text())["date"], "2026-10-30")
            self.assertEqual(json.loads(ledger.read_text())["source"], "scrape")

    def test_after_scrape_outside_window_does_not_post_or_record(self) -> None:
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = slack_task_digest.run_digest(
                    latest=_latest(),
                    now=datetime(2026, 10, 29, 14, 17, tzinfo=CT),
                    ledger_path=ledger,
                    after_scrape=True,
                    send=send,
                    weather_fn=lambda: None,
                )
            self.assertEqual(code, 0)
            self.assertEqual(sent, [])
            self.assertFalse(ledger.exists())
            self.assertIn("outside 05:00-07:59", buffer.getvalue())
            self.assertNotIn("Ada Lovelace", buffer.getvalue())

    def test_backup_posts_when_morning_scrape_did_not(self) -> None:
        sent: list[str] = []

        def send(message: str) -> bool:
            sent.append(message)
            return True

        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            sink = io.StringIO()
            with redirect_stdout(sink):
                code = slack_task_digest.run_digest(
                    latest=_latest(),
                    now=datetime(2026, 10, 29, 11, 49, tzinfo=CT),
                    ledger_path=ledger,
                    after_scrape=False,
                    send=send,
                    weather_fn=lambda: "WEATHER-BLOCK",
                )
            self.assertIn("WEATHER-BLOCK", sink.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(len(sent), 1)
            self.assertIn("WEATHER-BLOCK", sent[0])
            self.assertEqual(json.loads(ledger.read_text())["source"], "cron")

    def test_failed_send_leaves_ledger_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            sink = io.StringIO()
            with redirect_stdout(sink):
                code = slack_task_digest.run_digest(
                    latest=_latest(),
                    now=datetime(2026, 10, 29, 5, 17, tzinfo=CT),
                    ledger_path=ledger,
                    after_scrape=True,
                    send=lambda message: False,
                    weather_fn=lambda: None,
                )
            self.assertIn("ledger unchanged", sink.getvalue())
            self.assertEqual(code, 1)
            self.assertFalse(ledger.exists())

    def test_morning_window_edges(self) -> None:
        self.assertFalse(slack_task_digest.in_morning_window(datetime(2026, 10, 29, 4, 59, tzinfo=CT)))
        self.assertTrue(slack_task_digest.in_morning_window(datetime(2026, 10, 29, 5, 0, tzinfo=CT)))
        self.assertTrue(slack_task_digest.in_morning_window(datetime(2026, 10, 29, 7, 59, tzinfo=CT)))
        self.assertFalse(slack_task_digest.in_morning_window(datetime(2026, 10, 29, 8, 0, tzinfo=CT)))
        # 11:00 UTC is 06:00 CDT on this date, inside the window.
        utc = datetime(2026, 10, 29, 11, 0, tzinfo=ZoneInfo("UTC"))
        self.assertTrue(slack_task_digest.in_morning_window(utc))
        self.assertEqual(slack_task_digest.ct_day(utc), "2026-10-29")

    def test_send_errors_do_not_print_webhook(self) -> None:
        http_error = urllib.error.HTTPError(WEBHOOK, 403, "Forbidden", hdrs=None, fp=io.BytesIO(b""))
        url_error = urllib.error.URLError("no route")
        for exc in (http_error, url_error):
            buffer = io.StringIO()
            with mock.patch.dict(os.environ, {"DISCORD_WEBHOOK_TASKS": WEBHOOK}):
                with mock.patch("slack_task_digest.urllib.request.urlopen", side_effect=exc):
                    with redirect_stdout(buffer):
                        posted = slack_task_digest.send_to_discord("hello")
            output = buffer.getvalue()
            self.assertFalse(posted)
            self.assertNotIn("super-secret-token", output)
            self.assertNotIn(WEBHOOK, output)
            self.assertNotIn("discord.com", output)

    def test_workflows_post_after_scrape_and_keep_backup_cron(self) -> None:
        root = Path(__file__).resolve().parent
        scrape = (root / ".github" / "workflows" / "scrape.yml").read_text()
        digest = (root / ".github" / "workflows" / "slack_digest.yml").read_text()
        self.assertIn("python slack_task_digest.py --after-scrape", scrape)
        self.assertIn("continue-on-error: true", scrape)
        self.assertIn("docs/data/task_digest_ledger.json", scrape)
        self.assertIn('cron: "0 11 * * *"', digest)
        self.assertIn("docs/data/task_digest_ledger.json", digest)
        self.assertIn("python slack_task_digest.py", digest)


if __name__ == "__main__":
    unittest.main()
