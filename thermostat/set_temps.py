"""Set thermostat setpoints via Total Connect Comfort.

Usage:
    python3 thermostat/set_temps.py --cool 78 --heat 60 --target "6623 Leanna"
    python3 thermostat/set_temps.py --location-id 7712909
    python3 thermostat/set_temps.py --resume-schedule --target "6623 Leanna" --stop-launchagent
    python3 thermostat/set_temps.py --resume-schedule --all
    python3 thermostat/set_temps.py --all
"""

import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

import requests

# Reuse auth/session logic from scraper
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from padsplit_scraper.slack_notifier import post_slack_message
from thermostat.scraper import (
    PORTAL_URL,
    TIMEOUT,
    create_session,
    fetch_location_names,
    fetch_locations,
    load_credentials,
    login,
)

SUBMIT_URL = f"{PORTAL_URL}/Device/SubmitControlScreenChanges"
LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / "com.padsplit.thermostat-set-temps.plist"

DEFAULT_COOL = 75
DEFAULT_HEAT = 63


def normalize_text(value: str) -> str:
    cleaned = []
    for char in value.lower():
        cleaned.append(char if char.isalnum() else " ")
    return " ".join("".join(cleaned).split())


def tokenize(value: str) -> List[str]:
    normalized = normalize_text(value)
    return normalized.split() if normalized else []


def target_matches(target: str, candidate_strings: Iterable[str]) -> bool:
    target_tokens = tokenize(target)
    if not target_tokens:
        return False

    alpha_tokens = [token for token in target_tokens if not token.isdigit()]
    candidate_token_set: Set[str] = set()
    for candidate in candidate_strings:
        candidate_token_set.update(tokenize(candidate))

    if alpha_tokens:
        return all(token in candidate_token_set for token in alpha_tokens)
    return any(token in candidate_token_set for token in target_tokens)


def describe_location(loc: Dict, location_names: Dict[int, str]) -> str:
    location_id = loc.get("LocationID")
    location_name = location_names.get(location_id) or str(location_id)
    device_names = [str(dev.get("Name")) for dev in (loc.get("Devices") or []) if dev.get("Name")]
    if device_names:
        return f"{location_name} (id={location_id}, devices={', '.join(device_names)})"
    return f"{location_name} (id={location_id})"


def candidate_strings(loc: Dict, location_names: Dict[int, str]) -> List[str]:
    location_id = loc.get("LocationID")
    candidates = [str(location_id)]
    if location_names.get(location_id):
        candidates.append(location_names[location_id])
    if loc.get("Name"):
        candidates.append(str(loc["Name"]))
    for dev in loc.get("Devices") or []:
        if dev.get("Name"):
            candidates.append(str(dev["Name"]))
        if dev.get("DeviceID"):
            candidates.append(str(dev["DeviceID"]))
    return candidates


def find_location_by_id(
    locations: Sequence[Dict],
    location_names: Dict[int, str],
    location_id: int,
) -> Dict:
    matches = [loc for loc in locations if loc.get("LocationID") == location_id]
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(describe_location(loc, location_names) for loc in locations)
    raise RuntimeError(f"No thermostat location matched id={location_id}. Available: {available}")


def find_location_by_target(
    locations: Sequence[Dict],
    location_names: Dict[int, str],
    target: str,
) -> Dict:
    matches = [
        loc
        for loc in locations
        if target_matches(target, candidate_strings(loc, location_names))
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        available = ", ".join(describe_location(loc, location_names) for loc in locations)
        raise RuntimeError(f'No thermostat location matched target "{target}". Available: {available}')
    matched = ", ".join(describe_location(loc, location_names) for loc in matches)
    raise RuntimeError(f'Ambiguous thermostat target "{target}". Matches: {matched}')


def select_locations(
    locations: Sequence[Dict],
    location_names: Dict[int, str],
    targets: Sequence[str],
    location_ids: Sequence[int],
    allow_all: bool,
) -> List[Dict]:
    if allow_all:
        return list(locations)

    selected: Dict[int, Dict] = {}
    for location_id in location_ids:
        loc = find_location_by_id(locations, location_names, location_id)
        selected[loc["LocationID"]] = loc

    for target in targets:
        loc = find_location_by_target(locations, location_names, target)
        selected[loc["LocationID"]] = loc

    if not selected:
        raise RuntimeError("Specify at least one thermostat target via --target, --location-id, or --all")

    return list(selected.values())


def submit_device_change(
    session: requests.Session,
    device_id: int,
    payload: Dict,
    action_label: str,
) -> bool:
    try:
        resp = session.post(
            SUBMIT_URL,
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        success = data.get("success", False) if isinstance(data, dict) else False
        sys.stderr.write(
            f"[set] Device {device_id}: {action_label} "
            f"-> {'OK' if success else f'FAIL ({data})'}\n"
        )
        return success
    except Exception as exc:
        sys.stderr.write(f"[set] Device {device_id}: ERROR {exc}\n")
        return False


def set_hold_payload(device_id: int, cool_setpoint: int, heat_setpoint: int) -> Dict:
    return {
        "DeviceID": device_id,
        "SystemSwitch": None,
        "HeatSetpoint": heat_setpoint,
        "CoolSetpoint": cool_setpoint,
        "HeatNextPeriod": None,
        "CoolNextPeriod": None,
        "StatusHeat": 1,
        "StatusCool": 1,
        "FanMode": None,
    }


def set_resume_schedule_payload(device_id: int) -> Dict:
    return {
        "DeviceID": device_id,
        "SystemSwitch": None,
        "HeatSetpoint": None,
        "CoolSetpoint": None,
        "HeatNextPeriod": None,
        "CoolNextPeriod": None,
        "StatusHeat": 0,
        "StatusCool": 0,
        "FanMode": None,
    }


def stop_launchagent() -> None:
    result = subprocess.run(
        ["launchctl", "unload", str(LAUNCH_AGENT_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown launchctl error"
        raise RuntimeError(f"Failed to unload LaunchAgent {LAUNCH_AGENT_PATH}: {stderr}")


def apply_device_changes(
    cool_setpoint: int = DEFAULT_COOL,
    heat_setpoint: int = DEFAULT_HEAT,
    targets: Optional[Sequence[str]] = None,
    location_ids: Optional[Sequence[int]] = None,
    allow_all: bool = False,
    resume_schedule: bool = False,
    stop_scheduled_job: bool = False,
) -> None:
    creds = load_credentials()
    with create_session() as session:
        sys.stderr.write("[auth] Logging in\n")
        login(session, creds["email"], creds["password"])

        sys.stderr.write("[fetch] Getting device list\n")
        locations = fetch_locations(session)
        location_names = fetch_location_names(session)
        selected_locations = select_locations(
            locations=locations,
            location_names=location_names,
            targets=targets or [],
            location_ids=location_ids or [],
            allow_all=allow_all,
        )

        results: List[Dict] = []
        for loc in selected_locations:
            for dev in loc.get("Devices") or []:
                device_id = dev.get("DeviceID")
                device_name = dev.get("Name", str(device_id))
                if not device_id:
                    continue
                if resume_schedule:
                    payload = set_resume_schedule_payload(device_id)
                    action_label = "resume schedule"
                else:
                    payload = set_hold_payload(device_id, cool_setpoint, heat_setpoint)
                    action_label = f"cool={cool_setpoint} heat={heat_setpoint}"
                ok = submit_device_change(session, device_id, payload, action_label)
                results.append({"name": device_name, "id": device_id, "ok": ok})
                time.sleep(1)

        if not results:
            names = ", ".join(describe_location(loc, location_names) for loc in selected_locations)
            raise RuntimeError(f"No thermostat devices found for selected locations: {names}")

        total = len(results)
        succeeded = sum(1 for result in results if result["ok"])
        failed = [result for result in results if not result["ok"]]

        sys.stderr.write(f"\n[done] {succeeded}/{total} devices set successfully\n")

        if resume_schedule and stop_scheduled_job:
            stop_launchagent()
            sys.stderr.write(f"[launchagent] Unloaded {LAUNCH_AGENT_PATH}\n")

        if failed:
            names = ", ".join(result["name"] for result in failed)
            target_mode = "resume schedule" if resume_schedule else f"cool={cool_setpoint} heat={heat_setpoint}"
            msg = f"Thermostat set_temps: {succeeded}/{total} OK. Failed: {names}. Target: {target_mode}"
            sys.stderr.write(f"[alert] {msg}\n")
            try:
                post_slack_message(msg)
            except Exception as exc:
                sys.stderr.write(f"[alert] Slack notify failed: {exc}\n")


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(description="Set thermostat setpoints")
    parser.add_argument("--cool", type=int, default=DEFAULT_COOL, help=f"Cool setpoint (default {DEFAULT_COOL})")
    parser.add_argument("--heat", type=int, default=DEFAULT_HEAT, help=f"Heat setpoint (default {DEFAULT_HEAT})")
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help='Target one thermostat location by name or address, e.g. --target "6623 Leanna". Repeatable.',
    )
    parser.add_argument(
        "--location-id",
        type=int,
        action="append",
        default=[],
        help="Only target the specified Total Connect Comfort location ID. Repeatable.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Target every thermostat location.",
    )
    parser.add_argument(
        "--resume-schedule",
        "--resume-scheudle",
        dest="resume_schedule",
        action="store_true",
        help="Clear hold and resume the thermostat's TCC schedule.",
    )
    parser.add_argument(
        "--stop-launchagent",
        action="store_true",
        help="Unload the thermostat LaunchAgent after a targeted resume-schedule run.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.all and (args.target or args.location_id):
        parser.error("--all cannot be combined with --target or --location-id")
    if not args.all and not args.target and not args.location_id:
        parser.error("specify --target, --location-id, or --all")
    if args.stop_launchagent and args.all:
        parser.error("--stop-launchagent cannot be used with --all")
    if args.stop_launchagent and not args.resume_schedule:
        parser.error("--stop-launchagent requires --resume-schedule")

    apply_device_changes(
        cool_setpoint=args.cool,
        heat_setpoint=args.heat,
        targets=args.target,
        location_ids=args.location_id,
        allow_all=args.all,
        resume_schedule=args.resume_schedule,
        stop_scheduled_job=args.stop_launchagent,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except requests.exceptions.Timeout as exc:
        sys.stderr.write(f"[error] Timeout during thermostat set: {exc}\n")
    except requests.exceptions.ConnectionError as exc:
        sys.stderr.write(f"[error] Network error during thermostat set: {exc}\n")
    except requests.exceptions.RequestException as exc:
        sys.stderr.write(f"[error] HTTP error during thermostat set: {exc}\n")
    except RuntimeError as exc:
        sys.stderr.write(f"[error] {exc}\n")
    sys.exit(1)
