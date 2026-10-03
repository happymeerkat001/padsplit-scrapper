"""House clock resolution. Reads Honeywell schedules.json as data only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
SCHEDULES_PATH = ROOT_DIR / "thermostat" / "config" / "schedules.json"
CLOCKS_PATH = Path(__file__).resolve().parent / "config" / "clocks.json"

# (match tokens, schedules.json key or None, local clocks key or None)
ALLOW_LIST: Tuple[Tuple[Tuple[str, ...], Optional[str], Optional[str]], ...] = (
    (("pioneer",), "1404 pioneer", None),
    (("green", "hill"), "3406 green hill", None),
    (("leanna",), "6623 leanna", None),
    (("broken", "crest"), None, "1025 broken crest"),
)


@dataclass(frozen=True)
class Slot:
    hour: int
    minute: int
    cool: int


class AmbiguousHouseError(ValueError):
    """SmartHome name matched more than one allow-listed house."""


def tokenize(value: str) -> List[str]:
    cleaned = []
    for char in value.lower():
        cleaned.append(char if char.isalnum() else " ")
    return "".join(cleaned).split()


def find_active_slot(slots: Sequence[Slot], now: datetime) -> Slot:
    if not slots:
        raise RuntimeError("Cannot find active slot for an empty schedule")
    sorted_slots = sorted(slots, key=lambda slot: (slot.hour, slot.minute))
    current = (now.hour, now.minute)
    active = [slot for slot in sorted_slots if (slot.hour, slot.minute) <= current]
    return active[-1] if active else sorted_slots[-1]


def slot_key(house: str, slot: Slot) -> str:
    return f"{house}:{slot.hour:02d}:{slot.minute:02d}"


def match_houses(name: str) -> List[str]:
    tokens = set(tokenize(name))
    houses: List[str] = []
    for match_tokens, schedule_key, local_key in ALLOW_LIST:
        if all(token in tokens for token in match_tokens):
            houses.append(schedule_key or local_key or "")
    return [house for house in houses if house]


def resolve_house(name: str) -> Optional[str]:
    houses = match_houses(name)
    if not houses:
        return None
    if len(houses) > 1:
        raise AmbiguousHouseError(f"{name!r} matches multiple houses: {houses}")
    return houses[0]


def _load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _slots_from_payload(rows: Sequence[Dict]) -> List[Slot]:
    return [
        Slot(hour=int(row["hour"]), minute=int(row["minute"]), cool=int(row["cool"]))
        for row in rows
    ]


def load_slots(house: str, *, schedules_path: Path = SCHEDULES_PATH, clocks_path: Path = CLOCKS_PATH) -> Optional[List[Slot]]:
    for match_tokens, schedule_key, local_key in ALLOW_LIST:
        key = schedule_key or local_key
        if key != house:
            continue
        if schedule_key:
            data = _load_json(schedules_path)
            rows = data.get(schedule_key)
            if not rows:
                return None
            return _slots_from_payload(rows)
        data = _load_json(clocks_path)
        entry = data.get(local_key) or {}
        if not entry.get("enabled"):
            return None
        rows = entry.get("slots") or []
        if not rows:
            return None
        return _slots_from_payload(rows)
    return None


def active_cool(
    name: str,
    now: Optional[datetime] = None,
    *,
    schedules_path: Path = SCHEDULES_PATH,
    clocks_path: Path = CLOCKS_PATH,
) -> Optional[Tuple[str, Slot]]:
    house = resolve_house(name)
    if house is None:
        return None
    slots = load_slots(house, schedules_path=schedules_path, clocks_path=clocks_path)
    if not slots:
        return None
    when = now or datetime.now()
    return house, find_active_slot(slots, when)
