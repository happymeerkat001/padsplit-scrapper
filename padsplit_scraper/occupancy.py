"""Pure occupancy derivation from PadSplit messages and tasks."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    from padsplit_scraper.kpis import _parse_iso
except ModuleNotFoundError:  # Support execution through padsplit_scraper/scraper.py.
    from kpis import _parse_iso


CHICAGO = ZoneInfo("America/Chicago")
TURN_OPEN_STATUSES = {"submitted", "accepted", "in_progress"}
COMPLETED_STATUSES = {"completed", "complete"}
STREET_SUFFIXES = {"rd", "dr", "ave", "st", "street", "road", "drive", "avenue"}


def compute_occupancy(
    messages: List[Dict[str, Any]],
    tasks: Dict[str, List[Dict[str, Any]]],
    now: datetime,
) -> Dict[str, Any]:
    today = _chicago_date(now)
    rooms: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}

    for chat in messages or []:
        if not isinstance(chat, dict):
            continue
        _ingest_chat(rooms, chat, today)

    for ticket in _flatten_tasks(tasks):
        _ingest_ticket(rooms, ticket, today)

    rows = [_finalize_row(state, today) for state in rooms.values()]
    rows.sort(key=lambda row: (str(row.get("address") or ""), _room_sort_key(row.get("room_number"))))
    return {
        "scraped_at": _scraped_at(now),
        "derived_from": ["messages", "tasks"],
        "rooms": rows,
    }


def operator_lists(rooms: List[Dict[str, Any]], today: date) -> Dict[str, List[Dict[str, Any]]]:
    """Split occupancy rows into operator lists. Incoming is future next_move_in only."""
    incoming: List[Dict[str, Any]] = []
    rent_ready: List[Dict[str, Any]] = []
    occupied_after_move_out: List[Dict[str, Any]] = []
    vacant_not_ready: List[Dict[str, Any]] = []
    for room in rooms or []:
        if not isinstance(room, dict):
            continue
        next_move_in = _date_only(room.get("next_move_in"))
        listed = _date_only(room.get("listed_move_out"))
        if next_move_in is not None and next_move_in >= today:
            incoming.append(room)
        if room.get("rent_ready") is True:
            rent_ready.append(room)
        if room.get("occupant_present") is True and listed is not None and listed < today:
            occupied_after_move_out.append(room)
        if room.get("vacant") is True and room.get("rent_ready") is not True:
            vacant_not_ready.append(room)
    incoming.sort(key=lambda row: (str(row.get("next_move_in") or ""), str(row.get("address") or ""), _room_sort_key(row.get("room_number"))))
    rent_ready.sort(key=lambda row: (str(row.get("address") or ""), _room_sort_key(row.get("room_number"))))
    occupied_after_move_out.sort(key=lambda row: (str(row.get("listed_move_out") or ""), str(row.get("address") or ""), _room_sort_key(row.get("room_number"))))
    vacant_not_ready.sort(key=lambda row: (-int(row.get("days_vacant") or 0), str(row.get("address") or ""), _room_sort_key(row.get("room_number"))))
    return {
        "incoming": incoming,
        "rent_ready": rent_ready,
        "occupied_after_move_out": occupied_after_move_out,
        "vacant_not_ready": vacant_not_ready,
    }


def _flatten_tasks(tasks_by_bucket: Optional[Dict[str, List[Dict[str, Any]]]]) -> List[Dict[str, Any]]:
    tickets: List[Dict[str, Any]] = []
    if not isinstance(tasks_by_bucket, dict):
        return tickets
    for ticket_list in tasks_by_bucket.values():
        if not isinstance(ticket_list, list):
            continue
        for ticket in ticket_list:
            if isinstance(ticket, dict):
                tickets.append(ticket)
    return tickets


def _ingest_chat(rooms: Dict[Tuple[str, Optional[str]], Dict[str, Any]], chat: Dict[str, Any], today: date) -> None:
    street = _street_from_chat(chat)
    occupancy = chat.get("occupancy")
    occupancy_obj = occupancy if isinstance(occupancy, dict) else {}
    room_number = _room_from_obj(occupancy_obj.get("room") if isinstance(occupancy_obj.get("room"), dict) else {})
    if occupancy is None:
        room_number = room_number or _room_from_obj(chat)
    key = _room_key(street, room_number)
    state = _room_state(rooms, key, street, room_number)
    if occupancy is None:
        state["unsure"] = True
    listed = _date_only(occupancy_obj.get("moveOutDate"))
    if listed is not None:
        state["listed_dates"].append(listed)
    move_in = _date_only(occupancy_obj.get("moveInDate"))
    if move_in is not None and move_in > today:
        state["future_move_ins"].append(move_in)
    if _user_present(occupancy_obj.get("user")):
        state["present_signals"].append("user")
    if listed is not None and _last_message_after(chat.get("lastMessage"), listed):
        state["present_signals"].append("chat")
    state["photo_count"] = max(state["photo_count"], _chat_photo_count(chat))
    if listed is None:
        state["unsure"] = True


def _ingest_ticket(rooms: Dict[Tuple[str, Optional[str]], Dict[str, Any]], ticket: Dict[str, Any], today: date) -> None:
    street = _street_from_ticket(ticket)
    room_number = _normalize_room(ticket.get("room_number"))
    key = _room_key(street, room_number)
    state = _room_state(rooms, key, street, room_number)
    property_id = ticket.get("property_id")
    if property_id not in (None, ""):
        state["property_id"] = property_id
    extra = ticket.get("extra_data") if isinstance(ticket.get("extra_data"), dict) else {}
    listed = _date_only(extra.get("move_out_date"))
    if listed is not None:
        state["listed_dates"].append(listed)
    if extra.get("is_present_after_move_out") is True:
        state["present_signals"].append("present_after")
    state["photo_count"] = max(state["photo_count"], _ticket_photo_count(ticket))
    status = str(ticket.get("status") or "").lower()
    ticket_id = ticket.get("id")
    if status == "eviction":
        state["open_eviction"].append(ticket_id)
    elif status == "on_hold":
        state["open_hold"].append(ticket_id)
    elif status in TURN_OPEN_STATUSES and _is_turn_category(ticket.get("category")):
        state["open_turn"].append(ticket_id)
    if status in COMPLETED_STATUSES and _is_turn_category(ticket.get("category")):
        state["completed_turn"] = True
    if listed is None and not state["listed_dates"]:
        state["unsure"] = True


def _finalize_row(state: Dict[str, Any], today: date) -> Dict[str, Any]:
    listed = max(state["listed_dates"]) if state["listed_dates"] else None
    next_move_in = min(state["future_move_ins"]) if state["future_move_ins"] else None
    has_signal = bool(state["present_signals"])
    unsure = bool(state["unsure"]) and not has_signal
    if listed is None and not has_signal:
        unsure = True
    occupant_present = has_signal or unsure
    vacant = not occupant_present
    turned = bool(state["completed_turn"]) and state["photo_count"] > 0 and not state["open_eviction"] and not state["open_hold"]
    rent_ready = vacant and turned
    if occupant_present or listed is None:
        days_vacant = 0
    else:
        days_vacant = max((today - listed).days, 0)
    seo_eligible = rent_ready and days_vacant > 14
    return {
        "property_id": state.get("property_id"),
        "address": state.get("address") or "",
        "room_number": _coerce_room_number(state.get("room_number")),
        "occupant_present": occupant_present,
        "listed_move_out": listed.isoformat() if listed else None,
        "next_move_in": next_move_in.isoformat() if next_move_in else None,
        "open_turn_ticket_ids": _unique_ids(state["open_turn"]),
        "open_eviction_ticket_ids": _unique_ids(state["open_eviction"]),
        "open_hold_ticket_ids": _unique_ids(state["open_hold"]),
        "move_out_photos": int(state["photo_count"]),
        "vacant": vacant,
        "turned": turned,
        "rent_ready": rent_ready,
        "days_vacant": days_vacant,
        "seo_eligible": seo_eligible,
    }


def _room_state(
    rooms: Dict[Tuple[str, Optional[str]], Dict[str, Any]],
    key: Tuple[str, Optional[str]],
    street: str,
    room_number: Optional[str],
) -> Dict[str, Any]:
    if key not in rooms:
        rooms[key] = {
            "address": street,
            "room_number": room_number,
            "property_id": None,
            "listed_dates": [],
            "future_move_ins": [],
            "present_signals": [],
            "unsure": False,
            "photo_count": 0,
            "open_turn": [],
            "open_eviction": [],
            "open_hold": [],
            "completed_turn": False,
        }
    state = rooms[key]
    if street and not state.get("address"):
        state["address"] = street
    if room_number is not None and state.get("room_number") is None:
        state["room_number"] = room_number
    return state


def _room_key(street: str, room_number: Optional[str]) -> Tuple[str, Optional[str]]:
    return (_normalize_street(street), room_number)


def _normalize_street(value: Any) -> str:
    parts = str(value or "").strip().lower().split()
    if parts and parts[-1].rstrip(".") in STREET_SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


def _normalize_room(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value).strip()


def _coerce_room_number(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text


def _street_from_chat(chat: Dict[str, Any]) -> str:
    prop = chat.get("property") if isinstance(chat.get("property"), dict) else {}
    address = prop.get("address") if isinstance(prop.get("address"), dict) else {}
    return str(address.get("street1") or "").strip()


def _street_from_ticket(ticket: Dict[str, Any]) -> str:
    address = ticket.get("property_address")
    if isinstance(address, dict):
        return str(address.get("street1") or address.get("full_street") or "").strip()
    if address:
        return str(address).strip()
    return ""


def _room_from_obj(room: Dict[str, Any]) -> Optional[str]:
    return _normalize_room(room.get("roomNumber") or room.get("room_number"))


def _user_present(user: Any) -> bool:
    if not isinstance(user, dict) or not user:
        return False
    return True


def _last_message_after(last_message: Any, listed: date) -> bool:
    if not isinstance(last_message, dict):
        return False
    created = _message_date(last_message.get("created"))
    return created is not None and created > listed


def _message_date(value: Any) -> Optional[date]:
    parsed = _parse_iso(value) if isinstance(value, str) else None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(CHICAGO).date()
    return _date_only(value)


def _date_only(value: Any) -> Optional[date]:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        parsed = _parse_iso(text)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(CHICAGO).date()


def _chicago_date(now: datetime) -> date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(CHICAGO).date()


def _scraped_at(now: datetime) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_turn_category(category: Any) -> bool:
    return str(category or "").lower().replace("_", "-") == "room-turn"


def _ticket_photo_count(ticket: Dict[str, Any]) -> int:
    count = 0
    raw = ticket.get("moveout_photos_count")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        count = max(count, int(raw))
    media = ticket.get("media")
    if isinstance(media, list):
        count = max(count, len(media))
    return count


def _chat_photo_count(chat: Dict[str, Any]) -> int:
    count = 0
    for message in _iter_chat_messages(chat):
        if str(message.get("messageType") or "") != "MOVE_OUT_PHOTOS":
            continue
        attachments = message.get("attachments")
        if isinstance(attachments, list):
            count += len(attachments)
    return count


def _iter_chat_messages(chat: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    last_message = chat.get("lastMessage")
    if isinstance(last_message, dict):
        yield last_message
    recent = chat.get("recent_messages")
    if isinstance(recent, list):
        for message in recent:
            if isinstance(message, dict):
                yield message


def _unique_ids(values: List[Any]) -> List[Any]:
    seen = set()
    result: List[Any] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _room_sort_key(value: Any) -> Tuple[int, str]:
    if value is None:
        return (1, "")
    text = str(value)
    if text.isdigit():
        return (0, f"{int(text):06d}")
    return (0, text)
