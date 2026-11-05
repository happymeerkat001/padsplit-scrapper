#!/usr/bin/env python3
import io
import json
import os
import plistlib
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import thermostat.schedule as schedule


class ThermostatScheduleTests(unittest.TestCase):
    def test_fictional9_low_temp_alert_threshold_is_74f(self) -> None:
        self.assertEqual(schedule.LOW_TEMP_THRESHOLD_F, 74)

    def test_temp_alert_uses_tasks_webhook_not_scraper_url(self) -> None:
        env = {
            "DISCORD_WEBHOOK_TASKS": "https://discord.example/tasks",
            "DISCORD_WEBHOOK_URL": "https://discord.example/scraper",
            "DISCORD_WEBHOOK_MESSAGES": "https://discord.example/messages",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(schedule.temp_alert_webhook_url(), "https://discord.example/tasks")

    def test_temp_alert_webhook_unset_does_not_fall_back_to_scraper_url(self) -> None:
        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.example/scraper"}, clear=False):
            os.environ.pop("DISCORD_WEBHOOK_TASKS", None)
            self.assertIsNone(schedule.temp_alert_webhook_url())

    def test_post_temp_alert_posts_threshold_text_only(self) -> None:
        response = Mock()
        response.status_code = 204
        with patch.object(schedule.requests, "post", return_value=response) as post_mock:
            schedule._post_temp_alert("https://discord.example/tasks", "8108 fictional9", 72.4)
        post_mock.assert_called_once()
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://discord.example/tasks")
        content = kwargs["json"]["content"]
        self.assertIn("8108 fictional9 temp 72", content)
        self.assertIn("74", content)
        self.assertNotIn("code", content.lower())
        self.assertNotIn("pin", content.lower())

    def test_low_temp_alert_requires_private_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'target.json'
            with patch.dict(os.environ, {'PADSPLIT_TEMP_ALERT_CONFIG': str(path)}):
                self.assertEqual(schedule._low_temp_alert_target(), '')
                path.write_text('{"target":"Fictional Home"}')
                self.assertEqual(schedule._low_temp_alert_target(), 'fictional home')

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
        self.assertEqual(schedule.sanitize_label("8108 Fictional9"), "8108-fictional9")
        self.assertEqual(schedule.sanitize_label("8102 Fictional5"), "8102-fictional5")

    def test_build_plist_contains_expected_fields(self) -> None:
        slot = schedule.Slot(hour=7, minute=0, cool=76, heat=68)
        payload = plistlib.loads(schedule.build_plist("8108-fictional9", slot, ["--target", "8108 Fictional9"]).encode("utf-8"))
        self.assertEqual(payload["Label"], "com.padsplit.thermostat.8108-fictional9.0700")
        self.assertEqual(payload["StartCalendarInterval"], {"Hour": 7, "Minute": 0})
        self.assertIn("--target", payload["ProgramArguments"])
        self.assertIn("8108 Fictional9", payload["ProgramArguments"])

    def test_launchagent_logs_are_written_to_the_central_logs_directory(self) -> None:
        slot = schedule.Slot(hour=7, minute=0, cool=76, heat=68)
        stdout_path, stderr_path = schedule.log_paths_for("8108-fictional9", slot)
        enforcer = schedule.build_enforcer_plist()

        self.assertEqual(stdout_path.parent, schedule.ROOT_DIR / "logs")
        self.assertEqual(stderr_path.parent, schedule.ROOT_DIR / "logs")
        self.assertEqual(Path(enforcer["StandardOutPath"]).parent, schedule.ROOT_DIR / "logs")
        self.assertEqual(Path(enforcer["StandardErrorPath"]).parent, schedule.ROOT_DIR / "logs")

    def test_install_replaces_target_specific_plists_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_dir = Path(tmpdir)
            old_target = launch_dir / "com.padsplit.thermostat.8108-fictional9.0700.plist"
            old_target.write_text("old")
            other_target = launch_dir / "com.padsplit.thermostat.8102-fictional5.0700.plist"
            other_target.write_text("keep")
            slot = schedule.Slot(hour=18, minute=30, cool=77, heat=68)

            def fake_launchctl(args):
                return unittest.mock.Mock(returncode=0, stdout="", stderr="")

            with (
                patch.object(schedule, "LAUNCH_AGENT_DIR", launch_dir),
                patch.object(schedule, "launchctl", side_effect=fake_launchctl),
            ):
                schedule.install_schedule("8108-fictional9", ["--target", "8108 Fictional9"], [slot])

            self.assertFalse(old_target.exists())
            self.assertTrue(other_target.exists())
            self.assertTrue((launch_dir / "com.padsplit.thermostat.8108-fictional9.1830.plist").exists())

    def test_save_schedules_merges_existing_locations_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schedules_path = Path(tmpdir) / "config" / "schedules.json"
            schedules_path.parent.mkdir()
            schedules_path.write_text(
                json.dumps(
                    {
                        "8102 fictional5": [
                            {"hour": 7, "minute": 0, "cool": 75, "heat": 62},
                        ]
                    }
                )
            )

            with patch.object(schedule, "SCHEDULES_PATH", schedules_path):
                schedule.save_schedules(
                    {"8108 fictional9": [schedule.Slot(hour=2, minute=5, cool=70, heat=60)]}
                )
                loaded = schedule.load_schedules()

            self.assertIn("8102 fictional5", loaded)
            self.assertEqual(loaded["8108 fictional9"][0].cool, 70)
            self.assertFalse((Path(tmpdir) / "config" / "schedules.json.tmp").exists())

    def test_find_active_slot_wraps_before_first_slot(self) -> None:
        slots = [schedule.Slot(hour=2, minute=5, cool=70, heat=60), schedule.Slot(hour=22, minute=10, cool=73, heat=63)]
        active = schedule.find_active_slot(slots, datetime(2026, 1, 1, 0, 30))
        self.assertEqual(active.hour, 22)
        self.assertEqual(active.cool, 73)

    def test_build_enforcer_plist_contains_interval_and_enforce_command(self) -> None:
        payload = schedule.build_enforcer_plist()
        self.assertEqual(payload["Label"], "com.padsplit.thermostat.enforcer")
        self.assertEqual(payload["StartInterval"], 1800)
        self.assertEqual(payload["ProgramArguments"][-1], "enforce")

    def test_install_command_persists_config_installs_enforcer_and_removes_legacy_target(self) -> None:
        args = unittest.mock.Mock(all=False, target="8108 Fictional9", slot=[["8:00 AM", "76", "62"]])
        with (
            patch.object(schedule, "save_schedules") as save_mock,
            patch.object(schedule, "install_enforcer_if_needed") as enforcer_mock,
            patch.object(schedule, "target_plists", return_value=["old-plist"]) as target_plists_mock,
            patch.object(schedule, "unload_and_remove") as unload_mock,
        ):
            schedule.install_command(args)

        save_mock.assert_called_once()
        self.assertEqual(save_mock.call_args.args[0]["8108 fictional9"][0].cool, 76)
        enforcer_mock.assert_called_once_with()
        target_plists_mock.assert_called_once_with("8108-fictional9")
        unload_mock.assert_called_once_with(["old-plist"])

    def test_uninstall_target_with_resume_calls_set_temps(self) -> None:
        args = unittest.mock.Mock(all=False, target="8108 Fictional9", resume_schedule=True)
        with (
            patch.object(schedule, "unload_and_remove") as unload_mock,
            patch.object(schedule, "target_plists", return_value=[]),
            patch.object(schedule, "resume_schedule") as resume_mock,
        ):
            schedule.uninstall_command(args)

        unload_mock.assert_called_once()
        resume_mock.assert_called_once_with(["--target", "8108 Fictional9"])

    def test_status_reads_plists_and_prints_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            launch_dir = Path(tmpdir)
            slot = schedule.Slot(hour=18, minute=30, cool=77, heat=68)
            path = launch_dir / "com.padsplit.thermostat.8108-fictional9.1830.plist"
            path.write_bytes(
                plistlib.dumps(schedule.build_plist_dict("8108-fictional9", slot, ["--target", "8108 Fictional9"]))
            )
            with (
                patch.object(schedule, "LAUNCH_AGENT_DIR", launch_dir),
                patch.object(schedule, "SCHEDULES_PATH", Path(tmpdir) / "missing-schedules.json"),
                patch.object(schedule, "loaded_labels", return_value={"com.padsplit.thermostat.8108-fictional9.1830"}),
                patch.object(schedule, "fetch_live_setpoints", return_value=None),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target=None))

            text = out.getvalue()
            self.assertIn("loaded", text)
            self.assertIn("6:30 PM", text)
            self.assertIn("8108 Fictional9", text)
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
                                "name": " FICTIONAL9 ",
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
            self.assertEqual(snapshot["fictional9"]["temp"], 75.0)
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
                                "name": "Fictional9",
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
            target_path = launch_dir / "com.padsplit.thermostat.8108-fictional9.0700.plist"
            all_path = launch_dir / "com.padsplit.thermostat.all.2200.plist"
            target_path.write_bytes(
                plistlib.dumps(schedule.build_plist_dict("8108-fictional9", target_slot, ["--target", "8108 Fictional9"]))
            )
            all_path.write_bytes(plistlib.dumps(schedule.build_plist_dict("all", all_slot, ["--all"])))

            with (
                patch.object(schedule, "LAUNCH_AGENT_DIR", launch_dir),
                patch.object(schedule, "LATEST_SNAPSHOT_PATH", latest_path),
                patch.object(schedule, "SCHEDULES_PATH", Path(tmpdir) / "missing-schedules.json"),
                patch.object(schedule, "loaded_labels", return_value={"com.padsplit.thermostat.8108-fictional9.0700"}),
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
                        "8108 fictional9": [
                            {'hour': 1, 'minute': 5, 'heat': 60, 'cool': 70},
                            {'hour': 13, 'minute': 15, 'heat': 62, 'cool': 72},
                        ]
                    }
                )
            )
            legacy_path = launch_dir / "com.padsplit.thermostat.8108-fictional9.0800.plist"
            legacy_path.write_bytes(
                plistlib.dumps(
                    schedule.build_plist_dict(
                        "8108-fictional9",
                        schedule.Slot(hour=8, minute=0, cool=74, heat=60),
                        ["--target", "8108 Fictional9"],
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
                datetime_mock.now.return_value = datetime(2026, 1, 1, 14, 0)
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target=None))

            text = out.getvalue()
            self.assertIn("[enforcer]", text)
            self.assertIn("[calendar]", text)
            self.assertIn("[hybrid]", text)
            self.assertIn('1:15 PM', text)
            self.assertIn('72', text)

    def test_status_target_prints_full_configured_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schedules_path = Path(tmpdir) / "schedules.json"
            schedules_path.write_text(
                json.dumps(
                    {
                        "8108 fictional9": [
                            {'hour': 1, 'minute': 5, 'heat': 60, 'cool': 70},
                            {'hour': 9, 'minute': 10, 'heat': 61, 'cool': 71},
                            {'hour': 18, 'minute': 20, 'heat': 62, 'cool': 72},
                        ]
                    }
                )
            )

            with patch.object(schedule, "SCHEDULES_PATH", schedules_path):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target="8108 Fictional9"))

            text = out.getvalue()
            self.assertIn("Configured schedule for 8108 fictional9:", text)
            self.assertIn('1:05 AM', text)
            self.assertIn('9:10 AM', text)
            self.assertIn('6:20 PM', text)
            self.assertIn('cool=71 heat=61', text)

    def test_status_target_tolerates_wrapped_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            schedules_path = Path(tmpdir) / "schedules.json"
            schedules_path.write_text(
                json.dumps(
                    {
                        "8108 fictional9": [
                            {'hour': 5, 'minute': 35, 'heat': 61, 'cool': 71},
                        ]
                    }
                )
            )

            with patch.object(schedule, "SCHEDULES_PATH", schedules_path):
                out = io.StringIO()
                with redirect_stdout(out):
                    schedule.status_command(unittest.mock.Mock(target="8108\n  Fictional9"))

            text = out.getvalue()
            self.assertIn("Configured schedule for 8108 fictional9:", text)
            self.assertIn('5:35 AM', text)


if __name__ == "__main__":
    unittest.main()
