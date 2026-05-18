#!/usr/bin/env python3
import io
import json
import plistlib
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import thermostat.schedule as schedule


class ThermostatScheduleTests(unittest.TestCase):
    def test_parse_time_accepts_supported_12_hour_formats(self) -> None:
        self.assertEqual(schedule.parse_time("7am"), (7, 0))
        self.assertEqual(schedule.parse_time("7:30am"), (7, 30))
        self.assertEqual(schedule.parse_time("6:00pm"), (18, 0))

    def test_parse_time_rejects_army_and_ambiguous_formats(self) -> None:
        with self.assertRaises(Exception):
            schedule.parse_time("19:30")
        with self.assertRaises(Exception):
            schedule.parse_time("1700")
        with self.assertRaises(Exception):
            schedule.parse_time("7:00")

    def test_sanitize_label(self) -> None:
        self.assertEqual(schedule.sanitize_label("6623 Leanna"), "6623-leanna")
        self.assertEqual(schedule.sanitize_label("1404 Pioneer"), "1404-pioneer")

    def test_build_plist_contains_expected_fields(self) -> None:
        slot = schedule.Slot(hour=7, minute=0, cool=76, heat=68)
        payload = plistlib.loads(schedule.build_plist("6623-leanna", slot, ["--target", "6623 Leanna"]).encode("utf-8"))
        self.assertEqual(payload["Label"], "com.padsplit.thermostat.6623-leanna.0700")
        self.assertEqual(payload["StartCalendarInterval"], {"Hour": 7, "Minute": 0})
        self.assertIn("--target", payload["ProgramArguments"])
        self.assertIn("6623 Leanna", payload["ProgramArguments"])

    def test_install_replaces_target_specific_plists_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_dir = Path(tmpdir)
            old_target = launch_dir / "com.padsplit.thermostat.6623-leanna.0700.plist"
            old_target.write_text("old")
            other_target = launch_dir / "com.padsplit.thermostat.1404-pioneer.0700.plist"
            other_target.write_text("keep")
            slot = schedule.Slot(hour=18, minute=30, cool=77, heat=68)

            def fake_launchctl(args):
                return unittest.mock.Mock(returncode=0, stdout="", stderr="")

            with (
                patch.object(schedule, "LAUNCH_AGENT_DIR", launch_dir),
                patch.object(schedule, "launchctl", side_effect=fake_launchctl),
            ):
                schedule.install_schedule("6623-leanna", ["--target", "6623 Leanna"], [slot])

            self.assertFalse(old_target.exists())
            self.assertTrue(other_target.exists())
            self.assertTrue((launch_dir / "com.padsplit.thermostat.6623-leanna.1830.plist").exists())

    def test_uninstall_target_with_resume_calls_set_temps(self) -> None:
        args = unittest.mock.Mock(all=False, target="6623 Leanna", resume_schedule=True)
        with (
            patch.object(schedule, "unload_and_remove") as unload_mock,
            patch.object(schedule, "target_plists", return_value=[]),
            patch.object(schedule, "resume_schedule") as resume_mock,
        ):
            schedule.uninstall_command(args)

        unload_mock.assert_called_once()
        resume_mock.assert_called_once_with(["--target", "6623 Leanna"])

    def test_status_reads_plists_and_prints_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_dir = Path(tmpdir)
            slot = schedule.Slot(hour=18, minute=30, cool=77, heat=68)
            path = launch_dir / "com.padsplit.thermostat.6623-leanna.1830.plist"
            path.write_bytes(
                plistlib.dumps(schedule.build_plist_dict("6623-leanna", slot, ["--target", "6623 Leanna"]))
            )
            with (
                patch.object(schedule, "LAUNCH_AGENT_DIR", launch_dir),
                patch.object(schedule, "loaded_labels", return_value={"com.padsplit.thermostat.6623-leanna.1830"}),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command()

            text = out.getvalue()
            self.assertIn("loaded", text)
            self.assertIn("6:30 PM", text)
            self.assertIn("6623 Leanna", text)
            self.assertIn("LIVE_TEMP", text)

    def test_load_live_snapshot_skips_empty_devices_and_normalizes_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            latest_path = Path(tmpdir) / "latest.json"
            latest_path.write_text(
                json.dumps(
                    {
                        "scraped_at": "2026-05-18T12:00:00Z",
                        "locations": [
                            {
                                "name": " LEANNA ",
                                "devices": [
                                    {
                                        "temp": 75.0,
                                        "heat_setpoint": 68.0,
                                        "cool_setpoint": 72.0,
                                    }
                                ],
                            },
                            {"name": "Ignored House", "devices": []},
                        ],
                    }
                )
            )

            with patch.object(schedule, "LATEST_SNAPSHOT_PATH", latest_path):
                snapshot, scraped_at = schedule.load_live_snapshot()

            self.assertEqual(scraped_at, "2026-05-18T12:00:00Z")
            self.assertEqual(snapshot["leanna"]["temp"], 75.0)
            self.assertNotIn("ignored house", snapshot)

    def test_load_live_snapshot_returns_empty_on_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            latest_path = Path(tmpdir) / "latest.json"
            latest_path.write_text("{bad json")

            with patch.object(schedule, "LATEST_SNAPSHOT_PATH", latest_path):
                snapshot, scraped_at = schedule.load_live_snapshot()

            self.assertEqual(snapshot, {})
            self.assertIsNone(scraped_at)

    def test_status_matches_live_snapshot_and_warns_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_dir = Path(tmpdir) / "agents"
            launch_dir.mkdir()
            latest_path = Path(tmpdir) / "latest.json"
            stale_time = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0)
            latest_path.write_text(
                json.dumps(
                    {
                        "scraped_at": stale_time.isoformat().replace("+00:00", "Z"),
                        "locations": [
                            {
                                "name": "Leanna",
                                "devices": [
                                    {
                                        "temp": 75.0,
                                        "heat_setpoint": 68.0,
                                        "cool_setpoint": 72.0,
                                    }
                                ],
                            }
                        ],
                    }
                )
            )

            target_slot = schedule.Slot(hour=7, minute=0, cool=72, heat=68)
            all_slot = schedule.Slot(hour=22, minute=0, cool=76, heat=70)
            target_path = launch_dir / "com.padsplit.thermostat.6623-leanna.0700.plist"
            all_path = launch_dir / "com.padsplit.thermostat.all.2200.plist"
            target_path.write_bytes(
                plistlib.dumps(schedule.build_plist_dict("6623-leanna", target_slot, ["--target", "6623 Leanna"]))
            )
            all_path.write_bytes(plistlib.dumps(schedule.build_plist_dict("all", all_slot, ["--all"])))

            with (
                patch.object(schedule, "LAUNCH_AGENT_DIR", launch_dir),
                patch.object(schedule, "LATEST_SNAPSHOT_PATH", latest_path),
                patch.object(schedule, "loaded_labels", return_value={"com.padsplit.thermostat.6623-leanna.0700"}),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command()

            text = out.getvalue()
            self.assertIn("[warn] live data is stale", text)
            self.assertIn("75", text)
            self.assertIn("72", text)
            self.assertIn("68", text)
            self.assertIn("all", text)
            self.assertIn("---", text)


if __name__ == "__main__":
    unittest.main()
