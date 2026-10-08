"""Outbound webhook for booking and listing changes.

Diffs the scrape that just finished against the last projection in
``logs/frontload_webhook_state.json``. The first enabled run only stores
that baseline and emits nothing.

Events
    new_booking   — booking id first seen, and not already cancelled
    cancel        — status moved to a cancel/reject, or the chat isCancelled
                    flipped true. Leaving the pending inbox is not a cancel.
    move_in       — occupancy next_move_in, chat move-in date, or room
                    detailed_status entering move-in
    listing_edit  — base price, scheduled new_price, promo percent or weeks,
                    or a room entering listed status

Payload keys (no others): event_type, house, room, booking_id, intro_rent,
target_rent, fee_status, move_in_date, observed_at, event_id.

fee_status is always null. Partner properties expose a host fee contract
(service fee, booking-fee days, editor name, lease URL), not a per-booking
fee status, and that object is not sent.

intro_rent is set only when the room has active_promo.price_drop_percentage.
It is base_price times (1 - percent/100). target_rent is base_price (the
listed weekly rate). There is no separate intro/target field on the room.

observed_at is timezone-aware ISO-8601 (offset, not a naive clock).

event_id is a stable sha256 prefix. new_booking and cancel key on booking id.
move_in keys on house, room, and move-in date. listing_edit keys on the
transition (previous price/promo/status to the new values) plus the observed
date, so a later return to an earlier price emits again, while a rerun of the
same diff on that date stays one id. Sent ids live in the same gitignored
state file so a rerun does not send twice. A dry-run log records the id the
same way a successful POST does, so turning dry-run off does not replay it.

Sends go through ``runtime.send_enabled("frontload")``: not CI, not
collection-only, ``PADSPLIT_ENABLE_ACTION_HOOKS`` on, and
``FRONTLOAD_WEBHOOK_ENABLE`` on. An explicit collection-only policy skips
before any ledger write. Recording the shared projection from that job would
drop the delta before a permitted sender runs, so the safer path is to skip.

A non-retryable 4xx is stored under ``failed`` with event_id, status code,
and timestamp only, then dropped from pending. 429/5xx stay pending. After
5 failed deliveries across scrapes they move to ``failed`` too.

Grok Bot routine webhooks expect header ``Authorization: Bearer <key>``.
The routine panel copies that full header line (POST URL, key, and header).
FRONTLOAD_WEBHOOK_KEY is the key. A value that already starts with
``Bearer `` is not double-prefixed. The header name is ``Authorization``.

Env
    FRONTLOAD_WEBHOOK_ENABLE   default off
    FRONTLOAD_WEBHOOK_DRY_RUN  default on (append JSON lines to
                               logs/frontload_webhook.jsonl, no POST)
    FRONTLOAD_WEBHOOK_URL
    FRONTLOAD_WEBHOOK_KEY

Mac launchd owns live POST. Morning (full scrape) and afternoon
(messages-only) share the Mac's logs/ ledger. GitHub Actions cron also
runs the scraper, but this module does not POST when CI or GITHUB_ACTIONS
is set: the Actions disk is wiped every job, so a gitignored ledger cannot
dedupe the 17/47 cron, and it would double-send against the Mac.

POST uses a (5s, 20s) timeout and 3 attempts. 429 and 5xx back off 0.5s
then 1.5s. Other 4xx are not retried. Retryable failures stay pending for
the next scrape. Nothing here raises into the scraper.

Afternoon messages-only runs do not refresh partner rooms or occupancy.json.
Those sections of the projection are left as they were, so a messages-only
run cannot wipe listing state or invent listing edits.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

import requests

try:
    from padsplit_scraper import runtime
    from padsplit_scraper.new_booking import (
        booking_id_from_status,
        collect_booking_hits,
        fetch_pending_booking_requests,
    )
    from padsplit_scraper.occupancy import _normalize_room, _normalize_street
except ModuleNotFoundError:  # python3 padsplit_scraper/scraper.py
    import runtime  # type: ignore
    from new_booking import (  # type: ignore
        booking_id_from_status,
        collect_booking_hits,
        fetch_pending_booking_requests,
    )
    from occupancy import _normalize_room, _normalize_street  # type: ignore


REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "logs"
STATE_PATH = LOG_DIR / "frontload_webhook_state.json"
DRY_RUN_LOG_PATH = LOG_DIR / "frontload_webhook.jsonl"

ENABLE_ENV = "FRONTLOAD_WEBHOOK_ENABLE"
DRY_RUN_ENV = "FRONTLOAD_WEBHOOK_DRY_RUN"
URL_ENV = "FRONTLOAD_WEBHOOK_URL"
KEY_ENV = "FRONTLOAD_WEBHOOK_KEY"

AUTH_HEADER = "Authorization"
PAYLOAD_KEYS = (
    "event_type",
    "house",
    "room",
    "booking_id",
    "intro_rent",
    "target_rent",
    "fee_status",
    "move_in_date",
    "observed_at",
    "event_id",
)

CANCEL_STATUSES = {
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "DENIED",
    "WITHDRAWN",
    "EXPIRED",
    "DECLINED",
    "HOST_REJECTED",
    "MEMBER_CANCELLED",
    "MEMBER_CANCELED",
}
MOVE_IN_MESSAGE_TYPES = {"MOVE_IN", "APPROVE_MOVE_IN_REQUEST"}

POST_TIMEOUT = (5, 20)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 1.5)
MAX_PENDING_ATTEMPTS = 5
SENT_ID_CAP = 5000

PostFn = Callable[..., Any]
SleepFn = Callable[[float], None]


def authorization_value(key: str) -> str:
    """Bearer token for a Grok Bot routine webhook. Do not double-prefix."""
    token = (key or "").strip()
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def webhook_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    return _flag(ENABLE_ENV, default=False, environ=environ)


def dry_run_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Default on. Explicit 0/false/no/off turns live POST on (when enabled)."""
    return _flag(DRY_RUN_ENV, default=True, environ=environ)


def _flag(name: str, *, default: bool, environ: Optional[Mapping[str, str]]) -> bool:
    env = os.environ if environ is None else environ
    parsed = runtime.flag_value(env.get(name))
    if parsed is None:
        return default
    return parsed


def _ci(environ: Optional[Mapping[str, str]]) -> bool:
    if environ is None:
        return runtime.running_in_ci()
    return bool((environ.get("GITHUB_ACTIONS") or "").strip() or (environ.get("CI") or "").strip())


def observed_at_now(now: Optional[datetime] = None) -> str:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def make_event_id(event_type: str, *parts: Any) -> str:
    material = "|".join("" if part is None else str(part) for part in (event_type, *parts))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _date_only(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def _num(value: Any) -> Optional[float]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 2)


def _house_from_thread(thread: Optional[Dict[str, Any]]) -> Optional[str]:
    """Street only. Chat title is often the member name and is never used."""
    if not isinstance(thread, dict):
        return None
    address = (thread.get("property") or {}).get("address") if isinstance(thread.get("property"), dict) else None
    if not isinstance(address, dict):
        return None
    street = str(address.get("street1") or "").strip()
    return street or None


def _room_from_thread(thread: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(thread, dict):
        return None
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    room = occupancy.get("room") if isinstance(occupancy.get("room"), dict) else {}
    number = room.get("roomNumber")
    if number in (None, ""):
        return None
    return str(number).strip()


def _move_in_from_thread(thread: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(thread, dict):
        return None
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    return _date_only(occupancy.get("moveInDate"))


def _is_cancel_status(status: Any) -> bool:
    norm = str(status or "").strip().upper().replace(" ", "_").replace("-", "_")
    if not norm:
        return False
    if norm in CANCEL_STATUSES:
        return True
    return norm.startswith("CANCEL") or norm.startswith("REJECT") or norm.startswith("DENIED") or norm.startswith("WITHDRAW")


def _blank_booking() -> Dict[str, Any]:
    return {
        "status": "",
        "house": None,
        "room": None,
        "move_in_date": None,
        "cancelled": False,
        "seen_at": "",
    }


def _consider_booking(
    bookings: Dict[str, Dict[str, Any]],
    booking_id: str,
    *,
    status: str,
    house: Optional[str],
    room: Optional[str],
    move_in_date: Optional[str],
    cancelled: bool,
    seen_at: str,
) -> None:
    row = bookings.get(booking_id) or _blank_booking()
    if seen_at >= str(row.get("seen_at") or ""):
        if status:
            row["status"] = status
        row["seen_at"] = seen_at
    if house and not row.get("house"):
        row["house"] = house
    if room and not row.get("room"):
        row["room"] = room
    if move_in_date and not row.get("move_in_date"):
        row["move_in_date"] = move_in_date
    if cancelled:
        row["cancelled"] = True
    bookings[booking_id] = row


def _iter_booking_statuses(thread: Dict[str, Any]) -> Iterable[tuple]:
    for message in thread.get("recent_messages") or []:
        if not isinstance(message, dict):
            continue
        booking = message.get("bookingStatus")
        if isinstance(booking, dict):
            yield str(message.get("created") or ""), booking
    last = thread.get("lastMessage") if isinstance(thread.get("lastMessage"), dict) else {}
    booking = last.get("bookingStatus")
    if isinstance(booking, dict):
        yield str(last.get("created") or ""), booking


def project_bookings(
    messages: Optional[Sequence[Dict[str, Any]]],
    pending_inbox: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Booking rows from messenger bookingStatus plus new_booking inbox hits."""
    bookings: Dict[str, Dict[str, Any]] = {}
    threads = [thread for thread in (messages or []) if isinstance(thread, dict)]
    for thread in threads:
        house = _house_from_thread(thread)
        room = _room_from_thread(thread)
        move_in = _move_in_from_thread(thread)
        cancelled = bool(thread.get("isCancelled"))
        for seen_at, booking in _iter_booking_statuses(thread):
            booking_id = booking_id_from_status(booking)
            if not booking_id:
                continue
            _consider_booking(
                bookings,
                booking_id,
                status=str(booking.get("status") or ""),
                house=house,
                room=room,
                move_in_date=move_in,
                cancelled=cancelled,
                seen_at=seen_at,
            )
    for hit in collect_booking_hits(pending_inbox, threads):
        booking_id = str(hit.get("booking_id") or "")
        if not booking_id:
            continue
        thread = hit.get("thread") if isinstance(hit.get("thread"), dict) else {}
        node = hit.get("pending_node") if isinstance(hit.get("pending_node"), dict) else {}
        node_room = node.get("room") if isinstance(node.get("room"), dict) else {}
        room_number = node_room.get("roomNumber")
        room = str(room_number).strip() if room_number not in (None, "") else _room_from_thread(thread)
        _consider_booking(
            bookings,
            booking_id,
            status="PENDING",
            house=_house_from_thread(thread),
            room=room,
            move_in_date=_move_in_from_thread(thread),
            cancelled=bool(thread.get("isCancelled")),
            seen_at=str(node.get("created") or ""),
        )
    return bookings


def _message_move_in_date(message: Dict[str, Any]) -> Optional[str]:
    extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
    for key in ("newMoveInDate", "moveInDate", "originalMoveInDate"):
        found = _date_only(extra.get(key))
        if found:
            return found
    nested = extra.get("changeMoveInDateRequest")
    if isinstance(nested, dict):
        found = _date_only(nested.get("moveInDate"))
        if found:
            return found
    return None


def project_message_move_ins(messages: Optional[Sequence[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for thread in messages or []:
        if not isinstance(thread, dict):
            continue
        house = _house_from_thread(thread)
        room = _room_from_thread(thread)
        if not house or not room:
            continue
        booking_id = None
        best_date = _move_in_from_thread(thread)
        best_seen = ""
        candidates: List[Dict[str, Any]] = []
        recent = thread.get("recent_messages") if isinstance(thread.get("recent_messages"), list) else []
        candidates.extend(message for message in recent if isinstance(message, dict))
        last = thread.get("lastMessage")
        if isinstance(last, dict):
            candidates.append(last)
        for message in candidates:
            booking = message.get("bookingStatus")
            if isinstance(booking, dict) and not booking_id:
                booking_id = booking_id_from_status(booking)
            mtype = str(message.get("messageType") or "")
            if mtype not in MOVE_IN_MESSAGE_TYPES:
                continue
            found = _message_move_in_date(message) or _move_in_from_thread(thread)
            seen = str(message.get("created") or "")
            if found and seen >= best_seen:
                best_date = found
                best_seen = seen
        if not best_date:
            continue
        key = f"{_normalize_street(house)}|{_normalize_room(room)}"
        rows[key] = {
            "house": house,
            "room": room,
            "move_in_date": best_date,
            "booking_id": booking_id,
        }
    return rows


def project_listings(rooms: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    listings: Dict[str, Dict[str, Any]] = {}
    for room in rooms or []:
        if not isinstance(room, dict):
            continue
        room_id = str(room.get("id") or "").strip()
        if not room_id:
            continue
        address = room.get("address") if isinstance(room.get("address"), dict) else {}
        house = str(address.get("full_street") or address.get("street1") or "").strip() or None
        number = room.get("room_number")
        promo = room.get("active_promo") if isinstance(room.get("active_promo"), dict) else {}
        listings[room_id] = {
            "house": house,
            "room": None if number in (None, "") else str(number).strip(),
            "base_price": _num(room.get("base_price")),
            "promo_pct": _num(promo.get("price_drop_percentage")) if promo else None,
            "promo_weeks": _num(promo.get("duration_in_weeks")) if promo else None,
            "new_price": _num(room.get("new_price")),
            "detailed_status": str(room.get("detailed_status") or "").strip().lower(),
            "move_in_date": _date_only(room.get("latest_occupancy_move_in_date")),
        }
    return listings


def project_occupancy(occupancy: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    rows = occupancy.get("rooms") if isinstance(occupancy, dict) else None
    projected: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        house = str(row.get("address") or "").strip() or None
        number = row.get("room_number")
        room = None if number in (None, "") else str(number).strip()
        if not house or not room:
            continue
        key = f"{_normalize_street(house)}|{_normalize_room(room)}"
        projected[key] = {
            "house": house,
            "room": room,
            "next_move_in": _date_only(row.get("next_move_in")),
        }
    return projected


def build_projection(
    messages: Optional[Sequence[Dict[str, Any]]],
    *,
    rooms: Optional[Sequence[Dict[str, Any]]] = None,
    occupancy: Optional[Dict[str, Any]] = None,
    pending_inbox: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Current scrape view. ``None`` rooms/occupancy means this run did not refresh them."""
    return {
        "bookings": project_bookings(messages, pending_inbox),
        "listings": None if rooms is None else project_listings(rooms),
        "occupancy": None if occupancy is None else project_occupancy(occupancy),
        "message_move_ins": project_message_move_ins(messages),
    }


def _empty_projection() -> Dict[str, Any]:
    return {"bookings": {}, "listings": {}, "occupancy": {}, "message_move_ins": {}}


def resolve_projection(previous: Optional[Dict[str, Any]], built: Dict[str, Any]) -> Dict[str, Any]:
    """Keep listing/occupancy sections a messages-only run did not refresh."""
    prev = previous if isinstance(previous, dict) else {}
    bookings = dict(prev.get("bookings") or {})
    bookings.update(built.get("bookings") or {})
    listings = built.get("listings")
    if listings is None:
        listings = dict(prev.get("listings") or {})
    occupancy = built.get("occupancy")
    if occupancy is None:
        occupancy = dict(prev.get("occupancy") or {})
    return {
        "bookings": bookings,
        "listings": listings,
        "occupancy": occupancy,
        "message_move_ins": dict(built.get("message_move_ins") or {}),
    }


def intro_rent_for(base_price: Any, promo_pct: Any) -> Optional[float]:
    base = _num(base_price)
    promo = _num(promo_pct)
    if base is None or promo is None:
        return None
    amount = round(float(base) * (1 - float(promo) / 100.0), 2)
    return int(amount) if float(amount).is_integer() else amount


def _listing_index(listings: Mapping[str, Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
    index: Dict[tuple, Dict[str, Any]] = {}
    for row in listings.values():
        if not isinstance(row, dict):
            continue
        house = _normalize_street(row.get("house") or "")
        room = _normalize_room(row.get("room"))
        if house and room:
            index[(house, room)] = row
    return index


def _rents(house: Optional[str], room: Optional[str], listings: Mapping[str, Dict[str, Any]]) -> tuple:
    row = _listing_index(listings).get((_normalize_street(house or ""), _normalize_room(room)))
    if not row:
        return None, None
    target = row.get("base_price")
    return intro_rent_for(target, row.get("promo_pct")), target


def _payload(
    *,
    event_type: str,
    house: Optional[str],
    room: Optional[str],
    booking_id: Optional[str],
    intro: Optional[float],
    target: Optional[float],
    move_in_date: Optional[str],
    observed_at: str,
    event_id: str,
) -> Dict[str, Any]:
    payload = {
        "event_type": event_type,
        "house": house or None,
        "room": None if room in (None, "") else str(room),
        "booking_id": booking_id or None,
        "intro_rent": intro,
        "target_rent": target,
        "fee_status": None,
        "move_in_date": move_in_date or None,
        "observed_at": observed_at,
        "event_id": event_id,
    }
    return {key: payload[key] for key in PAYLOAD_KEYS}


def _booking_payload(
    event_type: str,
    booking_id: str,
    row: Mapping[str, Any],
    listings: Mapping[str, Dict[str, Any]],
    observed_at: str,
) -> Dict[str, Any]:
    house = row.get("house")
    room = row.get("room")
    intro, target = _rents(house, room, listings)
    detail = booking_id if event_type == "new_booking" else f"cancel|{booking_id}"
    return _payload(
        event_type=event_type,
        house=house,
        room=room,
        booking_id=booking_id,
        intro=intro,
        target=target,
        move_in_date=row.get("move_in_date"),
        observed_at=observed_at,
        event_id=make_event_id(event_type, detail),
    )


def _move_in_payload(
    *,
    house: Optional[str],
    room: Optional[str],
    move_in_date: str,
    booking_id: Optional[str],
    listings: Mapping[str, Dict[str, Any]],
    observed_at: str,
) -> Dict[str, Any]:
    intro, target = _rents(house, room, listings)
    return _payload(
        event_type="move_in",
        house=house,
        room=room,
        booking_id=booking_id,
        intro=intro,
        target=target,
        move_in_date=move_in_date,
        observed_at=observed_at,
        event_id=make_event_id(
            "move_in",
            _normalize_street(house or ""),
            _normalize_room(room) or "",
            move_in_date,
        ),
    )


def diff_projection(
    previous: Optional[Mapping[str, Any]],
    current: Mapping[str, Any],
    *,
    observed_at: str,
    listings_refreshed: bool,
    occupancy_refreshed: bool,
) -> List[Dict[str, Any]]:
    """Pure diff. ``previous`` None is the baseline caller; this returns []."""
    if not previous:
        return []
    prev_bookings = previous.get("bookings") or {}
    cur_bookings = current.get("bookings") or {}
    listings = current.get("listings") or {}
    events: List[Dict[str, Any]] = []

    for booking_id, row in cur_bookings.items():
        old = prev_bookings.get(booking_id)
        cancelled_now = bool(row.get("cancelled")) or _is_cancel_status(row.get("status"))
        if old is None:
            kind = "cancel" if cancelled_now else "new_booking"
            events.append(_booking_payload(kind, booking_id, row, listings, observed_at))
            continue
        was_cancelled = bool(old.get("cancelled")) or _is_cancel_status(old.get("status"))
        if cancelled_now and not was_cancelled:
            events.append(_booking_payload("cancel", booking_id, row, listings, observed_at))

    events.extend(
        _diff_move_ins(
            previous.get("message_move_ins") or {},
            current.get("message_move_ins") or {},
            listings,
            observed_at,
            booking_ids=_booking_ids_by_room(cur_bookings),
        )
    )
    if occupancy_refreshed:
        events.extend(
            _diff_occupancy_move_ins(
                previous.get("occupancy") or {},
                current.get("occupancy") or {},
                listings,
                observed_at,
                booking_ids=_booking_ids_by_room(cur_bookings),
            )
        )
    if listings_refreshed:
        events.extend(
            _diff_listings(
                previous.get("listings") or {},
                listings,
                observed_at,
                booking_ids=_booking_ids_by_room(cur_bookings),
            )
        )
    return _dedupe(events)


def _booking_ids_by_room(bookings: Mapping[str, Mapping[str, Any]]) -> Dict[tuple, str]:
    found: Dict[tuple, str] = {}
    for booking_id, row in bookings.items():
        house = _normalize_street(row.get("house") or "")
        room = _normalize_room(row.get("room"))
        if house and room and (house, room) not in found:
            found[(house, room)] = booking_id
    return found


def _diff_move_ins(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    listings: Mapping[str, Dict[str, Any]],
    observed_at: str,
    *,
    booking_ids: Mapping[tuple, str],
) -> List[Dict[str, Any]]:
    events = []
    for key, row in current.items():
        date = row.get("move_in_date")
        if not date:
            continue
        old = previous.get(key) or {}
        if old.get("move_in_date") == date:
            continue
        house = row.get("house")
        room = row.get("room")
        booking_id = row.get("booking_id") or booking_ids.get((_normalize_street(house or ""), _normalize_room(room)))
        events.append(
            _move_in_payload(
                house=house,
                room=room,
                move_in_date=date,
                booking_id=booking_id,
                listings=listings,
                observed_at=observed_at,
            )
        )
    return events


def _diff_occupancy_move_ins(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    listings: Mapping[str, Dict[str, Any]],
    observed_at: str,
    *,
    booking_ids: Mapping[tuple, str],
) -> List[Dict[str, Any]]:
    shaped = {
        key: {
            "house": row.get("house"),
            "room": row.get("room"),
            "move_in_date": row.get("next_move_in"),
            "booking_id": None,
        }
        for key, row in current.items()
        if isinstance(row, dict)
    }
    previous_shaped = {
        key: {"move_in_date": (row or {}).get("next_move_in")}
        for key, row in previous.items()
        if isinstance(row, dict)
    }
    return _diff_move_ins(previous_shaped, shaped, listings, observed_at, booking_ids=booking_ids)


def _diff_listings(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    observed_at: str,
    *,
    booking_ids: Mapping[tuple, str],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for room_id, row in current.items():
        old = previous.get(room_id)
        if not isinstance(old, dict):
            continue
        price_changed = any(
            old.get(key) != row.get(key) for key in ("base_price", "promo_pct", "promo_weeks", "new_price")
        )
        became_listed = row.get("detailed_status") == "listed" and old.get("detailed_status") != "listed"
        became_move_in = row.get("detailed_status") == "move-in" and old.get("detailed_status") != "move-in"
        if became_move_in:
            booking_id = booking_ids.get((_normalize_street(row.get("house") or ""), _normalize_room(row.get("room"))))
            events.append(
                _move_in_payload(
                    house=row.get("house"),
                    room=row.get("room"),
                    move_in_date=row.get("move_in_date") or "",
                    booking_id=booking_id,
                    listings=current,
                    observed_at=observed_at,
                )
            )
        if not price_changed and not became_listed:
            continue
        intro = intro_rent_for(row.get("base_price"), row.get("promo_pct"))
        target = row.get("base_price")
        booking_id = booking_ids.get((_normalize_street(row.get("house") or ""), _normalize_room(row.get("room"))))
        events.append(
            _payload(
                event_type="listing_edit",
                house=row.get("house"),
                room=row.get("room"),
                booking_id=booking_id,
                intro=intro,
                target=target,
                move_in_date=row.get("move_in_date"),
                observed_at=observed_at,
                event_id=make_event_id(
                    "listing_edit",
                    room_id,
                    old.get("base_price"),
                    row.get("base_price"),
                    old.get("promo_pct"),
                    row.get("promo_pct"),
                    old.get("promo_weeks"),
                    row.get("promo_weeks"),
                    old.get("new_price"),
                    row.get("new_price"),
                    old.get("detailed_status"),
                    row.get("detailed_status"),
                    observed_at[:10],
                ),
            )
        )
    return events


def _dedupe(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    chosen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for event in events:
        event_id = event.get("event_id")
        if not event_id:
            continue
        if event_id not in chosen:
            chosen[event_id] = event
            order.append(event_id)
            continue
        if not chosen[event_id].get("booking_id") and event.get("booking_id"):
            chosen[event_id] = event
    return [chosen[event_id] for event_id in order]


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(payload, dict):
        return _empty_state()
    payload.setdefault("version", 1)
    payload.setdefault("seeded", False)
    payload.setdefault("projection", _empty_projection())
    payload.setdefault("sent_event_ids", [])
    payload.setdefault("pending", [])
    payload.setdefault("failed", [])
    return payload


def _empty_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "seeded": False,
        "projection": _empty_projection(),
        "sent_event_ids": [],
        "pending": [],
        "failed": [],
    }


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sent = list(state.get("sent_event_ids") or [])
    if len(sent) > SENT_ID_CAP:
        state["sent_event_ids"] = sent[-SENT_ID_CAP:]
    failed = list(state.get("failed") or [])
    if len(failed) > SENT_ID_CAP:
        state["failed"] = failed[-SENT_ID_CAP:]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def _append_dry_run(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def post_payload(
    url: str,
    key: str,
    payload: Dict[str, Any],
    *,
    post_fn: Optional[PostFn] = None,
    sleep_fn: Optional[SleepFn] = None,
) -> tuple:
    """Return ``(ok|retry|reject, status_code)``. Never raises."""
    poster = post_fn or _default_post
    sleeper = sleep_fn or time.sleep
    headers = {
        "Content-Type": "application/json",
        AUTH_HEADER: authorization_value(key),
    }
    last_status: Optional[int] = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            try:
                sleeper(BACKOFF_SECONDS[attempt - 1])
            except Exception:
                pass
        try:
            response = poster(url, json=payload, headers=headers, timeout=POST_TIMEOUT)
        except Exception as exc:
            last_status = None
            sys.stderr.write(
                f"# Frontload webhook POST failed {payload.get('event_id')} {exc.__class__.__name__}\n"
            )
            continue
        status = getattr(response, "status_code", None)
        last_status = status if isinstance(status, int) else None
        if isinstance(status, int) and 200 <= status < 300:
            return "ok", status
        if isinstance(status, int) and 400 <= status < 500 and status != 429:
            sys.stderr.write(f"# Frontload webhook POST rejected {payload.get('event_id')} HTTP {status}\n")
            return "reject", status
        sys.stderr.write(f"# Frontload webhook POST failed {payload.get('event_id')} HTTP {status}\n")
    return "retry", last_status


def _default_post(url: str, *, json: Dict[str, Any], headers: Dict[str, str], timeout: Any) -> Any:
    return requests.post(url, json=json, headers=headers, timeout=timeout)


def _mark_sent(state: Dict[str, Any], event_id: str) -> None:
    sent = state.setdefault("sent_event_ids", [])
    if event_id not in sent:
        sent.append(event_id)
    state["pending"] = [row for row in state.get("pending") or [] if row.get("event_id") != event_id]


def _queue_pending(state: Dict[str, Any], payload: Dict[str, Any], attempts: int) -> None:
    event_id = payload.get("event_id")
    pending = [row for row in state.get("pending") or [] if row.get("event_id") != event_id]
    pending.append({"event_id": event_id, "payload": payload, "attempts": attempts})
    state["pending"] = pending


def _mark_failed(
    state: Dict[str, Any],
    event_id: str,
    *,
    status_code: Optional[int],
    at: str,
) -> None:
    """Stop retrying. Store status and time only — never the payload."""
    state["pending"] = [row for row in state.get("pending") or [] if row.get("event_id") != event_id]
    failed = [row for row in state.get("failed") or [] if isinstance(row, dict) and row.get("event_id") != event_id]
    failed.append({"event_id": event_id, "status_code": status_code, "at": at})
    state["failed"] = failed


def emit_for_scraper(
    messages: Optional[Sequence[Dict[str, Any]]],
    *,
    rooms: Optional[Sequence[Dict[str, Any]]] = None,
    occupancy: Optional[Dict[str, Any]] = None,
    session: Any = None,
    creds: Optional[Mapping[str, str]] = None,
    now: Optional[datetime] = None,
    state_path: Optional[Path] = None,
    dry_run_log_path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    post_fn: Optional[PostFn] = None,
    sleep_fn: Optional[SleepFn] = None,
    fetch_pending_fn: Optional[Callable[..., Any]] = None,
    policy: Any = None,
) -> Dict[str, Any]:
    """Detect and emit. Returns a result dict. Does not raise."""
    try:
        return _emit(
            messages,
            rooms=rooms,
            occupancy=occupancy,
            session=session,
            creds=creds,
            now=now,
            state_path=state_path or STATE_PATH,
            dry_run_log_path=dry_run_log_path or DRY_RUN_LOG_PATH,
            environ=environ,
            post_fn=post_fn,
            sleep_fn=sleep_fn,
            fetch_pending_fn=fetch_pending_fn,
            policy=policy,
        )
    except Exception as exc:
        sys.stderr.write(f"# Frontload webhook failed; continuing scrape: {exc.__class__.__name__}\n")
        return {"action": "error", "error": exc.__class__.__name__, "events": []}


def _emit(
    messages: Optional[Sequence[Dict[str, Any]]],
    *,
    rooms: Optional[Sequence[Dict[str, Any]]],
    occupancy: Optional[Dict[str, Any]],
    session: Any,
    creds: Optional[Mapping[str, str]],
    now: Optional[datetime],
    state_path: Path,
    dry_run_log_path: Path,
    environ: Optional[Mapping[str, str]],
    post_fn: Optional[PostFn],
    sleep_fn: Optional[SleepFn],
    fetch_pending_fn: Optional[Callable[..., Any]],
    policy: Any,
) -> Dict[str, Any]:
    env = os.environ if environ is None else environ
    blocked = _send_block(env, policy)
    if blocked is not None:
        return blocked

    dry_run = dry_run_enabled(env)
    url = str(env.get(URL_ENV) or "").strip()
    key = str(env.get(KEY_ENV) or "").strip()
    pending_inbox = _fetch_pending(session, creds, fetch_pending_fn=fetch_pending_fn)
    built = build_projection(messages, rooms=rooms, occupancy=occupancy, pending_inbox=pending_inbox)
    state = _load_state(state_path)
    previous = state.get("projection") if state.get("seeded") else None
    current = resolve_projection(previous, built)
    observed = observed_at_now(now)

    if not state.get("seeded"):
        state["seeded"] = True
        state["projection"] = current
        _save_state(state_path, state)
        sys.stderr.write("# Frontload webhook baseline saved; no events emitted\n")
        return {"action": "baseline", "events": []}

    fresh = diff_projection(
        previous,
        current,
        observed_at=observed,
        listings_refreshed=built.get("listings") is not None,
        occupancy_refreshed=built.get("occupancy") is not None,
    )
    sent = set(state.get("sent_event_ids") or [])
    failed_ids = {
        row.get("event_id")
        for row in state.get("failed") or []
        if isinstance(row, dict) and row.get("event_id")
    }
    pending_payloads = []
    attempt_of: Dict[str, int] = {}
    for row in state.get("pending") or []:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        event_id = row.get("event_id")
        if not isinstance(payload, dict) or not event_id or event_id in sent or event_id in failed_ids:
            continue
        pending_payloads.append({key: payload.get(key) for key in PAYLOAD_KEYS})
        attempt_of[str(event_id)] = int(row.get("attempts") or 0)
    events = _dedupe([*pending_payloads, *fresh])
    events = [
        event
        for event in events
        if event.get("event_id") not in sent and event.get("event_id") not in failed_ids
    ]

    delivered: List[Dict[str, Any]] = []
    failed = 0
    state["projection"] = current
    for event in events:
        outcome, status = _deliver(
            event,
            dry_run=dry_run,
            url=url,
            key=key,
            dry_run_log_path=dry_run_log_path,
            post_fn=post_fn,
            sleep_fn=sleep_fn,
        )
        event_id = str(event.get("event_id") or "")
        if outcome == "ok":
            _mark_sent(state, event_id)
            delivered.append(event)
        elif outcome == "reject":
            _mark_failed(state, event_id, status_code=status, at=observed)
            failed += 1
        elif outcome == "retry":
            attempts = int(attempt_of.get(event_id, 0)) + 1
            if attempts >= MAX_PENDING_ATTEMPTS:
                _mark_failed(state, event_id, status_code=status, at=observed)
            else:
                _queue_pending(state, event, attempts)
                attempt_of[event_id] = attempts
            failed += 1
        _save_state(state_path, state)

    if not events:
        _save_state(state_path, state)
    return {"action": "dry_run" if dry_run else "sent", "events": delivered, "failed": failed}


def _fetch_pending(
    session: Any,
    creds: Optional[Mapping[str, str]],
    *,
    fetch_pending_fn: Optional[Callable[..., Any]],
) -> Optional[List[Dict[str, Any]]]:
    if session is None or not creds:
        return None
    fetcher = fetch_pending_fn or fetch_pending_booking_requests
    try:
        inbox = fetcher(session, creds)
    except Exception as exc:
        sys.stderr.write(
            f"# Frontload pending-inbox fetch failed; using messages only: {exc.__class__.__name__}\n"
        )
        return None
    return list(inbox) if isinstance(inbox, list) else None


def _send_block(env: Mapping[str, str], policy: Any) -> Optional[Dict[str, Any]]:
    """Return a skip result when this run must not send or touch the ledger."""
    if policy is not None and not bool(getattr(policy, "allow_hooks", False)):
        sys.stderr.write("# Frontload webhook skipped (collection-only policy; not sending)\n")
        return {"action": "collection_skip", "events": []}
    if _ci(env):
        sys.stderr.write("# Frontload webhook skipped (CI does not own sending; Mac launchd does)\n")
        return {"action": "ci_skip", "events": []}
    if runtime.collection_only(env):
        sys.stderr.write("# Frontload webhook skipped (collection-only; not sending)\n")
        return {"action": "collection_skip", "events": []}
    if not runtime.send_enabled("frontload", env):
        return {"action": "disabled", "events": []}
    return None


def _deliver(
    event: Dict[str, Any],
    *,
    dry_run: bool,
    url: str,
    key: str,
    dry_run_log_path: Path,
    post_fn: Optional[PostFn],
    sleep_fn: Optional[SleepFn],
) -> tuple:
    if dry_run:
        try:
            _append_dry_run(dry_run_log_path, event)
        except Exception as exc:
            sys.stderr.write(
                f"# Frontload webhook dry-run log failed {event.get('event_id')} {exc.__class__.__name__}\n"
            )
            return "retry", None
        sys.stderr.write(f"# Frontload webhook dry-run {event.get('event_type')} {event.get('event_id')}\n")
        return "ok", None
    if not url or not key:
        sys.stderr.write(f"# Frontload webhook missing URL or key; will retry {event.get('event_id')}\n")
        return "retry", None
    outcome, status = post_payload(url, key, event, post_fn=post_fn, sleep_fn=sleep_fn)
    if outcome == "ok":
        sys.stderr.write(f"# Frontload webhook sent {event.get('event_type')} {event.get('event_id')}\n")
    return outcome, status
