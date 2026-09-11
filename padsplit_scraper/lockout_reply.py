#!/usr/bin/env python3
"""PadSplit host lockout auto-reply (Nest/Ang lock 9/7).

Sequential SEND on the PadSplit member thread when house + room are 100%
known: door code(s) first; lockbox / room code only after the member later
says the door still failed. Member got-it / I’m in / code-worked after the
door stage stops the ladder (no lockbox SEND, no Discord escalate).

Spanish Moss back door uses the existing Sifely path (live list / rotate /
#new-tenants inbound share). Never use Firestore/Tinghui static back_door
for Spanish Moss.

Every other house uses Firestore property_codes/{slug} at send time.

Safety: never write lock codes or PIN digits to git, logs, Discord outbound,
PR text, or README examples. Tests use labeled fake placeholders only.

LOCKOUT_REPLY_ENABLE default off. GitHub Actions / CI must not send.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv

try:
    from padsplit_scraper import lock_codes
    from padsplit_scraper import new_booking
    from padsplit_scraper import runtime
    from padsplit_scraper.scraper import (
        create_session,
        load_credentials,
        login,
    )
except ModuleNotFoundError:  # python3 padsplit_scraper/lockout_reply.py
    import lock_codes  # type: ignore
    import new_booking  # type: ignore
    import runtime  # type: ignore
    from scraper import create_session, load_credentials, login  # type: ignore


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
STATE_PATH = ROOT_DIR / "logs" / "lockout_reply_state.json"
CODES_COLLECTION = "property_codes"

# Digit-free Discord labels only. Street numbers stay out of Discord.
HOUSE_PROFILES: Dict[str, Dict[str, Any]] = {
    "leana_6623": {
        "label": "Leana",
        "aliases": ("leana", "leanna"),
        "require_front": True,
        "require_back": False,
        "sifely_back": False,
        "front_keys": ("front_door",),
        "back_keys": (),
        "expect_room_code": True,
    },
    "sylvia_2516": {
        "label": "Sylvia",
        "aliases": ("sylvia",),
        "require_front": True,
        "require_back": False,
        "sifely_back": False,
        "front_keys": ("front_door",),
        "back_keys": (),
        "expect_room_code": True,
    },
    "ridge_oak_10235": {
        "label": "Ridge Oak",
        "aliases": ("ridge oak", "ridgeoak"),
        "require_front": True,
        "require_back": True,
        "sifely_back": False,
        "front_keys": ("front_door",),
        "back_keys": ("back_door",),
        "expect_room_code": True,
    },
    "pebbleshores_3414": {
        "label": "Pebbleshores",
        "aliases": ("pebbleshores", "pebble shores", "pebble shore"),
        "require_front": True,
        "require_back": True,
        "sifely_back": False,
        "front_keys": ("front_back", "front_door"),
        "back_keys": ("front_back", "back_door"),
        "expect_room_code": True,
    },
    "greenhill_3406": {
        "label": "Greenhill",
        "aliases": ("greenhill", "green hill"),
        "require_front": True,
        "require_back": False,
        "sifely_back": False,
        "front_keys": ("front_door",),
        "back_keys": (),
        "expect_room_code": True,
    },
    "parker_4351": {
        "label": "Parker",
        "aliases": ("parker",),
        "require_front": True,
        "require_back": True,
        "sifely_back": False,
        "front_keys": ("front_door",),
        "back_keys": ("back_door",),
        "expect_room_code": True,
    },
    "pioneer_1404": {
        "label": "Pioneer",
        "aliases": ("pioneer",),
        "require_front": True,
        "require_back": True,
        "sifely_back": False,
        "front_keys": ("front_door",),
        "back_keys": ("back_door",),
        "expect_room_code": True,
    },
    "burton_5509": {
        "label": "Burton",
        "aliases": ("burton",),
        "require_front": True,
        "require_back": False,
        "sifely_back": False,
        "front_keys": ("front_door",),
        "back_keys": (),
        "expect_room_code": True,
    },
    "broken_crest_1025": {
        "label": "Broken Crest",
        "aliases": ("broken crest", "brokencrest"),
        "require_front": True,
        "require_back": False,
        "sifely_back": False,
        "front_keys": ("front_door",),
        "back_keys": (),
        "expect_room_code": True,
    },
    "spanish_moss": {
        "label": "Spanish Moss",
        "aliases": ("spanish moss", "spanishmoss"),
        "require_front": False,
        "require_back": True,
        "sifely_back": True,
        "front_keys": ("front_door",),
        "back_keys": (),
        "expect_room_code": False,
    },
}

HOST_ROLE_ID = "A_1"
TENANT_ROLE_ID = "A_0"
LOOKBACK = timedelta(hours=36)
IDEMPOTENCY_WINDOW = timedelta(hours=24)
STAGE_DOOR = "door"
STAGE_LOCKBOX = "lockbox"
LOCKOUT_PACK_MARKER = "Sorry you’re locked out — here’s entry for"
LOCKBOX_PACK_MARKER = "Sorry you’re locked out — here’s the room/lockbox for"
LOCKBOX_LINE_MARKER = "Room / lockbox:"
# Member-thread fail ladder only. Never put these on Discord.
JOE_FIELD_PHONE = "+1 (469) 373-2048"
PADSPLIT_MEMBER_SUPPORT_PHONE = "+1 (770) 373-7863"

# Member lockout phrases. Keep these about entry, not general maintenance.
_LOCKOUT_RE = re.compile(
    r"(?i)("
    r"\block(?:ed)?\s*out\b"
    r"|\blockout\b"
    r"|\bcan(?:['’]?t|not)\s+get\s+in\b"
    r"|\bcan(?:['’]?t|not)\s+get\s+into\b"
    r"|\bcan(?:['’]?t|not)\s+get\s+inside\b"
    r"|\bdoor\s+code\s+(?:fail|fails|failed|not\s+work|won['’]?t\s+work)\b"
    r"|\b(?:code|keypad)\s+(?:doesn['’]?t|didn['’]?t|does\s+not|won['’]?t|will\s+not|not)\s+"
    r"(?:work|open|let\s+me\s+in)\b"
    r"|\bemergency\s+entry\b"
    r"|\bstuck\s+outside\b"
    r"|\blocked\s+outside\b"
    r"|\bcan(?:['’]?t|not)\s+get\s+the\s+door\b"
    r")",
)

# Stage-2 only: member says the door still failed after we already sent door codes.
_DOOR_FAIL_FOLLOWUP_RE = re.compile(
    r"(?i)("
    r"\bstill\s+lock(?:ed)?\s*out\b"
    r"|\bstill\s+can(?:['’]?t|not)\s+get\s+(?:in|into|inside)\b"
    r"|\bdoor\s+still\s+(?:fail|fails|failed|won['’]?t)"
    r"|\b(?:code|keypad|door(?:\s+code)?)\s+(?:still\s+)?"
    r"(?:didn['’]?t|doesn['’]?t|does\s+not|won['’]?t|will\s+not|not)\s+"
    r"(?:work|open|let\s+me\s+in)\b"
    r"|\b(?:code|keypad|door(?:\s+code)?)\s+(?:still\s+)?(?:fail|fails|failed)\b"
    r"|\bdidn['’]?t\s+work\b"
    r"|\bstill\s+(?:doesn['’]?t|didn['’]?t|won['’]?t)\s+work\b"
    r"|\bstill\s+locked\b"
    r"|\bstill\s+outside\b"
    r"|\bstill\s+stuck\b"
    r")",
)

# After a door-stage send only. Do not treat these as a lockout on a quiet thread.
_ENTRY_SUCCESS_RE = re.compile(
    r"(?i)("
    r"\bgot[-\s]+it\b"
    r"|\bgot[-\s]+in(?:side)?\b"
    r"|\bgotcha\b"
    r"|\bmade\s+it\s+in(?:side)?\b"
    r"|\bi(?:['’]?m| am)\s+inside\b"
    r"|\bi(?:['’]?m| am)\s+in\b(?!\s*(?:terest|to\b|trouble|need\b))"
    r"|\bwe(?:['’]?re| are)\s+in(?:side)?\b(?!\s*(?:terest|to\b|trouble|need\b))"
    r"|\b(?:code|keypad|door(?:\s+code)?)\s+worked\b"
    r"|\b(?:it|that)\s+worked\b"
    r"|\bdoor\s+opened\b"
    r"|\b(?<!not\s)(?:all|we(?:['’]?re| are)|i(?:['’]?m| am))\s+good\b"
    r")",
)

_ROOM_MENTION_RE = re.compile(r"(?i)\b(?:room|rm|r)\s*[:#-]?\s*(\d{1,2})\b")
_FRONT_DOOR_RE = re.compile(r"(?i)\bfront\s+door\b")
_BACK_DOOR_RE = re.compile(r"(?i)\bback\s+door\b")


@dataclass
class HouseHit:
    slug: str
    label: str
    certainty: int
    reason: str


@dataclass
class RoomHit:
    room: Optional[str]
    certainty: int
    reason: str
    conflict: bool = False


@dataclass
class EntryCodes:
    """In-memory codes only. Do not log or put on Discord."""

    front: str = ""
    back: str = ""
    lockbox: str = ""
    location: str = ""
    missing: List[str] = field(default_factory=list)
    lockbox_missing: List[str] = field(default_factory=list)
    sifely_rotated: bool = False
    used_inbound_share: bool = False


@dataclass
class Decision:
    action: str
    reason: str
    certainty: int
    chat_id: str = ""
    house_label: str = ""
    room: str = ""
    slug: str = ""
    discord_kind: Optional[str] = None
    send_body: str = ""
    stage: str = ""


@dataclass
class RunResult:
    action: str
    reason: str
    results: List[Dict[str, Any]] = field(default_factory=list)
    discord_posts: List[str] = field(default_factory=list)
    sent: int = 0


def load_environment() -> None:
    load_dotenv(ENV_PATH)


def running_in_ci() -> bool:
    return runtime.running_in_ci()


def live_send_enabled() -> bool:
    """Default off until Mac .env sets LOCKOUT_REPLY_ENABLE. CI must not send.

    Darwin is not an implicit send permission. PADSPLIT_SEND_LOCKOUT is
    the Stage A–B alias; LOCKOUT_REPLY_ENABLE remains the legacy flag.
    """
    return runtime.send_enabled("lockout")


def _log(message: str) -> None:
    sys.stderr.write(f"[lockout-reply] {lock_codes.redact_for_log(message)}\n")


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def sender_name(sender: Dict[str, Any]) -> str:
    return normalize_text(
        sender.get("displayName")
        or f"{sender.get('firstName') or ''} {sender.get('lastName') or ''}"
    )


def occupant_names(thread: Dict[str, Any]) -> set[str]:
    user = ((thread.get("occupancy") or {}).get("user") or {})
    values = {
        normalize_text(thread.get("title")).lower(),
        normalize_text(user.get("displayName")).lower(),
        normalize_text(f"{user.get('firstName') or ''} {user.get('lastName') or ''}").lower(),
    }
    return {value for value in values if value}


def is_member_message(thread: Dict[str, Any], message: Dict[str, Any]) -> bool:
    sender = message.get("sender") or {}
    role = str(sender.get("roleId") or "")
    if role == TENANT_ROLE_ID:
        return True
    if role == HOST_ROLE_ID:
        return False
    name = sender_name(sender).lower()
    return bool(name and name in occupant_names(thread))


def is_host_message(message: Dict[str, Any]) -> bool:
    sender = message.get("sender") or {}
    return str(sender.get("roleId") or "") == HOST_ROLE_ID


def message_text(message: Dict[str, Any]) -> str:
    text = normalize_text(message.get("text"))
    if text:
        return text
    ticket = ((message.get("ticketStatus") or {}).get("ticket") or {})
    details = normalize_text(ticket.get("details"))
    category = normalize_text(str(ticket.get("category") or "").replace("_", " "))
    return " ".join(part for part in (category, details) if part)


def iter_thread_messages(thread: Dict[str, Any]) -> List[Dict[str, Any]]:
    recent = thread.get("recent_messages") or []
    if recent:
        return [row for row in recent if isinstance(row, dict)]
    last = thread.get("lastMessage")
    return [last] if isinstance(last, dict) else []


def detect_lockout(text: str) -> bool:
    return bool(_LOCKOUT_RE.search(text or ""))


def detect_door_fail_followup(text: str) -> bool:
    """True when the member says the door/code still failed after a door reply."""
    return bool(_DOOR_FAIL_FOLLOWUP_RE.search(text or ""))


def detect_entry_success(text: str) -> bool:
    """True when the member confirms they got in after a door-stage send.

    Precision: call this only after a door stage is already on the thread.
    If success and door-fail cues appear in the same text, this is still True
    and callers must prefer success (stop the ladder).
    """
    return bool(_ENTRY_SUCCESS_RE.search(text or ""))


def detect_fresh_lockout(text: str) -> bool:
    """Lockout cue that is not only a door-fail follow-up phrase.

    Used after a resolved confirmation so 'still locked out' noise does not
    re-open stage 2 without a new clear lockout.
    """
    stripped = _DOOR_FAIL_FOLLOWUP_RE.sub(" ", text or "")
    return detect_lockout(stripped)


def thread_street(thread: Dict[str, Any]) -> str:
    address = ((thread.get("property") or {}).get("address") or {})
    return normalize_text(address.get("street1") or address.get("full_street"))


def thread_room(thread: Dict[str, Any]) -> str:
    room = ((thread.get("occupancy") or {}).get("room") or {})
    value = room.get("roomNumber")
    if value in (None, ""):
        return ""
    return str(value).strip()


def current_occupant(thread: Dict[str, Any], now: datetime) -> bool:
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    user = occupancy.get("user")
    if not isinstance(user, dict) or not user:
        return False
    move_out = parse_dt(occupancy.get("moveOutDate"))
    if move_out is not None and move_out.date() < now.date():
        return False
    return True


def match_house(street: str, extra_text: str = "") -> HouseHit:
    haystack = f"{street} {extra_text}".lower()
    if lock_codes.is_spanish_moss_address(street) or lock_codes.is_spanish_moss_address(extra_text):
        if "green hill" in haystack or "greenhill" in haystack:
            return HouseHit("", "", 40, "spanish moss vs greenhill conflict")
        return HouseHit("spanish_moss", "Spanish Moss", 100, "spanish moss address")

    hits: List[Tuple[int, str]] = []
    for slug, profile in HOUSE_PROFILES.items():
        if slug == "spanish_moss":
            continue
        aliases = profile["aliases"]
        if any(alias in haystack for alias in aliases):
            hits.append((100 if street else 85, slug))
    if len(hits) == 1:
        slug = hits[0][1]
        return HouseHit(slug, HOUSE_PROFILES[slug]["label"], hits[0][0], "unique house alias")
    if len(hits) > 1:
        labels = ", ".join(HOUSE_PROFILES[item[1]]["label"] for item in hits)
        return HouseHit("", labels, 50, "multiple house aliases")
    return HouseHit("", "", 30, "house unknown")


def mentioned_room(text: str) -> str:
    match = _ROOM_MENTION_RE.search(text or "")
    return match.group(1) if match else ""


def resolve_room(thread: Dict[str, Any], member_text: str) -> RoomHit:
    occupancy_room = thread_room(thread)
    spoken = mentioned_room(member_text)
    if occupancy_room and spoken and occupancy_room != spoken:
        return RoomHit(occupancy_room, 85, "member room differs from occupancy", conflict=True)
    if occupancy_room:
        return RoomHit(occupancy_room, 100, "occupancy room")
    if spoken:
        return RoomHit(spoken, 80, "room from member text only")
    return RoomHit("", 70, "room unknown")


def score_certainty(
    house: HouseHit,
    room: RoomHit,
    member_text: str,
    *,
    codes_ready: bool,
) -> Tuple[int, str]:
    if house.certainty < 80 or not house.slug:
        return min(house.certainty, 70), "house not confirmed"
    profile = HOUSE_PROFILES[house.slug]
    door_ambiguous = False
    mentions_front = bool(_FRONT_DOOR_RE.search(member_text or ""))
    mentions_back = bool(_BACK_DOOR_RE.search(member_text or ""))
    if mentions_front and mentions_back:
        door_ambiguous = True
    if mentions_back and not profile["require_back"] and not profile["sifely_back"]:
        door_ambiguous = True
    if room.conflict or room.certainty < 100:
        return min(99, max(80, room.certainty)), room.reason
    if door_ambiguous:
        return 90, "ambiguous which door"
    if house.certainty < 100:
        return house.certainty, house.reason
    if not codes_ready:
        return 100, "house and room known; codes missing"
    return 100, "house and room known"


def first_filled(doc: Dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def pick_room_lockbox(doc: Dict[str, Any], room: str) -> Tuple[str, str]:
    """Prefer the lockbox/room code that matches the member room."""
    room_key = ""
    if room.isdigit():
        room_key = room
    else:
        digits = re.sub(r"\D", "", room or "")
        room_key = digits

    if room_key:
        lockbox = first_filled(doc, (f"lockbox_{room_key}",))
        location = first_filled(doc, (f"lockbox_{room_key}_location",))
        room_code = first_filled(doc, (f"r{room_key}",))
        if lockbox:
            return lockbox, location
        if room_code:
            return room_code, location

    extras: List[Tuple[str, str]] = []
    for index in (1, 2):
        code = first_filled(doc, (f"extra_lockbox_{index}_code",))
        if not code:
            continue
        extras.append((code, first_filled(doc, (f"extra_lockbox_{index}_location",))))
    if len(extras) == 1:
        return extras[0]
    return "", ""


def codes_from_property_doc(
    slug: str,
    doc: Dict[str, Any],
    room: str,
    *,
    sifely_back: str = "",
    sifely_rotated: bool = False,
    used_inbound_share: bool = False,
) -> EntryCodes:
    profile = HOUSE_PROFILES[slug]
    entry = EntryCodes(
        sifely_rotated=sifely_rotated,
        used_inbound_share=used_inbound_share,
    )
    if profile["sifely_back"]:
        # Never copy Firestore/Tinghui static back_door for Spanish Moss.
        entry.back = (sifely_back or "").strip()
        if not entry.back:
            entry.missing.append("sifely_back")
        entry.front = first_filled(doc, profile["front_keys"])
    else:
        if profile["require_front"]:
            entry.front = first_filled(doc, profile["front_keys"])
            if not entry.front:
                entry.missing.append("front")
        else:
            entry.front = first_filled(doc, profile["front_keys"])
        if profile["require_back"]:
            entry.back = first_filled(doc, profile["back_keys"])
            if not entry.back:
                entry.missing.append("back")
        else:
            entry.back = first_filled(doc, profile["back_keys"])

    lockbox, location = pick_room_lockbox(doc, room)
    entry.lockbox = lockbox
    entry.location = location
    if not entry.lockbox:
        entry.lockbox_missing.append("lockbox_or_room")
    return entry


def _fail_ladder_lines() -> List[str]:
    return [
        "If that still fails:",
        f"1) Call {JOE_FIELD_PHONE}",
        f"2) If no answer, call PadSplit Member Support {PADSPLIT_MEMBER_SUPPORT_PHONE}",
        "3) Message us here again with a photo of the lock (keypad + door) "
        "and we’ll escalate to field",
    ]


def format_door_lockout_body(house_label: str, room: str, entry: EntryCodes) -> str:
    """Stage-1 PadSplit body: door codes only. Never log this string."""
    if entry.missing:
        raise RuntimeError("refusing to format door lockout body with missing door codes")
    if not house_label or not room:
        raise RuntimeError("refusing to format lockout body without house and room")
    if not entry.front and not entry.back:
        raise RuntimeError("refusing to format door lockout body without a door code")
    lines = [
        f"{LOCKOUT_PACK_MARKER} {house_label} Rm {room}:",
        "",
    ]
    if entry.front:
        lines.append(f"Front door code: {entry.front}")
    if entry.back:
        lines.append(f"Back door code: {entry.back}")
    lines.extend(
        [
            "",
            "If the keypad lights but won’t open, try the deadbolt/top lock "
            "(turn thumbturn) and retry the code.",
            "",
            *_fail_ladder_lines(),
        ]
    )
    body = "\n".join(lines)
    if LOCKBOX_LINE_MARKER in body or "lockbox" in body.lower():
        raise RuntimeError("door lockout body must not mention lockbox")
    return body


def format_lockbox_lockout_body(house_label: str, room: str, entry: EntryCodes) -> str:
    """Stage-2 PadSplit body: lockbox / room code + location. Never log this string."""
    if entry.lockbox_missing or not entry.lockbox:
        raise RuntimeError("refusing to format lockbox lockout body with missing lockbox")
    if not house_label or not room:
        raise RuntimeError("refusing to format lockout body without house and room")
    location_bit = f" — {entry.location}" if entry.location else ""
    lines = [
        f"{LOCKBOX_PACK_MARKER} {house_label} Rm {room}:",
        "",
        f"{LOCKBOX_LINE_MARKER} {entry.lockbox}{location_bit}",
        "",
        "Please put any lockbox key back after use.",
    ]
    return "\n".join(lines)


def format_lockout_body(house_label: str, room: str, entry: EntryCodes) -> str:
    """Stage-1 door body. Lockbox is never included."""
    return format_door_lockout_body(house_label, room, entry)


def discord_lockout_detected_text(house_label: str, *, needs_tap: bool) -> str:
    house = house_label or "an unknown house"
    if needs_tap:
        text = (
            f"Lockout detected at {house}. Room or door is unclear. "
            "Needs a tap. No codes posted."
        )
    else:
        text = (
            f"Joe — lockout detected at {house}. House or room is unclear. "
            "No member send. No codes posted."
        )
    return lock_codes.assert_discord_outbound_safe(text)


def discord_missing_codes_text(house_label: str) -> str:
    house = house_label or "a house"
    text = (
        f"Lockout at {house} is missing a needed entry field. "
        "No member send. No codes posted."
    )
    return lock_codes.assert_discord_outbound_safe(text)


def discord_spanish_moss_rotated_text() -> str:
    return lock_codes.assert_discord_outbound_safe(lock_codes.discord_rotated_text())


def discord_sifely_unavailable_text() -> str:
    text = (
        "Spanish Moss lockout needs the Sifely path. "
        "API is down. Waiting on the existing new-tenants fallback. "
        "No codes posted."
    )
    return lock_codes.assert_discord_outbound_safe(text)


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"threads": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"threads": {}}
    if not isinstance(payload, dict):
        return {"threads": {}}
    payload.setdefault("threads", {})
    if not isinstance(payload["threads"], dict):
        payload["threads"] = {}
    return payload


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def _thread_state(state: Dict[str, Any], chat_id: str) -> Dict[str, Any]:
    row = (state.get("threads") or {}).get(chat_id) or {}
    return row if isinstance(row, dict) else {}


def _legacy_combined_pack(row: Dict[str, Any]) -> bool:
    """Old single-pack state (sent_at, no per-stage keys) counts as both stages."""
    return bool(row.get("sent_at") and not row.get("door_sent_at") and not row.get("lockbox_sent_at"))


def stage_sent_at(state: Dict[str, Any], chat_id: str, stage: str) -> Optional[datetime]:
    row = _thread_state(state, chat_id)
    if _legacy_combined_pack(row):
        return parse_dt(row.get("sent_at"))
    if stage == STAGE_DOOR:
        return parse_dt(row.get("door_sent_at") or row.get("sent_at"))
    if stage == STAGE_LOCKBOX:
        return parse_dt(row.get("lockbox_sent_at"))
    return None


def already_sent_stage(
    state: Dict[str, Any],
    chat_id: str,
    stage: str,
    *,
    now: datetime,
    window: timedelta = IDEMPOTENCY_WINDOW,
) -> bool:
    sent_at = stage_sent_at(state, chat_id, stage)
    if sent_at is None:
        return False
    return (now - sent_at) < window


def already_sent(
    state: Dict[str, Any],
    chat_id: str,
    *,
    now: datetime,
    window: timedelta = IDEMPOTENCY_WINDOW,
) -> bool:
    """True when the door stage was already sent in the idempotency window."""
    return already_sent_stage(state, chat_id, STAGE_DOOR, now=now, window=window)


def record_sent(
    state: Dict[str, Any],
    chat_id: str,
    *,
    now: datetime,
    action: str,
    stage: str = STAGE_DOOR,
) -> None:
    threads = state.setdefault("threads", {})
    row = threads.get(chat_id) if isinstance(threads.get(chat_id), dict) else {}
    stamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if stage == STAGE_LOCKBOX:
        row["lockbox_sent_at"] = stamp
    else:
        row["door_sent_at"] = stamp
    row["action"] = action
    row["stage"] = stage
    threads[chat_id] = row


def resolved_at(state: Dict[str, Any], chat_id: str) -> Optional[datetime]:
    row = _thread_state(state, chat_id)
    return parse_dt(row.get("resolved_at"))


def already_resolved(
    state: Dict[str, Any],
    chat_id: str,
    *,
    now: datetime,
    window: timedelta = IDEMPOTENCY_WINDOW,
) -> bool:
    stamp = resolved_at(state, chat_id)
    if stamp is None:
        return False
    return (now - stamp) < window


def record_resolved(
    state: Dict[str, Any],
    chat_id: str,
    *,
    now: datetime,
    reason: str = "member confirmed entry",
) -> None:
    """Persist resolved so later fail-ish noise does not re-open stage 2."""
    threads = state.setdefault("threads", {})
    row = threads.get(chat_id) if isinstance(threads.get(chat_id), dict) else {}
    if not row.get("resolved_at"):
        row["resolved_at"] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row["action"] = "resolved"
    row["resolved_reason"] = reason
    threads[chat_id] = row


def host_lockout_stage_messages(
    thread: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    door: List[Dict[str, Any]] = []
    lockbox: List[Dict[str, Any]] = []
    for message in iter_thread_messages(thread):
        if not is_host_message(message):
            continue
        text = message_text(message)
        if LOCKOUT_PACK_MARKER in text:
            door.append(message)
        if LOCKBOX_PACK_MARKER in text or LOCKBOX_LINE_MARKER in text:
            lockbox.append(message)
    return door, lockbox


def host_already_sent_stage(thread: Dict[str, Any], stage: str) -> bool:
    door, lockbox = host_lockout_stage_messages(thread)
    if stage == STAGE_DOOR:
        return bool(door)
    if stage == STAGE_LOCKBOX:
        return bool(lockbox)
    return False


def host_already_sent_lockout_pack(thread: Dict[str, Any]) -> bool:
    """True when an old combined pack (door + lockbox line) is already on the thread."""
    door, lockbox = host_lockout_stage_messages(thread)
    return bool(door) and bool(lockbox)


def latest_host_stage_at(thread: Dict[str, Any], stage: str) -> Optional[datetime]:
    door, lockbox = host_lockout_stage_messages(thread)
    messages = door if stage == STAGE_DOOR else lockbox
    latest: Optional[datetime] = None
    for message in messages:
        created = parse_dt(message.get("created"))
        if created is not None and (latest is None or created > latest):
            latest = created
    return latest


def door_reply_at(
    state: Optional[Dict[str, Any]],
    thread: Dict[str, Any],
    chat_id: str,
) -> Optional[datetime]:
    times: List[datetime] = []
    if state is not None:
        sent = stage_sent_at(state, chat_id, STAGE_DOOR)
        if sent is not None:
            times.append(sent)
    host_at = latest_host_stage_at(thread, STAGE_DOOR)
    if host_at is not None:
        times.append(host_at)
    return max(times) if times else None


def recent_member_lockout(
    thread: Dict[str, Any],
    *,
    now: datetime,
    lookback: timedelta = LOOKBACK,
    include_door_fail: bool = False,
) -> Optional[Dict[str, Any]]:
    newest: Optional[Dict[str, Any]] = None
    newest_at: Optional[datetime] = None
    for message in iter_thread_messages(thread):
        if message.get("deleted"):
            continue
        if not is_member_message(thread, message):
            continue
        created = parse_dt(message.get("created"))
        if created is None or (now - created) > lookback:
            continue
        text = message_text(message)
        matched = detect_lockout(text)
        if not matched and include_door_fail:
            matched = detect_door_fail_followup(text)
        if not matched:
            continue
        if newest_at is None or created > newest_at:
            newest = message
            newest_at = created
    return newest


def recent_door_fail_followup(
    thread: Dict[str, Any],
    *,
    now: datetime,
    after: datetime,
    lookback: timedelta = LOOKBACK,
) -> Optional[Dict[str, Any]]:
    """Newest member lockout / door-fail message strictly after the door reply."""
    newest: Optional[Dict[str, Any]] = None
    newest_at: Optional[datetime] = None
    for message in iter_thread_messages(thread):
        if message.get("deleted"):
            continue
        if not is_member_message(thread, message):
            continue
        created = parse_dt(message.get("created"))
        if created is None or created <= after:
            continue
        if (now - created) > lookback:
            continue
        text = message_text(message)
        if not (detect_door_fail_followup(text) or detect_lockout(text)):
            continue
        if newest_at is None or created > newest_at:
            newest = message
            newest_at = created
    return newest


def recent_entry_success(
    thread: Dict[str, Any],
    *,
    now: datetime,
    after: datetime,
    lookback: timedelta = LOOKBACK,
) -> Optional[Dict[str, Any]]:
    """Newest member entry-success message strictly after the door reply."""
    newest: Optional[Dict[str, Any]] = None
    newest_at: Optional[datetime] = None
    for message in iter_thread_messages(thread):
        if message.get("deleted"):
            continue
        if not is_member_message(thread, message):
            continue
        created = parse_dt(message.get("created"))
        if created is None or created <= after:
            continue
        if (now - created) > lookback:
            continue
        text = message_text(message)
        if not detect_entry_success(text):
            continue
        if newest_at is None or created > newest_at:
            newest = message
            newest_at = created
    return newest


def recent_fresh_lockout_and_fail(
    thread: Dict[str, Any],
    *,
    now: datetime,
    after: datetime,
    lookback: timedelta = LOOKBACK,
) -> Optional[Dict[str, Any]]:
    """After resolved: newest fail only when a fresh lockout (not fail-only noise) exists."""
    lockout_msg: Optional[Dict[str, Any]] = None
    fail_msg: Optional[Dict[str, Any]] = None
    fail_at: Optional[datetime] = None
    for message in iter_thread_messages(thread):
        if message.get("deleted"):
            continue
        if not is_member_message(thread, message):
            continue
        created = parse_dt(message.get("created"))
        if created is None or created <= after:
            continue
        if (now - created) > lookback:
            continue
        text = message_text(message)
        if detect_fresh_lockout(text):
            lockout_msg = message
        if detect_door_fail_followup(text):
            if fail_at is None or created > fail_at:
                fail_msg = message
                fail_at = created
    if lockout_msg is None or fail_msg is None:
        return None
    return fail_msg


def fetch_property_codes(slug: str) -> Dict[str, Any]:
    """Read property_codes/{slug}. Never log field values."""
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        _log("firebase-admin missing; cannot read property codes")
        return {}

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    google_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not firebase_admin._apps:
        if service_account_json:
            firebase_admin.initialize_app(credentials.Certificate(json.loads(service_account_json)))
        elif google_credentials:
            firebase_admin.initialize_app(credentials.Certificate(google_credentials))
        else:
            _log("Firestore credentials missing; cannot read property codes")
            return {}
    client = firestore.client()
    snap = client.collection(CODES_COLLECTION).document(slug).get()
    data = snap.to_dict() if snap.exists else None
    return data if isinstance(data, dict) else {}


def obtain_spanish_moss_back(
    *,
    sifely_session=None,
    inbound_messages: Optional[List[Dict[str, Any]]] = None,
    processed_share_ids: Optional[Sequence[str]] = None,
    rotate_if_needed: bool = True,
    generate_code: Optional[Callable[[], str]] = None,
) -> Tuple[str, str]:
    """Return (code, source). Code stays in memory. Never log it.

    source: sifely_current | sifely_rotated | inbound_share | missing
    """
    api_key = lock_codes.sifely_api_key()
    if not api_key:
        code, _share_id = lock_codes._first_new_share(
            inbound_messages,
            processed_ids=processed_share_ids or [],
        )
        return (code or "", "inbound_share" if code else "missing")

    try:
        locks = lock_codes.list_locks(api_key, session=sifely_session)
        lock = lock_codes.resolve_lock(locks)
        if lock is None:
            raise lock_codes.SifelyUnavailable("could not resolve Spanish Moss back-door lock")
        passcodes = lock_codes.list_passcodes(api_key, lock.get("lockId"), session=sifely_session)
        pwd_id = lock_codes.resolve_keyboard_pwd_id(passcodes)
        current = ""
        if pwd_id:
            for item in passcodes:
                if str(item.get("keyboardPwdId") or item.get("id") or "") != str(pwd_id):
                    continue
                value = item.get("keyboardPwd")
                if isinstance(value, str) and value.strip():
                    current = value.strip()
                    break
        if current:
            return current, "sifely_current"
        if not rotate_if_needed or not pwd_id:
            raise lock_codes.SifelyUnavailable("no current Spanish Moss passcode")
        new_code = (generate_code or lock_codes.generate_passcode)()
        lock_codes.change_passcode(
            api_key,
            lock_id=lock.get("lockId"),
            keyboard_pwd_id=pwd_id,
            new_code=new_code,
            session=sifely_session,
        )
        try:
            lock_codes.update_codes_page(new_code)
        except Exception as exc:
            _log(f"codes page update after rotate failed; continuing: {exc}")
        return new_code, "sifely_rotated"
    except lock_codes.SifelyUnavailable as exc:
        _log(f"Sifely path unavailable: {exc}")
        code, _share_id = lock_codes._first_new_share(
            inbound_messages,
            processed_ids=processed_share_ids or [],
        )
        return (code or "", "inbound_share" if code else "missing")


def decide(
    thread: Dict[str, Any],
    *,
    now: datetime,
    state: Optional[Dict[str, Any]] = None,
    codes_doc: Optional[Dict[str, Any]] = None,
    sifely_back: str = "",
    sifely_source: str = "",
) -> Decision:
    chat_id = str(thread.get("id") or "")
    if not chat_id:
        return Decision(action="skip", reason="missing chat id", certainty=0)
    if not current_occupant(thread, now):
        return Decision(action="skip", reason="not a current occupant thread", certainty=0, chat_id=chat_id)

    door_from_state = state is not None and already_sent_stage(state, chat_id, STAGE_DOOR, now=now)
    lockbox_from_state = state is not None and already_sent_stage(
        state, chat_id, STAGE_LOCKBOX, now=now
    )
    door_from_host = host_already_sent_stage(thread, STAGE_DOOR)
    lockbox_from_host = host_already_sent_stage(thread, STAGE_LOCKBOX)
    door_sent = door_from_state or door_from_host
    lockbox_sent = lockbox_from_state or lockbox_from_host

    if lockbox_sent:
        return Decision(
            action="already_sent",
            reason="lockbox stage already sent",
            certainty=100,
            chat_id=chat_id,
            stage=STAGE_LOCKBOX,
        )

    if door_sent:
        after = door_reply_at(state, thread, chat_id)
        if after is None:
            after = datetime.min.replace(tzinfo=timezone.utc)
        resolved_stamp = resolved_at(state, chat_id) if state is not None else None
        resolved_in_window = state is not None and already_resolved(state, chat_id, now=now)

        if resolved_in_window and resolved_stamp is not None:
            success_after = recent_entry_success(thread, now=now, after=resolved_stamp)
            reopen = recent_fresh_lockout_and_fail(thread, now=now, after=resolved_stamp)
            if success_after is not None or reopen is None:
                return Decision(
                    action="resolved",
                    reason="member confirmed entry",
                    certainty=100,
                    chat_id=chat_id,
                    stage=STAGE_DOOR,
                )
            stage = STAGE_LOCKBOX
            member_text = message_text(reopen)
        else:
            success = recent_entry_success(thread, now=now, after=after)
            if success is not None:
                return Decision(
                    action="resolved",
                    reason="member confirmed entry",
                    certainty=100,
                    chat_id=chat_id,
                    stage=STAGE_DOOR,
                )
            followup = recent_door_fail_followup(thread, now=now, after=after)
            if followup is None:
                return Decision(
                    action="already_sent",
                    reason="door stage already sent",
                    certainty=100,
                    chat_id=chat_id,
                    stage=STAGE_DOOR,
                )
            stage = STAGE_LOCKBOX
            member_text = message_text(followup)
    else:
        lockout_message = recent_member_lockout(thread, now=now)
        if lockout_message is None:
            return Decision(action="skip", reason="no recent member lockout", certainty=0, chat_id=chat_id)
        stage = STAGE_DOOR
        member_text = message_text(lockout_message)

    house = match_house(thread_street(thread), member_text)
    room = resolve_room(thread, member_text)
    profile = HOUSE_PROFILES.get(house.slug) or {}
    codes_ready_guess = True
    if house.slug and codes_doc is not None:
        preview = codes_from_property_doc(
            house.slug,
            codes_doc,
            room.room or "",
            sifely_back=sifely_back,
            sifely_rotated=sifely_source == "sifely_rotated",
            used_inbound_share=sifely_source == "inbound_share",
        )
        codes_ready_guess = (
            not preview.lockbox_missing if stage == STAGE_LOCKBOX else not preview.missing
        )
    certainty, why = score_certainty(house, room, member_text, codes_ready=codes_ready_guess)

    decision = Decision(
        action="skip",
        reason=why,
        certainty=certainty,
        chat_id=chat_id,
        house_label=house.label,
        room=room.room or "",
        slug=house.slug,
        stage=stage,
    )

    if certainty < 80:
        decision.action = "ask_joe"
        decision.discord_kind = "ask_joe"
        decision.reason = why
        return decision
    if certainty < 100:
        decision.action = "needs_tap"
        decision.discord_kind = "needs_tap"
        decision.reason = why
        return decision

    if not house.slug or not room.room:
        decision.action = "ask_joe"
        decision.discord_kind = "ask_joe"
        decision.reason = "house or room missing after score"
        decision.certainty = 70
        return decision

    doc = codes_doc if codes_doc is not None else {}
    entry = codes_from_property_doc(
        house.slug,
        doc,
        room.room,
        sifely_back=sifely_back,
        sifely_rotated=sifely_source == "sifely_rotated",
        used_inbound_share=sifely_source == "inbound_share",
    )
    if stage == STAGE_DOOR:
        if entry.missing:
            decision.action = "missing_codes"
            decision.discord_kind = (
                "sifely_unavailable"
                if profile.get("sifely_back") and "sifely_back" in entry.missing
                else "missing_codes"
            )
            decision.reason = "missing needed door field"
            return decision
        decision.action = "send"
        decision.reason = "100 percent house room and door codes"
        decision.send_body = format_door_lockout_body(house.label, room.room, entry)
        if entry.sifely_rotated:
            decision.discord_kind = "rotated"
        return decision

    if entry.lockbox_missing or not entry.lockbox:
        decision.action = "missing_codes"
        decision.discord_kind = "missing_codes"
        decision.reason = "missing needed lockbox field"
        return decision
    decision.action = "send"
    decision.reason = "100 percent house room and lockbox after door fail"
    decision.send_body = format_lockbox_lockout_body(house.label, room.room, entry)
    return decision


def post_automations_discord(
    text: str,
    *,
    token: Optional[str] = None,
    channel: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    safe = lock_codes.assert_discord_outbound_safe(text)
    token = token or (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    channel = channel or (
        (os.getenv("DISCORD_AUTOMATIONS_CHANNEL_ID") or "").strip()
        or (os.getenv("DISCORD_CHANNEL_ID") or "").strip()
    )
    if not token or not channel:
        _log("Discord token or automations channel missing; skip Discord post")
        return None
    return lock_codes.post_ops_discord(safe, token=token, channel=channel)


def _discord_text(kind: Optional[str], house_label: str) -> Optional[str]:
    if kind == "ask_joe":
        return discord_lockout_detected_text(house_label, needs_tap=False)
    if kind == "needs_tap":
        return discord_lockout_detected_text(house_label, needs_tap=True)
    if kind == "missing_codes":
        return discord_missing_codes_text(house_label)
    if kind == "rotated":
        return discord_spanish_moss_rotated_text()
    if kind == "sifely_unavailable":
        return discord_sifely_unavailable_text()
    return None


def process_lockouts(
    threads: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    state: Optional[Dict[str, Any]] = None,
    state_path: Path = STATE_PATH,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    close_tabs_fn: Optional[Callable[[Optional[List[Dict[str, Any]]], str], List[Dict[str, Any]]]] = None,
    send_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    codes_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
    sifely_fn: Optional[Callable[[], Tuple[str, str]]] = None,
    post_discord: Optional[Callable[[str], Any]] = None,
    send_enabled: bool = True,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    state = state if state is not None else load_state(state_path)
    results: List[Dict[str, Any]] = []
    codes_cache: Dict[str, Dict[str, Any]] = {}
    sifely_cache: Optional[Tuple[str, str]] = None

    for thread in threads:
        chat_id = str(thread.get("id") or "")
        lockout_message = recent_member_lockout(thread, now=now, include_door_fail=True)
        house_preview = match_house(
            thread_street(thread),
            message_text(lockout_message) if lockout_message else "",
        )
        codes_doc: Optional[Dict[str, Any]] = None
        sifely_back = ""
        sifely_source = ""
        # Codes / Sifely only after an in-window lockout from a current occupant.
        # Default obtain_spanish_moss_back never runs in preview/dry_run or when
        # send is disabled. An injected sifely_fn may still run for fixtures.
        if lockout_message and house_preview.slug and current_occupant(thread, now):
            if house_preview.slug not in codes_cache:
                loader = codes_fn or fetch_property_codes
                try:
                    codes_cache[house_preview.slug] = loader(house_preview.slug) or {}
                except Exception as exc:
                    _log(f"property codes lookup failed; treating as empty: {exc}")
                    codes_cache[house_preview.slug] = {}
            codes_doc = codes_cache[house_preview.slug]
            if HOUSE_PROFILES[house_preview.slug].get("sifely_back"):
                allow_default_sifely = send_enabled and not dry_run
                if sifely_fn is not None or allow_default_sifely:
                    if sifely_cache is None:
                        getter = sifely_fn or obtain_spanish_moss_back
                        try:
                            sifely_cache = getter()
                        except Exception as exc:
                            _log(f"Sifely obtain failed: {exc}")
                            sifely_cache = ("", "missing")
                    sifely_back, sifely_source = sifely_cache

        decision = decide(
            thread,
            now=now,
            state=state,
            codes_doc=codes_doc,
            sifely_back=sifely_back,
            sifely_source=sifely_source,
        )
        row = {
            "chat_id": decision.chat_id or chat_id,
            "action": decision.action,
            "reason": decision.reason,
            "certainty": decision.certainty,
            "house_label": decision.house_label,
            "stage": decision.stage,
        }

        if decision.discord_kind:
            text = _discord_text(decision.discord_kind, decision.house_label)
            if text:
                if send_enabled and not dry_run and post_discord is not None:
                    post_discord(text)
                elif send_enabled and not dry_run and post_discord is None:
                    try:
                        post_automations_discord(text)
                    except Exception as exc:
                        _log(f"Discord post failed; continuing: {exc}")
                row["discord"] = text

        if decision.action == "resolved" and not dry_run:
            record_resolved(
                state,
                decision.chat_id or chat_id,
                now=now,
                reason=decision.reason or "member confirmed entry",
            )

        if decision.action != "send":
            results.append(row)
            continue

        if not send_enabled or dry_run:
            row["action"] = "would_send"
            results.append(row)
            continue

        if send_fn is None:
            row["action"] = "skipped_no_send_fn"
            results.append(row)
            continue

        try:
            new_booking.require_leftover_drafts_cleared(
                leftover_compose_tabs,
                decision.chat_id,
                close_tabs_fn=close_tabs_fn,
            )
            send_fn(decision.chat_id, decision.send_body)
        except new_booking.LeftoverDraftGateError as exc:
            _log(f"leftover-draft hard skip: {exc}")
            row["action"] = "skipped_leftover_drafts"
            results.append(row)
            continue
        except Exception as exc:
            _log(f"PadSplit host send failed; continuing: {exc}")
            row["action"] = "send_failed"
            results.append(row)
            continue

        record_sent(
            state,
            decision.chat_id,
            now=now,
            action="sent",
            stage=decision.stage or STAGE_DOOR,
        )
        row["action"] = "sent"
        results.append(row)

    if not dry_run:
        save_state(state, state_path)
    return results


def load_host_messages() -> List[Dict[str, Any]]:
    return lock_codes.load_host_messages()


def run(
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    host_messages: Optional[List[Dict[str, Any]]] = None,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    leftover_tabs_path: Path = new_booking.LEFTOVER_TABS_PATH,
    send_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    codes_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
    sifely_fn: Optional[Callable[[], Tuple[str, str]]] = None,
    post_discord: Optional[Callable[[str], Any]] = None,
    state_path: Path = STATE_PATH,
    session=None,
    creds: Optional[Dict[str, str]] = None,
) -> RunResult:
    load_environment()
    current = now or datetime.now(timezone.utc)
    if running_in_ci() and not dry_run:
        _log("skip_ci: GitHub Actions / CI must not send lockout replies")
        return RunResult(action="skip_ci", reason="CI must not send")
    if not live_send_enabled() and not dry_run:
        _log("disabled (LOCKOUT_REPLY_ENABLE or CI)")
        return RunResult(action="disabled", reason="LOCKOUT_REPLY_ENABLE is off")

    messages = host_messages if host_messages is not None else load_host_messages()
    tabs = leftover_compose_tabs
    if tabs is None:
        tabs = new_booking.load_leftover_compose_tabs(leftover_tabs_path)

    def _send(chat_id: str, text: str) -> Dict[str, Any]:
        if send_fn is not None:
            return send_fn(chat_id, text)
        if session is None or creds is None:
            raise RuntimeError("send session is not wired")
        return new_booking.send_host_message(
            session,
            creds,
            chat_id,
            text,
            leftover_compose_tabs=tabs,
        )

    rows = process_lockouts(
        messages,
        now=current,
        state_path=state_path,
        leftover_compose_tabs=tabs,
        send_fn=_send if not dry_run else None,
        codes_fn=codes_fn,
        sifely_fn=sifely_fn,
        post_discord=post_discord,
        send_enabled=not dry_run,
        dry_run=dry_run,
    )
    if leftover_compose_tabs is None and not dry_run:
        new_booking.save_leftover_compose_tabs(tabs, leftover_tabs_path)

    sent = sum(1 for row in rows if row.get("action") == "sent")
    posts = [str(row["discord"]) for row in rows if row.get("discord")]
    for text in posts:
        lock_codes.assert_discord_outbound_safe(text)
    summary = "sent" if sent else (rows[0]["action"] if rows else "noop")
    result = RunResult(
        action=summary,
        reason=f"{len(rows)} lockout thread(s)",
        results=rows,
        discord_posts=posts,
        sent=sent,
    )
    _log(f"{result.action} ({result.reason})")
    return result


def run_for_scraper(
    session,
    creds: Dict[str, str],
    messages: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    state_path: Path = STATE_PATH,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    leftover_tabs_path: Path = new_booking.LEFTOVER_TABS_PATH,
) -> List[Dict[str, Any]]:
    if not live_send_enabled():
        _log("skipped in scraper (LOCKOUT_REPLY_ENABLE or CI)")
        return []
    result = run(
        now=now,
        dry_run=False,
        host_messages=messages,
        leftover_compose_tabs=leftover_compose_tabs,
        leftover_tabs_path=leftover_tabs_path,
        state_path=state_path,
        session=session,
        creds=creds,
    )
    return result.results


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PadSplit lockout auto-reply")
    parser.add_argument("--dry-run", action="store_true", help="Decide only; do not send")
    args = parser.parse_args(argv)
    load_environment()
    if running_in_ci() and not args.dry_run:
        _log("skip_ci: GitHub Actions / CI must not send lockout replies")
        return 0
    if not live_send_enabled() and not args.dry_run:
        _log("disabled (LOCKOUT_REPLY_ENABLE or CI)")
        return 0

    if args.dry_run:
        run(dry_run=True)
        return 0

    creds = load_credentials()
    with create_session() as session:
        login(session, creds["email"], creds["password"], force=False)
        run(dry_run=False, session=session, creds=creds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
