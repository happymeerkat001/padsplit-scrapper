#!/usr/bin/env python3
"""Simple Slack task digest for PadSplit data.

Reads docs/data/latest.json, summarizes Requests/Open tasks by property,
and posts to a Slack webhook if SLACK_WEBHOOK_TASKS is set.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from zoneinfo import ZoneInfo
import urllib.request
import urllib.error


DATA_PATH = Path(__file__).parent / "docs" / "data" / "latest.json"


def load_tasks() -> Dict:
    raw = json.loads(DATA_PATH.read_text())
    return raw.get("tasks") or {}


def collect_tasks(tasks: Dict[str, List[Dict]]) -> Tuple[Dict[str, List[Tuple[str, str, Optional[int]]]], int, int]:
    buckets = ("Requests", "Open")
    grouped: Dict[str, List[Tuple[str, str, Optional[int]]]] = {}
    total_req = total_open = 0
    for bucket in buckets:
        for task in tasks.get(bucket, []) or []:
            addr = (task.get("property_address") or {}).get("street1") or "Unknown"
            desc = task.get("details") or task.get("description") or "(no description)"
            room_number: Optional[int] = task.get("room_number")
            grouped.setdefault(addr, []).append((bucket, desc, room_number))
            if bucket == "Requests":
                total_req += 1
            elif bucket == "Open":
                total_open += 1
    return grouped, total_req, total_open


def format_message(grouped: Dict[str, List[Tuple[str, str, Optional[int]]]], total_req: int, total_open: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    if total_req + total_open == 0:
        return f"Tasks Digest ({today}): ✅ No open or pending tasks."

    lines = [f"Tasks Digest ({today}):"]
    for addr in sorted(grouped.keys()):
        lines.append(f"{addr}:")
        for bucket, desc, room_number in grouped[addr]:
            room_str = f" (Room {room_number})" if room_number is not None else ""
            lines.append(f"[{bucket}]{room_str} {desc}")
        lines.append("")  # blank line between properties
    lines.append(f"Total: {total_req} Requests, {total_open} Open")
    return "\n".join(lines)


def send_to_slack(message: str) -> None:
    webhook = os.environ.get("SLACK_WEBHOOK_TASKS")
    if not webhook:
        print("SLACK_WEBHOOK_TASKS not set — skipping POST.")
        return

    payload = json.dumps({"text": message}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.getcode()
            if 200 <= status < 300:
                print("Sent to Slack.")
            else:
                print(f"Slack webhook returned status {status}.")
    except urllib.error.HTTPError as exc:
        print(f"Slack webhook HTTP error: {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        print(f"Slack webhook URL error: {exc}")


def fetch_weather() -> Optional[str]:
    now_ct = datetime.now(ZoneInfo("America/Chicago"))
    hour = now_ct.hour
    if hour < 5 or hour >= 9:
        print("Skipping weather (not morning run.)")
        return None

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=33.1507&longitude=-96.8236"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&temperature_unit=fahrenheit"
        "&timezone=America%2FChicago"
    )
    try:
        with urllib.request.urlopen(url) as resp:
            data = json.loads(resp.read().decode())
        times = data.get("daily", {}).get("time", []) or []
        highs = data.get("daily", {}).get("temperature_2m_max", []) or []
        lows = data.get("daily", {}).get("temperature_2m_min", []) or []
        lines = ["🌤️  DFW 7-Day Forecast:"]
        for date_str, high, low in zip(times, highs, lows):
            d = datetime.strptime(date_str, "%Y-%m-%d")
            label = d.strftime("%a (%-m/%-d)")
            flags = ""
            if high is not None and high >= 98:
                flags += " 🚨"
            if low is not None and low <= 60:
                flags += " ❄️"
            lines.append(f"{label}: High {round(high)}°F / Low {round(low)}°F{flags}")
        return "\n".join(lines)
    except Exception as err:
        print(f"Weather fetch failed: {err}")
        return None


def main() -> None:
    weather_block = fetch_weather()
    tasks = load_tasks()
    grouped, total_req, total_open = collect_tasks(tasks)
    task_block = format_message(grouped, total_req, total_open)
    message = "\n\n".join(filter(None, [weather_block, task_block]))
    print(message)
    send_to_slack(message)


if __name__ == "__main__":
    main()
