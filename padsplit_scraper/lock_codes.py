#!/usr/bin/env python3
"""Spanish Moss back-door lock-code automation (v1).

Sifely Open API on the free Developer plan. Auth header is the raw
SIFELY_API_KEY value (sk- key, no Bearer). Base URL cus-openapi.sifely.com.

Not live until Ang merges. Spanish Moss back door only — not Green Hill,
not other houses. GitHub Actions / CI must not rotate locks or post Discord.

Safety: never write lock codes, PIN digits, API keys, or Sifely tokens to
git, logs, Discord outbound, or README examples. Tests use REDACTED.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

try:
    from padsplit_scraper.scraper import (
        DEFAULT_TIMEOUT,
        GRAPHQL_URL,
        _authed_request,
        create_session,
        load_credentials,
        login,
    )
except ModuleNotFoundError:  # python3 padsplit_scraper/lock_codes.py
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

# PadSplit Ops bot posts here. Outbound content must contain no digits.
# #new-tenants is inbound-only for the API-down Sifely-share fallback.
DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_NEW_TENANTS_CHANNEL_ID = "1542260130614354055"
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

NEED_YOU_MISSING_KEY = (
    "Need you: missing SIFELY_API_KEY. "
    "Spanish Moss back-door lock-code automation is blocked."
)
DISCORD_HUMAN_CHANGE = "Spanish Moss code changed."
DISCORD_ROTATED = "Spanish Moss lock was rotated."

_DIGIT_RUN = re.compile(r"\d+")
_CODE_TOKEN = re.compile(r"\b(?:passcode|code|pin|keyboard\s*pwd)\b\s*[:#-]?\s*(\S+)", re.I)
_PASSCODE_DIGITS = re.compile(r"^\d{4,8}$")
_SK_KEY = re.compile(r"sk-[A-Za-z0-9]+")
_SECRET_HEADER = re.compile(r"(?i)(authorization\s*:\s*)\S+")
_SIFELY_OK_CODES = {None, 0, "0", 200, "200"}
_HASH_PREFIX_V2 = "v2:"
_TEST_SHARE_PLACEHOLDER = "REDACTED"


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


def load_environment() -> None:
    load_dotenv(ENV_PATH)


def running_in_ci() -> bool:
    return bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))


def live_actions_enabled() -> bool:
    """Mac morning/afternoon only. GitHub Actions / CI must not rotate or post."""
    if running_in_ci():
        return False
    flag = (os.getenv("LOCK_CODES_ENABLE") or "").strip().lower()
    if flag in {"0", "false", "no"}:
        return False
    return True


def sifely_api_key() -> str:
    """Return SIFELY_API_KEY or empty. Never invent a key. Never print it."""
    return (os.getenv("SIFELY_API_KEY") or "").strip()


def has_digit_characters(text: str) -> bool:
    return bool(_DIGIT_RUN.search(text or ""))


def redact_for_log(text: str) -> str:
    """Strip keys and digit runs so logs never contain PIN digits or tokens."""
    cleaned = _SK_KEY.sub("SIFELY_API_KEY", text or "")
    cleaned = _SECRET_HEADER.sub(r"\1SIFELY_API_KEY", cleaned)
    cleaned = re.sub(r"\b\d{4,8}\b", "REDACTED", cleaned)
    return cleaned


def hash_passcode(code: str) -> str:
    """Keyed fingerprint. Never store the passcode itself.

    HMAC-SHA256 with SIFELY_API_KEY when present so a stolen state file
    cannot be reversed by enumerating 4-8 digit PINs. Unkeyed SHA-256 is
    only used when the key is missing (tests / Need-you). Prefixes let
    detect_human_change ignore algorithm migrations.
    """
    material = (code or "").encode("utf-8")
    key = (os.getenv("SIFELY_API_KEY") or "").encode("utf-8")
    if key:
        digest = hmac.new(key, material, hashlib.sha256).hexdigest()
        return f"{_HASH_PREFIX_V2}{digest}"
    return f"v1:{hashlib.sha256(material).hexdigest()}"


def _hash_scheme(digest: str) -> str:
    text = str(digest or "")
    if ":" in text:
        return text.split(":", 1)[0]
    return "legacy"


def is_supported_passcode_token(token: str) -> bool:
    """Accept Sifely 4-8 digit PINs, or the test placeholder REDACTED."""
    value = (token or "").strip()
    if value == _TEST_SHARE_PLACEHOLDER:
        return True
    return bool(_PASSCODE_DIGITS.fullmatch(value))


def generate_passcode() -> str:
    """In-memory PIN only. Tests mock this to REDACTED. Never log the value."""
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


def is_spanish_moss_address(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if "green hill" in text or "greenhill" in text:
        return False
    return "spanish" in text and "moss" in text


def is_spanish_moss_back_lock(lock: Dict[str, Any]) -> bool:
    label = f"{lock.get('lockAlias') or ''} {lock.get('lockName') or ''}".lower()
    if "green hill" in label or "greenhill" in label:
        return False
    if "front" in label:
        return False
    return "spanish" in label and "moss" in label


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
    today = now.astimezone(CT).date()
    current: List[Dict[str, Any]] = []
    for thread in messages:
        prop = thread.get("property") if isinstance(thread.get("property"), dict) else {}
        address = (prop.get("address") or {}) if isinstance(prop.get("address"), dict) else {}
        street = address.get("street1") or address.get("full_street") or ""
        if not is_spanish_moss_address(street):
            continue
        occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
        user = occupancy.get("user")
        if not isinstance(user, dict) or not user:
            continue
        move_out = _date_only(occupancy.get("moveOutDate"))
        if move_out is not None and move_out < today:
            continue
        if thread.get("id"):
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
    if not is_supported_passcode_token(token):
        return None
    return token


def _mentions_spanish_moss_lock(text: str) -> bool:
    lowered = (text or "").lower()
    if "green hill" in lowered or "greenhill" in lowered:
        return False
    return "spanish" in lowered and "moss" in lowered


def discord_human_change_text() -> str:
    return DISCORD_HUMAN_CHANGE


def discord_rotated_text() -> str:
    return DISCORD_ROTATED


def need_you_missing_key_text() -> str:
    return NEED_YOU_MISSING_KEY


def member_host_message(code: str) -> str:
    """PadSplit host inbox may include the new code. Do not log this string."""
    return (
        "Hi, the Spanish Moss back door lock code was rotated. "
        f"The new code is {code}."
    )


def assert_discord_outbound_safe(text: str) -> str:
    if has_digit_characters(text):
        raise RuntimeError("Refusing Discord outbound: message contains digits")
    return text


def decide(
    *,
    in_ci: bool,
    api_key_present: bool,
    api_available: bool,
    human_change: bool,
    pending_vacancy: bool,
    inbound_share: bool,
) -> Plan:
    """Pure v1 decision table. Side effects stay in execute/run."""
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
            action="auto_rotate",
            reason="PadSplit vacant and empty/turned photo",
            update_digest=True,
            notify_padsplit=True,
            discord_kind="rotated",
            rotate_via_api=True,
        )
    if not api_available and inbound_share:
        return Plan(
            action="fallback_share",
            reason="Sifely API cannot run; copy inbound #new-tenants share",
            update_digest=True,
            notify_padsplit=True,
            use_inbound_share=True,
        )
    return Plan(action="noop", reason="no Spanish Moss back-door action")


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(payload, dict):
        return _empty_state()
    payload.setdefault("passcode_hashes", {})
    payload.setdefault("rotated_vacancy_keys", [])
    payload.setdefault("processed_discord_ids", [])
    payload.setdefault("last_auto_rotate_hash", "")
    payload.setdefault("need_you_sent_on", "")
    payload.setdefault("pending_distribution", False)
    if not isinstance(payload["passcode_hashes"], dict):
        payload["passcode_hashes"] = {}
    return payload


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def _empty_state() -> Dict[str, Any]:
    return {
        "passcode_hashes": {},
        "rotated_vacancy_keys": [],
        "processed_discord_ids": [],
        "last_auto_rotate_hash": "",
        "need_you_sent_on": "",
        "pending_distribution": False,
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
        if not prior or prior == digest or digest == last_auto_rotate_hash:
            continue
        if _hash_scheme(prior) != _hash_scheme(digest):
            continue
        return True
    return False


def sifely_headers(api_key: str) -> Dict[str, str]:
    """Raw sk- key. Do not prefix Bearer."""
    return {
        "Authorization": api_key,
        "Accept": "application/json",
    }


def _unwrap_sifely(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if "code" in payload and payload.get("code") not in _SIFELY_OK_CODES:
        raise SifelyUnavailable("Sifely application error")
    if "data" in payload and payload.get("code") in _SIFELY_OK_CODES:
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
        params={"pageNo": "1", "pageSize": "20"},
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
    configured = (os.getenv("SIFELY_LOCK_ID") or "").strip()
    matches = [lock for lock in locks if is_spanish_moss_back_lock(lock)]
    if configured:
        for lock in locks:
            if str(lock.get("lockId")) != configured:
                continue
            label = f"{lock.get('lockAlias') or ''} {lock.get('lockName') or ''}".lower()
            if "green hill" in label or "greenhill" in label:
                return None
            return lock
        return None
    if len(matches) == 1:
        return matches[0]
    return None


def resolve_keyboard_pwd_id(passcodes: Sequence[Dict[str, Any]]) -> Optional[str]:
    configured = (os.getenv("SIFELY_KEYBOARD_PWD_ID") or "").strip()
    if configured:
        return configured
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
    if len(passcodes) == 1:
        only = passcodes[0]
        return str(only.get("keyboardPwdId") or only.get("id") or "") or None
    return None


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


def fetch_new_tenants_messages(token: str) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{DISCORD_API_BASE}/channels/{DISCORD_NEW_TENANTS_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {token}"},
        params={"limit": 30},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    messages = response.json() or []
    return list(reversed(messages))


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


def update_codes_page(code: str) -> bool:
    """Write the new code to Firestore property_codes (GitHub Pages codes.html)."""
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        _log("firebase-admin missing; skip codes page update")
        return False

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    google_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not firebase_admin._apps:
        if service_account_json:
            firebase_admin.initialize_app(credentials.Certificate(json.loads(service_account_json)))
        elif google_credentials:
            firebase_admin.initialize_app(credentials.Certificate(google_credentials))
        else:
            _log("FIREBASE_SERVICE_ACCOUNT_JSON missing; skip codes page update")
            return False
    client = firestore.client()
    client.collection(CODES_COLLECTION).document(PROPERTY_SLUG).set(
        {LOCK_FIELD: code, "updatedAt": datetime.now(timezone.utc).isoformat()},
        merge=True,
    )
    return True


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


def _discord_text_for(kind: Optional[str]) -> Optional[str]:
    if kind == "need_you":
        return need_you_missing_key_text()
    if kind == "human":
        return discord_human_change_text()
    if kind == "rotated":
        return discord_rotated_text()
    return None


def _mark_vacancies_handled(state: Dict[str, Any], rooms: Sequence[Dict[str, Any]]) -> None:
    keys = set(state.get("rotated_vacancy_keys") or [])
    for room in rooms:
        keys.add(vacancy_key(room))
    state["rotated_vacancy_keys"] = sorted(keys)


def _in_memory_passcode(passcodes: Sequence[Dict[str, Any]], keyboard_pwd_id: Any) -> Optional[str]:
    """Return the live keyboardPwd for redistribute. Do not log the value."""
    wanted = str(keyboard_pwd_id or "")
    if not wanted:
        return None
    for item in passcodes:
        pwd_id = str(item.get("keyboardPwdId") or item.get("id") or "")
        if pwd_id != wanted:
            continue
        code = item.get("keyboardPwd")
        if isinstance(code, str) and is_supported_passcode_token(code):
            return code
    return None


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
    notify_members: Optional[Callable[[str], int]] = None,
    generate_code: Optional[Callable[[], str]] = None,
    state_path: Path = STATE_PATH,
) -> RunResult:
    load_environment()
    current = now or datetime.now(timezone.utc)
    state = load_state(state_path)
    rooms = occupancy_rooms if occupancy_rooms is not None else load_occupancy_rooms()
    messages = host_messages if host_messages is not None else load_host_messages()
    pending_rooms = pending_auto_rotate_rooms(rooms, state.get("rotated_vacancy_keys") or [])
    pending_vacancy = bool(pending_rooms)

    api_key = sifely_api_key()
    api_available = False
    human_change = False
    inbound_share_code: Optional[str] = None
    inbound_share = False
    lock: Optional[Dict[str, Any]] = None
    keyboard_pwd_id: Optional[str] = None
    current_hashes: Dict[str, str] = {}
    live_passcodes: List[Dict[str, Any]] = []
    pending_distribution = bool(state.get("pending_distribution"))

    if api_key and not running_in_ci():
        try:
            locks = list_locks(api_key, session=sifely_session)
            lock = resolve_lock(locks)
            if lock is not None:
                live_passcodes = list_passcodes(api_key, lock.get("lockId"), session=sifely_session)
                keyboard_pwd_id = resolve_keyboard_pwd_id(live_passcodes)
                current_hashes = passcode_hashes_from_list(live_passcodes)
                human_change = detect_human_change(
                    current_hashes,
                    state.get("passcode_hashes") or {},
                    state.get("last_auto_rotate_hash") or "",
                )
                if pending_distribution:
                    human_change = False
            api_available = True
        except SifelyUnavailable as exc:
            _log(f"Sifely API cannot run: {exc}")
            api_available = False

    if api_key and not api_available and not running_in_ci():
        inbound_share_code, inbound_id = _first_new_share(
            inbound_messages,
            processed_ids=state.get("processed_discord_ids") or [],
        )
        inbound_share = bool(inbound_share_code)
    else:
        inbound_id = None

    plan = decide(
        in_ci=running_in_ci(),
        api_key_present=bool(api_key),
        api_available=api_available,
        human_change=human_change,
        pending_vacancy=pending_vacancy,
        inbound_share=inbound_share,
    )
    result = RunResult(action=plan.action, reason=plan.reason)
    _log(f"{plan.action} ({plan.reason})")

    if plan.action == "skip_ci":
        return result

    poster = post_discord or (lambda text: None if dry_run else post_ops_discord(text))
    digest_fn = update_digest or (lambda code: False if dry_run else update_codes_page(code))
    member_fn = notify_members or (
        lambda code: 0 if dry_run else _notify_current_members(code, messages, current)
    )
    new_code_fn = generate_code or generate_passcode

    if plan.discord_kind == "need_you":
        today = current.astimezone(CT).date().isoformat()
        if state.get("need_you_sent_on") != today and not dry_run:
            text = assert_discord_outbound_safe(need_you_missing_key_text())
            poster(text)
            result.discord_posts.append(text)
            state["need_you_sent_on"] = today
            save_state(state, state_path)
        elif dry_run:
            result.discord_posts.append(need_you_missing_key_text())
        return result

    if plan.action == "announce_human":
        text = assert_discord_outbound_safe(discord_human_change_text())
        if not dry_run:
            poster(text)
        result.discord_posts.append(text)
        if current_hashes:
            state["passcode_hashes"] = current_hashes
        if not dry_run:
            save_state(state, state_path)
        return result

    working_code: Optional[str] = None
    rotated_this_run = False
    if plan.rotate_via_api:
        if lock is None or not keyboard_pwd_id:
            _log("Need you: could not resolve Spanish Moss back-door lock or passcode id")
            return result
        if pending_distribution:
            working_code = _in_memory_passcode(live_passcodes, keyboard_pwd_id)
            if not working_code:
                _log("pending distribution; live passcode unavailable this run")
                if not dry_run:
                    save_state(state, state_path)
                return result
        else:
            working_code = new_code_fn()
            if not dry_run:
                try:
                    change_passcode(
                        api_key,
                        lock_id=lock.get("lockId"),
                        keyboard_pwd_id=keyboard_pwd_id,
                        new_code=working_code,
                        session=sifely_session,
                    )
                except SifelyUnavailable as exc:
                    _log(f"rotate failed; will wait for #new-tenants fallback: {exc}")
                    return result
            rotated_this_run = True
            state["last_auto_rotate_hash"] = hash_passcode(working_code)
            state["pending_distribution"] = True
            if keyboard_pwd_id:
                hashes = dict(state.get("passcode_hashes") or {})
                hashes[str(keyboard_pwd_id)] = state["last_auto_rotate_hash"]
                state["passcode_hashes"] = hashes
            elif current_hashes:
                state["passcode_hashes"] = current_hashes

    share_consumed = False
    if plan.use_inbound_share:
        working_code = inbound_share_code

    digest_ok = True
    members_ok = True
    if working_code and plan.update_digest:
        result.digest_updated = bool(digest_fn(working_code))
        digest_ok = result.digest_updated
        if not digest_ok:
            _log("digest update incomplete; will retry")
    if working_code and plan.notify_padsplit:
        expected_members = len(current_member_threads(messages, current))
        result.padsplit_notified = int(member_fn(working_code))
        if expected_members > 0 and result.padsplit_notified < expected_members:
            members_ok = False
            _log("PadSplit host notify incomplete; will retry")

    delivered = bool(working_code) and digest_ok and members_ok
    if delivered:
        _mark_vacancies_handled(state, pending_rooms)
        state["pending_distribution"] = False
        if plan.use_inbound_share and inbound_id:
            processed = set(state.get("processed_discord_ids") or [])
            processed.add(str(inbound_id))
            state["processed_discord_ids"] = sorted(processed)[-50:]
            share_consumed = True
    elif plan.use_inbound_share and not share_consumed:
        _log("inbound share not consumed; vacancy stays pending")

    if plan.discord_kind == "rotated" and rotated_this_run:
        text = assert_discord_outbound_safe(discord_rotated_text())
        if not dry_run:
            poster(text)
        result.discord_posts.append(text)

    if current_hashes and plan.action == "noop":
        state["passcode_hashes"] = current_hashes

    if not dry_run:
        save_state(state, state_path)
    return result


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
    parser = argparse.ArgumentParser(description="Spanish Moss back-door lock-code automation v1")
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
