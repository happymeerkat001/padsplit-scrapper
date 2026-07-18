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
    def test_leanna_low_temp_alert_threshold_is_74f(self) -> None:
        self.assertEqual(schedule.LOW_TEMP_THRESHOLD_F, 74)

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

    def test_save_schedules_merges_existing_locations_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schedules_path = Path(tmpdir) / "config" / "schedules.json"
            schedules_path.parent.mkdir()
            schedules_path.write_text(
                json.dumps(
                    {
                        "1404 pioneer": [
                            {"hour": 7, "minute": 0, "cool": 75, "heat": 62},
                        ]
                    }
                )
            )

            with patch.object(schedule, "SCHEDULES_PATH", schedules_path):
                schedule.save_schedules(
                    {"6623 leanna": [schedule.Slot(hour=19, minute=0, cool=76, heat=62)]}
                )
                loaded = schedule.load_schedules()

            self.assertIn("1404 pioneer", loaded)
            self.assertEqual(loaded["6623 leanna"][0].cool, 76)
            self.assertFalse((Path(tmpdir) / "config" / "schedules.json.tmp").exists())

    def test_find_active_slot_wraps_before_first_slot(self) -> None:
        slots = [
            schedule.Slot(hour=8, minute=0, cool=76, heat=62),
            schedule.Slot(hour=19, minute=0, cool=75, heat=61),
        ]
        active = schedule.find_active_slot(slots, datetime(2026, 5, 19, 2, 0))
        self.assertEqual(active.hour, 19)
        self.assertEqual(active.cool, 75)

    def test_build_enforcer_plist_contains_interval_and_enforce_command(self) -> None:
        payload = schedule.build_enforcer_plist()
        self.assertEqual(payload["Label"], "com.padsplit.thermostat.enforcer")
        self.assertEqual(payload["StartInterval"], 1800)
        self.assertEqual(payload["ProgramArguments"][-1], "enforce")

    def test_install_command_persists_config_installs_enforcer_and_removes_legacy_target(self) -> None:
        args = unittest.mock.Mock(all=False, target="6623 Leanna", slot=[["8:00 AM", "76", "62"]])
        with (
            patch.object(schedule, "save_schedules") as save_mock,
            patch.object(schedule, "install_enforcer_if_needed") as enforcer_mock,
            patch.object(schedule, "target_plists", return_value=["old-plist"]) as target_plists_mock,
            patch.object(schedule, "unload_and_remove") as unload_mock,
        ):
            schedule.install_command(args)

        save_mock.assert_called_once()
        self.assertEqual(save_mock.call_args.args[0]["6623 leanna"][0].cool, 76)
        enforcer_mock.assert_called_once_with()
        target_plists_mock.assert_called_once_with("6623-leanna")
        unload_mock.assert_called_once_with(["old-plist"])

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
                patch.object(schedule, "SCHEDULES_PATH", Path(tmpdir) / "missing-schedules.json"),
                patch.object(schedule, "loaded_labels", return_value={"com.padsplit.thermostat.6623-leanna.1830"}),
                patch.object(schedule, "fetch_live_setpoints", return_value=None),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target=None))

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
                patch.object(schedule, "SCHEDULES_PATH", Path(tmpdir) / "missing-schedules.json"),
                patch.object(schedule, "loaded_labels", return_value={"com.padsplit.thermostat.6623-leanna.0700"}),
                patch.object(schedule, "fetch_live_setpoints", return_value=None),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target=None))

            text = out.getvalue()
            self.assertIn("[info] live data is stale", text)
            self.assertIn("[warn] live fetch failed", text)
            self.assertIn("75", text)
            self.assertIn("72", text)
            self.assertIn("68", text)
            self.assertIn("all", text)
            self.assertIn("---", text)

    def test_status_enforcer_mode_shows_active_slot_and_hybrid_calendar_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_dir = Path(tmpdir) / "agents"
            launch_dir.mkdir()
            schedules_path = Path(tmpdir) / "schedules.json"
            enforcer_log = Path(tmpdir) / "enforcer.log"
            enforcer_log.write_text("last run")
            schedules_path.write_text(
                json.dumps(
                    {
                        "6623 leanna": [
                            {"hour": 8, "minute": 0, "cool": 76, "heat": 62},
                            {"hour": 19, "minute": 0, "cool": 75, "heat": 61},
                        ]
                    }
                )
            )
            legacy_path = launch_dir / "com.padsplit.thermostat.6623-leanna.0800.plist"
            legacy_path.write_bytes(
                plistlib.dumps(
                    schedule.build_plist_dict(
                        "6623-leanna",
                        schedule.Slot(hour=8, minute=0, cool=74, heat=60),
                        ["--target", "6623 Leanna"],
                    )
                )
            )

            with (
                patch.object(schedule, "LAUNCH_AGENT_DIR", launch_dir),
                patch.object(schedule, "SCHEDULES_PATH", schedules_path),
                patch.object(schedule, "ENFORCER_LOG_PATH", enforcer_log),
                patch.object(schedule, "loaded_labels", return_value={schedule.ENFORCER_LABEL}),
                patch.object(schedule, "datetime", wraps=datetime) as datetime_mock,
            ):
                datetime_mock.now.return_value = datetime(2026, 5, 19, 2, 0)
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target=None))

            text = out.getvalue()
            self.assertIn("[enforcer]", text)
            self.assertIn("[calendar]", text)
            self.assertIn("[hybrid]", text)
            self.assertIn("7:00 PM", text)
            self.assertIn("75", text)

    def test_status_target_prints_full_configured_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schedules_path = Path(tmpdir) / "schedules.json"
            schedules_path.write_text(
                json.dumps(
                    {
                        "6623 leanna": [
                            {"hour": 8, "minute": 0, "cool": 76, "heat": 62},
                            {"hour": 14, "minute": 0, "cool": 77, "heat": 62},
                            {"hour": 19, "minute": 0, "cool": 76, "heat": 62},
                        ]
                    }
                )
            )

            with patch.object(schedule, "SCHEDULES_PATH", schedules_path):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target="6623 Leanna"))

            text = out.getvalue()
            self.assertIn("Configured schedule for 6623 leanna:", text)
            self.assertIn("8:00 AM", text)
            self.assertIn("2:00 PM", text)
            self.assertIn("7:00 PM", text)
            self.assertIn("cool=77 heat=62", text)

    def test_status_target_tolerates_wrapped_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schedules_path = Path(tmpdir) / "schedules.json"
            schedules_path.write_text(
                json.dumps(
                    {
                        "6623 leanna": [
                            {"hour": 8, "minute": 0, "cool": 76, "heat": 62},
                        ]
                    }
                )
            )

            with patch.object(schedule, "SCHEDULES_PATH", schedules_path):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target="6623\n  Leanna"))

            text = out.getvalue()
            self.assertIn("Configured schedule for 6623 leanna:", text)
            self.assertIn("8:00 AM", text)


if __name__ == "__main__":
    unittest.main()
