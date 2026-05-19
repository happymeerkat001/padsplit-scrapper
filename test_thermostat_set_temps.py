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
            "LocationID": 7712909,
            "Name": "LEANNA",
            "Devices": [{"DeviceID": 9798099, "Name": "LEANNA"}],
        },
        {
            "LocationID": 7715771,
            "Name": "RIDGE OAK",
            "Devices": [{"DeviceID": 9766835, "Name": "RIDGE OAK"}],
        },
    ]


class ThermostatSetTempsTests(unittest.TestCase):
    def test_target_matches_leanna_even_with_street_number(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={7712909: "LEANNA", 7715771: "RIDGE OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(cool_setpoint=78, heat_setpoint=60, targets=["6623 Leanna"])

        submit_mock.assert_called_once_with(
            ANY,
            9798099,
            set_temps.set_hold_payload(9798099, 78, 60),
            "cool=78 heat=60",
        )

    def test_location_id_targets_single_location(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={7712909: "LEANNA", 7715771: "RIDGE OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(location_ids=[7715771])

        submit_mock.assert_called_once_with(
            ANY,
            9766835,
            set_temps.set_hold_payload(9766835, 75, 63),
            "cool=75 heat=63",
        )

    def test_ambiguous_target_fails_without_setting_anything(self) -> None:
        locations = sample_locations() + [
            {
                "LocationID": 8000000,
                "Name": "LEANNA NORTH",
                "Devices": [{"DeviceID": 9000000, "Name": "LEANNA NORTH"}],
            }
        ]
        names = {7712909: "LEANNA", 7715771: "RIDGE OAK", 8000000: "LEANNA NORTH"}
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=locations),
            patch.object(set_temps, "fetch_location_names", return_value=names),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, 'Ambiguous thermostat target "Leanna"'):
                set_temps.apply_device_changes(targets=["Leanna"])

        submit_mock.assert_not_called()

    def test_all_targets_every_location_when_explicit(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={7712909: "LEANNA", 7715771: "RIDGE OAK"}),
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
            patch.object(set_temps, "fetch_location_names", return_value={7712909: "LEANNA", 7715771: "RIDGE OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True) as submit_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(targets=["6623 Leanna"], resume_schedule=True)

        submit_mock.assert_called_once_with(
            ANY,
            9798099,
            set_temps.set_resume_schedule_payload(9798099),
            "resume schedule",
        )

    def test_resume_schedule_all_targets_all_devices(self) -> None:
        with (
            patch.object(set_temps, "load_credentials", return_value={"email": "user", "password": "pw"}),
            patch.object(set_temps, "create_session", return_value=DummySession()),
            patch.object(set_temps, "login"),
            patch.object(set_temps, "fetch_locations", return_value=sample_locations()),
            patch.object(set_temps, "fetch_location_names", return_value={7712909: "LEANNA", 7715771: "RIDGE OAK"}),
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
            patch.object(set_temps, "fetch_location_names", return_value={7712909: "LEANNA", 7715771: "RIDGE OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=True),
            patch.object(set_temps, "stop_launchagent") as stop_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            set_temps.apply_device_changes(
                targets=["6623 Leanna"],
                resume_schedule=True,
                stop_scheduled_job=True,
            )

        stop_mock.assert_called_once_with()

    def test_stop_launchagent_unloads_schedule_and_legacy_plists(self) -> None:
        paths = [
            set_temps.LAUNCH_AGENT_DIR / "com.padsplit.thermostat.6623-leanna.0700.plist",
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
            patch.object(set_temps, "fetch_location_names", return_value={7712909: "LEANNA", 7715771: "RIDGE OAK"}),
            patch.object(set_temps, "submit_device_change", return_value=False),
            patch.object(set_temps, "post_slack_message") as slack_mock,
            patch.object(set_temps.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Failed: LEANNA"):
                set_temps.apply_device_changes(targets=["6623 Leanna"])

        slack_mock.assert_called_once()

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
            set_temps.main(["--resume-scheudle", "--target", "6623 Leanna"])

        apply_mock.assert_called_once_with(
            cool_setpoint=75,
            heat_setpoint=63,
            targets=["6623 Leanna"],
            location_ids=[],
            allow_all=False,
            resume_schedule=True,
            stop_scheduled_job=False,
        )


if __name__ == "__main__":
    unittest.main()
