#!/usr/bin/env python3
"""Sifely Open API lock-code automation (move-in last-4 + Ang rotate).

Sifely Open API on the free Developer plan. Auth header is the raw
SIFELY_API_KEY value (sk- key, no Bearer). Base URL cus-openapi.sifely.com.

Default off until Mac ``LOCK_CODES_ENABLE=1`` (and action hooks). GitHub
Actions / CI must not rotate locks or post Discord.

New move-in: set that room lock to the last four digits of the tenant
phone, message the tenant on PadSplit (digits allowed there only), update
Firestore ``property_codes`` for codes.html, then post a digit-free notice
to Discord #ai-automations.

Move-out / member terminated: ask for scoped approval; do not reset from a
scheduled date, cancellation or payment signal. Ang or Joe must directly
reply "confirm vacant room reset" to the event's house/room request.
Shared front/back rotation separately requires Ang's direct yes reply.
Confirmed stage checkpoints prevent repeating completed physical changes,
Firestore writes and member notifications. Ambiguous interrupted effects
remain pending for readback/reconciliation, never blind retries.

Firebase service-account missing is fail-closed Need-you / skip.

Safety: never write lock codes, PIN digits, phone digits, API keys, or
Sifely tokens to git, logs, Discord outbound, or README examples.
Tests use REDACTED. Discord outbound must refuse digits.

lockout_reply Spanish Moss back-door obtain still uses list/resolve/change
on this module. Do not break that path.
"""

from __future__ import annotations

import argparse
import fcntl
import functools
import hashlib
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

try:
    from padsplit_scraper import runtime
    from padsplit_scraper.scraper import (
        DEFAULT_TIMEOUT,
        GRAPHQL_URL,
        _authed_request,
        create_session,
        load_credentials,
        login,
    )
except ModuleNotFoundError:  # python3 padsplit_scraper/lock_codes.py
    import runtime  # type: ignore
    from scraper import (  # type: ignore
        DEFAULT_TIMEOUT,
        GRAPHQL_URL,
        _authed_request,
        create_session,
        load_credentials,
        login,
    )


CT = ZoneInfo("America/Chicago")
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
STATE_PATH = ROOT_DIR / "logs" / "lock_codes_state.json"

SIFELY_BASE = "https://cus-openapi.sifely.com"
SIFELY_LOCK_LIST_PATH = "/v3/lock/list"
SIFELY_PASSCODE_LIST_PATH = "/v3/lock/listKeyboardPwd"
SIFELY_PASSCODE_CHANGE_PATH = "/v3/keyboardPwd/change"
# Gateway / Wi-Fi change. Bluetooth (1) requires the Sifely app on-device.
SIFELY_CHANGE_TYPE_GATEWAY = "2"

PROPERTY_LABEL = "Spanish Moss"
PROPERTY_SLUG = "spanish_moss"
LOCK_FIELD = "back_door"
CODES_COLLECTION = "property_codes"
# Vacant-room default after move-out. Never put this on Discord or in logs.
VACANT_ROOM_DEFAULT = "0417"
RECENT_EVENT_DAYS = 3

# Digit-free Discord labels only. Street numbers stay out of Discord.
# Aliases include compact forms (spanishmoss) for Sifely lock names.
HOUSE_LOCK_PROFILES: Dict[str, Dict[str, Any]] = {
    "leana_6623": {
        "label": "Leana",
        "aliases": ("leana", "leanna"),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "sylvia_2516": {
        "label": "Sylvia",
        "aliases": ("sylvia",),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "ridge_oak_10235": {
        "label": "Ridge Oak",
        "aliases": ("ridge oak", "ridgeoak"),
        "has_front": True,
        "has_back": True,
        "front_field": "front_door",
        "back_field": "back_door",
    },
    "pebbleshores_3414": {
        "label": "Pebbleshores",
        "aliases": ("pebbleshores", "pebble shores", "pebble shore"),
        "has_front": True,
        "has_back": True,
        "front_field": "front_back",
        "back_field": "front_back",
    },
    "greenhill_3406": {
        "label": "Greenhill",
        "aliases": ("greenhill", "green hill"),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "parker_4351": {
        "label": "Parker",
        "aliases": ("parker",),
        "has_front": True,
        "has_back": True,
        "front_field": "front_door",
        "back_field": "back_door",
    },
    "pioneer_1404": {
        "label": "Pioneer",
        "aliases": ("pioneer",),
        "has_front": True,
        "has_back": True,
        "front_field": "front_door",
        "back_field": "back_door",
    },
    "burton_5509": {
        "label": "Burton",
        "aliases": ("burton",),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "broken_crest_1025": {
        "label": "Broken Crest",
        "aliases": ("broken crest", "brokencrest"),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "spanish_moss": {
        "label": "Spanish Moss",
        "aliases": ("spanish moss", "spanishmoss"),
        "has_front": True,
        "has_back": True,
        "front_field": "front_door",
        "back_field": "back_door",
    },
}

# PadSplit Ops bot posts here. Outbound content must contain no digits.
# #new-tenants is inbound-only for the API-down Sifely-share fallback.
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_NEW_TENANTS_CHANNEL_ID = "1542260130614354055"
DISCORD_AUTOMATIONS_CHANNEL_ID = "1543396445799845908"
DISCORD_GUILD_ID = "1540475742104719380"

# Host UI mutation, same path as the Hirevire first-host-message PR.
SEND_MESSAGE_MUTATION = """
mutation sendMessage($chatId: ID!, $text: String, $attachments: [ChatAttachmentInput]) {
  messenger(
    messageTypes: [MOVE_OUT_PHOTOS, MOVE_OUT_CONFIRMED, TICKET_RATING, TICKET_UPDATE]
  ) {
    chat(id: $chatId) {
      sendMessage(text: $text, attachments: $attachments) {
        ok
        message {
          id
          text
          created
          messageType
        }
      }
    }
  }
}
"""

# Occupancy user phone for move-in last-four. Never log the result.
OCCUPANCY_PHONE_QUERY = """
query occupancyPhone($id: ID!) {
  occupancy(id: $id) {
    id
    user {
      phone
      phoneNumber
    }
  }
}
"""

NEED_YOU_MISSING_KEY = (
    "Need you: missing SIFELY_API_KEY. "
    "Lock-code automation is blocked."
)
NEED_YOU_MISSING_FIREBASE = (
    "Need you: missing Firebase service account. "
    "Lock-code records cannot update. Skipping rotate."
)
NEED_YOU_MISSING_PHONE = (
    "Need you: new move-in is missing a tenant phone. "
    "Room code was not set."
)
NEED_YOU_MISSING_LOCK = (
    "Need you: no Sifely lock matched that house or room. "
    "Skipping rotate."
)
DISCORD_HUMAN_CHANGE = "Spanish Moss code changed."
DISCORD_ROTATED = "Spanish Moss lock was rotated."

_DIGIT_RUN = re.compile(r"\d+")
_CODE_TOKEN = re.compile(r"\b(?:passcode|code|pin|keyboard\s*pwd)\b\s*[:#-]?\s*(\S+)", re.I)
_SK_KEY = re.compile(r"sk-[A-Za-z0-9]+")
_SECRET_HEADER = re.compile(r"(?i)(authorization\s*:\s*)\S+")
_ROOM_IN_LABEL = re.compile(
    r"(?i)(?:(?:room|rm)\s*[:#-]?\s*(\d{1,2})|\br(\d{1,2})\b)"
)
_ANG_YES = re.compile(
    r"(?i)^\s*(yes|yeah|yep|approve|approved|do it|ok|okay|y)(\s|[.!]|$)"
)
_ANG_NO = re.compile(
    r"(?i)^\s*(nope|do not|don'?t|negative|skip|no|n)(\s|[.!]|$)"
)
_TERMINATED_STATUSES = {
    "TERMINATED",
    "CANCELLED",
    "CANCELED",
    "MEMBER_TERMINATED",
    "ENDED",
}
_ROOM_WORDS = {
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
}


class SifelyUnavailable(RuntimeError):
    """Sifely Open API cannot run (network, HTTP error, or empty key)."""


@dataclass
class Plan:
    action: str
    reason: str
    update_digest: bool = False
    notify_padsplit: bool = False
    discord_kind: Optional[str] = None
    use_inbound_share: bool = False
    rotate_via_api: bool = False


@dataclass
class RunResult:
    action: str
    reason: str
    discord_posts: List[str] = field(default_factory=list)
    digest_updated: bool = False
    padsplit_notified: int = 0
    events: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class LockMatch:
    lock_id: str
    slug: str
    role: str  # front | back | room | shared
    room: str = ""
    alias: str = ""


@dataclass
class LockEvent:
    kind: str  # move_in | move_out | terminated
    key: str
    house_slug: str
    house_label: str
    room: str
    member: str
    chat_id: str = ""
    occupancy_id: str = ""
    phone: str = ""


def load_environment() -> None:
    load_dotenv(ENV_PATH)


def running_in_ci() -> bool:
    return runtime.running_in_ci()


def live_actions_enabled() -> bool:
    """Default off until LOCK_CODES_ENABLE=1. GitHub Actions / CI must not rotate or post."""
    return runtime.send_enabled("lock_codes")


def sifely_api_key() -> str:
    """Return SIFELY_API_KEY or empty. Never invent a key. Never print it."""
    return (os.getenv("SIFELY_API_KEY") or "").strip()


def firebase_credentials_ready() -> bool:
    """True when a service account is configured. Never invent credentials."""
    return bool(
        (os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip()
        or (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    )


def has_digit_characters(text: str) -> bool:
    return bool(_DIGIT_RUN.search(text or ""))


def redact_for_log(text: str) -> str:
    """Strip keys and digit runs so logs never contain PIN digits or tokens."""
    cleaned = _SK_KEY.sub("SIFELY_API_KEY", text or "")
    cleaned = _SECRET_HEADER.sub(r"\1SIFELY_API_KEY", cleaned)
    cleaned = re.sub(r"\b\d{4,8}\b", "REDACTED", cleaned)
    return cleaned


def hash_passcode(code: str) -> str:
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def generate_passcode() -> str:
    """In-memory PIN only. Tests mock this to REDACTED. Never log the value."""
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


def compact_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def is_spanish_moss_address(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    compact = compact_text(text)
    if "greenhill" in compact and "spanishmoss" not in compact:
        return False
    if "green hill" in text and "spanish" not in text:
        return False
    return "spanishmoss" in compact or ("spanish" in text and "moss" in text)


def is_spanish_moss_back_lock(lock: Dict[str, Any]) -> bool:
    label = f"{lock.get('lockAlias') or ''} {lock.get('lockName') or ''}".lower()
    compact = compact_text(label)
    if "greenhill" in compact and "spanishmoss" not in compact:
        return False
    if "front" in label and "back" not in label:
        return False
    return "spanishmoss" in compact or ("spanish" in label and "moss" in label)


def vacancy_allows_rotate(room: Dict[str, Any]) -> bool:
    """Vacant alone is not enough. Require an empty/turned photo."""
    if not is_spanish_moss_address(room.get("address")):
        return False
    if room.get("vacant") is not True:
        return False
    photos = room.get("move_out_photos") or 0
    try:
        photo_count = int(photos)
    except (TypeError, ValueError):
        photo_count = 0
    return photo_count > 0 or room.get("turned") is True


def vacancy_key(room: Dict[str, Any]) -> str:
    return "|".join(
        [
            str(room.get("property_id") or ""),
            str(room.get("address") or ""),
            str(room.get("room_number") or ""),
            str(room.get("listed_move_out") or ""),
        ]
    )


def pending_auto_rotate_rooms(
    rooms: Sequence[Dict[str, Any]],
    rotated_keys: Iterable[str],
) -> List[Dict[str, Any]]:
    seen = set(rotated_keys)
    pending: List[Dict[str, Any]] = []
    for room in rooms:
        if vacancy_allows_rotate(room) and vacancy_key(room) not in seen:
            pending.append(room)
    return pending


def current_member_threads(messages: Sequence[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Spanish Moss current occupants. lockout/v1 tests depend on this filter."""
    return house_member_threads(messages, "spanish_moss", now)


def house_member_threads(
    messages: Sequence[Dict[str, Any]],
    slug: str,
    now: datetime,
    *,
    exclude_chat_ids: Iterable[str] = (),
) -> List[Dict[str, Any]]:
    """Current occupants at one house. Used for the Ang-yes PadSplit door blast."""
    skip = {str(item) for item in exclude_chat_ids if item}
    current: List[Dict[str, Any]] = []
    for thread in messages:
        if not isinstance(thread, dict):
            continue
        if match_house_slug(_thread_street(thread)) != slug:
            continue
        if not _current_occupant(thread, now):
            continue
        chat_id = str(thread.get("id") or "")
        if not chat_id or chat_id in skip:
            continue
        current.append(thread)
    return current


def parse_sifely_share_code(text: str) -> Optional[str]:
    """Read an inbound #new-tenants Sifely share. Tests use REDACTED, not PINs."""
    if not _mentions_spanish_moss_lock(text):
        return None
    match = _CODE_TOKEN.search(text)
    if not match:
        return None
    token = match.group(1).strip().strip(".,;)")
    if not token:
        return None
    return token


def _mentions_spanish_moss_lock(text: str) -> bool:
    return is_spanish_moss_address(text)


def discord_human_change_text() -> str:
    return DISCORD_HUMAN_CHANGE


def discord_rotated_text() -> str:
    return DISCORD_ROTATED


def need_you_missing_key_text() -> str:
    return NEED_YOU_MISSING_KEY


def need_you_missing_firebase_text() -> str:
    return NEED_YOU_MISSING_FIREBASE


def need_you_missing_phone_text() -> str:
    return NEED_YOU_MISSING_PHONE


def need_you_missing_lock_text() -> str:
    return NEED_YOU_MISSING_LOCK


def discord_room_word(room: Any) -> str:
    """Digit-free room label for Discord. Unknown rooms become 'a room'."""
    key = str(room or "").strip()
    return _ROOM_WORDS.get(key, "a room")


def discord_member_label(name: str) -> str:
    cleaned = re.sub(r"\d+", "", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return cleaned or "a member"


def member_display_name(user: Any) -> str:
    if not isinstance(user, dict):
        return ""
    display = str(user.get("displayName") or "").strip()
    if display:
        return display
    parts = [str(user.get("firstName") or "").strip(), str(user.get("lastName") or "").strip()]
    return " ".join(part for part in parts if part)


def member_host_message(code: str) -> str:
    """PadSplit host inbox may include the new code. Do not log this string."""
    return (
        "Hi, the Spanish Moss back door lock code was rotated. "
        f"The new code is {code}."
    )


def member_move_in_message(house_label: str, room: str, code: str) -> str:
    """PadSplit member message may include the room code. Do not log this string."""
    house = house_label or "your house"
    room_bit = str(room or "").strip() or "your"
    return (
        f"Hi, welcome to {house}. "
        f"Your room {room_bit} lock code is {code}. "
        "This code is for your room lock only."
    )


def member_shared_door_message(house_label: str, codes: Dict[str, str]) -> str:
    """PadSplit housemate blast may include new door codes. Do not log this string."""
    house = house_label or "the house"
    front = (codes.get("front") or "").strip()
    back = (codes.get("back") or "").strip()
    shared = (codes.get("shared") or "").strip()
    if shared and not front and not back:
        return (
            f"Hi, the {house} front and back door code was rotated. "
            f"The new door code is {shared}."
        )
    lines = [f"Hi, the {house} front and back door codes were rotated."]
    if front:
        lines.append(f"Front door: {front}")
    if back:
        lines.append(f"Back door: {back}")
    if shared and shared not in {front, back}:
        lines.append(f"Door code: {shared}")
    return "\n".join(lines)


def discord_move_in_text(house_label: str, room: Any, member: str) -> str:
    text = (
        f"PadSplit Ops: room code set for new move-in at {house_label or 'a house'}, "
        f"{discord_room_word(room)}, member {discord_member_label(member)}. "
        "Codes page updated. No digits posted."
    )
    return assert_discord_outbound_safe(text)


def discord_ask_ang_text(house_label: str, room: Any, member: str, *, kind: str = "move_out") -> str:
    why = "has a termination signal" if kind == "terminated" else "has a move-out signal"
    text = (
        f"PadSplit Ops asking Ang: {discord_member_label(member)} {why} at "
        f"{house_label or 'a house'}, {discord_room_word(room)}. "
        "Ang or Joe: after confirming the room is vacant, directly reply "
        "confirm vacant room reset to authorize that room only. "
        "Ang: separately reply yes or no to this message for shared front and back doors. "
        "Dates, cancellation and payment signals do not authorize a reset."
    )
    return assert_discord_outbound_safe(text)


def discord_ang_yes_text(house_label: str, room: Any, *, shared_rotated: bool) -> str:
    shared = (
        "Shared front and back keycodes were rotated. "
        "Remaining members were messaged on PadSplit."
        if shared_rotated
        else "Shared front and back keycodes were not matched, so they were left unchanged."
    )
    text = (
        f"PadSplit Ops: {house_label or 'a house'} {discord_room_word(room)}. "
        f"{shared} Room lock was already reset to the vacant default. "
        "Codes page updated. No digits posted."
    )
    return assert_discord_outbound_safe(text)


def discord_ang_no_text(house_label: str, room: Any) -> str:
    text = (
        f"PadSplit Ops: {house_label or 'a house'} {discord_room_word(room)}. "
        "Shared front and back keycodes were left unchanged after Ang said no. "
        "Room lock was already reset to the vacant default. No digits posted."
    )
    return assert_discord_outbound_safe(text)


def assert_discord_outbound_safe(text: str) -> str:
    if has_digit_characters(text):
        raise RuntimeError("Refusing Discord outbound: message contains digits")
    return text


def phone_digits(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def phone_last4(phone: str) -> Optional[str]:
    """Last four of a tenant phone. In-memory only. Never log the value."""
    digits = phone_digits(phone)
    if len(digits) < 4:
        return None
    return digits[-4:]


def decide(
    *,
    in_ci: bool,
    api_key_present: bool,
    api_available: bool,
    human_change: bool,
    pending_vacancy: bool,
    inbound_share: bool,
    firebase_ready: bool = True,
) -> Plan:
    """Pure decision table. Side effects stay in execute/run."""
    if in_ci:
        return Plan(action="skip_ci", reason="GitHub Actions / CI must not rotate or post Discord")
    if not api_key_present:
        return Plan(
            action="need_you",
            reason="missing SIFELY_API_KEY",
            discord_kind="need_you",
        )
    if api_available and human_change:
        return Plan(
            action="announce_human",
            reason="human Sifely change is not our move-out rotate",
            discord_kind="human",
            update_digest=False,
            notify_padsplit=False,
        )
    if pending_vacancy and api_available:
        return Plan(
            action="ask_ang",
            reason="move-out asks Ang about shared doors only; room already vacant-default",
            discord_kind="ask_ang",
            update_digest=False,
            notify_padsplit=False,
            rotate_via_api=False,
        )
    if not api_available and inbound_share:
        return Plan(
            action="fallback_share",
            reason="Sifely API cannot run; copy inbound #new-tenants share",
            update_digest=True,
            notify_padsplit=True,
            use_inbound_share=True,
        )
    if not firebase_ready:
        return Plan(
            action="need_you",
            reason="missing Firebase service account",
            discord_kind="need_you_firebase",
        )
    return Plan(action="noop", reason="no pending lock-code decision")


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("Lock-code state unreadable; manual reconciliation required") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Lock-code state invalid; manual reconciliation required")
    empty = _empty_state()
    for key, default in empty.items():
        payload.setdefault(key, default if not isinstance(default, (dict, list)) else type(default)())
    if not isinstance(payload.get("passcode_hashes"), dict):
        payload["passcode_hashes"] = {}
    for list_key in (
        "rotated_vacancy_keys",
        "processed_discord_ids",
        "processed_move_ins",
        "processed_events",
        "pending_ang_asks",
        "seen_occupancies",
    ):
        if not isinstance(payload.get(list_key), list):
            payload[list_key] = []
    if not isinstance(payload.get("operation_stages"), dict):
        payload["operation_stages"] = {}
    return payload


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def _empty_state() -> Dict[str, Any]:
    return {
        "passcode_hashes": {},
        "rotated_vacancy_keys": [],
        "processed_discord_ids": [],
        "last_auto_rotate_hash": "",
        "need_you_sent_on": "",
        "processed_move_ins": [],
        "processed_events": [],
        "pending_ang_asks": [],
        "seen_occupancies": [],
        "operation_stages": {},
    }


def passcode_hashes_from_list(passcodes: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    for item in passcodes:
        pwd_id = str(item.get("keyboardPwdId") or item.get("id") or "")
        code = item.get("keyboardPwd")
        if not pwd_id or not isinstance(code, str) or not code:
            continue
        hashes[pwd_id] = hash_passcode(code)
    return hashes


def detect_human_change(
    current_hashes: Dict[str, str],
    previous_hashes: Dict[str, str],
    last_auto_rotate_hash: str,
) -> bool:
    if not previous_hashes:
        return False
    for pwd_id, digest in current_hashes.items():
        prior = previous_hashes.get(pwd_id)
        if prior and prior != digest and digest != last_auto_rotate_hash:
            return True
    return False


def sifely_headers(api_key: str) -> Dict[str, str]:
    """Raw sk- key. Do not prefix Bearer."""
    return {
        "Authorization": api_key,
        "Accept": "application/json",
    }


def _unwrap_sifely(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and payload.get("code") in (None, 200, "200"):
        return payload.get("data")
    return payload


def sifely_request(
    method: str,
    path: str,
    *,
    api_key: str,
    params: Optional[Dict[str, Any]] = None,
    session: Optional[requests.Session] = None,
) -> Any:
    if not api_key:
        raise SifelyUnavailable("missing SIFELY_API_KEY")
    http = session or requests
    url = f"{SIFELY_BASE}{path}"
    try:
        response = http.request(
            method,
            url,
            headers=sifely_headers(api_key),
            params=params or {},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SifelyUnavailable("Sifely request failed") from exc
    if response.status_code in (401, 403):
        raise SifelyUnavailable("Sifely rejected SIFELY_API_KEY")
    if response.status_code >= 400:
        raise SifelyUnavailable(f"Sifely HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SifelyUnavailable("Sifely returned a non-JSON body") from exc
    return _unwrap_sifely(payload)


def list_locks(api_key: str, *, session: Optional[requests.Session] = None) -> List[Dict[str, Any]]:
    payload = sifely_request(
        "POST",
        SIFELY_LOCK_LIST_PATH,
        api_key=api_key,
        params={"pageNo": "1", "pageSize": "100"},
        session=session,
    )
    rows = payload.get("list") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    clean: List[Dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        # Drop PIN-bearing fields such as noKeyPwd. Never persist them.
        clean.append(
            {
                "lockId": item.get("lockId"),
                "lockAlias": item.get("lockAlias"),
                "lockName": item.get("lockName"),
            }
        )
    return clean


def list_passcodes(
    api_key: str,
    lock_id: Any,
    *,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    payload = sifely_request(
        "GET",
        SIFELY_PASSCODE_LIST_PATH,
        api_key=api_key,
        params={"lockId": lock_id, "pageNo": "1", "pageSize": "50"},
        session=session,
    )
    rows = payload.get("list") if isinstance(payload, dict) else payload
    return [item for item in rows or [] if isinstance(item, dict)]


def change_passcode(
    api_key: str,
    *,
    lock_id: Any,
    keyboard_pwd_id: Any,
    new_code: str,
    session: Optional[requests.Session] = None,
) -> Any:
    return sifely_request(
        "POST",
        SIFELY_PASSCODE_CHANGE_PATH,
        api_key=api_key,
        params={
            "lockId": lock_id,
            "keyboardPwdId": keyboard_pwd_id,
            "newKeyboardPwd": new_code,
            "changeType": SIFELY_CHANGE_TYPE_GATEWAY,
        },
        session=session,
    )


def resolve_lock(locks: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Spanish Moss back door only. lockout_reply obtain path depends on this."""
    configured = (os.getenv("SIFELY_LOCK_ID") or "").strip()
    matches = [lock for lock in locks if is_spanish_moss_back_lock(lock)]
    if configured:
        for lock in locks:
            if str(lock.get("lockId")) != configured:
                continue
            label = f"{lock.get('lockAlias') or ''} {lock.get('lockName') or ''}".lower()
            compact = compact_text(label)
            if "greenhill" in compact and "spanishmoss" not in compact:
                return None
            return lock
        return None
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_keyboard_pwd_id(passcodes: Sequence[Dict[str, Any]]) -> Optional[str]:
    configured = (os.getenv("SIFELY_KEYBOARD_PWD_ID") or "").strip()
    if configured:
        matches = [item for item in passcodes if str(item.get("keyboardPwdId") or item.get("id") or "") == configured]
        if len(matches) != 1:
            return None
        name = str(matches[0].get("keyboardPwdName") or "").lower()
        return None if any(token in name for token in ("admin", "master", "owner")) else configured
    candidates: List[str] = []
    for item in passcodes:
        pwd_id = str(item.get("keyboardPwdId") or item.get("id") or "")
        if not pwd_id:
            continue
        name = str(item.get("keyboardPwdName") or "").lower()
        if any(token in name for token in ("admin", "master", "owner")):
            continue
        candidates.append(pwd_id)
    if len(candidates) == 1:
        return candidates[0]
    return None


def match_house_slug(text: str) -> Optional[str]:
    """Unique house slug from an address or Sifely lock label. None if ambiguous."""
    hay = str(text or "").lower()
    compact = compact_text(text)
    if not hay and not compact:
        return None
    moss = is_spanish_moss_address(text)
    green = "greenhill" in compact or "green hill" in hay
    if moss and green:
        return None
    hits: List[str] = []
    for slug, profile in HOUSE_LOCK_PROFILES.items():
        aliases: Sequence[str] = profile["aliases"]
        if any(alias in hay or compact_text(alias) in compact for alias in aliases):
            hits.append(slug)
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    return None


def classify_sifely_lock(lock: Dict[str, Any]) -> Optional[LockMatch]:
    alias = str(lock.get("lockAlias") or "")
    name = str(lock.get("lockName") or "")
    label = f"{alias} {name}".strip()
    slug = match_house_slug(label)
    if not slug:
        return None
    lock_id = str(lock.get("lockId") or "")
    if not lock_id:
        return None
    lowered = label.lower()
    has_front = "front" in lowered
    has_back = "back" in lowered
    room_match = _ROOM_IN_LABEL.search(label)
    if has_front and has_back:
        return LockMatch(lock_id=lock_id, slug=slug, role="shared", alias=alias)
    if has_front:
        return LockMatch(lock_id=lock_id, slug=slug, role="front", alias=alias)
    if has_back:
        return LockMatch(lock_id=lock_id, slug=slug, role="back", alias=alias)
    if room_match:
        room = room_match.group(1) or room_match.group(2) or ""
        if room:
            return LockMatch(lock_id=lock_id, slug=slug, role="room", room=str(room), alias=alias)
    return None


def inventory_locks(locks: Sequence[Dict[str, Any]]) -> List[LockMatch]:
    found: List[LockMatch] = []
    for lock in locks:
        if not isinstance(lock, dict):
            continue
        match = classify_sifely_lock(lock)
        if match is not None:
            found.append(match)
    return found


def find_lock(
    inventory: Sequence[LockMatch],
    slug: str,
    role: str,
    room: str = "",
) -> Optional[LockMatch]:
    matches: List[LockMatch] = []
    for item in inventory:
        if item.slug != slug:
            continue
        if role == "room":
            if item.role == "room" and str(item.room) == str(room):
                matches.append(item)
            continue
        if item.role == role or item.role == "shared":
            matches.append(item)
    if len(matches) == 1:
        return matches[0]
    if role in {"front", "back"}:
        exact = [item for item in matches if item.role == role]
        if len(exact) == 1:
            return exact[0]
        shared = [item for item in matches if item.role == "shared"]
        if len(shared) == 1:
            return shared[0]
    return None


def codes_field_for(slug: str, role: str, room: str = "") -> str:
    profile = HOUSE_LOCK_PROFILES.get(slug) or {}
    if role == "room":
        digits = re.sub(r"\D", "", str(room or ""))
        return f"r{digits}" if digits else ""
    if role == "front":
        return str(profile.get("front_field") or "front_door")
    if role == "back":
        return str(profile.get("back_field") or "back_door")
    if role == "shared":
        return str(profile.get("front_field") or profile.get("back_field") or "front_back")
    return ""


def send_host_message(
    session: requests.Session,
    creds: Dict[str, str],
    chat_id: str,
    text: str,
    *,
    request_fn=None,
) -> Dict[str, Any]:
    """SEND the host message on the member thread. Reuses the scraper session."""
    request_fn = request_fn or _authed_request
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": f"https://www.padsplit.com/host/communication/{chat_id}",
    }
    resp = request_fn(
        session,
        "POST",
        GRAPHQL_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        json={
            "query": SEND_MESSAGE_MUTATION,
            "variables": {"chatId": chat_id, "text": text, "attachments": []},
        },
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError("sendMessage GraphQL errors")
    sent = (
        ((payload.get("data") or {}).get("messenger") or {}).get("chat") or {}
    ).get("sendMessage") or {}
    if not sent.get("ok"):
        raise RuntimeError("sendMessage did not send")
    return sent


def post_ops_discord(text: str, *, token: Optional[str] = None, channel: Optional[str] = None) -> Optional[Dict[str, Any]]:
    safe = assert_discord_outbound_safe(text)
    token = token or (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    channel = channel or (os.getenv("DISCORD_CHANNEL_ID") or "").strip()
    if not token or not channel:
        _log("DISCORD_BOT_TOKEN or DISCORD_CHANNEL_ID missing; skip PadSplit Ops post")
        return None
    response = requests.post(
        f"{DISCORD_API_BASE}/channels/{channel}/messages",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        json={"content": safe},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def automations_channel_id() -> str:
    return (
        (os.getenv("DISCORD_AUTOMATIONS_CHANNEL_ID") or "").strip()
        or DISCORD_AUTOMATIONS_CHANNEL_ID
    )


def post_automations_discord(
    text: str,
    *,
    token: Optional[str] = None,
    channel: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    safe = assert_discord_outbound_safe(text)
    token = token or (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    channel = channel or automations_channel_id()
    if not token or not channel:
        _log("DISCORD_BOT_TOKEN or automations channel missing; skip PadSplit Ops post")
        return None
    return post_ops_discord(safe, token=token, channel=channel)


def fetch_new_tenants_messages(token: str) -> List[Dict[str, Any]]:
    return fetch_channel_messages(DISCORD_NEW_TENANTS_CHANNEL_ID, token)


def fetch_channel_messages(channel_id: str, token: str, *, limit: int = 50) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {token}"},
        params={"limit": limit},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    messages = response.json() or []
    return list(reversed(messages))


def classify_ang_reply(text: str) -> Optional[str]:
    """Return yes/no from a digit-free Ang reply. Digit-bearing replies are ignored."""
    content = (text or "").strip()
    if not content or has_digit_characters(content):
        return None
    if re.fullmatch(r"(?i)(yes|yeah|yep|approve|approved|do it|ok|okay|y)[.!]?", content):
        return "yes"
    if re.fullmatch(r"(?i)(nope|do not|don't|dont|negative|skip|no|n)[.!]?", content):
        return "no"
    return None


def parse_ang_reply(messages: Sequence[Dict[str, Any]], ask_message_id: str) -> Optional[str]:
    """Accept only a direct human reply from the configured approval owner."""
    ask_id = str(ask_message_id or "")
    owner_id = (os.getenv("LOCK_CODES_APPROVER_USER_ID") or "").strip()
    if not ask_id or not owner_id:
        return None
    for message in messages or []:
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        if str(author.get("id") or "") != owner_id or author.get("bot") or message.get("webhook_id"):
            continue
        ref = message.get("message_reference") if isinstance(message.get("message_reference"), dict) else {}
        if str(ref.get("message_id") or "") != ask_id:
            continue
        decision = classify_ang_reply(str(message.get("content") or ""))
        if decision:
            return decision
    return None


def load_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_occupancy_rooms() -> List[Dict[str, Any]]:
    for path in (
        ROOT_DIR / "docs" / "data" / "occupancy.json",
        ROOT_DIR / "padsplit_scraper" / "output" / "occupancy.json",
    ):
        payload = load_json_file(path)
        if isinstance(payload, dict) and isinstance(payload.get("rooms"), list):
            return [row for row in payload["rooms"] if isinstance(row, dict)]
    return []


def load_host_messages() -> List[Dict[str, Any]]:
    for path in (
        ROOT_DIR / "docs" / "data" / "latest.json",
        ROOT_DIR / "padsplit_scraper" / "output" / "latest.json",
    ):
        payload = load_json_file(path)
        if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
            return [row for row in payload["messages"] if isinstance(row, dict)]
    return []


def update_codes_records(slug: str, fields: Dict[str, Any]) -> bool:
    """Merge fields into Firestore property_codes/{slug} (codes.html). Fail closed."""
    if not firebase_credentials_ready():
        _log("FIREBASE_SERVICE_ACCOUNT_JSON missing; fail closed, skip codes page update")
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        _log("firebase-admin missing; fail closed, skip codes page update")
        return False

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    google_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not firebase_admin._apps:
        if service_account_json:
            firebase_admin.initialize_app(credentials.Certificate(json.loads(service_account_json)))
        elif google_credentials:
            firebase_admin.initialize_app(credentials.Certificate(google_credentials))
        else:
            _log("FIREBASE_SERVICE_ACCOUNT_JSON missing; fail closed, skip codes page update")
            return False
    client = firestore.client()
    payload = dict(fields)
    payload["updatedAt"] = datetime.now(timezone.utc).isoformat()
    client.collection(CODES_COLLECTION).document(slug).set(payload, merge=True)
    return True


def update_codes_page(code: str, *, slug: str = PROPERTY_SLUG, field: str = LOCK_FIELD) -> bool:
    """Write one lock field. lockout_reply still calls this with the SM back-door code."""
    return update_codes_records(slug, {field: code})


def occupancy_phone(thread: Dict[str, Any], fetch_fn: Optional[Callable[[Dict[str, Any]], str]] = None) -> str:
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    user = occupancy.get("user") if isinstance(occupancy.get("user"), dict) else {}
    for key in ("phone", "phoneNumber", "mobilePhone", "mobile"):
        value = user.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if fetch_fn is not None:
        return str(fetch_fn(thread) or "").strip()
    return ""


def fetch_occupancy_phone(
    session: requests.Session,
    creds: Dict[str, str],
    occupancy_id: str,
    *,
    request_fn=None,
) -> str:
    """Live PadSplit phone lookup. Never log the returned value."""
    if not occupancy_id:
        return ""
    request_fn = request_fn or _authed_request
    resp = request_fn(
        session,
        "POST",
        GRAPHQL_URL,
        creds=creds,
        login_fn=login,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        json={"query": OCCUPANCY_PHONE_QUERY, "variables": {"id": occupancy_id}},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        return ""
    user = (((payload.get("data") or {}).get("occupancy") or {}).get("user") or {})
    if not isinstance(user, dict):
        return ""
    for key in ("phone", "phoneNumber"):
        value = user.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _fetch_phone_for_thread(thread: Dict[str, Any]) -> str:
    occupancy_id = _thread_occupancy_id(thread)
    if not occupancy_id:
        return ""
    try:
        creds = load_credentials()
        with create_session() as session:
            login(session, creds["email"], creds["password"], force=False)
            return fetch_occupancy_phone(session, creds, occupancy_id)
    except Exception:
        _log("Need you: tenant phone lookup unavailable")
        return ""


def _log(message: str) -> None:
    sys.stderr.write(f"[lock-codes] {redact_for_log(message)}\n")


def _date_only(value: Any):
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        from datetime import date

        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_recent_date(value: Any, now: datetime, *, days: int = RECENT_EVENT_DAYS) -> bool:
    day = _date_only(value)
    if day is None:
        return False
    today = now.astimezone(CT).date()
    delta = (today - day).days
    return 0 <= delta <= days


def _is_recent_dt(value: Any, now: datetime, *, days: int = RECENT_EVENT_DAYS) -> bool:
    parsed = _parse_dt(value)
    if parsed is None:
        return False
    delta = now - parsed
    return timedelta(0) <= delta <= timedelta(days=days)


def _thread_street(thread: Dict[str, Any]) -> str:
    prop = thread.get("property") if isinstance(thread.get("property"), dict) else {}
    address = prop.get("address") if isinstance(prop.get("address"), dict) else {}
    return str(address.get("street1") or address.get("full_street") or "")


def _thread_room(thread: Dict[str, Any]) -> str:
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    room = occupancy.get("room") if isinstance(occupancy.get("room"), dict) else {}
    value = room.get("roomNumber")
    if value in (None, ""):
        return ""
    return str(value).strip()


def _thread_occupancy_id(thread: Dict[str, Any]) -> str:
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    return str(occupancy.get("id") or "")


def _current_occupant(thread: Dict[str, Any], now: datetime) -> bool:
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    user = occupancy.get("user")
    if not isinstance(user, dict) or not user:
        return False
    move_out = _date_only(occupancy.get("moveOutDate"))
    today = now.astimezone(CT).date()
    move_in = _date_only(occupancy.get("moveInDate"))
    if move_in is not None and move_in > today:
        return False
    if move_out is not None and move_out < today:
        return False
    if thread.get("isCancelled") is True:
        return False
    return True


def _terminated_status(thread: Dict[str, Any]) -> bool:
    if thread.get("isCancelled") is True:
        return True
    last = thread.get("lastMessage") if isinstance(thread.get("lastMessage"), dict) else {}
    booking = last.get("bookingStatus") if isinstance(last.get("bookingStatus"), dict) else {}
    status = str(booking.get("status") or "").upper()
    if status in _TERMINATED_STATUSES:
        return True
    for message in thread.get("recent_messages") or []:
        if not isinstance(message, dict):
            continue
        row = message.get("bookingStatus") if isinstance(message.get("bookingStatus"), dict) else {}
        if str(row.get("status") or "").upper() in _TERMINATED_STATUSES:
            return True
    return False


def _event_house(thread: Dict[str, Any]) -> Tuple[str, str]:
    slug = match_house_slug(_thread_street(thread))
    if not slug:
        return "", ""
    return slug, str(HOUSE_LOCK_PROFILES[slug]["label"])


def move_in_event_key(thread: Dict[str, Any]) -> str:
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    slug, _label = _event_house(thread)
    return "|".join(
        [
            "move_in",
            _thread_occupancy_id(thread) or str(thread.get("id") or ""),
            slug,
            _thread_room(thread),
            str(occupancy.get("moveInDate") or ""),
        ]
    )


def move_out_event_key(thread: Dict[str, Any], *, kind: str) -> str:
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    slug, _label = _event_house(thread)
    return "|".join(
        [
            kind,
            _thread_occupancy_id(thread) or str(thread.get("id") or ""),
            slug,
            _thread_room(thread),
            str(occupancy.get("moveOutDate") or ""),
        ]
    )


def collect_move_in_events(
    messages: Sequence[Dict[str, Any]],
    now: datetime,
    processed: Iterable[str],
    pending: Iterable[str] = (),
) -> List[LockEvent]:
    seen = set(processed)
    pending_keys = set(pending)
    events: List[LockEvent] = []
    for thread in messages:
        if not isinstance(thread, dict):
            continue
        if not _current_occupant(thread, now):
            continue
        occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
        if not _is_recent_date(occupancy.get("moveInDate"), now) and move_in_event_key(thread) not in pending_keys:
            continue
        slug, label = _event_house(thread)
        room = _thread_room(thread)
        if not slug or not room:
            continue
        key = move_in_event_key(thread)
        if key in seen:
            continue
        events.append(
            LockEvent(
                kind="move_in",
                key=key,
                house_slug=slug,
                house_label=label,
                room=room,
                member=member_display_name(occupancy.get("user")),
                chat_id=str(thread.get("id") or ""),
                occupancy_id=_thread_occupancy_id(thread),
                phone=occupancy_phone(thread),
            )
        )
        seen.add(key)
    return events


def collect_move_out_events(
    messages: Sequence[Dict[str, Any]],
    rooms: Sequence[Dict[str, Any]],
    now: datetime,
    processed: Iterable[str],
    pending_keys: Iterable[str],
) -> List[LockEvent]:
    skip = set(processed) | set(pending_keys)
    events: List[LockEvent] = []
    seen_keys = set()
    seen_rooms: set[tuple[str, str]] = set()

    def _add(event: LockEvent) -> None:
        room_id = (event.house_slug, str(event.room))
        if event.key in skip or event.key in seen_keys or room_id in seen_rooms:
            return
        if not event.house_slug or not event.room:
            return
        events.append(event)
        seen_keys.add(event.key)
        seen_rooms.add(room_id)

    for thread in messages:
        if not isinstance(thread, dict):
            continue
        occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
        slug, label = _event_house(thread)
        room = _thread_room(thread)
        member = member_display_name(occupancy.get("user"))
        terminated = _terminated_status(thread)
        move_out = occupancy.get("moveOutDate")
        recent_out = _is_recent_date(move_out, now)
        last = thread.get("lastMessage") if isinstance(thread.get("lastMessage"), dict) else {}
        recent_activity = _is_recent_dt(last.get("created"), now)
        if terminated and (recent_out or recent_activity):
            _add(
                LockEvent(
                    kind="terminated",
                    key=move_out_event_key(thread, kind="terminated"),
                    house_slug=slug,
                    house_label=label,
                    room=room,
                    member=member,
                    chat_id=str(thread.get("id") or ""),
                    occupancy_id=_thread_occupancy_id(thread),
                )
            )
            continue
        today = now.astimezone(CT).date()
        out_day = _date_only(move_out)
        if out_day is not None and out_day <= today and recent_out:
            _add(
                LockEvent(
                    kind="move_out",
                    key=move_out_event_key(thread, kind="move_out"),
                    house_slug=slug,
                    house_label=label,
                    room=room,
                    member=member,
                    chat_id=str(thread.get("id") or ""),
                    occupancy_id=_thread_occupancy_id(thread),
                )
            )

    for room in rooms:
        if not isinstance(room, dict) or room.get("vacant") is not True:
            continue
        if not _is_recent_date(room.get("listed_move_out"), now):
            continue
        slug = match_house_slug(str(room.get("address") or ""))
        if not slug:
            continue
        room_number = str(room.get("room_number") or "").strip()
        key = "|".join(
            [
                "move_out",
                str(room.get("property_id") or ""),
                slug,
                room_number,
                str(room.get("listed_move_out") or ""),
            ]
        )
        _add(
            LockEvent(
                kind="move_out",
                key=key,
                house_slug=slug,
                house_label=str(HOUSE_LOCK_PROFILES[slug]["label"]),
                room=room_number,
                member="a member",
            )
        )
    return events


def _discord_text_for(kind: Optional[str]) -> Optional[str]:
    if kind == "need_you":
        return need_you_missing_key_text()
    if kind == "need_you_firebase":
        return need_you_missing_firebase_text()
    if kind == "need_you_phone":
        return need_you_missing_phone_text()
    if kind == "need_you_lock":
        return need_you_missing_lock_text()
    if kind == "human":
        return discord_human_change_text()
    if kind == "rotated":
        return discord_rotated_text()
    return None


def _mark_list(state: Dict[str, Any], field: str, value: str) -> None:
    items = [str(item) for item in (state.get(field) or []) if item]
    if value and value not in items:
        items.append(value)
    state[field] = items[-200:]


def _pending_keys(state: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    for row in state.get("pending_ang_asks") or []:
        if isinstance(row, dict) and row.get("event_key"):
            keys.append(str(row["event_key"]))
    return keys


def _handled_rooms(state: Dict[str, Any]) -> set[tuple[str, str]]:
    rooms: set[tuple[str, str]] = set()
    for row in state.get("pending_ang_asks") or []:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("house_slug") or "")
        room = str(row.get("room") or "")
        if slug and room:
            rooms.add((slug, room))
    for raw in state.get("processed_events") or []:
        parts = str(raw).split("|")
        if len(parts) >= 4 and parts[0] in {"move_out", "terminated", "vacancy"}:
            slug = parts[2]
            room = parts[3]
            if slug and room:
                rooms.add((slug, room))
    return rooms


def _need_you_once(
    state: Dict[str, Any],
    now: datetime,
    kind: str,
    poster: Callable[[str], Any],
    result: RunResult,
    *,
    dry_run: bool,
) -> None:
    today = now.astimezone(CT).date().isoformat()
    stamp = f"{kind}:{today}"
    if state.get("need_you_sent_on") == stamp and not dry_run:
        return
    text = _discord_text_for(kind)
    if not text:
        return
    safe = assert_discord_outbound_safe(text)
    if not dry_run:
        poster(safe)
        state["need_you_sent_on"] = stamp
    result.discord_posts.append(safe)


def _serialized_run(function):
    @functools.wraps(function)
    def guarded(*args, **kwargs):
        state_path = Path(kwargs.get("state_path", STATE_PATH))
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with state_path.with_suffix(state_path.suffix + ".lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return RunResult(action="busy", reason="another lock-code run owns this state")
            try:
                return function(*args, **kwargs)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    return guarded


@_serialized_run
def run(
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    sifely_session: Optional[requests.Session] = None,
    occupancy_rooms: Optional[List[Dict[str, Any]]] = None,
    host_messages: Optional[List[Dict[str, Any]]] = None,
    inbound_messages: Optional[List[Dict[str, Any]]] = None,
    post_discord: Optional[Callable[[str], Any]] = None,
    update_digest: Optional[Callable[[str], bool]] = None,
    update_records: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
    notify_members: Optional[Callable[[str], int]] = None,
    notify_member: Optional[Callable[[str, str], int]] = None,
    generate_code: Optional[Callable[[], str]] = None,
    fetch_phone: Optional[Callable[[Dict[str, Any]], str]] = None,
    fetch_discord: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    locks: Optional[List[Dict[str, Any]]] = None,
    passcodes_by_lock: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    change_fn: Optional[Callable[..., Any]] = None,
    firebase_ready: Optional[bool] = None,
    state_path: Path = STATE_PATH,
) -> RunResult:
    load_environment()
    current = now or datetime.now(timezone.utc)
    if running_in_ci() and not dry_run:
        return RunResult(action="skip_ci", reason="GitHub Actions / CI must not rotate or post Discord")
    if not live_actions_enabled() and not dry_run:
        return RunResult(action="disabled", reason="LOCK_CODES_ENABLE is off")

    state = load_state(state_path)
    rooms = occupancy_rooms if occupancy_rooms is not None else load_occupancy_rooms()
    messages = host_messages if host_messages is not None else load_host_messages()
    pending_rooms = pending_auto_rotate_rooms(rooms, state.get("rotated_vacancy_keys") or [])

    api_key = sifely_api_key()
    ready = firebase_credentials_ready() if firebase_ready is None else bool(firebase_ready)
    inventory: List[LockMatch] = []
    api_available = False

    if api_key and not running_in_ci():
        try:
            lock_rows = locks if locks is not None else list_locks(api_key, session=sifely_session)
            inventory = inventory_locks(lock_rows)
            api_available = True
        except SifelyUnavailable as exc:
            _log(f"Sifely API cannot run: {exc}")
            api_available = False

    plan = decide(
        in_ci=running_in_ci(),
        api_key_present=bool(api_key),
        api_available=api_available or bool(inventory),
        human_change=False,
        pending_vacancy=False,
        inbound_share=False,
        firebase_ready=True,
    )
    result = RunResult(action=plan.action, reason=plan.reason)
    _log(f"{plan.action} ({plan.reason})")

    if plan.action == "skip_ci":
        return result

    poster = post_discord or (lambda text: None if dry_run else post_automations_discord(text))
    records_fn = update_records or (
        (lambda slug, fields: False if dry_run else update_codes_records(slug, fields))
    )
    digest_fn = update_digest
    member_one = notify_member
    member_all = notify_members
    new_code_fn = generate_code or generate_passcode
    changer = change_fn or (
        (
            lambda **kwargs: change_passcode(
                api_key,
                session=sifely_session,
                **kwargs,
            )
        )
        if api_key
        else None
    )

    if plan.action == "need_you":
        kind = plan.discord_kind or "need_you"
        _need_you_once(state, current, kind, poster, result, dry_run=dry_run)
        if not dry_run:
            save_state(state, state_path)
        return result

    def _passcodes_for(lock_id: str) -> List[Dict[str, Any]]:
        if passcodes_by_lock is not None:
            return list(passcodes_by_lock.get(str(lock_id)) or [])
        if not api_key:
            return []
        return list_passcodes(api_key, lock_id, session=sifely_session)

    def checkpoint() -> None:
        if not dry_run:
            save_state(state, state_path)

    def operation(key: str) -> Dict[str, Any]:
        return state.setdefault("operation_stages", {}).setdefault(key, {})

    def _rotate_lock(match: LockMatch, new_code: str, event_key: str) -> bool:
        if dry_run:
            return True
        if changer is None:
            return False
        stage = operation(event_key).setdefault("locks", {}).setdefault(match.lock_id, {})
        wanted_hash = hash_passcode(new_code)
        if stage.get("done"):
            return stage.get("hash") == wanted_hash
        rows = _passcodes_for(match.lock_id)
        pwd_id = resolve_keyboard_pwd_id(rows)
        if stage.get("started"):
            # A previous attempt may have succeeded before the process stopped.
            # Read back; never repeat an ambiguous physical action.
            matches = [row for row in rows if str(row.get("keyboardPwdId") or row.get("id") or "") == stage.get("pwd_id")]
            if len(matches) == 1 and hash_passcode(str(matches[0].get("keyboardPwd") or "")) == stage.get("hash") == wanted_hash:
                stage["done"] = True
                checkpoint()
                return True
            _log("Need you: interrupted lock operation requires readback reconciliation")
            return False
        if not pwd_id:
            _log("Need you: could not resolve keyboard passcode id")
            return False
        stage.update(started=True, pwd_id=pwd_id, hash=wanted_hash)
        checkpoint()
        try:
            changer(lock_id=match.lock_id, keyboard_pwd_id=pwd_id, new_code=new_code)
        except SifelyUnavailable as exc:
            _log(f"rotate failed: {exc}")
            return False
        stage["done"] = True
        checkpoint()
        return True

    def _recover_code(match: LockMatch, event_key: str) -> Optional[str]:
        stage = operation(event_key).get("locks", {}).get(match.lock_id, {})
        if not stage.get("started"):
            return None
        rows = _passcodes_for(match.lock_id)
        matches = [row for row in rows if str(row.get("keyboardPwdId") or row.get("id") or "") == stage.get("pwd_id")]
        if len(matches) == 1:
            code = str(matches[0].get("keyboardPwd") or "")
            if code and hash_passcode(code) == stage.get("hash"):
                return code
        return None

    def _write_fields(slug: str, fields: Dict[str, Any], event_key: str) -> bool:
        clean = {key: value for key, value in fields.items() if key and value}
        if not clean:
            return False
        fingerprint = hash_passcode(json.dumps(clean, sort_keys=True))
        writes = operation(event_key).setdefault("writes", [])
        if fingerprint in writes:
            return True
        if digest_fn is not None and slug == PROPERTY_SLUG and LOCK_FIELD in clean:
            digest_fn(str(clean[LOCK_FIELD]))
        ok = bool(records_fn(slug, clean))
        if ok:
            writes.append(fingerprint)
            checkpoint()
        return ok

    _ = inbound_messages
    _process_pending_asks(
        state=state,
        result=result,
        inventory=inventory,
        ready=ready,
        now=current,
        dry_run=dry_run,
        poster=poster,
        fetch_discord=fetch_discord,
        rotate_lock=_rotate_lock,
        write_fields=_write_fields,
        new_code_fn=new_code_fn,
        messages=messages,
        notify_one=member_one,
        checkpoint=checkpoint,
        recover_code=_recover_code,
    )

    if api_key and api_available:
        _process_move_ins(
            messages=messages,
            state=state,
            result=result,
            inventory=inventory,
            ready=ready,
            now=current,
            dry_run=dry_run,
            poster=poster,
            fetch_phone=fetch_phone or (None if dry_run else _fetch_phone_for_thread),
            rotate_lock=_rotate_lock,
            write_fields=_write_fields,
            notify_one=member_one,
            notify_all=member_all,
            checkpoint=checkpoint,
        )
        _process_move_outs(
            messages=messages,
            rooms=rooms,
            pending_rooms=pending_rooms,
            state=state,
            result=result,
            inventory=inventory,
            ready=ready,
            now=current,
            dry_run=dry_run,
            poster=poster,
            rotate_lock=_rotate_lock,
            write_fields=_write_fields,
            checkpoint=checkpoint,
        )

    if result.events:
        result.action = result.events[-1]
        result.reason = f"{len(result.events)} lock-code event(s)"
    elif result.action == "noop":
        result.reason = "no new move-in or move-out events"

    if not dry_run:
        save_state(state, state_path)
    return result


def _notify_once(stages, chat_id, body, notify_one, dry_run, checkpoint) -> bool:
    """Keep completed deliveries; uncertain interrupted sends require reconciliation."""
    delivery = stages.setdefault("notifications", {}).setdefault(chat_id, {})
    if delivery.get("done"):
        return True
    if delivery.get("started"):
        _log("Need you: interrupted member notification requires reconciliation")
        return False
    if dry_run:
        return True
    delivery["started"] = True
    checkpoint()
    try:
        sent = int(notify_one(chat_id, body) if notify_one is not None else _notify_one_member(chat_id, body))
    except Exception:
        _log("Need you: member delivery outcome uncertain; reconciliation required")
        return False
    delivery["done"] = sent > 0
    delivery["started"] = False
    checkpoint()
    return sent > 0


def parse_room_reset_approval(messages, ask_message_id) -> Optional[str]:
    """An exact action, scoped by direct reply, from verified Ang or Joe only."""
    owners = {(os.getenv(name) or "").strip() for name in
              ("LOCK_CODES_APPROVER_USER_ID", "LOCK_CODES_JOE_USER_ID")}
    owners.discard("")
    if not ask_message_id or not owners:
        return None
    for message in messages or []:
        author = message.get("author") or {}
        ref = message.get("message_reference") or {}
        if not isinstance(author, dict) or not isinstance(ref, dict):
            continue
        if str(author.get("id") or "") not in owners or author.get("bot") or message.get("webhook_id"):
            continue
        if str(ref.get("message_id") or "") != ask_message_id:
            continue
        if str(message.get("content") or "").strip().lower() == "confirm vacant room reset":
            return str(author["id"])
    return None


def _process_pending_asks(
    *, state, result, inventory, ready, now, dry_run, poster, fetch_discord,
    rotate_lock, write_fields, new_code_fn, messages, notify_one, checkpoint,
    recover_code,
) -> None:
    pending = state.get("pending_ang_asks") or []
    if not pending:
        return
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    rows = fetch_discord() if fetch_discord is not None else None
    if rows is None and token and not dry_run:
        try:
            rows = fetch_channel_messages(automations_channel_id(), token)
        except requests.RequestException:
            rows = []
    rows = rows or []
    for ask in list(pending):
        event_key = str(ask.get("event_key") or "")
        stages = state.setdefault("operation_stages", {}).setdefault(event_key, {})
        ask_id = str(ask.get("discord_message_id") or "")
        if not ask.get("room_reset_approved_by"):
            author = parse_room_reset_approval(rows, ask_id)
            if author:
                ask["room_reset_approved_by"] = author
                checkpoint()
        if not ask.get("door_decision"):
            decision = parse_ang_reply(rows, ask_id)
            if decision:
                ask["door_decision"] = decision
                checkpoint()
        if not ask.get("room_reset"):
            if not ask.get("room_reset_approved_by"):
                continue
            # A later tenancy invalidates the old vacancy action even if its
            # earlier confirmation is still in the queue.
            occupied_again = any(
                _thread_room(thread) == str(ask.get("room") or "")
                for thread in house_member_threads(messages, str(ask.get("house_slug") or ""), now,
                    exclude_chat_ids=(str(ask.get("chat_id") or ""),))
            )
            if occupied_again:
                continue
            if not _reset_vacated_room(ask, inventory=inventory, ready=ready, now=now,
                    dry_run=dry_run, poster=poster, result=result, rotate_lock=rotate_lock,
                    write_fields=write_fields, state=state):
                continue
            ask["room_reset"] = True
            checkpoint()
        decision = ask.get("door_decision")
        if decision is None:
            continue
        slug, label, room = (str(ask.get(key) or "") for key in ("house_slug", "house_label", "room"))
        if decision == "yes":
            if not ready:
                continue
            profile = HOUSE_LOCK_PROFILES.get(slug) or {}
            targets = []
            mapping_ok = True
            for role in ("front", "back"):
                if not profile.get("has_" + role):
                    continue
                match = find_lock(inventory, slug, role)
                if match is None:
                    mapping_ok = False
                    break
                if match.lock_id not in {m.lock_id for m in targets}:
                    targets.append(match)
            if not mapping_ok or not targets:
                continue
            fields, codes = {}, {}
            all_rotated = True
            for match in targets:
                lock_stage = stages.get("locks", {}).get(match.lock_id, {})
                code = recover_code(match, event_key) if lock_stage.get("started") else new_code_fn()
                if not code or not rotate_lock(match, code, event_key):
                    all_rotated = False
                    break
                fields[codes_field_for(slug, match.role, room)] = code
                codes[match.role] = code
            if not all_rotated:
                continue
            if not _write_or_skip(write_fields, slug, fields, result, event_key):
                continue
            notified = True
            for thread in house_member_threads(messages, slug, now,
                    exclude_chat_ids=(str(ask.get("chat_id") or ""),)):
                chat_id = str(thread.get("id") or "")
                if not chat_id:
                    notified = False
                    continue
                was_done = stages.get("notifications", {}).get(chat_id, {}).get("done")
                ok = _notify_once(stages, chat_id, member_shared_door_message(label, codes),
                                  notify_one, dry_run, checkpoint)
                if ok and not was_done:
                    result.padsplit_notified += 1
                notified = notified and ok
            if not notified:
                continue
        text = (discord_ang_yes_text(label, room, shared_rotated=True) if decision == "yes"
                else discord_ang_no_text(label, room))
        if not dry_run:
            poster(text)
        result.discord_posts.append(text)
        result.events.append("ang_" + decision)
        _mark_list(state, "processed_events", event_key)
        pending.remove(ask)
        checkpoint()


def _reset_vacated_room(
    event_or_ask: Any,
    *,
    inventory: Sequence[LockMatch],
    ready: bool,
    now: datetime,
    dry_run: bool,
    poster: Callable[[str], Any],
    result: RunResult,
    rotate_lock: Callable[[LockMatch, str, str], bool],
    write_fields: Callable[[str, Dict[str, Any], str], bool],
    state: Dict[str, Any],
) -> bool:
    """Execute an explicitly approved vacant-room reset and record completed stages."""
    if isinstance(event_or_ask, LockEvent):
        house_slug = event_or_ask.house_slug
        room = event_or_ask.room
        event_key = event_or_ask.key
    else:
        house_slug = str(event_or_ask.get("house_slug") or "")
        room = str(event_or_ask.get("room") or "")
        event_key = str(event_or_ask.get("event_key") or "")
    if not ready:
        _need_you_once(state, now, "need_you_firebase", poster, result, dry_run=dry_run)
        return False
    match = find_lock(inventory, house_slug, "room", room)
    if match is None or not rotate_lock(match, VACANT_ROOM_DEFAULT, event_key):
        _need_you_once(state, now, "need_you_lock", poster, result, dry_run=dry_run)
        return False
    field_name = codes_field_for(house_slug, "room", room)
    if field_name and not _write_or_skip(write_fields, house_slug, {field_name: VACANT_ROOM_DEFAULT}, result, event_key):
        _need_you_once(state, now, "need_you_firebase", poster, result, dry_run=dry_run)
        return False
    return True


def _blast_house_door_codes(
    messages: Sequence[Dict[str, Any]],
    house_slug: str,
    house_label: str,
    codes: Dict[str, str],
    now: datetime,
    *,
    dry_run: bool,
    notify_one: Optional[Callable[[str, str], int]],
    exclude_chat_ids: Iterable[str] = (),
) -> int:
    """PadSplit-blast remaining housemates. Digits allowed here only. Never log."""
    if not any(codes.values()):
        return 0
    body = member_shared_door_message(house_label, codes)
    sent = 0
    for thread in house_member_threads(messages, house_slug, now, exclude_chat_ids=exclude_chat_ids):
        chat_id = str(thread.get("id") or "")
        if not chat_id:
            continue
        if notify_one is not None:
            sent += int(notify_one(chat_id, body))
        elif not dry_run:
            sent += _notify_one_member(chat_id, body)
    return sent


def _write_or_skip(
    write_fields: Callable[[str, Dict[str, Any], str], bool],
    slug: str,
    fields: Dict[str, Any],
    result: RunResult,
    event_key: str,
) -> bool:
    ok = write_fields(slug, fields, event_key)
    if ok:
        result.digest_updated = True
    return ok


def _process_move_ins(
    *,
    messages: Sequence[Dict[str, Any]],
    state: Dict[str, Any],
    result: RunResult,
    inventory: Sequence[LockMatch],
    ready: bool,
    now: datetime,
    dry_run: bool,
    poster: Callable[[str], Any],
    fetch_phone: Optional[Callable[[Dict[str, Any]], str]],
    rotate_lock: Callable[[LockMatch, str, str], bool],
    write_fields: Callable[[str, Dict[str, Any], str], bool],
    notify_one: Optional[Callable[[str, str], int]],
    notify_all: Optional[Callable[[str], int]],
    checkpoint: Callable[[], None],
) -> None:
    events = collect_move_in_events(messages, now, state.get("processed_move_ins") or [], state.get("operation_stages") or {})
    if not events:
        return
    if not ready:
        _need_you_once(state, now, "need_you_firebase", poster, result, dry_run=dry_run)
        return
    threads_by_id = {str(thread.get("id") or ""): thread for thread in messages if isinstance(thread, dict)}
    for event in events:
        thread = threads_by_id.get(event.chat_id) or {}
        phone = event.phone or occupancy_phone(thread, fetch_phone)
        last4 = phone_last4(phone)
        if not last4:
            _need_you_once(state, now, "need_you_phone", poster, result, dry_run=dry_run)
            continue
        match = find_lock(inventory, event.house_slug, "room", event.room)
        if match is None or not rotate_lock(match, last4, event.key):
            _need_you_once(state, now, "need_you_lock", poster, result, dry_run=dry_run)
            continue
        field_name = codes_field_for(event.house_slug, "room", event.room)
        if not _write_or_skip(write_fields, event.house_slug, {field_name: last4} if field_name else {}, result, event.key):
            _need_you_once(state, now, "need_you_firebase", poster, result, dry_run=dry_run)
            continue
        body = member_move_in_message(event.house_label, event.room, last4)
        stages = state.setdefault("operation_stages", {}).setdefault(event.key, {})
        if not event.chat_id:
            continue
        notifier = notify_one
        if notifier is None and notify_all is not None:
            notifier = lambda chat_id, body: notify_all(last4)
        was_done = stages.get("notifications", {}).get(event.chat_id, {}).get("done")
        if not _notify_once(stages, event.chat_id, body, notifier, dry_run, checkpoint):
            continue
        if not was_done:
            result.padsplit_notified += 1
        text = discord_move_in_text(event.house_label, event.room, event.member)
        if not dry_run:
            poster(text)
        result.discord_posts.append(text)
        result.events.append("move_in")
        _mark_list(state, "processed_move_ins", event.key)
        if event.occupancy_id:
            _mark_list(state, "seen_occupancies", event.occupancy_id)


def _process_move_outs(
    *,
    messages: Sequence[Dict[str, Any]],
    rooms: Sequence[Dict[str, Any]],
    pending_rooms: Sequence[Dict[str, Any]],
    state: Dict[str, Any],
    result: RunResult,
    inventory: Sequence[LockMatch],
    ready: bool,
    now: datetime,
    dry_run: bool,
    poster: Callable[[str], Any],
    rotate_lock: Callable[[LockMatch, str, str], bool],
    write_fields: Callable[[str, Dict[str, Any], str], bool],
    checkpoint: Callable[[], None],
) -> None:
    asked_rooms = _handled_rooms(state)
    events = [
        event
        for event in collect_move_out_events(
            messages,
            rooms,
            now,
            state.get("processed_events") or [],
            _pending_keys(state),
        )
        if (event.house_slug, str(event.room)) not in asked_rooms
    ]
    asked_rooms.update((event.house_slug, str(event.room)) for event in events)
    for room in pending_rooms:
        slug = match_house_slug(str(room.get("address") or ""))
        room_number = str(room.get("room_number") or "")
        if not slug or (slug, room_number) in asked_rooms:
            continue
        key = "vacancy|" + vacancy_key(room)
        if key in set(state.get("processed_events") or []) or key in set(_pending_keys(state)):
            continue
        events.append(
            LockEvent(
                kind="move_out",
                key=key,
                house_slug=slug,
                house_label=str(HOUSE_LOCK_PROFILES[slug]["label"]),
                room=room_number,
                member="a member",
            )
        )
        asked_rooms.add((slug, room_number))
    for event in events:
        text = discord_ask_ang_text(event.house_label, event.room, event.member, kind=event.kind)
        posted = None if dry_run else poster(text)
        result.discord_posts.append(text)
        result.events.append("ask_ang")
        ask = {
            "event_key": event.key,
            "house_slug": event.house_slug,
            "house_label": event.house_label,
            "room": event.room,
            "member": discord_member_label(event.member),
            "kind": event.kind,
            "chat_id": event.chat_id,
            "room_reset": False,
            "discord_message_id": "",
            "asked_at": now.astimezone(timezone.utc).isoformat(),
        }
        if isinstance(posted, dict) and posted.get("id"):
            ask["discord_message_id"] = str(posted["id"])
        pending = [row for row in (state.get("pending_ang_asks") or []) if isinstance(row, dict)]
        pending.append(ask)
        state["pending_ang_asks"] = pending
        checkpoint()


def _first_new_share(
    inbound_messages: Optional[List[Dict[str, Any]]],
    *,
    processed_ids: Sequence[str],
) -> tuple[Optional[str], Optional[str]]:
    seen = {str(item) for item in processed_ids}
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    rows = inbound_messages
    if rows is None:
        if not token:
            return None, None
        try:
            rows = fetch_new_tenants_messages(token)
        except requests.RequestException as exc:
            _log(f"#new-tenants fetch failed: {exc}")
            return None, None
    for message in rows or []:
        message_id = str(message.get("id") or "")
        if not message_id or message_id in seen:
            continue
        code = parse_sifely_share_code(str(message.get("content") or ""))
        if code:
            return code, message_id
    return None, None


def _notify_one_member(chat_id: str, text: str) -> int:
    if not chat_id:
        return 0
    try:
        creds = load_credentials()
        with create_session() as session:
            login(session, creds["email"], creds["password"], force=False)
            send_host_message(session, creds, chat_id, text)
        return 1
    except Exception as exc:
        # Do not convert an unknown send result into a retryable failure.
        raise RuntimeError("PadSplit member delivery outcome uncertain") from exc


def _notify_current_members(
    code: str,
    messages: Sequence[Dict[str, Any]],
    now: datetime,
) -> int:
    threads = current_member_threads(messages, now)
    if not threads:
        _log("no current Spanish Moss member thread; skip PadSplit host message")
        return 0
    creds = load_credentials()
    sent = 0
    with create_session() as session:
        login(session, creds["email"], creds["password"], force=False)
        for thread in threads:
            chat_id = str(thread.get("id") or "")
            if not chat_id:
                continue
            try:
                send_host_message(session, creds, chat_id, member_host_message(code))
                sent += 1
            except Exception as exc:
                _log(f"PadSplit host send failed; continuing: {exc}")
    return sent


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sifely lock-code automation (move-in last-four + Ang rotate)")
    parser.add_argument("--dry-run", action="store_true", help="Decide only; do not rotate or post")
    args = parser.parse_args(argv)
    load_environment()
    if running_in_ci() and not args.dry_run:
        _log("skip_ci: GitHub Actions / CI must not rotate locks or post Discord")
        return 0
    if not live_actions_enabled() and not args.dry_run:
        _log("disabled (LOCK_CODES_ENABLE or CI)")
        return 0
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
