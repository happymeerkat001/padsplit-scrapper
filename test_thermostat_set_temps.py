#!/usr/bin/env python3
import unittest
from unittest.mock import ANY, patch

import thermostat.set_temps as set_temps


class DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def sample_locations():
    return [
        {
            "LocationID": 9100001,
            "Name": "FICTIONAL9",
            "Devices": [{"DeviceID": 9100006, "Name": "FICTIONAL9"}],
        },
        {
            "LocationID": 9100002,
            "Name": "FICTIONAL17 OAK",
            "Devices": [{"DeviceID": 9100005, "Name": "FICTIONAL17 OAK"}],
        },
    ]


class ThermostatSetTempsTests(unittest.TestCase):
    def test_target_matches_fictional9_even_with_street_number(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={9100001: "FICTIONAL9", 9100002: "FICTIONAL17 OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(cool_setpoint=78, heat_setpoint=60, targets=["8108 Fictional9"])

        submit_mock.assert_called_once_with(
            ANY,
            9100006,
            set_temps.set_hold_payload(9100006, 78, 60),
            "cool=78 heat=60",
        )

    def test_location_id_targets_single_location(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={9100001: "FICTIONAL9", 9100002: "FICTIONAL17 OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(location_ids=[9100002])

        submit_mock.assert_called_once_with(
            ANY,
            9100005,
            set_temps.set_hold_payload(9100005, 75, 63),
            "cool=75 heat=63",
        )

    def test_ambiguous_target_fails_without_setting_anything(self) -> None:
        locations = sample_locations() + [
            {
                "LocationID": 9100003,
                "Name": "FICTIONAL9 NORTH",
                "Devices": [{"DeviceID": 9100004, "Name": "FICTIONAL9 NORTH"}],
            }
        ]
        names = {9100001: "FICTIONAL9", 9100002: "FICTIONAL17 OAK", 9100003: "FICTIONAL9 NORTH"}
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=locations),
            patch.object(set_temps, "fetch_location_names", return_value=names),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, 'Ambiguous thermostat target "Fictional9"'):
                set_temps.apply_device_changes(targets=["Fictional9"])

        submit_mock.assert_not_called()

    def test_all_targets_every_location_when_explicit(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={9100001: "FICTIONAL9", 9100002: "FICTIONAL17 OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(allow_all=True)

        self.assertEqual(submit_mock.call_count, 2)

    def test_resume_schedule_targets_single_location(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={9100001: "FICTIONAL9", 9100002: "FICTIONAL17 OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(targets=["8108 Fictional9"], resume_schedule=True)

        submit_mock.assert_called_once_with(
            ANY,
            9100006,
            set_temps.set_resume_schedule_payload(9100006),
            "resume schedule",
        )

    def test_resume_schedule_all_targets_all_devices(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={9100001: "FICTIONAL9", 9100002: "FICTIONAL17 OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(allow_all=True, resume_schedule=True)

        self.assertEqual(submit_mock.call_count, 2)

    def test_resume_schedule_can_stop_launchagent(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={9100001: "FICTIONAL9", 9100002: "FICTIONAL17 OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True),
            patch.object(set_temps, "stop_launchagent") as stop_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(
                targets=["8108 Fictional9"],
                resume_schedule=True,
                stop_scheduled_job=True,
            )

        stop_mock.assert_called_once_with()

    def test_stop_launchagent_unloads_schedule_and_legacy_plists(self) -> None:
        paths = [
            set_temps.LAUNCH_AGENT_DIR / "com.padsplit.thermostat.8108-fictional9.0700.plist",
            set_temps.LEGACY_LAUNCH_AGENT_PATH,
        ]
        with (
            patch.object(set_temps, "launchagent_paths", return_value=paths),
            patch.object(set_temps.subprocess, "run") as run_mock,
        ):
            run_mock.return_value = unittest.mock.Mock(returncode=0, stderr="", stdout="")
            set_temps.stop_launchagent()

        self.assertEqual(run_mock.call_count, 2)

    def test_failed_device_write_raises_after_alert(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={9100001: "FICTIONAL9", 9100002: "FICTIONAL17 OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=False),
            patch.object(set_temps, "post_discord_message") as discord_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Failed: FICTIONAL9"):
                set_temps.apply_device_changes(targets=["8108 Fictional9"])

        discord_mock.assert_called_once()

    def test_main_requires_target_or_all(self) -> None:
        with self.assertRaises(SystemExit) as exc:
            set_temps.main([])
        self.assertEqual(exc.exception.code, 2)

    def test_main_rejects_stop_launchagent_with_all(self) -> None:
        with self.assertRaises(SystemExit) as exc:
            set_temps.main(["--resume-schedule", "--all", "--stop-launchagent"])
        self.assertEqual(exc.exception.code, 2)

    def test_main_accepts_resume_schedule_typo_alias(self) -> None:
        with patch.object(set_temps, "apply_device_changes") as apply_mock:
            set_temps.main(["--resume-scheudle", "--target", "8108 Fictional9"])

        apply_mock.assert_called_once_with(
            cool_setpoint=75,
            heat_setpoint=63,
            targets=["8108 Fictional9"],
            location_ids=[],
            allow_all=False,
            resume_schedule=True,
            stop_scheduled_job=False,
        )


if __name__ == "__main__":
    unittest.main()
