"""Send Hirevire once on a new PENDING booking, then pack #new-tenants for Joe.

Booking-hit source: GraphQL hostPendingBookingRequests
(`bookingRequests.all(pending: true)`). Messenger digest is join/context only.

Runs inside the existing PadSplit scraper session. This SENDS via the host
GraphQL sendMessage mutation — it does not write drafts.json and it does not
click Approve or Reject.

Eviction on the occupant / rental-history card in the last 7 years follows
the auto-deny path: record the deny and do not message the member, do not
send Hirevire, and do not post a Discord pack.

Leftover compose/draft closer is a HARD GATE before every host send.
If leftover drafts cannot be cleared: HARD SKIP that send (do not send).
The scrape continues. GraphQL sendMessage does not open compose tabs; this
gate still refuses to send when leftover tab records remain, which is the
Alexandria Haltom Rm 2 8/31 triple-bubble class of duplicate.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
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
LEFTOVER_TABS_PATH = OUTPUT_DIR / "leftover_compose_tabs.json"
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
BOOKING_HIT_SOURCE = "hostPendingBookingRequests"
DISCORD_WEBHOOK_NEW_TENANTS_ENV = "DISCORD_WEBHOOK_NEW_TENANTS"
DISCORD_JOE_USER_ID_ENV = "DISCORD_JOE_USER_ID"
# Pack text must never @ Cindy, never include these, never post as Ang.
PACK_FORBIDDEN_RE = re.compile(
    r"(?i)(\bcindy\b|ssn|social security|lock ?code|door code|"
    r"password|\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b)"
)

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

# Same bookingRequests.all node type as allRejectedBookingRequests (proven fields).
PENDING_INBOX_QUERY_RICH = """
query hostPendingBookingRequests($pending: Boolean) {
  bookingRequests {
    all(pending: $pending) {
      edges {
        node {
          id
          created
          approved
          room {
            id
            roomNumber
            name
            status
          }
        }
      }
    }
  }
}
"""

# Live host bundle query (id + approved only). Fallback if richer fields error.
PENDING_INBOX_QUERY_THIN = """
query hostPendingBookingRequests($pending: Boolean) {
  bookingRequests {
    all(pending: $pending) {
      edges {
        node {
          id
          approved
        }
      }
    }
  }
}
"""

GET_CHAT_ID_QUERY = """
query getChatId($occupancyId: Int!) {
  archived: messenger(
    messageTypes: [MOVE_OUT_PHOTOS, MOVE_OUT_CONFIRMED, TICKET_RATING, TICKET_UPDATE]
  ) {
    chats(occupancyPks: [$occupancyId], archived: true) {
      edges {
        node {
          id
        }
      }
    }
  }
  unarchived: messenger(
    messageTypes: [MOVE_OUT_PHOTOS, MOVE_OUT_CONFIRMED, TICKET_RATING, TICKET_UPDATE]
  ) {
    chats(occupancyPks: [$occupancyId], archived: false) {
      edges {
        node {
          id
        }
      }
    }
  }
}
"""


class LeftoverDraftGateError(RuntimeError):
    """Hard skip: leftover compose/draft tabs could not be cleared. Do not send."""


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


def load_leftover_compose_tabs(path: Path = LEFTOVER_TABS_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if isinstance(payload, list):
        return [tab for tab in payload if isinstance(tab, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("tabs"), list):
        return [tab for tab in payload["tabs"] if isinstance(tab, dict)]
    return []


def save_leftover_compose_tabs(
    tabs: List[Dict[str, Any]], path: Path = LEFTOVER_TABS_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tabs, indent=2) + "\n")


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

    PadSplit host compose UI duplicates if old drafts stay open (Haltom Rm 2
    8/31 triple bubble). GraphQL sendMessage does not open those tabs; this
    still clears leftover tab records for the chat so a later UI send cannot
    double-post. Live PadSplit has no leftover-tab GraphQL.
    """
    if open_tabs is None:
        raise LeftoverDraftGateError("leftover-draft closer not wired; hard skip send")
    tabs = open_tabs
    closed = [tab for tab in tabs if (tab.get("chat_id") or chat_id) == chat_id]
    remaining = [tab for tab in tabs if tab not in closed]
    tabs.clear()
    tabs.extend(remaining)
    return closed


def leftover_tabs_open(open_tabs: Optional[Sequence[Dict[str, Any]]], chat_id: str) -> bool:
    if open_tabs is None:
        return True
    return any((tab.get("chat_id") or chat_id) == chat_id for tab in open_tabs)


def require_leftover_drafts_cleared(
    open_tabs: Optional[List[Dict[str, Any]]],
    chat_id: str,
    close_tabs_fn: Optional[Callable[[Optional[List[Dict[str, Any]]], str], List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """HARD GATE: leftover drafts must be cleared before any host send.

    Hard skip (not hard-fail the scrape) if the closer is missing, raises, or
    leftover tabs for this chat remain.
    """
    if open_tabs is None:
        raise LeftoverDraftGateError("leftover-draft closer not wired; hard skip send")
    closer = close_tabs_fn or close_leftover_compose_tabs
    try:
        closed = closer(open_tabs, chat_id)
    except LeftoverDraftGateError:
        raise
    except Exception as exc:
        raise LeftoverDraftGateError(
            f"leftover compose/draft closer failed; hard skip send: {exc}"
        ) from exc
    if leftover_tabs_open(open_tabs, chat_id):
        raise LeftoverDraftGateError(
            "leftover compose/draft tabs still open; hard skip send"
        )
    return closed or []


def booking_id_from_status(booking_status: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(booking_status, dict):
        return None
    booking_id = booking_status.get("id")
    return str(booking_id) if booking_id else None


def pending_booking_status(thread: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Messenger PENDING extra — join/context only, not the booking-hit source."""
    for message in thread.get("recent_messages") or []:
        booking = message.get("bookingStatus")
        if isinstance(booking, dict) and str(booking.get("status") or "").upper() == "PENDING":
            return booking
    last = (thread.get("lastMessage") or {}).get("bookingStatus")
    if isinstance(last, dict) and str(last.get("status") or "").upper() == "PENDING":
        return last
    return None


def occupancy_pk_from_gid(gid: Optional[str]) -> Optional[int]:
    raw = str(gid or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        import base64

        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="replace")
        if ":" in decoded:
            return int(decoded.rsplit(":", 1)[-1])
    except Exception:
        return None
    return None


def parse_pending_inbox_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    edges = (
        ((payload.get("data") or {}).get("bookingRequests") or {}).get("all") or {}
    ).get("edges") or []
    nodes: List[Dict[str, Any]] = []
    for edge in edges:
        node = (edge or {}).get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict) or not node.get("id"):
            continue
        if node.get("approved") is True:
            continue
        nodes.append(node)
    return nodes


def collect_booking_hits(
    pending_inbox: Optional[Sequence[Dict[str, Any]]],
    messages: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Hits come from hostPendingBookingRequests only. Messenger is join/context."""
    threads = [thread for thread in messages if isinstance(thread, dict)]
    chats_by_occ: Dict[str, Dict[str, Any]] = {}
    chats_by_booking: Dict[str, Dict[str, Any]] = {}
    for thread in threads:
        occ_id = (thread.get("occupancy") or {}).get("id")
        if occ_id:
            chats_by_occ[str(occ_id)] = thread
        booking = pending_booking_status(thread)
        booking_id = booking_id_from_status(booking)
        if booking_id:
            chats_by_booking[booking_id] = thread

    hits: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for node in pending_inbox or []:
        if not isinstance(node, dict):
            continue
        booking_id = str(node.get("id") or "")
        if not booking_id or booking_id in seen:
            continue
        if node.get("approved") is True:
            continue
        seen.add(booking_id)
        thread = chats_by_occ.get(booking_id) or chats_by_booking.get(booking_id)
        hits.append(
            {
                "booking_id": booking_id,
                "source": BOOKING_HIT_SOURCE,
                "thread": thread,
                "pending_node": node,
            }
        )
    return hits


def host_already_sent_hirevire(thread: Optional[Dict[str, Any]], template_text: str = "") -> bool:
    if not isinstance(thread, dict):
        return False
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
    if meta.get("action") in {"auto_deny_eviction", "skipped_already_sent"}:
        return True
    return meta.get("action") == "sent" and bool(meta.get("pack_posted"))


def record_booking(
    state: Dict[str, Any],
    booking_id: str,
    *,
    chat_id: str,
    action: str,
    now: datetime,
    pack_posted: bool = False,
    source: str = BOOKING_HIT_SOURCE,
) -> None:
    state.setdefault("bookings", {})[booking_id] = {
        "chat_id": chat_id,
        "action": action,
        "pack_posted": pack_posted,
        "source": source,
        "at": iso_utc(now),
    }


def last_initial(last_name: Optional[str]) -> str:
    raw = (last_name or "").strip()
    return raw[0].upper() if raw else ""


def build_screening_snapshot(
    *,
    booking_id: str,
    thread: Optional[Dict[str, Any]],
    pending_node: Optional[Dict[str, Any]] = None,
    hirevire_sent: bool,
) -> Dict[str, Any]:
    """Safe screening snapshot for #new-tenants. No email, phone, SSN, photos, lock codes, credit."""
    occupancy = (thread or {}).get("occupancy") or {}
    user = occupancy.get("user") or {}
    room = occupancy.get("room") or (pending_node or {}).get("room") or {}
    address = ((thread or {}).get("property") or {}).get("address") or {}
    city = address.get("city") if isinstance(address.get("city"), dict) else {}
    first = (user.get("firstName") or (thread or {}).get("title") or "").strip()
    street = (address.get("street1") or "").strip()
    city_name = (city.get("name") or "").strip()
    property_line = ", ".join(part for part in (street, city_name) if part)
    room_number = room.get("roomNumber")
    return {
        "booking_id": booking_id,
        "source": BOOKING_HIT_SOURCE,
        "first_name": first,
        "last_initial": last_initial(user.get("lastName")),
        "property": property_line,
        "room": room_number,
        "move_in_date": occupancy.get("moveInDate"),
        "eviction_on_card_last_7y": False,
        "hirevire_sent": hirevire_sent,
        "wait_for_hirevire_video": True,
        "decision": "Ang taps Approve/Reject — do not auto-approve",
    }


def format_new_tenants_pack(
    snapshot: Dict[str, Any],
    *,
    joe_user_id: str = "",
) -> str:
    mention = f"<@{joe_user_id}>" if (joe_user_id or "").strip() else "Joe"
    room = snapshot.get("room")
    room_bit = f" · Rm {room}" if room not in (None, "") else ""
    name = snapshot.get("first_name") or "Applicant"
    initial = snapshot.get("last_initial") or ""
    display_name = f"{name} {initial}.".strip() if initial else name
    property_line = snapshot.get("property") or "unknown property"
    move_in = snapshot.get("move_in_date") or "unknown"
    hirevire = "sent" if snapshot.get("hirevire_sent") else "not sent"
    lines = [
        f"{mention} New pending booking — Hirevire {hirevire}. "
        "Wait for the Hirevire video before calling. "
        "Ang taps Approve/Reject (do not auto-approve).",
        "",
        f"Name: {display_name}",
        f"Property: {property_line}{room_bit}",
        f"Move-in: {move_in}",
        "Eviction card (7y): clear",
        f"Hirevire: {hirevire}",
        f"Source: {BOOKING_HIT_SOURCE}",
    ]
    text = "\n".join(lines)
    if PACK_FORBIDDEN_RE.search(text):
        raise RuntimeError("new-tenants pack failed safety check; refusing to post")
    return text


def post_new_tenants_webhook(text: str, *, post_fn=None) -> Dict[str, Any]:
    webhook = (os.getenv(DISCORD_WEBHOOK_NEW_TENANTS_ENV) or "").strip()
    if not webhook:
        return {"posted": False, "reason": "missing_webhook"}
    if PACK_FORBIDDEN_RE.search(text):
        raise RuntimeError("new-tenants pack failed safety check; refusing to post")
    poster = post_fn or (
        lambda url, payload: requests.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
    )
    # Do not set username/avatar — never post Discord as Ang.
    resp = poster(webhook, {"content": text})
    if hasattr(resp, "raise_for_status"):
        resp.raise_for_status()
    return {"posted": True}


def notify_new_tenants_pack_for_joe(
    result: Dict[str, Any],
    *,
    post_fn=None,
) -> Dict[str, Any]:
    """Post Discord #new-tenants pack (@ Joe only). CI must not post."""
    if not live_send_enabled():
        return {"posted": False, "reason": "ci"}
    snapshot = result.get("screening_snapshot") or {}
    joe_user_id = (os.getenv(DISCORD_JOE_USER_ID_ENV) or "").strip()
    text = format_new_tenants_pack(snapshot, joe_user_id=joe_user_id)
    return post_new_tenants_webhook(text, post_fn=post_fn)


def send_host_message(
    session: requests.Session,
    creds: Dict[str, str],
    chat_id: str,
    text: str,
    *,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    close_tabs_fn: Optional[Callable[[Optional[List[Dict[str, Any]]], str], List[Dict[str, Any]]]] = None,
    request_fn=None,
) -> Dict[str, Any]:
    """SEND the host message on the member thread. Not a draft.

    Leftover-draft closer is a hard gate: leftover_compose_tabs=None (unknown)
    or leftover tabs that cannot be cleared → LeftoverDraftGateError, no POST.
    """
    closed = require_leftover_drafts_cleared(
        leftover_compose_tabs, chat_id, close_tabs_fn=close_tabs_fn
    )
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
    sent = dict(sent)
    sent["closed_tabs"] = closed
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


def fetch_pending_booking_requests(
    session: requests.Session,
    creds: Dict[str, str],
    *,
    request_fn=None,
) -> List[Dict[str, Any]]:
    """Primary booking-hit source: host pending-booking inbox."""
    request_fn = request_fn or _authed_request
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://www.padsplit.com/host/",
    }

    def _post(query: str) -> Dict[str, Any]:
        resp = request_fn(
            session,
            "POST",
            GRAPHQL_URL,
            creds=creds,
            login_fn=login,
            headers=headers,
            json={"query": query, "variables": {"pending": True}},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    payload = _post(PENDING_INBOX_QUERY_RICH)
    if payload.get("errors"):
        payload = _post(PENDING_INBOX_QUERY_THIN)
        if payload.get("errors"):
            raise RuntimeError(
                f"hostPendingBookingRequests GraphQL errors: {payload['errors']}"
            )
    return parse_pending_inbox_payload(payload)


def fetch_chat_id_for_occupancy(
    session: requests.Session,
    creds: Dict[str, str],
    occupancy_id: str,
    *,
    request_fn=None,
) -> Optional[str]:
    pk = occupancy_pk_from_gid(occupancy_id)
    if pk is None:
        return None
    request_fn = request_fn or _authed_request
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://www.padsplit.com/host/communication",
    }
    resp = request_fn(
        session,
        "POST",
        GRAPHQL_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        json={"query": GET_CHAT_ID_QUERY, "variables": {"occupancyId": pk}},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        return None
    data = payload.get("data") or {}
    for key in ("unarchived", "archived"):
        edges = ((data.get(key) or {}).get("chats") or {}).get("edges") or []
        for edge in edges:
            node = (edge or {}).get("node") if isinstance(edge, dict) else None
            if isinstance(node, dict) and node.get("id"):
                return str(node["id"])
    return None


def send_first_host_message(
    *,
    chat_id: str,
    text: str,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    close_tabs_fn: Optional[Callable[[Optional[List[Dict[str, Any]]], str], List[Dict[str, Any]]]] = None,
    send_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Close leftover compose/draft tabs (hard gate), then SEND one host copy."""
    closed = require_leftover_drafts_cleared(
        leftover_compose_tabs, chat_id, close_tabs_fn=close_tabs_fn
    )
    sender = send_fn or (lambda _chat, _text: {"ok": False})
    sent = sender(chat_id, text)
    return {"closed_tabs": closed, "sent": sent}


def _occupancy_id_for_hit(hit: Dict[str, Any]) -> Optional[str]:
    thread = hit.get("thread") or {}
    occ_id = (thread.get("occupancy") or {}).get("id")
    if occ_id:
        return str(occ_id)
    booking_id = hit.get("booking_id")
    return str(booking_id) if booking_id else None


def _post_pack_for_result(
    result: Dict[str, Any],
    *,
    notify_fn: Optional[Callable[[Dict[str, Any]], Any]],
) -> Dict[str, Any]:
    notifier = notify_fn if notify_fn is not None else notify_new_tenants_pack_for_joe
    posted = notifier(result)
    if posted is None:
        return {"posted": False, "reason": "hook_noop"}
    if isinstance(posted, dict):
        return posted
    return {"posted": bool(posted)}


def process_new_bookings(
    messages: Iterable[Dict[str, Any]],
    *,
    pending_inbox: Optional[Sequence[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    state: Optional[Dict[str, Any]] = None,
    state_path: Path = STATE_PATH,
    load_template: Optional[Callable[[], Dict[str, str]]] = None,
    rental_history_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    leftover_compose_tabs: Optional[List[Dict[str, Any]]] = None,
    close_tabs_fn: Optional[Callable[[Optional[List[Dict[str, Any]]], str], List[Dict[str, Any]]]] = None,
    send_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    notify_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
    resolve_chat_id_fn: Optional[Callable[[str], Optional[str]]] = None,
    send_enabled: bool = True,
) -> List[Dict[str, Any]]:
    """Handle each hostPendingBookingRequests hit once. Never Approve/Reject.

    pending_inbox is required as the booking-hit source. An empty inbox means
    no hits — messenger PENDING alone is not enough (digest inference).
    leftover_compose_tabs=None hard-skips send (gate not wired).
    """
    now = now or datetime.now(timezone.utc)
    state = state if state is not None else load_state(state_path)
    results: List[Dict[str, Any]] = []
    template: Optional[Dict[str, str]] = None
    hits = collect_booking_hits(pending_inbox, messages)

    for hit in hits:
        booking_id = hit["booking_id"]
        thread = hit.get("thread")
        chat_id = (thread or {}).get("id")
        if not chat_id and resolve_chat_id_fn is not None:
            occ_id = _occupancy_id_for_hit(hit)
            if occ_id:
                try:
                    chat_id = resolve_chat_id_fn(occ_id)
                except Exception as exc:
                    sys.stderr.write(f"# getChatId failed for {booking_id}: {exc}\n")
                    chat_id = None
        if already_handled(state, booking_id):
            results.append(
                {
                    "booking_id": booking_id,
                    "chat_id": chat_id,
                    "action": "already_handled",
                    "source": BOOKING_HIT_SOURCE,
                }
            )
            continue

        meta = (state.get("bookings") or {}).get(booking_id) or {}
        hirevire_already = meta.get("action") == "sent" or host_already_sent_hirevire(
            thread, (template or {}).get("text") if template else ""
        )

        if template is None:
            loader = load_template or load_live_new_booking_template
            template = loader()
        text = template["text"]
        if not hirevire_already:
            hirevire_already = host_already_sent_hirevire(thread, text)

        occupancy_id = _occupancy_id_for_hit(hit)
        if not hirevire_already:
            try:
                if rental_history_fn is not None:
                    rental_history = rental_history_fn(thread or {"occupancy": {"id": occupancy_id}})
                elif occupancy_id:
                    rental_history = {}  # live fetch is injected by run_for_scraper
                else:
                    rental_history = {}
            except Exception as exc:
                sys.stderr.write(f"# Rental history failed for {booking_id}; skipping send: {exc}\n")
                results.append(
                    {
                        "booking_id": booking_id,
                        "chat_id": chat_id,
                        "action": "skipped_rental_history_error",
                        "source": BOOKING_HIT_SOURCE,
                    }
                )
                continue

            if has_recent_eviction(rental_history, now=now):
                record_booking(
                    state,
                    booking_id,
                    chat_id=str(chat_id or ""),
                    action="auto_deny_eviction",
                    now=now,
                )
                results.append(
                    {
                        "booking_id": booking_id,
                        "chat_id": chat_id,
                        "action": "auto_deny_eviction",
                        "source": BOOKING_HIT_SOURCE,
                    }
                )
                continue

            if not chat_id:
                results.append(
                    {
                        "booking_id": booking_id,
                        "chat_id": None,
                        "action": "skipped_no_chat",
                        "source": BOOKING_HIT_SOURCE,
                    }
                )
                continue

            if not send_enabled:
                results.append(
                    {
                        "booking_id": booking_id,
                        "chat_id": chat_id,
                        "action": "would_send",
                        "source": BOOKING_HIT_SOURCE,
                    }
                )
                continue

            if send_fn is None:
                results.append(
                    {
                        "booking_id": booking_id,
                        "chat_id": chat_id,
                        "action": "skipped_no_send_fn",
                        "source": BOOKING_HIT_SOURCE,
                    }
                )
                continue

            try:
                sent = send_first_host_message(
                    chat_id=str(chat_id),
                    text=text,
                    leftover_compose_tabs=leftover_compose_tabs,
                    close_tabs_fn=close_tabs_fn,
                    send_fn=send_fn,
                )
            except LeftoverDraftGateError as exc:
                sys.stderr.write(f"# Leftover-draft hard skip for {booking_id}: {exc}\n")
                results.append(
                    {
                        "booking_id": booking_id,
                        "chat_id": chat_id,
                        "action": "skipped_leftover_drafts",
                        "source": BOOKING_HIT_SOURCE,
                    }
                )
                continue
            except Exception as exc:
                sys.stderr.write(f"# First host send failed for {booking_id}; continuing: {exc}\n")
                results.append(
                    {
                        "booking_id": booking_id,
                        "chat_id": chat_id,
                        "action": "send_failed",
                        "source": BOOKING_HIT_SOURCE,
                    }
                )
                continue

            record_booking(
                state,
                booking_id,
                chat_id=str(chat_id),
                action="sent",
                now=now,
                pack_posted=False,
            )
            hirevire_already = True
            snapshot = build_screening_snapshot(
                booking_id=booking_id,
                thread=thread,
                pending_node=hit.get("pending_node"),
                hirevire_sent=True,
            )
            result = {
                "booking_id": booking_id,
                "chat_id": chat_id,
                "action": "sent",
                "source": BOOKING_HIT_SOURCE,
                "closed_tabs": sent.get("closed_tabs") or [],
                "screening_snapshot": snapshot,
            }
        else:
            snapshot = build_screening_snapshot(
                booking_id=booking_id,
                thread=thread,
                pending_node=hit.get("pending_node"),
                hirevire_sent=True,
            )
            result = {
                "booking_id": booking_id,
                "chat_id": chat_id,
                "action": "skipped_already_sent",
                "source": BOOKING_HIT_SOURCE,
                "screening_snapshot": snapshot,
            }
            if meta.get("action") != "sent":
                record_booking(
                    state,
                    booking_id,
                    chat_id=str(chat_id or ""),
                    action="skipped_already_sent",
                    now=now,
                    pack_posted=False,
                )

        if not send_enabled:
            if result.get("action") != "sent":
                results.append(result)
            continue

        try:
            pack = _post_pack_for_result(result, notify_fn=notify_fn)
        except Exception as exc:
            sys.stderr.write(f"# new-tenants pack failed for {booking_id}: {exc}\n")
            pack = {"posted": False, "reason": "post_failed"}
        result["pack_posted"] = bool(pack.get("posted"))
        pack_action = result["action"]
        if pack_action == "skipped_already_sent" and meta.get("action") == "sent":
            pack_action = "sent"
        record_booking(
            state,
            booking_id,
            chat_id=str(chat_id or ""),
            action=pack_action,
            now=now,
            pack_posted=bool(pack.get("posted")),
        )
        if result["action"] == "skipped_already_sent" and result.get("pack_posted"):
            result["action"] = "pack_posted"
        results.append(result)

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
    leftover_tabs_path: Path = LEFTOVER_TABS_PATH,
) -> List[Dict[str, Any]]:
    if not live_send_enabled():
        sys.stderr.write("# New-booking first host message skipped (CI must not send)\n")
        return []

    try:
        pending_inbox = fetch_pending_booking_requests(session, creds)
    except Exception as exc:
        sys.stderr.write(
            f"# hostPendingBookingRequests failed; not inferring from message digest: {exc}\n"
        )
        return []

    hits = collect_booking_hits(pending_inbox, messages)
    if not hits:
        return []

    tabs = leftover_compose_tabs
    if tabs is None:
        tabs = load_leftover_compose_tabs(leftover_tabs_path)

    def send_fn(chat_id: str, text: str) -> Dict[str, Any]:
        return send_host_message(
            session,
            creds,
            chat_id,
            text,
            leftover_compose_tabs=tabs,
        )

    def rental_history_fn(thread: Dict[str, Any]) -> Dict[str, Any]:
        occupancy_id = (thread.get("occupancy") or {}).get("id")
        if not occupancy_id:
            raise RuntimeError("occupancy.id missing; cannot read rental history")
        return fetch_rental_history(session, creds, str(occupancy_id))

    def resolve_chat_id_fn(occupancy_id: str) -> Optional[str]:
        return fetch_chat_id_for_occupancy(session, creds, occupancy_id)

    results = process_new_bookings(
        messages,
        pending_inbox=pending_inbox,
        now=now,
        state_path=state_path,
        rental_history_fn=rental_history_fn,
        leftover_compose_tabs=tabs,
        send_fn=send_fn,
        resolve_chat_id_fn=resolve_chat_id_fn,
        send_enabled=True,
    )
    save_leftover_compose_tabs(tabs, leftover_tabs_path)
    return results
