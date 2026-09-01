"""Send the live new-booking Hirevire template as the first host message.

Runs inside the existing PadSplit scraper session. This SENDS via the host
GraphQL sendMessage mutation — it does not write drafts.json and it does not
click Approve or Reject.

Eviction on the occupant / rental-history card in the last 7 years follows
the auto-deny path: record the deny and do not message the member.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import requests

try:
    from padsplit_scraper.scraper import (
        DEFAULT_TIMEOUT,
        GRAPHQL_URL,
        _authed_request,
        login,
    )
except ModuleNotFoundError:  # Support python3 padsplit_scraper/scraper.py
    from scraper import DEFAULT_TIMEOUT, GRAPHQL_URL, _authed_request, login


OUTPUT_DIR = Path(__file__).resolve().parent / "output"
STATE_PATH = OUTPUT_DIR / "new_booking_first_messages.json"
TEMPLATES_DOC_URL = (
    "https://firestore.googleapis.com/v1/projects/padsplit-scrapper/"
    "databases/(default)/documents/templates/shared"
)
NEW_BOOKING_LABEL = "new booking request"
HIREVIRE_ACCOUNT_EMAIL = "liaisonventuresmanagement@gmail.com"
HIREVIRE_APPLICATION_ID = "977d344b-8592-4fc5-bd41-38717d6fa90a"
HIREVIRE_URL_RE = re.compile(
    r"https://app\.hirevire\.com/applications/"
    rf"{re.escape(HIREVIRE_APPLICATION_ID)}(?:\?lang=EN)?",
    re.IGNORECASE,
)
EVICTION_LOOKBACK_YEARS = 7
HOST_ROLE_ID = "A_1"

# Host UI mutation, verified from the live PadSplit host bundle.
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

# Occupant / rental-history card. Eviction fields only — no email, phone, SSN, photos.
RENTAL_HISTORY_QUERY = """
query occupancyRentalHistory($id: ID!) {
  occupancy(id: $id) {
    id
    user {
      totalEvictions
      latestEvictionMonth
      isInEviction
      inEvictionSince
    }
  }
}
"""


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"bookings": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"bookings": {}}
    if not isinstance(payload, dict):
        return {"bookings": {}}
    payload.setdefault("bookings", {})
    if not isinstance(payload["bookings"], dict):
        payload["bookings"] = {}
    return payload


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def live_send_enabled() -> bool:
    """Mac morning/afternoon scraper sends. GitHub Actions scrape must not."""
    if (os.getenv("GITHUB_ACTIONS") or "").strip():
        return False
    if (os.getenv("CI") or "").strip():
        return False
    return True


def parse_firestore_string_map(doc: Dict[str, Any]) -> Dict[str, str]:
    fields = doc.get("fields") if isinstance(doc, dict) else None
    if not isinstance(fields, dict):
        return {}
    parsed: Dict[str, str] = {}
    for key, value in fields.items():
        if isinstance(value, dict) and "stringValue" in value:
            parsed[str(key)] = str(value.get("stringValue") or "")
        elif isinstance(value, str):
            parsed[str(key)] = value
    return parsed


def load_live_new_booking_template(
    fetch_doc: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, str]:
    """Read the live **new booking request** card from docs/templates.html storage."""
    if fetch_doc is None:
        def fetch_doc() -> Dict[str, Any]:
            resp = requests.get(TEMPLATES_DOC_URL, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            return resp.json()

    fields = parse_firestore_string_map(fetch_doc())
    for index in range(9):
        label = (fields.get(f"n{index}") or "").strip()
        if label.lower() == NEW_BOOKING_LABEL:
            body = template_send_body(label, fields.get(f"t{index}") or "")
            verify_hirevire_template(body)
            return {"label": label, "text": body, "index": str(index)}
    raise RuntimeError("Live templates doc has no 'new booking request' card")


def template_send_body(label: str, text: str) -> str:
    body = (text or "").strip()
    prefix = (label or "").strip()
    if prefix and body.lower().startswith(prefix.lower()):
        body = body[len(prefix) :].lstrip("\n")
    return body.strip()


def verify_hirevire_template(text: str) -> str:
    """Require the Hirevire application owned by liaisonventuresmanagement@gmail.com."""
    match = HIREVIRE_URL_RE.search(text or "")
    if not match:
        raise RuntimeError(
            f"New-booking template is missing the {HIREVIRE_ACCOUNT_EMAIL} Hirevire link"
        )
    return match.group(0)


def parse_year_month(value: Optional[str]) -> Optional[datetime]:
    if value is None or value == "":
        return None
    raw = str(value).strip()
    for fmt, size in (("%Y-%m-%d", 10), ("%Y-%m", 7)):
        try:
            return datetime.strptime(raw[:size], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return parse_dt(raw)


def has_recent_eviction(
    rental_history: Optional[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> bool:
    """True when occupant rental history shows any eviction in the last 7 years."""
    history = rental_history or {}
    now = now or datetime.now(timezone.utc)
    cutoff = datetime(now.year - EVICTION_LOOKBACK_YEARS, now.month, 1, tzinfo=timezone.utc)

    if history.get("isInEviction"):
        return True

    in_eviction_since = parse_dt(history.get("inEvictionSince")) or parse_year_month(
        history.get("inEvictionSince")
    )
    if in_eviction_since and in_eviction_since >= cutoff:
        return True

    latest = parse_year_month(history.get("latestEvictionMonth"))
    total = history.get("totalEvictions")
    try:
        total_n = int(total) if total is not None else 0
    except (TypeError, ValueError):
        total_n = 0

    if latest is not None:
        return latest >= cutoff
    # Fail closed: known evictions with no month still auto-deny.
    return total_n > 0


def close_leftover_compose_tabs(
    open_tabs: Optional[List[Dict[str, Any]]],
    chat_id: str,
) -> List[Dict[str, Any]]:
    """Close leftover host compose/draft tabs for this thread before send.

    PadSplit host compose UI duplicates if old drafts stay open. GraphQL
    sendMessage does not open those tabs; this still clears leftover tab
    records for the chat so a later UI send cannot double-post.
    """
    tabs = open_tabs if open_tabs is not None else []
    closed = [tab for tab in tabs if (tab.get("chat_id") or chat_id) == chat_id]
    remaining = [tab for tab in tabs if tab not in closed]
    tabs.clear()
    tabs.extend(remaining)
    return closed


def leftover_tabs_open(open_tabs: Optional[Sequence[Dict[str, Any]]], chat_id: str) -> bool:
    return any((tab.get("chat_id") or chat_id) == chat_id for tab in (open_tabs or []))


def booking_id_from_status(booking_status: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(booking_status, dict):
        return None
    booking_id = booking_status.get("id")
    return str(booking_id) if booking_id else None


def pending_booking_status(thread: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for message in thread.get("recent_messages") or []:
        booking = message.get("bookingStatus")
        if isinstance(booking, dict) and str(booking.get("status") or "").upper() == "PENDING":
            return booking
    last = (thread.get("lastMessage") or {}).get("bookingStatus")
    if isinstance(last, dict) and str(last.get("status") or "").upper() == "PENDING":
        return last
    return None


def host_already_sent_hirevire(thread: Dict[str, Any], template_text: str = "") -> bool:
    needle = (template_text or "").strip()
    for message in thread.get("recent_messages") or []:
        if message.get("deleted"):
            continue
        sender = message.get("sender") or {}
        if sender.get("roleId") and sender.get("roleId") != HOST_ROLE_ID:
            continue
        text = message.get("text") or ""
        if HIREVIRE_URL_RE.search(text):
            return True
        if needle and needle in text:
            return True
    return False


def already_handled(state: Dict[str, Any], booking_id: str) -> bool:
    meta = (state.get("bookings") or {}).get(booking_id)
    if not isinstance(meta, dict):
        return False
    return meta.get("action") in {"sent", "auto_deny_eviction", "skipped_already_sent"}


def record_booking(
    state: Dict[str, Any],
    booking_id: str,
    *,
    chat_id: str,
    action: str,
    now: datetime,
) -> None:
    state.setdefault("bookings", {})[booking_id] = {
        "chat_id": chat_id,
        "action": action,
        "at": iso_utc(now),
    }


def send_host_message(
    session: requests.Session,
    creds: Dict[str, str],
    chat_id: str,
    text: str,
    *,
    request_fn=None,
) -> Dict[str, Any]:
    """SEND the host message on the member thread. Not a draft."""
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
        raise RuntimeError(f"sendMessage GraphQL errors: {payload['errors']}")
    sent = (
        ((payload.get("data") or {}).get("messenger") or {}).get("chat") or {}
    ).get("sendMessage") or {}
    if not sent.get("ok"):
        raise RuntimeError(f"sendMessage did not send: {payload}")
    return sent


def fetch_rental_history(
    session: requests.Session,
    creds: Dict[str, str],
    occupancy_id: str,
    *,
    request_fn=None,
) -> Dict[str, Any]:
    request_fn = request_fn or _authed_request
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": f"https://www.padsplit.com/host/occupant-profile/{occupancy_id}",
    }
    resp = request_fn(
        session,
        "POST",
        GRAPHQL_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        json={"query": RENTAL_HISTORY_QUERY, "variables": {"id": occupancy_id}},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(f"rental history GraphQL errors: {payload['errors']}")
    user = (((payload.get("data") or {}).get("occupancy") or {}).get("user")) or {}
    return {
        "totalEvictions": user.get("totalEvictions"),
        "latestEvictionMonth": user.get("latestEvictionMonth"),
        "isInEviction": user.get("isInEviction"),
        "inEvictionSince": user.get("inEvictionSince"),
    }


def notify_new_tenants_pack_for_joe(result: Dict[str, Any]) -> None:
    """Hook for Discord #new-tenants pack (Joe only).

    Out of scope for this PR: do not post and do not @ anyone.
    After send, the usual screening snapshot should wait for the Hirevire
    video before calling. Do not auto-approve.
    """
    return None


def send_first_host_message(
    *,
    chat_id: str,
    text: str,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    close_tabs_fn: Optional[Callable[[Optional[List[Dict[str, Any]]], str], List[Dict[str, Any]]]] = None,
    send_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Close leftover compose/draft tabs, then SEND one host copy."""
    closer = close_tabs_fn or close_leftover_compose_tabs
    sender = send_fn or (lambda _chat, _text: {"ok": False})
    closed = closer(leftover_compose_tabs, chat_id)
    if leftover_tabs_open(leftover_compose_tabs, chat_id):
        raise RuntimeError("leftover compose/draft tabs still open; refusing to send")
    sent = sender(chat_id, text)
    return {"closed_tabs": closed, "sent": sent}


def process_new_bookings(
    messages: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    state: Optional[Dict[str, Any]] = None,
    state_path: Path = STATE_PATH,
    load_template: Optional[Callable[[], Dict[str, str]]] = None,
    rental_history_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    close_tabs_fn: Optional[Callable[[Optional[List[Dict[str, Any]]], str], List[Dict[str, Any]]]] = None,
    send_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    notify_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
    send_enabled: bool = True,
) -> List[Dict[str, Any]]:
    """Handle each new PENDING booking once. Never Approve/Reject."""
    now = now or datetime.now(timezone.utc)
    state = state if state is not None else load_state(state_path)
    results: List[Dict[str, Any]] = []
    template: Optional[Dict[str, str]] = None

    for thread in messages:
        booking = pending_booking_status(thread)
        if not booking:
            continue
        booking_id = booking_id_from_status(booking)
        chat_id = thread.get("id")
        if not booking_id or not chat_id:
            continue
        if already_handled(state, booking_id):
            results.append({"booking_id": booking_id, "chat_id": chat_id, "action": "already_handled"})
            continue

        if template is None:
            loader = load_template or load_live_new_booking_template
            template = loader()
        text = template["text"]

        if host_already_sent_hirevire(thread, text):
            record_booking(state, booking_id, chat_id=str(chat_id), action="skipped_already_sent", now=now)
            results.append({"booking_id": booking_id, "chat_id": chat_id, "action": "skipped_already_sent"})
            continue

        occupancy = thread.get("occupancy") or {}
        occupancy_id = occupancy.get("id")
        try:
            if rental_history_fn is not None:
                rental_history = rental_history_fn(thread)
            elif occupancy_id:
                rental_history = {}  # live fetch is injected by run_for_scraper
            else:
                rental_history = {}
        except Exception as exc:
            sys.stderr.write(f"# Rental history failed for {booking_id}; skipping send: {exc}\n")
            results.append({"booking_id": booking_id, "chat_id": chat_id, "action": "skipped_rental_history_error"})
            continue

        if has_recent_eviction(rental_history, now=now):
            record_booking(state, booking_id, chat_id=str(chat_id), action="auto_deny_eviction", now=now)
            results.append({"booking_id": booking_id, "chat_id": chat_id, "action": "auto_deny_eviction"})
            continue

        if not send_enabled:
            results.append({"booking_id": booking_id, "chat_id": chat_id, "action": "would_send"})
            continue

        if send_fn is None:
            results.append({"booking_id": booking_id, "chat_id": chat_id, "action": "skipped_no_send_fn"})
            continue

        try:
            sent = send_first_host_message(
                chat_id=str(chat_id),
                text=text,
                leftover_compose_tabs=leftover_compose_tabs,
                close_tabs_fn=close_tabs_fn,
                send_fn=send_fn,
            )
        except Exception as exc:
            sys.stderr.write(f"# First host send failed for {booking_id}; continuing: {exc}\n")
            results.append({"booking_id": booking_id, "chat_id": chat_id, "action": "send_failed"})
            continue

        record_booking(state, booking_id, chat_id=str(chat_id), action="sent", now=now)
        result = {
            "booking_id": booking_id,
            "chat_id": chat_id,
            "action": "sent",
            "closed_tabs": sent.get("closed_tabs") or [],
        }
        results.append(result)
        notifier = notify_fn if notify_fn is not None else notify_new_tenants_pack_for_joe
        notifier(result)

    save_state(state, state_path)
    return results


def run_for_scraper(
    session: requests.Session,
    creds: Dict[str, str],
    messages: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    state_path: Path = STATE_PATH,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if not live_send_enabled():
        sys.stderr.write("# New-booking first host message skipped (CI must not send)\n")
        return []

    pending = [thread for thread in messages if pending_booking_status(thread)]
    if not pending:
        return []

    def send_fn(chat_id: str, text: str) -> Dict[str, Any]:
        return send_host_message(session, creds, chat_id, text)

    def rental_history_fn(thread: Dict[str, Any]) -> Dict[str, Any]:
        occupancy_id = (thread.get("occupancy") or {}).get("id")
        if not occupancy_id:
            raise RuntimeError("occupancy.id missing; cannot read rental history")
        return fetch_rental_history(session, creds, str(occupancy_id))

    return process_new_bookings(
        messages,
        now=now,
        state_path=state_path,
        rental_history_fn=rental_history_fn,
        leftover_compose_tabs=leftover_compose_tabs if leftover_compose_tabs is not None else [],
        send_fn=send_fn,
        send_enabled=True,
    )
