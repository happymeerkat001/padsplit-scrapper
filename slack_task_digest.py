#!/usr/bin/env python3
"""Simple Discord task digest for PadSplit data.

Reads tasks from docs/data/latest.json and posts a combined digest to Discord
if DISCORD_WEBHOOK_TASKS is set. Does not treat stats.json vacancy_rooms as
occupancy; those are listed-status and may be stale.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional
from zoneinfo import ZoneInfo
import urllib.request
import urllib.error


DATA_DIR = Path(__file__).parent / "docs" / "data"
LATEST_PATH = DATA_DIR / "latest.json"
# stats.json vacancy_rooms is listed-status and may be stale. Do not merge it here.
STATS_PATH = DATA_DIR / "stats.json"


def load_data() -> Dict[str, Any]:
    return json.loads(LATEST_PATH.read_text())


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


def compose_message(
    latest: Dict[str, Any],
    *,
    weather_block: Optional[str] = None,
    kpis: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the digest from live tasks. kpis/vacancy_rooms are ignored."""
    del kpis
    tasks = latest.get("tasks") or {}
    grouped, total_req, total_open = collect_tasks(tasks)
    task_block = format_message(grouped, total_req, total_open)
    return "\n\n".join(filter(None, [weather_block, task_block]))


DISCORD_MESSAGE_LIMIT = 2000
TRUNCATION_MARKER = "… [truncated]"


def truncate_for_discord(message: str) -> str:
    if len(message) <= DISCORD_MESSAGE_LIMIT:
        return message
    cutoff = DISCORD_MESSAGE_LIMIT - len(TRUNCATION_MARKER)
    return message[:cutoff] + TRUNCATION_MARKER


def send_to_discord(message: str) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_TASKS")
    if not webhook:
        print("DISCORD_WEBHOOK_TASKS not set — skipping POST.")
        return

    payload = json.dumps({"content": truncate_for_discord(message)}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={
            "Content-Type": "application/json",
            # Discord's edge blocks urllib's default "Python-urllib/x.y" UA (Cloudflare error 1010).
            "User-Agent": "padsplit-scraper (https://github.com/happymeerkat001/padsplit-scrapper, 1.0)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.getcode()
            if 200 <= status < 300:
                print("Sent to Discord.")
            else:
                sys.exit(f"Discord webhook returned status {status}.")
    except urllib.error.HTTPError as exc:
        sys.exit(f"Discord webhook HTTP error: {exc.code} {exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"Discord webhook URL error: {exc}")


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
    payload = load_data()
    message = compose_message(payload, weather_block=weather_block)
    print(message)
    send_to_discord(message)


if __name__ == "__main__":
    main()
