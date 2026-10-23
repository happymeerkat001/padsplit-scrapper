#!/usr/bin/env python3
"""Triage Discord alert for PadSplit maintenance-ticket leaks.

Messenger threads stay on leak_reply.run_for_scraper. This module reads the
tickets scraper.fetch_tasks() already returned. It does not read mail.

detect_ticket_leak is broader than leak_reply.detect_leak (bare "leaked"
counts). It still drops the existing negation and resolved-history patterns.
Strict detect_leak still owns the tenant auto-reply, shutoff pack, texts,
and calls. This path only posts one LEAK_TICKET line to Discord
#ai-automations, or writes a preview line when dry-run is on.

LEAK_TICKET_ALERT_ENABLE defaults off. CI and collection-only are no-ops.
LEAK_ALERT_DRY_RUN defaults on: preview goes to logs/leak_ticket_preview.jsonl
and Discord is not called until that flag is explicitly off.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

try:
    from padsplit_scraper import leak_alert
    from padsplit_scraper import leak_reply
    from padsplit_scraper import lock_codes
    from padsplit_scraper import lockout_reply
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/leak_ticket.py
    import leak_alert  # type: ignore
    import leak_reply  # type: ignore
    import lock_codes  # type: ignore
    import lockout_reply  # type: ignore
    import runtime  # type: ignore


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT_DIR / "logs" / "leak_ticket_state.json"
PREVIEW_PATH = ROOT_DIR / "logs" / "leak_ticket_preview.jsonl"

MARKER = "LEAK_TICKET"

# New requests and the Open bucket only. In progress, hold, eviction, and
# complete stay out of triage.
_NEW_OR_OPEN_STATUS = {"submitted", "accepted", "new", "open"}
_NEW_OR_OPEN_BUCKET = {"requests", "open", "new"}

_IDENTITY_KEYS = {
    "reported_by",
    "reporter",
    "member_name",
    "member",
    "author_name",
    "name",
    "first_name",
    "last_name",
    "firstname",
    "lastname",
    "displayname",
    "display_name",
    "phone",
    "phone_number",
    "mobile",
    "email",
    "contact",
    "contact_phone",
}

# leak(s|ed|ing)?, water, flood, burst, or pipe. Flooding/bursting/pipes
# follow the same stems the emergency regexes already use.
_TICKET_LEAK_RE = re.compile(
    r"(?i)\b(?:leak(?:s|ed|ing)?|water|flood(?:ing|ed|s)?|burst(?:ing|ed|s)?|pipes?)\b"
)
_ROOM_RE = re.compile(r"[A-Za-z]?\d{1,3}[A-Za-z]?")
_TICKET_ID_RE = re.compile(r"\d{1,12}")


class CorruptTicketState(RuntimeError):
    """Ticket alert state is unreadable. Refuse to post."""


def _log(message: str) -> None:
    sys.stderr.write(f"[leak-ticket] {lock_codes.redact_for_log(message)}\n")


def alert_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Default off. CI and collection-only never enable the triage post."""
    env = os.environ if environ is None else environ
    return runtime.send_enabled("leak_ticket", env)  # type: ignore[arg-type]


def dry_run_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Default on. Live Discord requires LEAK_ALERT_DRY_RUN explicitly off."""
    env = os.environ if environ is None else environ
    parsed = runtime.flag_value(env.get("LEAK_ALERT_DRY_RUN"))
    if parsed is None:
        return True
    return parsed


def detect_ticket_leak(text: str) -> bool:
    """True when details or location still name a leak after negation and history.

    Broader than leak_reply.detect_leak. Does not authorize a tenant send.
    """
    stripped = leak_reply._NEGATED_EMERGENCY_RE.sub(" ", text or "")
    stripped = leak_reply._RESOLVED_HISTORY_RE.sub(" ", stripped)
    return bool(_TICKET_LEAK_RE.search(stripped))


def ticket_leak_text(ticket: Mapping[str, Any]) -> str:
    """Location and details only. Identity fields stay out of the match text."""
    location = lockout_reply.normalize_text(ticket.get("location"))
    details = lockout_reply.normalize_text(ticket.get("details") or ticket.get("description"))
    parts: List[str] = []
    if location:
        parts.append(f"Location: {location}")
    if details:
        parts.append(f"Details: {details}")
    return " / ".join(parts)


def assert_leak_ticket_safe(text: str, *, ticket_id: str, room: str) -> str:
    """Digit-free except the labeled ticket id and room number. No lock codes."""
    remainder = text or ""
    ticket_pattern = rf"ticket={re.escape(str(ticket_id))}(?!\d)"
    updated, ticket_hits = re.subn(ticket_pattern, "ticket=", remainder, count=1)
    if ticket_hits != 1:
        raise RuntimeError("Refusing Discord outbound: ticket id missing")
    remainder = updated
    room_token = str(room or "")
    if room_token and room_token != "unknown":
        room_pattern = rf"room={re.escape(room_token)}(?!\d)"
        updated, room_hits = re.subn(room_pattern, "room=", remainder, count=1)
        if room_hits != 1:
            raise RuntimeError("Refusing Discord outbound: room missing")
        remainder = updated
    if lock_codes.has_digit_characters(remainder):
        raise RuntimeError("Refusing Discord outbound: digits outside ticket id and room")
    lowered = (text or "").lower()
    if "lock code" in lowered or "door code" in lowered or "ssn" in lowered:
        raise RuntimeError("Refusing Discord outbound: codes or SSN")
    return text


def _identity_fragments(ticket: Mapping[str, Any]) -> List[str]:
    found: List[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            text = lockout_reply.normalize_text(value)
            if len(text) < 3:
                return
            digits = re.sub(r"\D", "", text)
            if " " in text or "@" in text or len(digits) >= 7:
                found.append(text)
                if len(digits) >= 7:
                    found.append(digits)
        elif isinstance(value, dict):
            for key, inner in value.items():
                if str(key).lower() in _IDENTITY_KEYS:
                    add(inner)

    for key, value in ticket.items():
        if str(key).lower() in _IDENTITY_KEYS:
            add(value)
    author = ticket.get("author")
    if isinstance(author, dict):
        add(author)
    return found


def assert_no_member_identity(text: str, ticket: Mapping[str, Any]) -> str:
    lowered = (text or "").lower()
    compact = re.sub(r"\D", "", text or "")
    for fragment in _identity_fragments(ticket):
        if fragment.lower() in lowered or (fragment.isdigit() and fragment in compact):
            raise RuntimeError("Refusing Discord outbound: member identity")
    return text


def house_street_label(ticket: Mapping[str, Any]) -> str:
    """Street name without the house number. Digit-free."""
    street = _ticket_street(ticket)
    label = leak_reply.street_label_for_discord(street)
    label = re.sub(r"\d+", " ", label)
    label = re.sub(r"[^A-Za-z\s]", " ", label)
    label = re.sub(r"\s+", " ", label).strip()
    return label or "unknown"


def _ticket_street(ticket: Mapping[str, Any]) -> str:
    address = ticket.get("property_address")
    if isinstance(address, dict):
        return lockout_reply.normalize_text(
            address.get("street1") or address.get("full_street") or address.get("street")
        )
    if isinstance(address, str):
        return lockout_reply.normalize_text(address)
    prop = ticket.get("property") if isinstance(ticket.get("property"), dict) else {}
    nested = prop.get("address") if isinstance(prop.get("address"), dict) else {}
    return lockout_reply.normalize_text(nested.get("street1") or nested.get("full_street"))


def room_token(ticket: Mapping[str, Any]) -> str:
    raw = ticket.get("room_number")
    if raw in (None, ""):
        raw = ticket.get("room")
    if isinstance(raw, dict):
        raw = raw.get("room_number") or raw.get("roomNumber")
    text = lockout_reply.normalize_text(raw)
    if _ROOM_RE.fullmatch(text):
        return text
    return "unknown"


def ticket_id_token(ticket: Mapping[str, Any]) -> str:
    raw = ticket.get("id")
    if raw in (None, ""):
        raw = ticket.get("ticket_id")
    text = str(raw).strip() if raw not in (None, "") else ""
    if _TICKET_ID_RE.fullmatch(text):
        return text
    return ""


def category_token(text: str) -> str:
    label = leak_alert.leak_category(text)
    cleaned = re.sub(r"[^a-z_]", "", (label or "").lower())
    return cleaned or "water"


def format_leak_ticket_text(
    *,
    house: str,
    room: str,
    category: str,
    ticket_id: str,
    ticket: Optional[Mapping[str, Any]] = None,
) -> str:
    """House street label, room, category, and ticket id. Nothing else."""
    text = (
        f"{MARKER} "
        f'house="{house or "unknown"}" '
        f"room={room or 'unknown'} "
        f"category={category or 'water'} "
        f"ticket={ticket_id}"
    )
    safe = assert_leak_ticket_safe(text, ticket_id=ticket_id, room=room or "")
    if ticket is not None:
        assert_no_member_identity(safe, ticket)
    return safe


def is_new_or_open(ticket: Mapping[str, Any], bucket: str = "") -> bool:
    status = str(ticket.get("status") or "").strip().lower()
    if status:
        return status in _NEW_OR_OPEN_STATUS
    return str(bucket or "").strip().lower() in _NEW_OR_OPEN_BUCKET


def iter_tickets(tasks: Any) -> Iterable[tuple]:
    """Yield (bucket, ticket) from a fetch_tasks() map or a flat list."""
    if isinstance(tasks, dict):
        for bucket, items in tasks.items():
            if not isinstance(items, list):
                continue
            for ticket in items:
                if isinstance(ticket, dict):
                    yield str(bucket or ""), ticket
        return
    if isinstance(tasks, list):
        for ticket in tasks:
            if isinstance(ticket, dict):
                yield "", ticket


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"tickets": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CorruptTicketState(f"corrupt leak ticket state at {path}") from exc
    if not isinstance(payload, dict):
        raise CorruptTicketState(f"corrupt leak ticket state at {path}: not an object")
    payload.setdefault("tickets", {})
    if not isinstance(payload["tickets"], dict):
        raise CorruptTicketState(f"corrupt leak ticket state at {path}: tickets is not an object")
    return payload


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def already_seen(state: Mapping[str, Any], ticket_id: str, *, live: bool) -> bool:
    row = (state.get("tickets") or {}).get(ticket_id)
    if not isinstance(row, dict):
        return False
    action = row.get("action")
    if action == "posted":
        return True
    return (not live) and action == "preview"


def record(state: Dict[str, Any], ticket_id: str, *, action: str, now: datetime) -> None:
    tickets = state.setdefault("tickets", {})
    tickets[ticket_id] = {
        "action": action,
        "at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def append_preview(row: Mapping[str, Any], path: Path = PREVIEW_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True) + "\n")


def _post_default(text: str, *, ticket_id: str, room: str) -> None:
    result = leak_reply.post_automations_discord(
        text,
        guard=lambda body: assert_leak_ticket_safe(body, ticket_id=ticket_id, room=room),
    )
    if result is None:
        raise RuntimeError("Discord automations post was not sent")


def run_for_scraper(
    tasks: Any,
    *,
    environ: Optional[Mapping[str, str]] = None,
    state_path: Path = STATE_PATH,
    preview_path: Path = PREVIEW_PATH,
    post_discord: Optional[Callable[[str], Any]] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Alert on new or open leak tickets. Returns the rows emitted this call."""
    env = os.environ if environ is None else environ
    if runtime.running_in_ci(env):  # type: ignore[arg-type]
        _log("skip_ci: GitHub Actions / CI must not post leak ticket alerts")
        return []
    if not alert_enabled(env):
        _log("disabled (LEAK_TICKET_ALERT_ENABLE)")
        return []

    dry_run = dry_run_enabled(env)
    current = now or datetime.now(timezone.utc)
    try:
        state = load_state(state_path)
    except CorruptTicketState as exc:
        _log(f"corrupt state; refusing to alert: {exc.__class__.__name__}")
        return []

    emitted: List[Dict[str, Any]] = []
    for bucket, ticket in iter_tickets(tasks):
        if not is_new_or_open(ticket, bucket):
            continue
        ticket_id = ticket_id_token(ticket)
        if not ticket_id:
            continue
        text = ticket_leak_text(ticket)
        if not detect_ticket_leak(text):
            continue
        if already_seen(state, ticket_id, live=not dry_run):
            continue
        house = house_street_label(ticket)
        room = room_token(ticket)
        category = category_token(text)
        try:
            body = format_leak_ticket_text(
                house=house,
                room=room,
                category=category,
                ticket_id=ticket_id,
                ticket=ticket,
            )
        except RuntimeError as exc:
            _log(f"refusing ticket alert: {exc.__class__.__name__}")
            continue

        action = "preview" if dry_run else "posted"
        try:
            if dry_run:
                append_preview(
                    {
                        "tag": MARKER,
                        "house": house,
                        "room": room,
                        "category": category,
                        "ticket_id": ticket_id,
                        "body": body,
                    },
                    preview_path,
                )
            elif post_discord is not None:
                post_discord(body)
            else:
                _post_default(body, ticket_id=ticket_id, room=room)
        except Exception as exc:
            _log(f"ticket alert failed; continuing: {exc.__class__.__name__}")
            continue

        record(state, ticket_id, action=action, now=current)
        save_state(state, state_path)
        emitted.append(
            {
                "ticket_id": ticket_id,
                "action": action,
                "house": house,
                "room": room,
                "category": category,
                "body": body,
            }
        )
    if emitted:
        _log(f"{emitted[-1]['action']} {len(emitted)} leak ticket(s)")
    return emitted
