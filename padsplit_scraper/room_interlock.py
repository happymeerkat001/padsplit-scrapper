"""Occupied-room safety interlock.

A lock-code change, vacancy reset, lockout rotation, or room clear is
allowed only when PadSplit live members show the room is empty, the
occupant is terminated, or move-out is confirmed. Unknown, failed, or
stale lookups refuse. Snapshots older than 30 minutes cannot allow.

Discord text uses room-number words and never digits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

try:
    from padsplit_scraper import partner_members
    from padsplit_scraper.sifely_client import HOUSE_LOCK_PROFILES
except ModuleNotFoundError:  # python padsplit_scraper/room_interlock.py
    import partner_members  # type: ignore
    from sifely_client import HOUSE_LOCK_PROFILES  # type: ignore


STALE_AFTER = timedelta(minutes=30)

_ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)
_DIGIT_RUN = re.compile(r"\d")
_MOVE_OUT_EVENT = "MOVE_OUT_CONFIRMED"


@dataclass(frozen=True)
class Allowed:
    reason: str


@dataclass(frozen=True)
class Refused:
    reason: str


def room_words(room: Any) -> str:
    """Spell a room number so Discord text can stay digit-free."""
    digits = re.sub(r"\D", "", str(room or ""))
    if not digits:
        return "unknown"
    number = int(digits)
    if number < 0:
        return "unknown"
    if number < len(_ONES):
        return _ONES[number]
    if number < 100:
        tens, ones = divmod(number, 10)
        if ones == 0:
            return _TENS[tens]
        return f"{_TENS[tens]} {_ONES[ones]}"
    return "unknown"


def digit_free_label(label: str) -> str:
    cleaned = re.sub(r"\d+", " ", label or "")
    cleaned = " ".join(cleaned.split())
    return cleaned or "that house"


def house_label(slug: str) -> str:
    profile = HOUSE_LOCK_PROFILES.get(slug) or {}
    return digit_free_label(str(profile.get("label") or "that house"))


def refusal_discord(action: str, house: str, room: str = "") -> str:
    """Digit-free automations flag. Raises if a digit would be posted."""
    label = digit_free_label(house_label(house) if house in HOUSE_LOCK_PROFILES else house)
    action_text = " ".join(re.sub(r"[^A-Za-z]+", " ", action or "change").split()).lower() or "change"
    room_bit = f" room {room_words(room)}" if str(room or "").strip() else ""
    text = f"Blocked {action_text} at {label}{room_bit}: PadSplit still shows an occupant"
    if _DIGIT_RUN.search(text):
        raise RuntimeError("Refusing Discord outbound: message contains digits")
    return text


def _as_date(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _move_out_confirmed(row: Dict[str, Any]) -> bool:
    status = str(row.get("occupancy_status") or row.get("status") or "")
    if _MOVE_OUT_EVENT in status.upper().replace(" ", "_").replace("-", "_"):
        return True
    events = row.get("events") or row.get("event_types") or []
    if isinstance(events, str):
        events = [events]
    if not isinstance(events, list):
        return False
    for event in events:
        if isinstance(event, dict):
            token = str(event.get("type") or event.get("event") or event.get("name") or "")
        else:
            token = str(event or "")
        if token.upper().replace(" ", "_").replace("-", "_") == _MOVE_OUT_EVENT:
            return True
    return False


def _present_after_move_out(row: Dict[str, Any]) -> bool:
    if row.get("present_after_move_out") is True:
        return True
    if row.get("present") is True:
        return True
    return False


def _move_out_passed(row: Dict[str, Any], now: datetime) -> bool:
    parsed = _as_date(row.get("move_out_date"))
    if parsed is None:
        return False
    today = now.astimezone(timezone.utc).date()
    return parsed.date() < today


def _cleared(row: Dict[str, Any], now: datetime) -> bool:
    if _move_out_confirmed(row) and not _present_after_move_out(row):
        return True
    if _move_out_passed(row, now) and not _present_after_move_out(row):
        return True
    return False


def _blocks(row: Dict[str, Any], now: datetime) -> bool:
    if not isinstance(row, dict):
        return True
    if row.get("is_terminated") is True:
        return False
    if _cleared(row, now):
        return False
    return True


def _is_live_occupant(row: Dict[str, Any], now: datetime) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("is_terminated") is not False:
        return False
    if not partner_members.move_out_is_open(row.get("move_out_date"), now):
        return False
    move_in = _as_date(row.get("move_in_date"))
    if move_in is not None and move_in.date() > now.astimezone(timezone.utc).date():
        return False
    return True


def _snapshot_stale(directory: Any, now: datetime) -> bool:
    as_of = getattr(directory, "as_of", None)
    if as_of is None:
        return False
    parsed = as_of if isinstance(as_of, datetime) else _as_date(as_of)
    if parsed is None:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (current - parsed) > STALE_AFTER


def _property_id(directory: Any, house: str, property_id: str) -> str:
    explicit = str(property_id or "").strip()
    if explicit:
        return explicit
    index = getattr(directory, "property_index", None)
    if not callable(index):
        return ""
    try:
        refs = list(index() or [])
    except Exception:
        return ""
    profile = HOUSE_LOCK_PROFILES.get(house) or {}
    aliases = tuple(profile.get("aliases") or ())
    hits = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        street = str(ref.get("street") or "").lower()
        if aliases and any(alias in street for alias in aliases):
            ident = str(ref.get("id") or "").strip()
            if ident:
                hits.append(ident)
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    return ""


def _room_rows(rows: Sequence[Dict[str, Any]], room: str) -> list[Dict[str, Any]]:
    return [
        row
        for row in rows
        if isinstance(row, dict) and partner_members.rooms_match(row.get("room_number"), room)
    ]


def assert_room_action_allowed(
    house: str,
    room: str,
    action: str,
    *,
    directory: Any,
    now: datetime,
    property_id: str = "",
    new_occupancy_id: str = "",
) -> Allowed | Refused:
    """Fail closed. directory=None, errors, and stale snapshots refuse."""
    if directory is None:
        return Refused("no live member lookup")
    if _snapshot_stale(directory, now):
        return Refused("stale snapshot")
    prop = _property_id(directory, house, property_id)
    if not prop:
        return Refused("property unknown")
    try:
        rows = list(directory.members_for(prop) or [])
    except Exception:
        return Refused("member lookup failed")
    matched = _room_rows(rows, room)
    if action == "move_in":
        live = [row for row in matched if _is_live_occupant(row, now)]
        if len(live) != 1:
            return Refused("move-in occupant not confirmed")
        wanted = str(new_occupancy_id or "").strip()
        if not wanted or not partner_members.occupancy_ids_match(live[0].get("occupancy_id"), wanted):
            return Refused("move-in occupant not confirmed")
        return Allowed("new occupant is the live occupant")
    blockers = [row for row in matched if _blocks(row, now)]
    if blockers:
        return Refused("occupant")
    return Allowed("room clear")
