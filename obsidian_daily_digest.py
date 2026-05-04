#!/usr/bin/env python3
import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


LOCAL_TZ = ZoneInfo("America/Chicago")
ENV_VAR = "OBSIDIAN_DAILY_NOTES_DIR"
START_MARKER = "<!-- padsplit-daily-digest:start -->"
END_MARKER = "<!-- padsplit-daily-digest:end -->"
PADSPLIT_DATA_PATH = Path(__file__).parent / "padsplit_scraper" / "output" / "latest.json"
THERMOSTAT_DATA_PATH = Path(__file__).parent / "thermostat" / "output" / "latest.json"
RECENT_MESSAGE_LIMIT = 5
TASKS_PER_ADDRESS_LIMIT = 3
THERMOSTAT_LIMIT = 10

def load_environment() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(env_path)


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=LOCAL_TZ)
    return parsed


def format_local_timestamp(value: Optional[str]) -> str:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return "unknown time"
    return parsed.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %I:%M %p %Z")


def normalize_whitespace(value: Optional[str]) -> str:
    return " ".join((value or "").split())


def load_json(path: Path) -> Dict:
    return json.loads(path.read_text())


def get_daily_note_path(base_dir: Path, note_date: datetime) -> Path:
    if not base_dir.exists() or not base_dir.is_dir():
        raise RuntimeError(
            f"{ENV_VAR} must point to an existing daily notes directory. Current value: {base_dir}"
        )
    return base_dir / f"{note_date.strftime('%Y-%m-%d')}.md"


def get_counts(tasks: Dict) -> Tuple[int, int, int]:
    requests_count = len(tasks.get("Requests") or [])
    open_count = len(tasks.get("Open") or [])
    complete_count = len(tasks.get("Complete") or [])
    return requests_count, open_count, complete_count


def format_task_groups(tasks: Dict) -> List[str]:
    grouped: Dict[str, List[Tuple[str, str, Optional[int]]]] = defaultdict(list)
    for bucket in ("Requests", "Open"):
        for task in tasks.get(bucket, []) or []:
            address = ((task.get("property_address") or {}).get("street1") or "Unknown address").strip()
            details = normalize_whitespace(task.get("details") or task.get("description")) or "(no details)"
            room_number = task.get("room_number")
            grouped[address].append((bucket, details, room_number))

    if not grouped:
        return ["- No active PadSplit tasks."]

    lines: List[str] = []
    for address in sorted(grouped):
        rendered = []
        for bucket, details, room_number in grouped[address][:TASKS_PER_ADDRESS_LIMIT]:
            room = f" Room {room_number}" if room_number is not None else ""
            rendered.append(f"{bucket}{room}: {details}")
        extra_count = max(len(grouped[address]) - TASKS_PER_ADDRESS_LIMIT, 0)
        suffix = f" (+{extra_count} more)" if extra_count else ""
        lines.append(f"- {address}: {' | '.join(rendered)}{suffix}")
    return lines


def get_thread_occupant_names(thread: Dict) -> set[str]:
    occupancy_user = ((thread.get("occupancy") or {}).get("user") or {})
    names = set()
    title = normalize_whitespace(thread.get("title")).lower()
    if title:
        names.add(title)
    display_name = normalize_whitespace(occupancy_user.get("displayName")).lower()
    if display_name:
        names.add(display_name)
    full_name = normalize_whitespace(
        f"{occupancy_user.get('firstName') or ''} {occupancy_user.get('lastName') or ''}"
    ).lower()
    if full_name:
        names.add(full_name)
    return names


def select_recent_tenant_messages(payload: Dict) -> List[str]:
    scraped_at = parse_iso_datetime(payload.get("scraped_at")) or datetime.now(LOCAL_TZ)
    cutoff = scraped_at - timedelta(days=2)
    candidates = []

    for thread in payload.get("messages") or []:
        room_number = ((thread.get("occupancy") or {}).get("room") or {}).get("roomNumber")
        occupant_names = get_thread_occupant_names(thread)
        if not occupant_names:
            continue

        for message in thread.get("recent_messages") or []:
            created = parse_iso_datetime(message.get("created"))
            if not created or created < cutoff:
                continue
            text = normalize_whitespace(message.get("text"))
            if not text or message.get("deleted"):
                continue
            sender = message.get("sender") or {}
            sender_name = normalize_whitespace(
                sender.get("displayName")
                or f"{sender.get('firstName') or ''} {sender.get('lastName') or ''}"
            ).lower()
            if sender_name not in occupant_names:
                continue
            candidates.append(
                (
                    created,
                    f"- {thread.get('title') or 'Unknown tenant'}"
                    f"{f' (Room {room_number})' if room_number is not None else ''}"
                    f" at {created.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %I:%M %p %Z')}: {text}",
                )
            )

    if not candidates:
        return ["- No recent tenant messages in the last 48 hours."]

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in candidates[:RECENT_MESSAGE_LIMIT]]


def format_thermostat_lines(payload: Optional[Dict]) -> List[str]:
    if not payload:
        return ["- Thermostat data unavailable."]

    lines: List[str] = []
    for location in (payload.get("locations") or [])[:THERMOSTAT_LIMIT]:
        devices = location.get("devices") or []
        if not devices:
            lines.append(f"- {location.get('name') or 'Unknown location'}: no devices found")
            continue
        device = devices[0]
        temp = device.get("temp")
        heat = device.get("heat_setpoint")
        cool = device.get("cool_setpoint")
        humidity = device.get("humidity")
        summary = (
            f"- {location.get('name') or 'Unknown location'}: "
            f"{temp if temp is not None else '?'}F"
            f", heat {heat if heat is not None else '?'}F"
            f", cool {cool if cool is not None else '?'}F"
        )
        if humidity is not None:
            summary += f", humidity {humidity}%"
        lines.append(summary)
    return lines or ["- Thermostat data unavailable."]


def render_block(padsplit_payload: Dict, thermostat_payload: Optional[Dict], generated_at: datetime) -> str:
    tasks = padsplit_payload.get("tasks") or {}
    req_count, open_count, complete_count = get_counts(tasks)
    lines = [
        START_MARKER,
        "## PadSplit Daily Digest",
        f"- Generated: {generated_at.strftime('%Y-%m-%d %I:%M %p %Z')}",
        f"- PadSplit scraped: {format_local_timestamp(padsplit_payload.get('scraped_at'))}",
        f"- Task counts: Requests {req_count} | Open {open_count} | Complete {complete_count}",
        "",
        "### Tasks",
        *format_task_groups(tasks),
        "",
        "### Recent Tenant Messages",
        *select_recent_tenant_messages(padsplit_payload),
        "",
        "### Thermostat",
    ]

    if thermostat_payload:
        lines.append(f"- Thermostat scraped: {format_local_timestamp(thermostat_payload.get('scraped_at'))}")
    lines.extend(format_thermostat_lines(thermostat_payload))
    lines.append(END_MARKER)
    return "\n".join(lines).rstrip() + "\n"


def upsert_managed_block(existing_text: str, block: str) -> str:
    start = existing_text.find(START_MARKER)
    end = existing_text.find(END_MARKER)
    if start != -1 and end != -1 and end > start:
        end += len(END_MARKER)
        prefix = existing_text[:start].rstrip()
        suffix = existing_text[end:].lstrip("\n")
        parts = [part for part in (prefix, block.rstrip(), suffix.rstrip()) if part]
        return "\n\n".join(parts).rstrip() + "\n"

    if not existing_text.strip():
        return block
    return existing_text.rstrip() + "\n\n" + block


def write_daily_digest(
    padsplit_payload: Dict,
    thermostat_payload: Optional[Dict],
    daily_notes_dir: Path,
    generated_at: Optional[datetime] = None,
) -> Path:
    generated_at = generated_at or datetime.now(LOCAL_TZ)
    note_path = get_daily_note_path(daily_notes_dir, generated_at)
    existing = note_path.read_text() if note_path.exists() else ""
    block = render_block(padsplit_payload, thermostat_payload, generated_at)
    note_path.write_text(upsert_managed_block(existing, block))
    return note_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write PadSplit and thermostat digest to today's Obsidian note.")
    args = parser.parse_args()
    del args

    load_environment()

    raw_dir = (os.getenv(ENV_VAR) or "").strip()
    if not raw_dir:
        # Changed the error message so it doesn't send you to the wrong folder!
        raise RuntimeError(f"Missing {ENV_VAR} in the root .env file")

    if not PADSPLIT_DATA_PATH.exists():
        raise RuntimeError(f"Missing PadSplit data file: {PADSPLIT_DATA_PATH}")

    padsplit_payload = load_json(PADSPLIT_DATA_PATH)
    thermostat_payload = load_json(THERMOSTAT_DATA_PATH) if THERMOSTAT_DATA_PATH.exists() else None
    note_path = write_daily_digest(
        padsplit_payload=padsplit_payload,
        thermostat_payload=thermostat_payload,
        daily_notes_dir=Path(raw_dir).expanduser(),
    )
    print(f"Wrote Obsidian digest to {note_path}")


if __name__ == "__main__":
    main()
