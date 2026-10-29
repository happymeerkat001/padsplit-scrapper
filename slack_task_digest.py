#!/usr/bin/env python3
"""Discord task digest for PadSplit maintenance tickets.

Reads tasks from docs/data/latest.json and posts a combined digest to Discord
when DISCORD_WEBHOOK_TASKS is set. Does not treat stats.json vacancy_rooms as
occupancy; those are listed-status and may be stale.

The morning PadSplit scrape posts once during 05:00–07:59 America/Chicago.
A ledger at docs/data/task_digest_ledger.json records that Chicago date so
later scrapes and the 11:00 UTC Discord Digests workflow skip the rest of
the day. The cron remains the backup when no morning scrape posted.

Dry run (counts only, no Discord post, no ledger write):

    python3 slack_task_digest.py --dry-run

The dry-run lines are `requests`, `open`, and `properties`. They do not
include tenant names, street addresses, ticket text, codes, or amounts.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import urllib.request
import urllib.error


DATA_DIR = Path(__file__).parent / "docs" / "data"
LATEST_PATH = DATA_DIR / "latest.json"
LEDGER_PATH = DATA_DIR / "task_digest_ledger.json"
# stats.json vacancy_rooms is listed-status and may be stale. Do not merge it here.
STATS_PATH = DATA_DIR / "stats.json"

CT = ZoneInfo("America/Chicago")
# Inclusive clock hours. Scrape cron is :17 and :47 UTC, which falls in this
# window at 05:17–07:47 America/Chicago in both CST and CDT.
MORNING_START_HOUR = 5
MORNING_END_HOUR = 7

# Requests and Open match the PadSplit columns Joe works in the morning.
# Eviction, Other, In Progress, On Hold, and Complete stay off this list.
TASK_BUCKETS = ("Requests", "Open")

LEDGER_KEYS = ("date", "posted", "source", "posted_at")


def _as_ct(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=CT)
    return now.astimezone(CT)


def ct_day(now: datetime) -> str:
    return _as_ct(now).date().isoformat()


def in_morning_window(now: datetime) -> bool:
    hour = _as_ct(now).hour
    return MORNING_START_HOUR <= hour <= MORNING_END_HOUR


def load_data(path: Optional[Path] = None) -> Dict[str, Any]:
    return json.loads((path or LATEST_PATH).read_text())


def load_ledger(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def already_posted_today(ledger: Dict[str, Any], day: str) -> bool:
    return bool(ledger.get("posted")) and ledger.get("date") == day


def post_decision(*, already_posted: bool, after_scrape: bool, now: datetime) -> Optional[str]:
    """Return a skip reason, or None when the digest should be sent."""
    if already_posted:
        return "already posted today"
    if after_scrape and not in_morning_window(now):
        return "outside 05:00-07:59 America/Chicago"
    return None


def write_ledger(path: Path, *, day: str, source: str, posted_at: datetime) -> None:
    payload = {
        "date": day,
        "posted": True,
        "source": source,
        "posted_at": _as_ct(posted_at).isoformat(timespec="seconds"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def collect_tasks(
    tasks: Dict[str, List[Dict]],
) -> Tuple[Dict[str, List[Tuple[str, str, Optional[int]]]], int, int]:
    grouped: Dict[str, List[Tuple[str, str, Optional[int]]]] = {}
    total_req = total_open = 0
    for bucket in TASK_BUCKETS:
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


def digest_counts(latest: Dict[str, Any]) -> Dict[str, int]:
    tasks = latest.get("tasks") or {}
    grouped, total_req, total_open = collect_tasks(tasks)
    return {
        "requests": total_req,
        "open": total_open,
        "properties": len(grouped),
    }


def format_dry_run(counts: Dict[str, int]) -> str:
    """Counts only. Callers must not print the composed digest in dry-run."""
    return "\n".join(
        [
            "dry-run",
            f"requests: {counts['requests']}",
            f"open: {counts['open']}",
            f"properties: {counts['properties']}",
        ]
    )


def format_message(
    grouped: Dict[str, List[Tuple[str, str, Optional[int]]]],
    total_req: int,
    total_open: int,
    *,
    today: Optional[str] = None,
) -> str:
    today = today or ct_day(datetime.now(CT))
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
    today: Optional[str] = None,
) -> str:
    """Build the digest from live tasks. kpis/vacancy_rooms are ignored."""
    del kpis
    tasks = latest.get("tasks") or {}
    grouped, total_req, total_open = collect_tasks(tasks)
    task_block = format_message(grouped, total_req, total_open, today=today)
    return "\n\n".join(filter(None, [weather_block, task_block]))


DISCORD_MESSAGE_LIMIT = 2000
TRUNCATION_MARKER = "… [truncated]"


def truncate_for_discord(message: str) -> str:
    if len(message) <= DISCORD_MESSAGE_LIMIT:
        return message
    cutoff = DISCORD_MESSAGE_LIMIT - len(TRUNCATION_MARKER)
    return message[:cutoff] + TRUNCATION_MARKER


def send_to_discord(message: str) -> bool:
    """Post the digest. Never print the webhook URL or any other secret."""
    webhook = os.environ.get("DISCORD_WEBHOOK_TASKS")
    if not webhook:
        print("DISCORD_WEBHOOK_TASKS not set — skipping POST.")
        return False

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
                return True
            print(f"Discord webhook returned status {status}.")
            return False
    except urllib.error.HTTPError as exc:
        print(f"Discord webhook HTTP error: {exc.code}")
        return False
    except urllib.error.URLError:
        print("Discord webhook URL error")
        return False


def fetch_weather() -> Optional[str]:
    now_ct = datetime.now(CT)
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


def run_digest(
    *,
    latest: Dict[str, Any],
    now: datetime,
    ledger_path: Path = LEDGER_PATH,
    after_scrape: bool = False,
    dry_run: bool = False,
    send: Callable[[str], bool] = send_to_discord,
    weather_fn: Callable[[], Optional[str]] = fetch_weather,
) -> int:
    """Compose and maybe post. Dry-run prints counts and always returns 0."""
    if dry_run:
        compose_message(latest, weather_block=None, today=ct_day(now))
        print(format_dry_run(digest_counts(latest)))
        return 0

    day = ct_day(now)
    reason = post_decision(
        already_posted=already_posted_today(load_ledger(ledger_path), day),
        after_scrape=after_scrape,
        now=now,
    )
    if reason:
        print(f"Skip task digest: {reason}.")
        return 0

    message = compose_message(latest, weather_block=weather_fn(), today=day)
    print(message)
    if not send(message):
        print("Task digest was not posted; ledger unchanged.")
        return 1

    write_ledger(
        ledger_path,
        day=day,
        source="scrape" if after_scrape else "cron",
        posted_at=now,
    )
    print(f"Recorded task digest for {day}.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Post the PadSplit task digest to Discord, or print counts only."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Compose the digest and print request/open/property counts only. "
            "Does not post to Discord and does not write the ledger. "
            "Output has no tenant names, codes, or amounts."
        ),
    )
    parser.add_argument(
        "--after-scrape",
        action="store_true",
        help=(
            "Morning scrape hook. Post only during 05:00-07:59 America/Chicago, "
            "and only when today's ledger date is not already recorded."
        ),
    )
    parser.add_argument("--latest", type=Path, default=LATEST_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    latest = load_data(args.latest)
    return run_digest(
        latest=latest,
        now=datetime.now(CT),
        ledger_path=args.ledger,
        after_scrape=args.after_scrape,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
