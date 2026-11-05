"""Hourly SmartHome enforcer. Does not import Honeywell write paths."""

from __future__ import annotations

import argparse
import json
import plistlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from smarthome import cloud, identity, intent, policy, session

ROOT_DIR = Path(__file__).resolve().parent.parent
STREAK_PATH = ROOT_DIR / "logs" / "smarthome_fail_streak.json"
DIGEST_PATH = ROOT_DIR / "logs" / "smarthome_digest.json"
WATCHER_LABEL = "com.padsplit.smarthome.watcher"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{WATCHER_LABEL}.plist"
LOG_PATH = ROOT_DIR / "logs" / "smarthome-watcher.log"
MAX_STREAK = 3
START_INTERVAL = 3600
DIGEST_HOURS = (6, 14, 20)
FLOOR_F = 74


def _notify(text: str) -> None:
    try:
        from padsplit_scraper.discord_notifier import post_discord_message

        post_discord_message(text)
    except Exception as exc:
        sys.stderr.write(f"Discord notify failed: {exc}\n")


def _load_streak(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or STREAK_PATH
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"count": 0, "day": None}


def _save_streak(count: int, day: str, path: Optional[Path] = None) -> None:
    path = path or STREAK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"count": count, "day": day}) + "\n")


def _today(now: datetime) -> str:
    return now.date().isoformat()


def _live_running(dev: Any) -> Optional[bool]:
    return cloud._readback_running(dev)


def _read_live(
    client: Any,
    item: Dict[str, Any],
    device_fn: Optional[Callable[..., Any]],
) -> tuple[Optional[bool], Optional[float]]:
    if device_fn is not None:
        dev = device_fn(client, item.get("id"))
    else:
        dev = cloud._device(client, item.get("id"))
    return _live_running(dev), cloud._readback_temp_f(dev)


def _load_digest(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or DIGEST_PATH
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"day": None, "hours": []}


def _digest_due(now: datetime, path: Optional[Path] = None) -> bool:
    if now.hour not in DIGEST_HOURS:
        return False
    data = _load_digest(path)
    if data.get("day") != _today(now):
        return True
    return now.hour not in (data.get("hours") or [])


def _mark_digest(now: datetime, path: Optional[Path] = None) -> None:
    path = path or DIGEST_PATH
    data = _load_digest(path)
    day = _today(now)
    hours = list(data.get("hours") or []) if data.get("day") == day else []
    if now.hour not in hours:
        hours.append(now.hour)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"day": day, "hours": hours}) + "\n")


def _format_digest(when: datetime, rows: List[str]) -> str:
    stamp = when.strftime("%H:%M")
    body = "\n".join(rows) if rows else "(no units)"
    return f"SmartHome {stamp}\n{body}"


def enforce_tick(
    *,
    now: Optional[datetime] = None,
    connect_fn: Callable[[], Any] = cloud.connect,
    set_temp_fn: Callable[..., None] = cloud.set_temp,
    turn_off_fn: Callable[..., None] = cloud.turn_off,
    list_acs_fn: Callable[..., List[Dict[str, Any]]] = cloud.list_acs,
    device_fn: Optional[Callable[..., Any]] = None,
    intent_path: Optional[Path] = None,
    notify_fn: Callable[[str], None] = _notify,
) -> Dict[str, Any]:
    when = now or datetime.now()
    intent_path = intent_path or intent.INTENT_PATH
    day = _today(when)
    streak = _load_streak()
    if streak.get("day") == day and int(streak.get("count") or 0) >= MAX_STREAK:
        notify_fn("SmartHome watcher skipped: three consecutive all-unit failures today")
        return {"skipped": True, "failed": [], "ok": []}

    failed: List[str] = []
    ok: List[str] = []
    digest_rows: List[str] = []
    with session.session_lock():
        fingerprint = identity.current_fingerprint()
        if session.cooldown_active(fingerprint=fingerprint):
            notify_fn("SmartHome watcher skipped: 65027 cooldown")
            return {"skipped": True, "failed": [], "ok": []}
        try:
            client = connect_fn()
            units = list_acs_fn(client)
        except cloud.SmartHomeSessionLimitError:
            session.start_cooldown(fingerprint=fingerprint)
            notify_fn("SmartHome watcher: session limit")
            _save_streak(int(streak.get("count") or 0) + 1, day)
            return {"skipped": True, "failed": ["auth"], "ok": []}
        except cloud.SmartHomeError as exc:
            notify_fn(f"SmartHome watcher: login/list failed. {exc}")
            _save_streak(int(streak.get("count") or 0) + 1, day)
            return {"skipped": True, "failed": ["auth"], "ok": []}

        for item in units:
            name = str(item.get("name") or "")
            if not name:
                continue
            action = policy.resolve_action(name, when, intent_path=intent_path)
            try:
                live_on, live_f = _read_live(client, item, device_fn)
                if action["kind"] in {"off", "night_off"}:
                    if live_on is False:
                        ok.append(name)
                    else:
                        turn_off_fn(client, name)
                        ok.append(name)
                    shown = "off"
                elif action["kind"] == "floor":
                    target = float(action.get("f") or FLOOR_F)
                    if live_on and live_f is not None and live_f >= target - 0.5:
                        ok.append(name)
                    else:
                        set_temp_fn(client, name, target)
                        live_f = target
                        live_on = True
                        ok.append(name)
                    shown = f"on {live_f:.0f}°F" if live_f is not None else "on"
                else:
                    shown = "skip"
                digest_rows.append(f"{name}: {shown}")
            except Exception as exc:
                sys.stderr.write(f"SmartHome watcher failed {name}: {exc}\n")
                failed.append(name)
                digest_rows.append(f"{name}: failed")

    if failed:
        notify_fn(f"SmartHome watcher: {len(ok)}/{len(ok) + len(failed)} OK. Failed: {', '.join(failed)}")
        if not ok:
            _save_streak(int(streak.get("count") or 0) + 1 if streak.get("day") == day else 1, day)
        else:
            _save_streak(0, day)
    else:
        _save_streak(0, day)
    if _digest_due(when):
        notify_fn(_format_digest(when, digest_rows))
        _mark_digest(when)
    return {"skipped": False, "failed": failed, "ok": ok}


def build_plist() -> Dict[str, Any]:
    python = ROOT_DIR / "venv" / "bin" / "python3"
    return {
        "Label": WATCHER_LABEL,
        "ProgramArguments": [str(python), "-m", "smarthome.watcher"],
        "WorkingDirectory": str(ROOT_DIR),
        "StartInterval": START_INTERVAL,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }


def write_plist(path: Path = PLIST_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(build_plist()))
    return path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="SmartHome window AC watcher")
    parser.add_argument("command", nargs="?", default="enforce", choices=["enforce", "write-plist"])
    args = parser.parse_args(argv)
    if args.command == "write-plist":
        dest = write_plist()
        print(dest)
        return 0
    result = enforce_tick()
    if result.get("skipped") or result.get("failed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
