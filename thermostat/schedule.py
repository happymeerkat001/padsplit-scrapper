#!/usr/bin/env python3
"""Manage thermostat schedules via LaunchAgents."""

import argparse
import json
import logging
import logging.handlers
import os
import plistlib
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests


ROOT_DIR = Path(__file__).resolve().parent.parent
LAUNCH_AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PREFIX = "com.padsplit.thermostat."
ENFORCER_LABEL = f"{PLIST_PREFIX}enforcer"
PYTHON_PATH = ROOT_DIR / "venv" / "bin" / "python3"
PYTHON = PYTHON_PATH
SET_TEMPS_PATH = ROOT_DIR / "thermostat" / "set_temps.py"
LOG_DIR = ROOT_DIR / "thermostat"
SCHEDULES_PATH = ROOT_DIR / "thermostat" / "config" / "schedules.json"
ENFORCER_LOG_PATH = LOG_DIR / "enforcer.log"
LATEST_SNAPSHOT_PATH = ROOT_DIR / "thermostat" / "output" / "latest.json"
_LAST_LIVE_FETCH_ERROR: Optional[str] = None

LOW_TEMP_ALERT_TARGET = "leanna"
LOW_TEMP_THRESHOLD_F = 75
TEMP_ALERT_STATE_PATH = LOG_DIR / "temp_alert_state.json"


def _post_temp_alert(webhook_url: str, target: str, temp_f: float) -> None:
    _logger = logging.getLogger(__name__)
    try:
        requests.post(
            webhook_url,
            json={"text": f"Leanna temp {temp_f:.0f}\u00b0F \u2014 below {LOW_TEMP_THRESHOLD_F}\u00b0F threshold"},
            timeout=10,
        )
    except Exception as exc:
        _logger.error("Slack temp alert failed: %s", exc)


def _should_send_temp_alert() -> bool:
    """True if no alert sent in last hour (file-based debounce, Unix timestamp)."""
    try:
        state = json.loads(TEMP_ALERT_STATE_PATH.read_text())
        import time as _time

        if _time.time() - float(state["last_alerted"]) < 3600:
            return False
    except (FileNotFoundError, KeyError, ValueError, OSError):
        pass
    return True


def _record_temp_alert() -> None:
    import time as _time

    try:
        TEMP_ALERT_STATE_PATH.write_text(json.dumps({"last_alerted": _time.time()}))
    except OSError:
        pass


@dataclass(frozen=True)
class Slot:
    hour: int
    minute: int
    cool: int
    heat: int

    @property
    def hhmm(self) -> str:
        return f"{self.hour:02d}{self.minute:02d}"

    @property
    def display(self) -> str:
        hour_12 = self.hour % 12 or 12
        suffix = "AM" if self.hour < 12 else "PM"
        return f"{hour_12}:{self.minute:02d} {suffix}"


def parse_time(value: str) -> Tuple[int, int]:
    normalized = value.strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)", normalized)
    if not match:
        raise argparse.ArgumentTypeError(f"Invalid time '{value}'. Use 12-hour format like 7am or 6:30pm.")
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    suffix = match.group(3)
    if hour < 1 or hour > 12 or minute > 59:
        raise argparse.ArgumentTypeError(f"Invalid time '{value}'. Use 12-hour format like 7am or 6:30pm.")
    if suffix == "am":
        hour = 0 if hour == 12 else hour
    else:
        hour = 12 if hour == 12 else hour + 12
    return hour, minute


def parse_slot(values: Sequence[str]) -> Slot:
    if len(values) != 3:
        raise argparse.ArgumentTypeError("--slot requires TIME COOL HEAT")
    hour, minute = parse_time(values[0])
    try:
        cool = int(values[1])
        heat = int(values[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--slot COOL and HEAT must be integers") from exc
    return Slot(hour=hour, minute=minute, cool=cool, heat=heat)


def slot_to_dict(slot: Slot) -> Dict[str, int]:
    return {"hour": slot.hour, "minute": slot.minute, "cool": slot.cool, "heat": slot.heat}


def slot_from_dict(value: Dict[str, object]) -> Slot:
    return Slot(
        hour=int(value["hour"]),
        minute=int(value["minute"]),
        cool=int(value["cool"]),
        heat=int(value["heat"]),
    )


def load_schedules() -> Dict[str, List[Slot]]:
    try:
        payload = json.loads(SCHEDULES_PATH.read_text())
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Failed to load schedules from {SCHEDULES_PATH}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid schedules file {SCHEDULES_PATH}: expected object")

    schedules: Dict[str, List[Slot]] = {}
    for target, slots in payload.items():
        if not isinstance(target, str) or not isinstance(slots, list):
            raise RuntimeError(f"Invalid schedules file {SCHEDULES_PATH}: malformed entry for {target!r}")
        schedules[target] = [slot_from_dict(slot) for slot in slots if isinstance(slot, dict)]
    return schedules


def save_schedules(data: Dict[str, List[Slot]]) -> None:
    existing = load_schedules() if SCHEDULES_PATH.exists() else {}
    merged = dict(existing)
    for target, slots in data.items():
        merged[target] = sorted(slots, key=lambda slot: (slot.hour, slot.minute))

    payload = {
        target: [slot_to_dict(slot) for slot in slots]
        for target, slots in sorted(merged.items())
    }
    SCHEDULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = SCHEDULES_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(tmp_path, SCHEDULES_PATH)


def find_active_slot(slots: List[Slot], now: datetime) -> Slot:
    if not slots:
        raise RuntimeError("Cannot find active slot for an empty schedule")
    sorted_slots = sorted(slots, key=lambda slot: (slot.hour, slot.minute))
    current = (now.hour, now.minute)
    active = [slot for slot in sorted_slots if (slot.hour, slot.minute) <= current]
    return active[-1] if active else sorted_slots[-1]


def sanitize_label(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    slug = slug.strip("-")
    if not slug:
        raise RuntimeError(f"Cannot derive LaunchAgent label from target '{name}'")
    return slug


def label_for(slug: str, slot: Slot) -> str:
    return f"{PLIST_PREFIX}{slug}.{slot.hhmm}"


def plist_path_for(slug: str, slot: Slot) -> Path:
    return LAUNCH_AGENT_DIR / f"{label_for(slug, slot)}.plist"


def log_paths_for(slug: str, slot: Slot) -> Tuple[Path, Path]:
    base = LOG_DIR / f"schedule-{slug}-{slot.hhmm}"
    return base.with_suffix(".stdout.log"), base.with_suffix(".stderr.log")


def build_program_arguments(slot: Slot, target_args: Sequence[str]) -> List[str]:
    return [
        str(PYTHON_PATH),
        str(SET_TEMPS_PATH),
        "--cool",
        str(slot.cool),
        "--heat",
        str(slot.heat),
        *target_args,
    ]


def build_plist_dict(slug: str, slot: Slot, target_args: Sequence[str]) -> Dict:
    stdout_path, stderr_path = log_paths_for(slug, slot)
    return {
        "Label": label_for(slug, slot),
        "ProgramArguments": build_program_arguments(slot, target_args),
        "WorkingDirectory": str(ROOT_DIR),
        "StartCalendarInterval": {"Hour": slot.hour, "Minute": slot.minute},
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
    }


def build_plist(slug: str, slot: Slot, target_args: Sequence[str]) -> str:
    return plistlib.dumps(build_plist_dict(slug, slot, target_args)).decode("utf-8")


def enforcer_plist_path() -> Path:
    return LAUNCH_AGENT_DIR / f"{ENFORCER_LABEL}.plist"


def build_enforcer_plist() -> Dict:
    return {
        "Label": ENFORCER_LABEL,
        "ProgramArguments": [
            str(PYTHON),
            str(ROOT_DIR / "thermostat" / "schedule.py"),
            "enforce",
        ],
        "WorkingDirectory": str(ROOT_DIR),
        "StartInterval": 1800,
        "StandardOutPath": str(LOG_DIR / "enforcer.stdout.log"),
        "StandardErrorPath": str(LOG_DIR / "enforcer.stderr.log"),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
    }


def install_enforcer_if_needed() -> None:
    LAUNCH_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    loaded = loaded_labels()
    path = enforcer_plist_path()
    if ENFORCER_LABEL in loaded:
        return
    path.write_bytes(plistlib.dumps(build_enforcer_plist()))
    result = launchctl(["launchctl", "load", str(path)])
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown launchctl error"
        raise RuntimeError(f"Failed to load enforcer LaunchAgent {path}: {stderr}")


def launchctl(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def managed_plists() -> List[Path]:
    return sorted(LAUNCH_AGENT_DIR.glob(f"{PLIST_PREFIX}*.plist"))


def calendar_plists() -> List[Path]:
    return [path for path in managed_plists() if path.name != f"{ENFORCER_LABEL}.plist"]


def target_plists(slug: str) -> List[Path]:
    return [
        path
        for path in sorted(LAUNCH_AGENT_DIR.glob(f"{PLIST_PREFIX}{slug}.*.plist"))
        if path.name != f"{ENFORCER_LABEL}.plist"
    ]


def unload_and_remove(paths: Iterable[Path]) -> None:
    errors: List[str] = []
    for path in paths:
        unload_result = launchctl(["launchctl", "unload", str(path)])
        if unload_result.returncode not in (0,):
            stderr = unload_result.stderr.strip()
            stdout = unload_result.stdout.strip()
            combined = stderr or stdout
            if combined and "Could not find specified service" not in combined:
                errors.append(f"{path}: {combined}")
        if path.exists():
            path.unlink()
    if errors:
        raise RuntimeError("LaunchAgent unload failed: " + "; ".join(errors))


def install_schedule(target_slug: str, target_args: Sequence[str], slots: Sequence[Slot]) -> None:
    LAUNCH_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    unload_and_remove(target_plists(target_slug))
    for slot in sorted(slots, key=lambda item: (item.hour, item.minute)):
        path = plist_path_for(target_slug, slot)
        path.write_text(build_plist(target_slug, slot, target_args))
        result = launchctl(["launchctl", "load", str(path)])
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip() or "unknown launchctl error"
            raise RuntimeError(f"Failed to load LaunchAgent {path}: {stderr}")


def install_command(args: argparse.Namespace) -> int:
    slots = [parse_slot(values) for values in args.slot]
    if args.all:
        raise RuntimeError("Global enforcer schedules must be installed per --target, not --all")
    else:
        slug = sanitize_label(args.target)
        save_schedules({normalize_location_name(args.target): slots})
        install_enforcer_if_needed()
        unload_and_remove(target_plists(slug))
    return 0


def resume_schedule(target_args: Sequence[str]) -> None:
    cmd = [str(PYTHON_PATH), str(SET_TEMPS_PATH), "--resume-schedule", *target_args]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Resume-schedule command failed with exit code {result.returncode}")


def uninstall_command(args: argparse.Namespace) -> int:
    if args.all:
        unload_and_remove(managed_plists())
        if args.resume_schedule:
            resume_schedule(["--all"])
        return 0

    slug = sanitize_label(args.target)
    unload_and_remove(target_plists(slug))
    if args.resume_schedule:
        resume_schedule(["--target", args.target])
    return 0


def loaded_labels() -> set[str]:
    result = launchctl(["launchctl", "list"])
    if result.returncode != 0:
        return set()
    labels = set()
    for line in result.stdout.splitlines():
        if PLIST_PREFIX in line:
            parts = line.split()
            if parts:
                labels.add(parts[-1])
    return labels


def normalize_location_name(value: str) -> str:
    return value.lower().strip()


def setup_enforcer_logging() -> logging.Logger:
    logger = logging.getLogger("thermostat.enforcer")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        ENFORCER_LOG_PATH,
        maxBytes=1_000_000,
        backupCount=3,
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def find_device_for_target(
    norm_target: str,
    raw_locations: Sequence[Dict],
    location_names: Dict[int, str],
) -> Tuple[Optional[int], Optional[Dict]]:
    for location in raw_locations:
        location_id = location.get("LocationID")
        candidates = []
        if isinstance(location_id, int) and location_names.get(location_id):
            candidates.append(location_names[location_id])
        if location.get("Name"):
            candidates.append(str(location["Name"]))
        # Fallback: device's own Name field (mirrors scraper.py build_output logic)
        first_device = next((d for d in (location.get("Devices") or []) if isinstance(d, dict)), None)
        if first_device and first_device.get("Name"):
            candidates.append(str(first_device["Name"]))

        normalized_candidates = [normalize_location_name(candidate) for candidate in candidates]
        matched = any(
            norm_target == candidate or norm_target in candidate or candidate in norm_target
            for candidate in normalized_candidates
            if candidate
        )
        if not matched:
            continue

        for device in location.get("Devices") or []:
            if not isinstance(device, dict):
                continue
            device_id = device.get("DeviceID")
            if device_id is not None:
                return int(device_id), device
    return None, None


def status_is_permanent_hold(value: object) -> bool:
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return False


def enforce_command() -> int:
    logger = setup_enforcer_logging()
    try:
        try:
            from thermostat.scraper import (
                create_session,
                fetch_device_uidata,
                fetch_location_names,
                fetch_locations,
                load_credentials,
                login,
            )
            from thermostat.set_temps import set_hold_payload, submit_device_change
        except ImportError:
            from scraper import (  # type: ignore[no-redef]
                create_session,
                fetch_device_uidata,
                fetch_location_names,
                fetch_locations,
                load_credentials,
                login,
            )
            from set_temps import set_hold_payload, submit_device_change  # type: ignore[no-redef]

        schedules = load_schedules()
        if not schedules:
            logger.warning("No schedules configured. Run: schedule.py install --target ...")
            return 0

        creds = load_credentials()
        now = datetime.now()
        with create_session() as session:
            login(session, creds["email"], creds["password"])
            raw_locations = fetch_locations(session)
            location_names = fetch_location_names(session)
            slack_webhook = os.getenv("SLACK_WEBHOOK_URL")

            for norm_target, slots in schedules.items():
                active_slot = find_active_slot(slots, now)
                device_id, _device = find_device_for_target(norm_target, raw_locations, location_names)
                if device_id is None:
                    logger.warning("[skip] %s: no matching device found", norm_target)
                    continue

                ui_data = fetch_device_uidata(session, device_id)
                live_cool = ui_data.get("CoolSetpoint")
                live_heat = ui_data.get("HeatSetpoint")
                status_cool = ui_data.get("StatusCool")
                status_heat = ui_data.get("StatusHeat")

                live_temp = ui_data.get("DispTemperature")
                if slack_webhook and live_temp is not None and LOW_TEMP_ALERT_TARGET in norm_target:
                    try:
                        live_temp_f = float(live_temp)
                    except (TypeError, ValueError):
                        logger.warning("[alert] %s: invalid live temperature %r", norm_target, live_temp)
                    else:
                        if live_temp_f < LOW_TEMP_THRESHOLD_F and _should_send_temp_alert():
                            _record_temp_alert()
                            threading.Thread(
                                target=_post_temp_alert,
                                args=(slack_webhook, norm_target, live_temp_f),
                                daemon=True,
                            ).start()
                            logger.info("[alert] %s temp %.0f\u00b0F \u2014 Slack notified", norm_target, live_temp_f)

                if (
                    live_cool is not None
                    and live_heat is not None
                    and int(round(float(live_cool))) == active_slot.cool
                    and int(round(float(live_heat))) == active_slot.heat
                    and status_is_permanent_hold(status_cool)
                    and status_is_permanent_hold(status_heat)
                ):
                    logger.info(
                        "[skip] %s: cool=%d heat=%d (Permanent Hold)",
                        norm_target,
                        active_slot.cool,
                        active_slot.heat,
                    )
                    continue

                payload = set_hold_payload(device_id, active_slot.cool, active_slot.heat)
                ok = submit_device_change(session, device_id, payload, norm_target)
                logger.info(
                    "[set] %s: cool=%d heat=%d -> %s",
                    norm_target,
                    active_slot.cool,
                    active_slot.heat,
                    "OK" if ok else "FAIL",
                )
        return 0
    except Exception:
        logger.exception("Enforcer failed")
        return 1


def load_live_snapshot() -> Tuple[Dict[str, Dict[str, object]], Optional[str]]:
    try:
        payload = json.loads(LATEST_SNAPSHOT_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, None

    snapshot: Dict[str, Dict[str, object]] = {}
    for location in payload.get("locations", []):
        name = location.get("name")
        devices = location.get("devices")
        if not isinstance(name, str) or not isinstance(devices, list) or not devices:
            continue
        device = devices[0]
        if not isinstance(device, dict):
            continue
        snapshot[normalize_location_name(name)] = {
            "temp": device.get("temp"),
            "heat_setpoint": device.get("heat_setpoint"),
            "cool_setpoint": device.get("cool_setpoint"),
        }
    scraped_at = payload.get("scraped_at")
    return snapshot, scraped_at if isinstance(scraped_at, str) else None


def fetch_live_setpoints() -> Optional[Dict[str, Dict[str, object]]]:
    global _LAST_LIVE_FETCH_ERROR
    _LAST_LIVE_FETCH_ERROR = None

    result: Dict[str, Optional[Dict[str, Dict[str, object]]]] = {"snapshot": None}
    error: Dict[str, Optional[str]] = {"message": None}

    def worker() -> None:
        try:
            try:
                from thermostat.scraper import (
                    create_session,
                    extract_device,
                    fetch_device_uidata,
                    fetch_locations,
                    fetch_location_names,
                    load_credentials,
                    login,
                )
            except ImportError:
                from scraper import (  # type: ignore[no-redef]
                    create_session,
                    extract_device,
                    fetch_device_uidata,
                    fetch_locations,
                    fetch_location_names,
                    load_credentials,
                    login,
                )

            creds = load_credentials()
            snapshot: Dict[str, Dict[str, object]] = {}
            with create_session() as session:
                login(session, creds["email"], creds["password"])
                location_names = fetch_location_names(session)
                raw_locations = fetch_locations(session)
                for location in raw_locations:
                    devices = location.get("Devices") or []
                    if not isinstance(devices, list) or not devices:
                        continue
                    first_device = devices[0]
                    if not isinstance(first_device, dict):
                        continue
                    device_id = first_device.get("DeviceID")
                    if device_id is None:
                        continue
                    ui_data = fetch_device_uidata(session, device_id)
                    thermostat_data = first_device.get("ThermostatData") or {}
                    first_device["ThermostatData"] = thermostat_data
                    for key in ("HeatSetpoint", "CoolSetpoint"):
                        if ui_data.get(key) is not None:
                            thermostat_data[key] = ui_data[key]
                    extracted = extract_device(first_device)
                    location_id = location.get("LocationID")
                    location_name = (
                        location_names.get(location_id) if isinstance(location_id, int) else None
                    ) or location.get("Name") or extracted.get("name") or str(location_id or "")
                    if not isinstance(location_name, str) or not location_name.strip():
                        continue
                    snapshot[normalize_location_name(location_name)] = {
                        "temp": extracted.get("temp"),
                        "cool_setpoint": extracted.get("cool_setpoint"),
                        "heat_setpoint": extracted.get("heat_setpoint"),
                        "humidity": extracted.get("humidity"),
                    }
            result["snapshot"] = snapshot
        except (Exception, SystemExit) as exc:
            error["message"] = str(exc) or exc.__class__.__name__

    thread = threading.Thread(target=worker, name="schedule-live-fetch", daemon=True)
    thread.start()
    thread.join(timeout=90)
    if thread.is_alive():
        _LAST_LIVE_FETCH_ERROR = "timed out after 90s"
        return None
    if error["message"]:
        _LAST_LIVE_FETCH_ERROR = error["message"]
        return None
    return result["snapshot"]


def heal_snapshot_cache(live_data: Dict[str, Dict[str, object]]) -> None:
    try:
        payload = json.loads(LATEST_SNAPSHOT_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return

    scraped_at_dt = parse_scraped_at(payload.get("scraped_at") if isinstance(payload, dict) else None)
    if scraped_at_dt is not None:
        age = datetime.now(timezone.utc) - scraped_at_dt.astimezone(timezone.utc)
        if age < timedelta(minutes=10):
            return

    if not isinstance(payload, dict):
        return
    locations = payload.get("locations")
    if not isinstance(locations, list):
        return

    updated = False
    for location in locations:
        if not isinstance(location, dict):
            continue
        name = location.get("name")
        devices = location.get("devices")
        if not isinstance(name, str) or not isinstance(devices, list) or not devices:
            continue
        device = devices[0]
        if not isinstance(device, dict):
            continue
        fresh = live_data.get(normalize_location_name(name))
        if fresh is None:
            continue
        for key in ("cool_setpoint", "heat_setpoint", "temp", "humidity"):
            if key in fresh:
                device[key] = fresh.get(key)
        updated = True

    if not updated:
        return

    payload["scraped_at"] = datetime.now(timezone.utc).isoformat()
    LATEST_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = LATEST_SNAPSHOT_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp_path, LATEST_SNAPSHOT_PATH)


def parse_scraped_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_snapshot_value(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    return "---"


def live_row_for_target(target: str, live_snapshot: Dict[str, Dict[str, object]]) -> Dict[str, str]:
    if target == "all":
        return {"temp": "---", "cool_setpoint": "---", "heat_setpoint": "---"}

    normalized_target = normalize_location_name(target)
    for location_name, device in live_snapshot.items():
        if normalized_target in location_name or location_name in normalized_target:
            return {
                "temp": format_snapshot_value(device.get("temp")),
                "cool_setpoint": format_snapshot_value(device.get("cool_setpoint")),
                "heat_setpoint": format_snapshot_value(device.get("heat_setpoint")),
            }
    return {"temp": "---", "cool_setpoint": "---", "heat_setpoint": "---"}


def plist_summary(path: Path) -> Dict[str, str]:
    payload = plistlib.loads(path.read_bytes())
    label = str(payload["Label"])
    schedule = payload["StartCalendarInterval"]
    hour = int(schedule["Hour"])
    minute = int(schedule["Minute"])
    slot = Slot(hour=hour, minute=minute, cool=0, heat=0)
    args = payload["ProgramArguments"]
    cool = args[args.index("--cool") + 1]
    heat = args[args.index("--heat") + 1]
    if "--all" in args:
        target = "all"
    else:
        target = args[args.index("--target") + 1]
    return {
        "label": label,
        "target": target,
        "time": slot.display,
        "cool": cool,
        "heat": heat,
        "path": str(path),
    }


def legacy_status_command(paths: Optional[List[Path]] = None) -> int:
    paths = calendar_plists() if paths is None else paths
    if not paths:
        print("No thermostat schedules installed.")
        return 0

    now_utc = datetime.now(timezone.utc)
    live_snapshot, scraped_at = load_live_snapshot()
    scraped_at_dt = parse_scraped_at(scraped_at)
    age: Optional[timedelta] = None
    if scraped_at_dt is not None:
        age = now_utc - scraped_at_dt.astimezone(timezone.utc)
    is_stale = scraped_at_dt is None or (age is not None and age > timedelta(hours=2))
    if is_stale:
        if age is None:
            print("[info] live data is stale (missing scraped_at); fetching from TCC...", flush=True)
        else:
            stale_hours = max(1, int(age.total_seconds() // 3600))
            print(f"[info] live data is stale ({stale_hours}h ago); fetching from TCC...", flush=True)
        fresh = fetch_live_setpoints()
        if fresh is not None:
            live_snapshot = fresh
            heal_snapshot_cache(fresh)
        else:
            reason = _LAST_LIVE_FETCH_ERROR or "unknown error"
            print(f"[warn] live fetch failed ({reason}); showing stale data")

    loaded = loaded_labels()
    print(
        f"{'STATE':<10} {'TIME':<9} {'SCHED_COOL':<11} {'SCHED_HEAT':<11} "
        f"{'LIVE_TEMP':<10} {'LIVE_COOL':<10} {'LIVE_HEAT':<10} TARGET"
    )
    for path in paths:
        summary = plist_summary(path)
        state = "loaded" if summary["label"] in loaded else "file-only"
        live = live_row_for_target(summary["target"], live_snapshot)
        print(
            f"{state:<10} {summary['time']:<9} {summary['cool']:<11} {summary['heat']:<11} "
            f"{live['temp']:<10} {live['cool_setpoint']:<10} {live['heat_setpoint']:<10} {summary['target']}"
        )
    return 0


def format_last_run(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "---"


def status_command() -> int:
    if not SCHEDULES_PATH.exists():
        return legacy_status_command()

    schedules = load_schedules()
    loaded = loaded_labels()
    legacy_paths = calendar_plists()
    legacy_summaries = [plist_summary(path) for path in legacy_paths]
    legacy_targets = {normalize_location_name(summary["target"]) for summary in legacy_summaries}

    print(
        f"{'STATE':<12} {'LABEL':<10} {'TIME':<9} {'SCHED_COOL':<11} {'SCHED_HEAT':<11} "
        f"{'LAST_RUN':<16} TARGET"
    )
    enforcer_state = "loaded" if ENFORCER_LABEL in loaded else "not-loaded"
    print(
        f"{enforcer_state:<12} {'[enforcer]':<10} {'---':<9} {'---':<11} {'---':<11} "
        f"{format_last_run(ENFORCER_LOG_PATH):<16} {ENFORCER_LABEL}"
    )

    now = datetime.now()
    for target, slots in sorted(schedules.items()):
        active_slot = find_active_slot(slots, now)
        state = "[hybrid]" if target in legacy_targets else "configured"
        print(
            f"{state:<12} {'[enforcer]':<10} {active_slot.display:<9} {active_slot.cool:<11} "
            f"{active_slot.heat:<11} {'---':<16} {target}"
        )

    for summary in legacy_summaries:
        norm_target = normalize_location_name(summary["target"])
        state = "[hybrid]" if norm_target in schedules else (
            "loaded" if summary["label"] in loaded else "file-only"
        )
        print(
            f"{state:<12} {'[calendar]':<10} {summary['time']:<9} {summary['cool']:<11} "
            f"{summary['heat']:<11} {'---':<16} {summary['target']}"
        )

    if any(normalize_location_name(summary["target"]) in schedules for summary in legacy_summaries):
        print("[note] Hybrid targets still have old calendar plists. Re-run install for those targets.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage thermostat LaunchAgent schedules")
    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install", help="Install thermostat LaunchAgent schedules")
    install_scope = install_parser.add_mutually_exclusive_group(required=True)
    install_scope.add_argument("--target", help='Target one thermostat location, e.g. "6623 Leanna".')
    install_scope.add_argument("--all", action="store_true", help="Apply same schedule to all thermostat locations.")
    install_parser.add_argument(
        "--slot",
        action="append",
        nargs=3,
        metavar=("TIME", "COOL", "HEAT"),
        required=True,
        help="Add schedule slot using 12-hour time like 7am or 6:30pm.",
    )

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove thermostat LaunchAgent schedules")
    uninstall_scope = uninstall_parser.add_mutually_exclusive_group(required=True)
    uninstall_scope.add_argument("--target", help='Target one thermostat location, e.g. "6623 Leanna".')
    uninstall_scope.add_argument("--all", action="store_true", help="Remove all thermostat schedules.")
    uninstall_parser.add_argument(
        "--resume-schedule",
        action="store_true",
        help="Also restore TCC schedule for the same target scope.",
    )

    subparsers.add_parser("status", help="Show installed thermostat schedules")
    subparsers.add_parser("enforce", help="Enforce configured thermostat schedules")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.error("specify install, uninstall, status, or enforce")

    if args.command == "install":
        return install_command(args)
    if args.command == "uninstall":
        return uninstall_command(args)
    if args.command == "status":
        return status_command()
    if args.command == "enforce":
        return enforce_command()
    parser.error(f"unsupported command {args.command}")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("[cancelled] Interrupted by user\n")
        sys.exit(130)
    except RuntimeError as exc:
        sys.stderr.write(f"[error] {exc}\n")
    sys.exit(1)
