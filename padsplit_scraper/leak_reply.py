#!/usr/bin/env python3
"""PadSplit host water-leak auto-reply (Ang GO / Chief).

When a current member reports a water leak at 100% precision, SEND a
1:1 adaptation of Firestore templates/shared t5 (n5 Water leak
announcement) plus Quo, and emit a digit-free WATER_KEY_ORDER event on
Discord #ai-automations for Cart. No Amazon purchase in this scraper.

High precision only: member messages, current leak language. Do not fire
on host reminder blasts or historical “previous leak” chatter.

LEAK_REPLY_ENABLE default off. GitHub Actions / CI must not send.
Discord outbound never includes lock codes or digit door codes.
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
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import requests
from dotenv import load_dotenv

try:
    from padsplit_scraper import lock_codes
    from padsplit_scraper import lockout_reply
    from padsplit_scraper import new_booking
    from padsplit_scraper import runtime
    from padsplit_scraper.scraper import (
        DEFAULT_TIMEOUT,
        create_session,
        load_credentials,
        login,
    )
except ModuleNotFoundError:  # python3 padsplit_scraper/leak_reply.py
    import lock_codes  # type: ignore
    import lockout_reply  # type: ignore
    import new_booking  # type: ignore
    import runtime  # type: ignore
    from scraper import (  # type: ignore
        DEFAULT_TIMEOUT,
        create_session,
        load_credentials,
        login,
    )


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
STATE_PATH = ROOT_DIR / "logs" / "leak_reply_state.json"

LOOKBACK = timedelta(hours=36)
IDEMPOTENCY_WINDOW = timedelta(hours=24)

# Member-thread only. Same Quo field line as lockout. t5 does not include Quo.
QUO_FIELD_PHONE = lockout_reply.JOE_FIELD_PHONE
WATER_KEY_YOUTUBE_URL = "https://youtube.com/shorts/SCryjPiyZcs"
LEAK_PACK_MARKER = "Thanks for reporting the water leak"
WATER_KEY_ORDER_MARKER = "WATER_KEY_ORDER"
# Cart Amazon same-day. ASIN digits are spelled on Discord; keyword is digit-free.
WATER_KEY_ASIN = "B0786ZQ5SL"
WATER_KEY_KEYWORD = "water curb key"
T5_LABEL = "Water leak announcement"
T6_LABEL = "Water leak new tenants"
WATER_LEAK_ANNOUNCEMENT_LABEL = "water leak announcement"

# Canonical t5 from live Firestore templates/shared (Mac fetch). House-wide
# copy; AUTO member reply adapts this to 1:1 and adds Quo.
BAKED_T5_TEXT = """Dear All,

Quick important update for everyone’s safety:

In case of a water leak or pipe issue, please act immediately to prevent flooding and damage. Please place this in the common closet where everyone can reach it.

👉 Location:
The water shut-off box is located between the water meter and the house.

👉 What to do:
1. Use the water key to turn OFF the water immediately
2. This helps prevent damage to the house and your personal belongings
3. You may turn it ON briefly if needed (for drinking water), but please keep it OFF afterward

We will send someone out ASAP to fix the issue.

🎥 How to use a water key:
https://youtube.com/shorts/SCryjPiyZcs?si=NfvowNCrVSnHK99w

⚠️ Reminder:
Leaks and flooding are considered emergency maintenance issues, so quick action helps protect everyone.

If you’re unsure, message us immediately.

Thank you for helping keep the home safe."""

_DIGIT_WORDS = (
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
)
_DIGIT_TOKEN_RE = re.compile(
    r"\[(" + "|".join(_DIGIT_WORDS) + r")\]"
)
_LEADING_HOUSE_NUM_RE = re.compile(r"^\s*\d+\s*")

# Curb-key / whole-house shutoff: burst, pipe, main, flooding, wall/ceiling only.
# Bare water leak, slow leak, drip, and seepage do not fire.
_ACTIVE_WATER_RE = re.compile(
    r"(?i)("
    r"\bpipe\s+(?:burst|broke|broken|(?:is\s+)?leak(?:ing)?)\b"
    r"|\bburst(?:ing)?\s+pipe\b"
    r"|\bwater\s+main\b"
    r"|\bmain\s+(?:water\s+)?(?:line|pipe|break|broke|burst)\b"
    r"|\bleak(?:ing)?\s+from\s+(?:the\s+)?(?:wall|ceiling|pipe)\b"
    r"|\b(?:wall|ceiling)\s+(?:is\s+)?leak(?:ing)?\b"
    r"|\bwater\s+leaking\s+from\s+(?:the\s+)?(?:wall|ceiling|pipe)\b"
    r"|\bflood(?:ing|ed)\b"
    r")",
)

_LOW_URGENCY_RE = re.compile(
    r"(?i)("
    r"\bslow\s+leak"
    r"|\bdrip(?:ping|s|ped)?\b"
    r"|\bseep(?:age|ing|s)?\b"
    r")",
)

_LOW_URGENCY_OVERRIDE_RE = re.compile(
    r"(?i)("
    r"\bflood(?:ing|ed)\b"
    r"|\bburst"
    r"|\bwater\s+main\b"
    r"|\bmain\s+(?:water\s+)?(?:line|pipe|break|broke|burst)\b"
    r")",
)

_TOILET_ONLY_RE = re.compile(
    r"(?i)("
    r"\btoilet\b"
    r"|\bwax\s+ring\b"
    r"|\b(?:base|bowl)\s+(?:seep|leak)"
    r"|\bseep(?:ing)?\s+(?:at\s+)?(?:the\s+)?(?:base|bowl|wax)"
    r"|\bclog(?:ged|ging)?\b"
    r")",
)

_TOILET_EMERGENCY_RE = re.compile(
    r"(?i)("
    r"\bflood(?:ing|ed)\b"
    r"|\bpipe\s+(?:burst|broke|broken|(?:is\s+)?leak(?:ing)?)\b"
    r"|\bburst(?:ing)?\s+pipe\b"
    r"|\bwater\s+main\b"
    r"|\bleak(?:ing)?\s+from\s+(?:the\s+)?(?:wall|ceiling)\b"
    r"|\b(?:wall|ceiling)\s+(?:is\s+)?leak(?:ing)?\b"
    r"|\bwater\s+leaking\s+from\b"
    r")",
)

# Negation of an emergency term ("there is no flooding").
_NEGATED_EMERGENCY_RE = re.compile(
    r"(?i)("
    r"\b(?:there\s+(?:is|was)\s+)?no\s+(?:active\s+|current\s+)?"
    r"(?:flood(?:ing|ed)?|pipe\s+burst|burst(?:ing)?\s+pipe|water\s+main|"
    r"water\s+leak(?:ing)?|leak(?:ing)?)\b"
    r"|\b(?:is|was|are|were)\s+not\s+(?:flood(?:ing|ed)?|burst(?:ing)?|leaking)\b"
    r"|\b(?:isn['’]?t|aren['’]?t|wasn['’]?t|weren['’]?t)\s+"
    r"(?:flood(?:ing|ed)?|burst(?:ing)?|leaking)\b"
    r"|\bno\s+longer\s+(?:flood(?:ing|ed)?|burst(?:ing)?|leaking)\b"
    r")"
)

# Past emergency that is already resolved ("pipe burst last week and is fixed").
_RESOLVED_HISTORY_RE = re.compile(
    r"(?i)("
    r"(?:the\s+)?(?:pipe\s+burst|burst(?:ing)?\s+pipe|water\s+main|"
    r"flood(?:ing|ed)|water\s+leak(?:ing)?|pipe\s+leak).{0,80}"
    r"(?:last\s+(?:week|month|year|night)|yesterday|"
    r"\d+\s+(?:days?|weeks?|months?)\s+ago).{0,80}"
    r"(?:fix(?:ed)?|resolved|repaired|already|not\s+anymore|no\s+longer)"
    r")"
)

# Location / identity questions ("Where is the water main?").
_INFO_QUESTION_RE = re.compile(
    r"(?i)("
    r"\b(?:where|what|which|how(?:\s+do(?:\s+i|\s+you)?|s)?)\b"
    r".{0,80}\b(?:water\s+main|shut-?off|water\s+key|curb\s+key|meter)\b"
    r"|\b(?:water\s+main|shut-?off).{0,24}\?"
    r")"
)

# Host reminder / historical / non-water chatter. Stripped before the
# active-water re-check. Toilet-only leftover after this still does not fire.
_LEAK_EXCLUDE_RE = re.compile(
    r"(?i)("
    r"water usage and leak reminder"
    r"|in case of a water leak"
    r"|how to use a water key"
    r"|youtube\.com/shorts/scryjpiyzcs"
    r"|please be reminded to use water"
    r"|report any leak(?:ing)?"
    r"|leak reminder"
    r"|prevent (?:possible )?leaks"
    r"|prevent flooding"
    r"|avoid flooding"
    r"|leaks and flooding"
    r"|flooding and (?:damage|costly)"
    r"|\bprevious (?:water )?leak"
    r"|\blast (?:water )?leak"
    r"|\bold (?:water )?leak"
    r"|\bwas leak(?:ing)?"
    r"|\bhad (?:a )?(?:water )?leak"
    r"|\bthere was (?:a )?(?:water )?leak"
    r"|\bleak(?:ing)? (?:that )?(?:was|is) (?:already )?fix"
    r"|\bfixed (?:the )?(?:water )?leak"
    r"|\bno (?:water )?leak"
    r"|\bnothing leak"
    r"|\bcheck (?:for )?(?:any )?(?:water )?leak"
    r"|\bgas leak"
    r"|\binfo(?:rmation)? leak"
    r")",
)

_HOST_BLAST_RE = re.compile(
    r"(?i)("
    r"water usage and leak reminder"
    r"|in case of a water leak"
    r"|how to use a water key"
    r"|youtube\.com/shorts/scryjpiyzcs"
    r"|please be reminded to use water"
    r"|dear all"
    r"|quick important update for everyone"
    r")",
)

_YOUTUBE_SHORT_RE = re.compile(
    r"https?://(?:www\.)?youtube\.com/shorts/SCryjPiyZcs[^\s)\]>]*",
    re.IGNORECASE,
)
_DEAR_ALL_RE = re.compile(r"(?i)^\s*dear\s+all\s*,?\s*")
_HOUSEWIDE_OPENER_RE = re.compile(
    r"(?i)^\s*Quick important update for everyone['’]s safety:\s*"
)
_COMMON_CLOSET_RE = re.compile(
    r"(?i)\s*Please place this in the common closet where everyone can reach it\.?\s*"
)
_IN_CASE_OPENER_RE = re.compile(
    r"(?i)^In case of a water leak or pipe issue, please act immediately "
    r"to prevent flooding and damage\.\s*"
)
_UNSURE_MESSAGE_RE = re.compile(
    r"(?i)If you['’]re unsure, message us immediately\.?"
)
_STATE_ABBREV = {
    "texas": "TX",
    "tx": "TX",
}
_HOUSE_NUM_RE = re.compile(r"\d")
_LETTER_RE = re.compile(r"[A-Za-z]")


@dataclass
class Decision:
    action: str
    reason: str
    certainty: int
    chat_id: str = ""
    house_label: str = ""
    street: str = ""
    room: str = ""
    send_body: str = ""
    discord: str = ""
    ship_to: str = ""


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
    """Default off until Mac .env sets LEAK_REPLY_ENABLE. CI must not send.

    Darwin is not an implicit send permission. PADSPLIT_SEND_LEAK is the
    Stage A–B alias; LEAK_REPLY_ENABLE remains the legacy flag.
    """
    return runtime.send_enabled("leak")


def _log(message: str) -> None:
    sys.stderr.write(f"[leak-reply] {lock_codes.redact_for_log(message)}\n")


def encode_digits_for_discord(value: str) -> str:
    """Spell each digit so Discord outbound stays digit-free. Cart decodes [one]…[nine]."""
    out: List[str] = []
    for char in str(value or ""):
        if char.isdigit():
            out.append(f"[{_DIGIT_WORDS[int(char)]}]")
        else:
            out.append(char)
    return "".join(out)


def decode_digits_from_discord(value: str) -> str:
    """Inverse of encode_digits_for_discord. Tests / Cart pickup only."""
    index = {word: str(i) for i, word in enumerate(_DIGIT_WORDS)}

    def _repl(match: re.Match[str]) -> str:
        return index[match.group(1)]

    return _DIGIT_TOKEN_RE.sub(_repl, value or "")


def street_label_for_discord(street: str) -> str:
    """Drop leading house numbers. Prefer thread_ship_to for Cart."""
    return _LEADING_HOUSE_NUM_RE.sub("", lockout_reply.normalize_text(street)).strip()


def _state_abbrev(value: str) -> str:
    raw = lockout_reply.normalize_text(value)
    return _STATE_ABBREV.get(raw.lower(), raw)


def thread_ship_to(thread: Dict[str, Any]) -> str:
    """Full ship-to of the leaking property from the PadSplit thread only.

    Never invent an address. Never substitute Spanish Moss for another house.
    """
    address = ((thread.get("property") or {}).get("address") or {})
    if not isinstance(address, dict):
        return ""
    street = lockout_reply.normalize_text(
        address.get("street1") or address.get("full_street")
    )
    street2 = lockout_reply.normalize_text(address.get("street2"))
    zip_code = lockout_reply.normalize_text(address.get("zip") or address.get("postalCode"))
    city_obj = address.get("city")
    city = ""
    state = ""
    if isinstance(city_obj, dict):
        city = lockout_reply.normalize_text(city_obj.get("name"))
        state_obj = city_obj.get("state")
        if isinstance(state_obj, dict):
            state = _state_abbrev(
                str(state_obj.get("code") or state_obj.get("name") or "")
            )
        else:
            state = _state_abbrev(str(state_obj or ""))
    else:
        city = lockout_reply.normalize_text(city_obj or address.get("city_name"))
        state = _state_abbrev(str(address.get("state") or address.get("state_code") or ""))
    if not street:
        return ""
    line = street
    if street2:
        line = f"{line} {street2}"
    locality = ", ".join(part for part in (city, state) if part)
    if locality and zip_code:
        return f"{line}, {locality} {zip_code}"
    if locality:
        return f"{line}, {locality}"
    if zip_code:
        return f"{line} {zip_code}"
    return line


def is_full_ship_to(address: str) -> bool:
    """Need a house number plus a street name so Cart can ship."""
    text = lockout_reply.normalize_text(address)
    return bool(text and _HOUSE_NUM_RE.search(text) and _LETTER_RE.search(text))


def assert_water_key_order_safe(text: str, *, ship_to: str) -> str:
    """Digit-free except the leaking property ship-to. No lock codes."""
    remainder = text or ""
    if ship_to:
        remainder = remainder.replace(ship_to, "", 1)
    if lock_codes.has_digit_characters(remainder):
        raise RuntimeError("Refusing Discord outbound: digits outside ship-to address")
    lowered = (text or "").lower()
    if "lock code" in lowered or "door code" in lowered or "ssn" in lowered:
        raise RuntimeError("Refusing Discord outbound: codes or SSN")
    return text


def detect_leak(text: str) -> bool:
    """True only for burst / pipe / main / flooding / wall-ceiling emergencies."""
    raw = text or ""
    if _HOST_BLAST_RE.search(raw) and len(raw) > 280:
        return False
    stripped = _LEAK_EXCLUDE_RE.sub(" ", raw)
    stripped = _NEGATED_EMERGENCY_RE.sub(" ", stripped)
    stripped = _RESOLVED_HISTORY_RE.sub(" ", stripped)
    stripped = _INFO_QUESTION_RE.sub(" ", stripped)
    if _LOW_URGENCY_RE.search(stripped) and not _LOW_URGENCY_OVERRIDE_RE.search(stripped):
        return False
    if not _ACTIVE_WATER_RE.search(stripped):
        return False
    if _TOILET_ONLY_RE.search(stripped) and not _TOILET_EMERGENCY_RE.search(stripped):
        return False
    return True


def canonicalize_water_key_youtube(text: str) -> str:
    """Keep the canonical short URL; strip ?si= / utm tracking."""
    return _YOUTUBE_SHORT_RE.sub(WATER_KEY_YOUTUBE_URL, text or "")


def load_shared_template_fields(
    fetch_doc: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, str]:
    """Same templates/shared doc new_booking uses for the Hirevire card."""
    if fetch_doc is None:

        def fetch_doc() -> Dict[str, Any]:
            resp = requests.get(new_booking.TEMPLATES_DOC_URL, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            return resp.json()

    return new_booking.parse_firestore_string_map(fetch_doc())


def pick_t5_source(fields: Dict[str, str]) -> str:
    for index in range(16):
        label = (fields.get(f"n{index}") or "").strip()
        if label.lower() == WATER_LEAK_ANNOUNCEMENT_LABEL:
            return new_booking.template_send_body(label, fields.get(f"t{index}") or "")
    return new_booking.template_send_body(fields.get("n5") or T5_LABEL, fields.get("t5") or "")


def load_t5_source(
    fetch_doc: Optional[Callable[[], Dict[str, Any]]] = None,
) -> str:
    try:
        source = pick_t5_source(load_shared_template_fields(fetch_doc))
        if source.strip():
            return source
    except Exception as exc:
        _log(f"templates/shared t5 load failed; using baked copy: {exc}")
    return BAKED_T5_TEXT


def adapt_t5_to_member_thread(text: str) -> str:
    """1:1 tone from house-wide t5. Always add Quo. No lock codes / SSN / Amazon."""
    body = new_booking.template_send_body(T5_LABEL, text or "")
    if not body.strip():
        body = BAKED_T5_TEXT
    body = _DEAR_ALL_RE.sub("", body).strip()
    body = _HOUSEWIDE_OPENER_RE.sub("", body).strip()
    body = _COMMON_CLOSET_RE.sub("\n\n", body)
    body = _IN_CASE_OPENER_RE.sub(
        f"{LEAK_PACK_MARKER} — please act immediately to prevent flooding and damage.\n\n",
        body,
        count=1,
    )
    if LEAK_PACK_MARKER not in body:
        body = (
            f"{LEAK_PACK_MARKER} — please act immediately to prevent flooding "
            f"and damage.\n\n{body}"
        )
    body = canonicalize_water_key_youtube(body)
    body = _UNSURE_MESSAGE_RE.sub(
        f"If you’re unsure, call/text Quo: {QUO_FIELD_PHONE}.",
        body,
    )
    quo = f"Call/text Quo: {QUO_FIELD_PHONE}"
    if quo not in body:
        parts = body.split("\n\n", 1)
        body = (
            f"{parts[0]}\n\n{quo}\n\n{parts[1]}"
            if len(parts) == 2
            else f"{body}\n\n{quo}"
        )
    if WATER_KEY_YOUTUBE_URL not in body:
        body = f"{body}\n\nHow to use a water key:\n{WATER_KEY_YOUTUBE_URL}"
    body = canonicalize_water_key_youtube(body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def format_leak_body(
    source: Optional[str] = None,
    *,
    fetch_doc: Optional[Callable[[], Dict[str, Any]]] = None,
) -> str:
    """PadSplit 1:1 body from live t5 (or baked t5). Quo + canonical YouTube."""
    raw = source if source is not None else load_t5_source(fetch_doc)
    body = adapt_t5_to_member_thread(raw)
    if QUO_FIELD_PHONE not in body:
        raise RuntimeError("refusing to format leak body without Quo number")
    if "lock code" in body.lower() or "ssn" in body.lower():
        raise RuntimeError("refusing leak body that mentions lock codes or SSN")
    return body


def discord_water_key_order_text(
    *,
    house_label: str = "",
    ship_to: str = "",
    street: str = "",
    room: str = "",
    chat_id: str = "",
) -> Optional[str]:
    """Cart event. Digit-free except full ship-to. Requires house + address.

    Never uses a Spanish Moss override address for another house.
    """
    house = lockout_reply.normalize_text(house_label)
    dest = lockout_reply.normalize_text(ship_to) or lockout_reply.normalize_text(street)
    if not house or not is_full_ship_to(dest):
        return None
    if lock_codes.is_spanish_moss_address(dest) and "spanish moss" not in house.lower():
        return None
    room_token = encode_digits_for_discord(room) if room else "unknown"
    chat_token = encode_digits_for_discord(chat_id) if chat_id else "unknown"
    asin_token = encode_digits_for_discord(WATER_KEY_ASIN)
    text = (
        f"{WATER_KEY_ORDER_MARKER} "
        f'house="{house}" '
        f'ship_to="{dest}" '
        f'keyword="{WATER_KEY_KEYWORD}" '
        f"asin={asin_token} "
        f"room={room_token} "
        f"chat={chat_token}"
    )
    return assert_water_key_order_safe(text, ship_to=dest)


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"threads": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise lockout_reply.CorruptStateError(f"corrupt leak state at {path}") from exc
    if not isinstance(payload, dict):
        raise lockout_reply.CorruptStateError(f"corrupt leak state at {path}: not an object")
    payload.setdefault("threads", {})
    if not isinstance(payload["threads"], dict):
        raise lockout_reply.CorruptStateError(
            f"corrupt leak state at {path}: threads is not an object"
        )
    return payload


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def already_sent(
    state: Dict[str, Any],
    chat_id: str,
    *,
    now: datetime,
    window: timedelta = IDEMPOTENCY_WINDOW,
) -> bool:
    row = (state.get("threads") or {}).get(chat_id) or {}
    if not isinstance(row, dict):
        return False
    sent_at = lockout_reply.parse_dt(row.get("sent_at"))
    if sent_at is None:
        return False
    return (now - sent_at) < window


def record_sent(state: Dict[str, Any], chat_id: str, *, now: datetime) -> None:
    threads = state.setdefault("threads", {})
    row = threads.get(chat_id) if isinstance(threads.get(chat_id), dict) else {}
    row["sent_at"] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row["action"] = "sent"
    threads[chat_id] = row


def host_already_sent_leak_pack(thread: Dict[str, Any]) -> bool:
    for message in lockout_reply.iter_thread_messages(thread):
        if not lockout_reply.is_host_message(message):
            continue
        if LEAK_PACK_MARKER in lockout_reply.message_text(message):
            return True
    return False


def recent_member_leak(
    thread: Dict[str, Any],
    *,
    now: datetime,
    lookback: timedelta = LOOKBACK,
) -> Optional[Dict[str, Any]]:
    newest: Optional[Dict[str, Any]] = None
    newest_at: Optional[datetime] = None
    for message in lockout_reply.iter_thread_messages(thread):
        if message.get("deleted"):
            continue
        if not lockout_reply.is_member_message(thread, message):
            continue
        created = lockout_reply.parse_dt(message.get("created"))
        if created is None or (now - created) > lookback:
            continue
        if not detect_leak(lockout_reply.message_text(message)):
            continue
        if newest_at is None or created > newest_at:
            newest = message
            newest_at = created
    return newest


def decide(
    thread: Dict[str, Any],
    *,
    now: datetime,
    state: Optional[Dict[str, Any]] = None,
    leak_body: Optional[str] = None,
) -> Decision:
    chat_id = str(thread.get("id") or "")
    if not chat_id:
        return Decision(action="skip", reason="missing chat id", certainty=0)
    if not lockout_reply.current_occupant(thread, now):
        return Decision(
            action="skip",
            reason="not a current occupant thread",
            certainty=0,
            chat_id=chat_id,
        )
    if state is not None and already_sent(state, chat_id, now=now):
        return Decision(
            action="already_sent",
            reason="leak pack already sent",
            certainty=100,
            chat_id=chat_id,
        )
    if host_already_sent_leak_pack(thread):
        return Decision(
            action="already_sent",
            reason="host leak pack already on thread",
            certainty=100,
            chat_id=chat_id,
        )

    leak_message = recent_member_leak(thread, now=now)
    if leak_message is None:
        return Decision(
            action="skip",
            reason="no recent member leak",
            certainty=0,
            chat_id=chat_id,
        )

    member_text = lockout_reply.message_text(leak_message)
    street = lockout_reply.thread_street(thread)
    # House + ship-to from the thread property only. Do not let member text
    # or a Spanish Moss default override the leaking house address.
    house = lockout_reply.match_house(street, "")
    ship_to = thread_ship_to(thread)
    if lock_codes.is_spanish_moss_address(ship_to) and house.slug != "spanish_moss":
        ship_to = ""
    room = lockout_reply.resolve_room(thread, member_text)
    return Decision(
        action="send",
        reason="100 percent current member leak",
        certainty=100,
        chat_id=chat_id,
        house_label=house.label,
        street=street,
        ship_to=ship_to,
        room=room.room or "",
        send_body=leak_body if leak_body is not None else format_leak_body(),
        discord=discord_water_key_order_text(
            house_label=house.label,
            ship_to=ship_to,
            street=street,
            room=room.room or "",
            chat_id=chat_id,
        )
        or "",
    )


def post_automations_discord(
    text: str,
    *,
    ship_to: str = "",
    token: Optional[str] = None,
    channel: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Post WATER_KEY_ORDER. Digit-free except ship_to. Do not use lock_codes.assert on the full text."""
    safe = assert_water_key_order_safe(text, ship_to=ship_to)
    token = token or (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    channel = channel or (
        (os.getenv("DISCORD_AUTOMATIONS_CHANNEL_ID") or "").strip()
        or (os.getenv("DISCORD_CHANNEL_ID") or "").strip()
    )
    if not token or not channel:
        _log("Discord token or automations channel missing; skip WATER_KEY_ORDER")
        return None
    response = requests.post(
        f"{lock_codes.DISCORD_API_BASE}/channels/{channel}/messages",
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        json={"content": safe},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def process_leaks(
    threads: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    state: Optional[Dict[str, Any]] = None,
    state_path: Path = STATE_PATH,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    close_tabs_fn: Optional[Callable[[Optional[List[Dict[str, Any]]], str], List[Dict[str, Any]]]] = None,
    send_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    post_discord: Optional[Callable[[str], Any]] = None,
    send_enabled: bool = True,
    dry_run: bool = False,
    fetch_doc: Optional[Callable[[], Dict[str, Any]]] = None,
    leak_body: Optional[str] = None,
) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    if state is None:
        try:
            state = load_state(state_path)
        except lockout_reply.CorruptStateError as exc:
            _log(f"corrupt state; refusing to send: {exc}")
            return [
                {
                    "action": "blocked_corrupt_state",
                    "reason": "corrupt state",
                    "certainty": 0,
                }
            ]
    results: List[Dict[str, Any]] = []
    body = leak_body if leak_body is not None else format_leak_body(fetch_doc=fetch_doc)

    for thread in threads:
        decision = decide(thread, now=now, state=state, leak_body=body)
        row = {
            "chat_id": decision.chat_id,
            "action": decision.action,
            "reason": decision.reason,
            "certainty": decision.certainty,
            "house_label": decision.house_label,
            "room": decision.room,
            "ship_to": decision.ship_to,
        }
        if decision.discord:
            row["discord"] = decision.discord

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

        record_sent(state, decision.chat_id, now=now)
        if not dry_run:
            save_state(state, state_path)
        if decision.discord:
            try:
                if post_discord is not None:
                    post_discord(decision.discord)
                else:
                    post_automations_discord(decision.discord, ship_to=decision.ship_to)
            except Exception as exc:
                _log(f"Discord post failed; continuing: {exc}")
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
    post_discord: Optional[Callable[[str], Any]] = None,
    state_path: Path = STATE_PATH,
    session=None,
    creds: Optional[Dict[str, str]] = None,
    fetch_doc: Optional[Callable[[], Dict[str, Any]]] = None,
) -> RunResult:
    load_environment()
    current = now or datetime.now(timezone.utc)
    if running_in_ci() and not dry_run:
        _log("skip_ci: GitHub Actions / CI must not send leak replies")
        return RunResult(action="skip_ci", reason="CI must not send")
    if not live_send_enabled() and not dry_run:
        _log("disabled (LEAK_REPLY_ENABLE or CI)")
        return RunResult(action="disabled", reason="LEAK_REPLY_ENABLE is off")

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

    rows = process_leaks(
        messages,
        now=current,
        state_path=state_path,
        leftover_compose_tabs=tabs,
        send_fn=_send if not dry_run else None,
        post_discord=post_discord,
        send_enabled=not dry_run,
        dry_run=dry_run,
        fetch_doc=fetch_doc,
    )
    if leftover_compose_tabs is None and not dry_run:
        new_booking.save_leftover_compose_tabs(tabs, leftover_tabs_path)

    sent = sum(1 for row in rows if row.get("action") == "sent")
    posts = [str(row["discord"]) for row in rows if row.get("discord") and row.get("action") == "sent"]
    for row in rows:
        if row.get("discord") and row.get("action") == "sent":
            assert_water_key_order_safe(str(row["discord"]), ship_to=str(row.get("ship_to") or ""))
    summary = "sent" if sent else (rows[0]["action"] if rows else "noop")
    result = RunResult(
        action=summary,
        reason=f"{len(rows)} leak thread(s)",
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
        _log("skipped in scraper (LEAK_REPLY_ENABLE or CI)")
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
    parser = argparse.ArgumentParser(description="PadSplit water-leak auto-reply")
    parser.add_argument("--dry-run", action="store_true", help="Decide only; do not send")
    args = parser.parse_args(argv)
    load_environment()
    if running_in_ci() and not args.dry_run:
        _log("skip_ci: GitHub Actions / CI must not send leak replies")
        return 0
    if not live_send_enabled() and not args.dry_run:
        _log("disabled (LEAK_REPLY_ENABLE or CI)")
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
