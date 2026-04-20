import argparse
import json
import os
import sys
from statistics import median
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv


# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
BASE_URL = "https://www.padsplit.com"
LOGIN_URL = f"{BASE_URL}/api/auth/login"
GRAPHQL_URL = f"{BASE_URL}/api/graphql/"
PARTNER_PROPERTIES_URL = f"{BASE_URL}/api/partner/properties/"
PARTNER_ROOMS_URL = f"{BASE_URL}/api/partner/rooms/"
PARTNER_EARNINGS_URL = f"{BASE_URL}/api/partner/earnings/"
PARTNER_MONTHLY_FLIP_URL = f"{BASE_URL}/api/partner/metrics/properties/monthly-average-days-to-flip/"
PARTNER_MONTHLY_OCCUPANCY_URL = f"{BASE_URL}/api/partner/metrics/properties/monthly-average-occupancy/"
PARTNER_TENURE_SUMMARY_URL = f"{BASE_URL}/api/partner/metrics/properties/total-average-tenure-days/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = (10, 30)  # (connect, read)
RECENT_DAYS = 5

# ==========================================
# GRAPHQL QUERIES
# ==========================================
CHAT_LIST_QUERY = """
    query chatList($first: Int, $after: String, $searchMember: String, $searchProperty: String, $moveIn: Boolean, $moveOut: Boolean, $active: Boolean, $archived: Boolean) {
  messenger(
    messageTypes: [BOOKING_STATUS, MOVE_IN, MOVE_OUT_PHOTOS, MOVE_OUT_CONFIRMED, TICKET_RATING, TICKET_UPDATE, PAYMENT_EXTENSION_REQUEST, PAYMENT_EXTENSION_APPROVED, PAYMENT_EXTENSION_REJECTED, COME_LIVE_WITH_ME_EXPERIMENT, CHANGE_MOVE_IN_REQUEST, APPROVE_MOVE_IN_REQUEST, DENY_MOVE_IN_REQUEST]
  ) {
    chats(
      first: $first
      after: $after
      searchMember: $searchMember
      searchProperty: $searchProperty
      moveIn: $moveIn
      moveOut: $moveOut
      active: $active
      archived: $archived
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        node {
          ...baseChatListFields
        }
      }
    }
  }
}

    fragment baseChatListFields on MessengerChatType {
  id
  title
  chatType
  isArchived
  occupancy {
    moveInDate
    moveOutDate
    conditionalEligibilityApplied
    room {
      pk
      roomNumber
    }
    user {
      picture
      firstName
      lastName
      displayName
    }
  }
  property {
    host {
      firstName
      lastName
      displayName
      picture
    }
    description
    address {
      street1
      street2
      zip
      city {
        name
        state {
          name
        }
      }
    }
  }
  isCancelled
  lastMessage {
    id
    created
    text
    deleted
    extra {
      ... on ApproveMoveInDateRequestChatExtraType {
        newMoveInDate
      }
      ... on ChangeMoveInDateRequestChatExtraType {
        newMoveInDate
        originalMoveInDate
      }
      ... on DenyMoveInDateRequestChatExtraType {
        originalMoveInDate
      }
    }
    messageType
    sender {
      pk
      firstName
      lastName
      displayName
    }
    attachments {
      mediaType
      deleted
    }
    paymentExtensionStatus {
      ...basePaymentExtensionRequestFields
    }
    ticketStatus {
      ...baseMessengerTicketStatusFields
    }
    bookingStatus {
      id
      created
      status
    }
  }
  member {
    seenAt
    isPinned
    isUnread
  }
}

    fragment basePaymentExtensionRequestFields on MessengerPaymentExtensionStatusType {
  newDate
  status
  id
  created
  changedFromDate
  date
  paymentExtensionRequest {
    id
    reason
    comment
    minimumPayment
    status
    dateChanged
    endDate
  }
}


    fragment baseMessengerTicketStatusFields on MessengerMessageTicketStatus {
  id
  created
  status
  canRate
  ticket {
    id
    author {
      firstName
      lastName
      id
      displayName
    }
    details
    location
    status
    rating
    comment
    category
    onHoldReason
    withdrawReason
  }
}
"""

MESSAGE_LIST_QUERY = """
    query messageList($chatId: ID!, $first: Int, $after: String) {
  messenger(
    messageTypes: [BOOKING_STATUS, MOVE_IN, CHANGE_MOVE_IN_REQUEST, APPROVE_MOVE_IN_REQUEST, DENY_MOVE_IN_REQUEST, MOVE_OUT_PHOTOS, MOVE_OUT_CONFIRMED, TICKET_RATING, TICKET_UPDATE, PAYMENT_EXTENSION_REQUEST, PAYMENT_EXTENSION_APPROVED, PAYMENT_EXTENSION_REJECTED, REFERRALS_MONTHLY_UPDATE, COME_LIVE_WITH_ME_EXPERIMENT]
  ) {
    chat(id: $chatId) {
      messages(first: $first, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          node {
            ...baseMessageListFields
          }
        }
      }
    }
  }
}

    fragment baseMessageListFields on MessengerMessageType {
  id
  text
  created
  messageType
  deleted
  isBroadcast
  extra {
    ... on ChangeMoveInDateRequestChatExtraType {
      changeMoveInDateRequest {
        id
        decision
        moveInDate
        previousMoveInDate
        stale
        pk
      }
      originalMoveInDate
      newMoveInDate
    }
    ... on ApproveMoveInDateRequestChatExtraType {
      changeMoveInDateRequest {
        id
        decision
        moveInDate
        previousMoveInDate
        stale
        pk
      }
      newMoveInDate
    }
    ... on DenyMoveInDateRequestChatExtraType {
      changeMoveInDateRequest {
        id
        decision
        moveInDate
        previousMoveInDate
        stale
        pk
      }
      originalMoveInDate
    }
  }
  sender {
    id
    pk
    roleId
    picture
    preferredPicture
    firstName
    lastName
    isActive
    displayName
    padmateProfileId
  }
  attachments {
    id
    deleted
    mediaType
    location
    filename
  }
  reactions {
    id
    reaction
  }
  paymentExtensionStatus {
    ...basePaymentExtensionRequestFields
  }
  ticketStatus {
    ...baseMessengerTicketStatusFields
  }
  bookingStatus {
    id
    created
    status
    verificationTimeInHours
  }
}

    fragment basePaymentExtensionRequestFields on MessengerPaymentExtensionStatusType {
  newDate
  status
  id
  created
  changedFromDate
  date
  paymentExtensionRequest {
    id
    reason
    comment
    minimumPayment
    status
    dateChanged
    endDate
  }
}

    fragment baseMessengerTicketStatusFields on MessengerMessageTicketStatus {
  id
  created
  status
  canRate
  ticket {
    id
    author {
      firstName
      lastName
      id
      displayName
    }
    details
    location
    status
    rating
    comment
    category
    onHoldReason
    withdrawReason
  }
}
"""


# ==========================================
# SCRAPER FUNCTIONS
# ==========================================
def load_credentials() -> Dict[str, str]:
    load_dotenv()
    email = os.getenv("PADSPLIT_EMAIL")
    password = os.getenv("PADSPLIT_PASSWORD")
    if not email or not password:
        sys.exit("Missing PADSPLIT_EMAIL or PADSPLIT_PASSWORD in environment/.env")
    return {"email": email, "password": password}


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PATCH"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _authed_request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    creds: Dict[str, str],
    login_fn,
    **kwargs,
) -> requests.Response:
    resp = session.request(method, url, **kwargs)
    if resp.status_code in (401, 403):
        login_fn(session, creds["email"], creds["password"])
        resp = session.request(method, url, **kwargs)
        if resp.status_code in (401, 403):
            raise RuntimeError("Session could not be refreshed — check credentials")
    return resp


def login(session: requests.Session, email: str, password: str) -> None:
    payload = {
        "email": email,
        "password": password,
        "mfa_code": "",
        "force_login": True,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": BASE_URL + "/",
    }
    resp = session.post(LOGIN_URL, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed: {resp.status_code} {resp.text}")
    if not session.cookies.get("sessionid"):
        raise RuntimeError("Login did not set sessionid cookie")


def fetch_messages(session: requests.Session, creds: Dict[str, str], page_size: int = 10) -> List[Dict]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/host/communication",
    }

    all_edges: List[Dict] = []
    after: Optional[str] = None

    while True:
        variables = {
            "first": page_size,
            "after": after,
            "searchMember": "",
            "searchProperty": "",
            "moveIn": False,
            "moveOut": False,
            "active": False,
            "archived": False,
        }
        resp = _authed_request(
            session,
            "POST",
            GRAPHQL_URL,
            creds=creds,
            login_fn=login,
            headers=headers,
            json={"query": CHAT_LIST_QUERY, "variables": variables},
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL returned errors: {data['errors']}")

        chat_list = data.get("data", {}).get("messenger", {}).get("chats")
        if not chat_list:
            break

        edges = chat_list.get("edges", [])
        all_edges.extend(edges)

        page_info = chat_list.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break

    # Return just the nodes (chats) to match the requested output shape
    return [edge.get("node") for edge in all_edges if edge.get("node")]


def fetch_thread_messages(
    session: requests.Session, creds: Dict[str, str], chat_id: str, first: int = 10
) -> List[Dict]:
    """Fetch the most recent `first` messages for a single chat thread."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/host/communication/{chat_id}",
    }
    resp = _authed_request(
        session,
        "POST",
        GRAPHQL_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        json={"query": MESSAGE_LIST_QUERY, "variables": {"chatId": chat_id, "first": first}},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        sys.stderr.write(f"GraphQL errors fetching thread {chat_id}: {data['errors']}\n")
        return []
    edges = (
        data.get("data", {})
        .get("messenger", {})
        .get("chat", {})
        .get("messages", {})
        .get("edges", [])
    ) or []
    return [e["node"] for e in edges if e.get("node")]


def fetch_tasks(session: requests.Session, creds: Dict[str, str]) -> Dict[str, List[Dict]]:
    """Fetch maintenance tickets and group them by status to mirror UI buckets."""

    headers = {
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/host/tasks",
    }

    resp = _authed_request(
        session,
        "GET",
        f"{BASE_URL}/api/admin-new/property/maintenance/tickets/",
        creds=creds,
        login_fn=login,
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    tickets = resp.json()
    if not isinstance(tickets, list):
        tickets = []

    # Map API status values to UI bucket names
    status_map = {
        "submitted": "Requests",
        "accepted": "Open",
        "in_progress": "In Progress",
        "on_hold": "On Hold",
        "eviction": "Eviction",
        "completed": "Complete",
    }
    status_order = ["Requests", "Open", "In Progress", "On Hold", "Eviction", "Complete", "Other"]
    grouped: Dict[str, List[Dict]] = {s: [] for s in status_order}

    for ticket in tickets:
        raw_status = ticket.get("status") or ""
        bucket = status_map.get(raw_status, "Other")
        grouped[bucket].append(ticket)

    # Remove empty buckets for cleaner output
    return {k: v for k, v in grouped.items() if v}


def fetch_properties_stats(session: requests.Session, creds: Dict[str, str]) -> List[Dict]:
    """Fetch per-property occupancy stats used by the dashboard stats page."""

    headers = {
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/host/dashboard",
    }
    resp = _authed_request(
        session,
        "GET",
        PARTNER_PROPERTIES_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        return []
    return payload


def fetch_rooms(session: requests.Session, creds: Dict[str, str], page_size: int = 50) -> List[Dict]:
    """Fetch all rooms with pagination."""

    headers = {
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/host/rooms",
    }
    all_rooms: List[Dict] = []
    page = 1
    while True:
        resp = _authed_request(
            session,
            "GET",
            f"{PARTNER_ROOMS_URL}?page_size={page_size}&page={page}",
            creds=creds,
            login_fn=login,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            break
        results = payload.get("results", [])
        if isinstance(results, list):
            all_rooms.extend(r for r in results if isinstance(r, dict))
        if not payload.get("next"):
            break
        page += 1
    return all_rooms


def fetch_earnings(session: requests.Session, creds: Dict[str, str]) -> Dict[str, Any]:
    """Fetch monthly earnings history."""

    headers = {
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/host/earnings",
    }
    resp = _authed_request(
        session,
        "GET",
        PARTNER_EARNINGS_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        return {}
    return payload


def fetch_performance_history(session: requests.Session, creds: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """Fetch portfolio-level monthly performance history from host performance endpoints."""

    headers = {
        "Accept": "application/json",
        "Referer": f"{BASE_URL}/host/performance/timetoflip",
    }

    flip_resp = _authed_request(
        session,
        "GET",
        PARTNER_MONTHLY_FLIP_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )
    flip_resp.raise_for_status()
    flip_data = flip_resp.json() if flip_resp.content else {}

    occ_resp = _authed_request(
        session,
        "GET",
        PARTNER_MONTHLY_OCCUPANCY_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )
    occ_resp.raise_for_status()
    occ_data = occ_resp.json() if occ_resp.content else {}

    tenure_resp = _authed_request(
        session,
        "GET",
        PARTNER_TENURE_SUMMARY_URL,
        creds=creds,
        login_fn=login,
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )
    tenure_resp.raise_for_status()
    tenure_data = tenure_resp.json() if tenure_resp.content else {}

    months: Dict[str, Dict[str, Any]] = {}

    for row in (flip_data.get("monthly_averages") if isinstance(flip_data, dict) else []) or []:
        if not isinstance(row, dict):
            continue
        year = int(_to_num(row.get("year")))
        month = int(_to_num(row.get("month")))
        if year <= 0 or month <= 0:
            continue
        key = f"{year:04d}-{month:02d}"
        months.setdefault(key, {})["avg_flip_days"] = round(_to_num(row.get("average_days_to_flip")), 1)

    for row in (occ_data.get("monthly_averages") if isinstance(occ_data, dict) else []) or []:
        if not isinstance(row, dict):
            continue
        year = int(_to_num(row.get("year")))
        month = int(_to_num(row.get("month")))
        if year <= 0 or month <= 0:
            continue
        key = f"{year:04d}-{month:02d}"
        months.setdefault(key, {})["occupancy_pct"] = round(_to_num(row.get("occupancy")), 1)

    # Tenure endpoint is currently portfolio-level summary (not monthly), keep as context only.
    avg_tenure_summary = (
        round(_to_num(tenure_data.get("average_portfolio_tenure")), 1)
        if isinstance(tenure_data, dict)
        else 0.0
    )
    for key in months:
        months[key]["avg_tenure_days"] = avg_tenure_summary

    return months


def compute_monthly_kpis(month_data: Dict[str, Any]) -> Dict[str, Any]:
    """Approximate monthly bonuses/penalties using historical metrics that are available."""

    occupancy_pct = round(_to_num(month_data.get("occupancy_pct")), 1)
    avg_flip_days = round(_to_num(month_data.get("avg_flip_days")), 1)
    avg_tenure_days = round(_to_num(month_data.get("avg_tenure_days")), 1)

    bonus_items: List[Dict[str, Any]] = []
    penalty_items: List[Dict[str, Any]] = []
    score = 100

    if occupancy_pct >= 95:
        score += 10
        bonus_items.append({"label": "occupancy >= 95%", "points": 10})
    elif occupancy_pct >= 88:
        score += 5
        bonus_items.append({"label": "occupancy 88-94%", "points": 5})

    # Historical endpoint currently exposes only a portfolio tenure summary, not true per-month tenure.
    if avg_tenure_days > 120:
        score += 10
        bonus_items.append({"label": "avg tenure > 120d", "points": 10})

    if occupancy_pct < 75:
        score -= 20
        penalty_items.append({"label": "occupancy < 75%", "points": -20, "count": 1})
    elif occupancy_pct < 88:
        score -= 8
        penalty_items.append({"label": "occupancy 75-87%", "points": -8, "count": 1})

    # Approximation: if monthly average is above threshold, count as one trigger.
    if avg_flip_days > 5:
        score -= 5
        penalty_items.append({"label": "flip rooms > 5d (approx)", "points": -5, "count": 1})

    score = max(0, min(125, int(round(score))))
    return {
        "score": score,
        "bonuses": bonus_items,
        "penalties": penalty_items,
        "partial": True,
        "note": "ticket penalties not available for historical months",
    }


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_num(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return 0.0
    return 0.0


def _find_property_key(room: Dict[str, Any]) -> Optional[str]:
    candidates = [
        room.get("psproperty_id"),
        room.get("property_id"),
        room.get("property_pk"),
        room.get("property"),
        (room.get("property_data") or {}).get("id") if isinstance(room.get("property_data"), dict) else None,
    ]
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        return str(candidate)
    address = room.get("property_address") or room.get("property_name")
    if address:
        return str(address)
    return None


def _room_property_label(room: Dict[str, Any]) -> str:
    address_obj = room.get("address")
    if isinstance(address_obj, dict):
        street = str(address_obj.get("full_street") or "").strip()
        city = str(address_obj.get("city") or "").strip()
        state = str(address_obj.get("state") or "").strip()
        if street and city and state:
            return f"{street}, {city}, {state}"
        if street:
            return street

    parts = []
    address = room.get("property_address")
    location = room.get("property_location")
    if address:
        parts.append(str(address))
    if location:
        parts.append(str(location))
    if parts:
        return ", ".join(parts)
    prop = room.get("property_data")
    if isinstance(prop, dict):
        maybe_address = prop.get("address") or prop.get("display_address") or prop.get("name")
        if maybe_address:
            return str(maybe_address)
    return "Unknown Property"


def _extract_earnings_rows(earnings_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("earnings", "results", "data", "items"):
        val = earnings_payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return []


def _find_net_revenue(row: Dict[str, Any]) -> float:
    for key in ("net_revenue", "netRevenue", "net", "host_net", "hostNet", "owner_net"):
        if key in row:
            return _to_num(row.get(key))
    return 0.0


def _flatten_tasks(tasks_by_bucket: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    tickets: List[Dict[str, Any]] = []
    for ticket_list in tasks_by_bucket.values():
        if not isinstance(ticket_list, list):
            continue
        for ticket in ticket_list:
            if isinstance(ticket, dict):
                tickets.append(ticket)
    return tickets


def compute_kpis(
    rooms: List[Dict[str, Any]],
    properties: List[Dict[str, Any]],
    earnings_payload: Dict[str, Any],
    tasks_by_bucket: Dict[str, List[Dict[str, Any]]],
    now: datetime,
) -> Dict[str, Any]:
    listed_rooms = [r for r in rooms if str(r.get("detailed_status", "")).lower() == "listed"]
    occupied_rooms = [r for r in rooms if str(r.get("detailed_status", "")).lower() == "occupied"]
    flip_rooms = [r for r in rooms if str(r.get("detailed_status", "")).lower() == "flip-room"]
    move_in_rooms = [r for r in rooms if str(r.get("detailed_status", "")).lower() == "move-in"]

    listed_days = [_to_num(r.get("days_in_current_status")) for r in listed_rooms]
    occupied_days = [_to_num(r.get("days_in_current_status")) for r in occupied_rooms]
    flip_days = [_to_num(r.get("days_in_current_status")) for r in flip_rooms]

    listed_over_14 = [r for r in listed_rooms if _to_num(r.get("days_in_current_status")) > 14]
    listed_over_21 = [r for r in listed_rooms if _to_num(r.get("days_in_current_status")) > 21]
    flip_over_5 = [r for r in flip_rooms if _to_num(r.get("days_in_current_status")) > 5]

    total_rooms = sum(len(p.get("rooms", [])) for p in properties if isinstance(p.get("rooms"), list))
    if total_rooms == 0:
        total_rooms = len(rooms)
    occupied_from_properties = int(sum(_to_num(p.get("occupied")) for p in properties))
    inactive_from_properties = int(sum(_to_num(p.get("inactive")) for p in properties))
    listed_count = len(listed_rooms)
    flip_count = len(flip_rooms)
    occupied = occupied_from_properties if occupied_from_properties > 0 else len(occupied_rooms)

    effective_total = max(total_rooms - inactive_from_properties, 0)
    occupancy_pct = round((occupied / effective_total) * 100, 2) if effective_total else 0.0

    tickets = _flatten_tasks(tasks_by_bucket)
    open_statuses = {"submitted", "accepted", "in_progress", "on_hold", "eviction"}
    open_tickets: List[Dict[str, Any]] = []
    open_ticket_ages: List[float] = []
    tickets_over_7d = 0
    tickets_over_14d = 0
    for ticket in tickets:
        status = str(ticket.get("status", "")).lower()
        if status not in open_statuses:
            continue
        created_dt = _parse_iso(ticket.get("created"))
        age_days = 0.0
        if created_dt:
            age_days = max((now - created_dt).total_seconds() / 86400.0, 0.0)
        if age_days > 7:
            tickets_over_7d += 1
        if age_days > 14:
            tickets_over_14d += 1
        open_ticket_ages.append(age_days)
        open_tickets.append(
            {
                "id": ticket.get("id"),
                "status": ticket.get("status"),
                "category": ticket.get("category"),
                "location": ticket.get("location"),
                "room_number": ticket.get("room_number"),
                "details": ticket.get("details"),
                "property_id": ticket.get("property_id"),
                "property_address": (ticket.get("property_address") or {}).get("street1")
                if isinstance(ticket.get("property_address"), dict)
                else None,
                "created": ticket.get("created"),
                "age_days": round(age_days, 1),
            }
        )
    open_tickets.sort(key=lambda t: _to_num(t.get("age_days")), reverse=True)

    earnings_rows = _extract_earnings_rows(earnings_payload)
    earnings_rows_sorted = sorted(
        earnings_rows,
        key=lambda r: (_parse_iso(r.get("month")) or _parse_iso(r.get("date")) or datetime.min.replace(tzinfo=timezone.utc)),
    )
    monthly_revenue_series = []
    for row in earnings_rows_sorted:
        net = _find_net_revenue(row)
        label = str(row.get("month") or row.get("date") or row.get("label") or "")
        monthly_revenue_series.append({"label": label, "net_revenue": round(net, 2)})
    monthly_net_revenue = monthly_revenue_series[-1]["net_revenue"] if monthly_revenue_series else 0.0
    revenue_per_room = round(monthly_net_revenue / total_rooms, 2) if total_rooms else 0.0

    property_room_map: Dict[str, Dict[str, Any]] = {}
    for p in properties:
        pid = str(p.get("id", ""))
        label = ", ".join([str(p.get("address") or "").strip(), str(p.get("location") or "").strip()]).strip(", ")
        property_room_map[pid] = {
            "property_id": p.get("id"),
            "property": label or str(p.get("address") or p.get("location") or "Unknown Property"),
            "rooms": len(p.get("rooms", [])) if isinstance(p.get("rooms"), list) else int(_to_num(p.get("rooms"))),
            "occupied": int(_to_num(p.get("occupied"))),
            "vacant": int(_to_num(p.get("vacant"))),
            "inactive": int(_to_num(p.get("inactive"))),
            "needs_flip": int(_to_num(p.get("needs_flip"))),
            "move_in": int(_to_num(p.get("move_in"))),
            "listed": 0,
            "flip": 0,
        }
    for room in rooms:
        key = _find_property_key(room)
        if not key:
            continue
        if key not in property_room_map:
            property_room_map[key] = {
                "property_id": key,
                "property": _room_property_label(room),
                "rooms": 0,
                "occupied": 0,
                "vacant": 0,
                "inactive": 0,
                "needs_flip": 0,
                "move_in": 0,
                "listed": 0,
                "flip": 0,
            }
        property_room_map[key]["rooms"] += 1
        status = str(room.get("detailed_status", "")).lower()
        if status == "listed":
            property_room_map[key]["listed"] += 1
        if status == "flip-room":
            property_room_map[key]["flip"] += 1
    per_property = []
    for item in property_room_map.values():
        denom = max(item["rooms"] - item["inactive"], 0)
        item["occupancy_pct"] = round((item["occupied"] / denom) * 100, 2) if denom else 0.0
        item["mini_score"] = max(
            0,
            min(
                100,
                round(
                    item["occupancy_pct"]
                    - (item["listed"] * 2.0)
                    - (item["flip"] * 3.0),
                    1,
                ),
            ),
        )
        per_property.append(item)
    per_property.sort(key=lambda p: p.get("occupancy_pct", 0.0))

    avg_listed_days = round(sum(listed_days) / len(listed_days), 1) if listed_days else 0.0
    max_listed_days = int(max(listed_days)) if listed_days else 0
    avg_tenure_days = round(sum(occupied_days) / len(occupied_days), 1) if occupied_days else 0.0
    median_tenure_days = int(round(median(occupied_days))) if occupied_days else 0
    avg_flip_days = round(sum(flip_days) / len(flip_days), 1) if flip_days else 0.0
    max_flip_days = int(max(flip_days)) if flip_days else 0
    avg_ticket_age_days = round(sum(open_ticket_ages) / len(open_ticket_ages), 1) if open_ticket_ages else 0.0

    score = 100
    bonus_items = []
    penalty_items = []

    if occupancy_pct >= 95:
        score += 10
        bonus_items.append({"label": "occupancy >= 95%", "points": 10})
    elif occupancy_pct >= 88:
        score += 5
        bonus_items.append({"label": "occupancy 88-94%", "points": 5})

    if avg_tenure_days > 120:
        score += 10
        bonus_items.append({"label": "avg tenure > 120d", "points": 10})

    if len(listed_over_21) == 0 and listed_count > 0:
        score += 5
        bonus_items.append({"label": "no rooms listed > 21d", "points": 5})

    if open_tickets and tickets_over_7d == 0:
        score += 5
        bonus_items.append({"label": "all open tickets < 7d", "points": 5})

    if occupancy_pct < 75:
        score -= 20
        penalty_items.append({"label": "occupancy < 75%", "points": -20, "count": 1})
    elif occupancy_pct < 88:
        score -= 8
        penalty_items.append({"label": "occupancy 75-87%", "points": -8, "count": 1})

    listed_penalty = len(listed_over_14) * 3
    if listed_penalty:
        score -= listed_penalty
        penalty_items.append(
            {"label": "rooms listed > 14d", "points": -listed_penalty, "count": len(listed_over_14)}
        )

    flip_penalty = len(flip_over_5) * 5
    if flip_penalty:
        score -= flip_penalty
        penalty_items.append(
            {"label": "flip rooms > 5d", "points": -flip_penalty, "count": len(flip_over_5)}
        )

    ticket_penalty = (tickets_over_7d * 2) + (tickets_over_14d * 5)
    if ticket_penalty:
        score -= ticket_penalty
        penalty_items.append(
            {
                "label": "open tickets > 7d / >14d",
                "points": -ticket_penalty,
                "count": tickets_over_7d + tickets_over_14d,
            }
        )

    score = max(0, min(125, int(round(score))))

    vacancy_rooms = [
        {
            "id": r.get("id"),
            "property": _room_property_label(r),
            "room_number": r.get("room_number"),
            "days_listed": round(_to_num(r.get("days_in_current_status")), 1),
            "base_price": _to_num(r.get("base_price")),
            "last_room_price": _to_num(r.get("last_room_price")),
            "latest_occupancy_move_out_date": r.get("latest_occupancy_move_out_date"),
        }
        for r in listed_rooms
    ]
    vacancy_rooms.sort(key=lambda x: _to_num(x.get("days_listed")), reverse=True)

    flip_room_rows = [
        {
            "id": r.get("id"),
            "property": _room_property_label(r),
            "room_number": r.get("room_number"),
            "days_in_flip": round(_to_num(r.get("days_in_current_status")), 1),
        }
        for r in flip_rooms
    ]
    flip_room_rows.sort(key=lambda x: _to_num(x.get("days_in_flip")), reverse=True)

    retention_histogram = {"0-30": 0, "31-90": 0, "91-180": 0, "181+": 0}
    for days in occupied_days:
        if days <= 30:
            retention_histogram["0-30"] += 1
        elif days <= 90:
            retention_histogram["31-90"] += 1
        elif days <= 180:
            retention_histogram["91-180"] += 1
        else:
            retention_histogram["181+"] += 1

    return {
        "score": score,
        "base_score": 100,
        "bonus_points": sum(item["points"] for item in bonus_items),
        "penalty_points": -sum(abs(item["points"]) for item in penalty_items),
        "bonuses": bonus_items,
        "penalties": penalty_items,
        "occupancy_pct": occupancy_pct,
        "total_rooms": total_rooms,
        "occupied": occupied,
        "listed": listed_count,
        "flip": flip_count,
        "move_in": len(move_in_rooms),
        "avg_listed_days": avg_listed_days,
        "max_listed_days": max_listed_days,
        "rooms_over_14d": len(listed_over_14),
        "avg_tenure_days": avg_tenure_days,
        "median_tenure_days": median_tenure_days,
        "avg_flip_days": avg_flip_days,
        "max_flip_days": max_flip_days,
        "open_tickets": len(open_tickets),
        "tickets_over_7d": tickets_over_7d,
        "tickets_over_14d": tickets_over_14d,
        "avg_ticket_age_days": avg_ticket_age_days,
        "monthly_net_revenue": monthly_net_revenue,
        "revenue_per_room": revenue_per_room,
        "per_property": per_property,
        "vacancy_rooms": vacancy_rooms,
        "flip_rooms": flip_room_rows,
        "open_ticket_items": open_tickets,
        "monthly_revenue_series": monthly_revenue_series,
        "retention_histogram": retention_histogram,
    }


def update_task_status(
    session: requests.Session, creds: Dict[str, str], task_id: int, new_status: str
) -> Dict:
    resp = _authed_request(
        session,
        "PATCH",
        f"{BASE_URL}/api/admin-new/property/maintenance/tickets/{task_id}/",
        creds=creds,
        login_fn=login,
        json={"status": new_status},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ==========================================
# MAIN EXECUTION
# ==========================================
def run(messages_only: bool = False) -> None:
    creds = load_credentials()
    base_dir = Path(__file__).resolve().parent

    with create_session() as session:
        sys.stderr.write("Logging in to Padsplit...\n")
        login(session, creds["email"], creds["password"])

        sys.stderr.write("Fetching messages...\n")
        messages = fetch_messages(session, creds)

        # Enrich threads active in the last RECENT_DAYS days with full message context
        cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)
        for thread in messages:
            created_str = (thread.get("lastMessage") or {}).get("created", "")
            if not created_str:
                continue
            try:
                last_dt = datetime.fromisoformat(created_str)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if last_dt < cutoff:
                continue
            chat_id = thread.get("id", "")
            if not chat_id:
                continue
            sys.stderr.write(f"# Fetching context for thread {chat_id} (last active {created_str})\n")
            thread["recent_messages"] = fetch_thread_messages(session, creds, chat_id)

        scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload: Dict = {"scraped_at": scraped_at, "messages": messages}
        tasks_for_kpis: Dict[str, List[Dict[str, Any]]] = {}

        if not messages_only:
            sys.stderr.write("Fetching tasks...\n")
            fetched_tasks = fetch_tasks(session, creds)
            payload["tasks"] = fetched_tasks
            tasks_for_kpis = fetched_tasks

        sys.stderr.write("Fetching room stats...\n")
        rooms = fetch_rooms(session, creds)
        sys.stderr.write("Fetching property stats...\n")
        properties = fetch_properties_stats(session, creds)
        sys.stderr.write("Fetching earnings stats...\n")
        earnings_payload = fetch_earnings(session, creds)
        kpis = compute_kpis(rooms, properties, earnings_payload, tasks_for_kpis, datetime.now(timezone.utc))
        sys.stderr.write("Fetching performance history stats...\n")
        performance_history = fetch_performance_history(session, creds)

        docs_stats = base_dir.parent / "docs" / "data" / "stats.json"
        docs_data_dir = base_dir.parent / "docs" / "data"
        docs_data_dir.mkdir(parents=True, exist_ok=True)
        docs_monthly_history = docs_data_dir / "monthly_history.json"
        score_history: List[Dict[str, Any]] = []
        if docs_stats.exists():
            try:
                prev = json.loads(docs_stats.read_text())
                score_history = (prev.get("kpis") or {}).get("score_history", [])
            except Exception:
                score_history = []

        today = scraped_at[:10]
        score_history = [e for e in score_history if e.get("date") != today]
        score_history.append({"date": today, "score": kpis["score"]})
        current_month = today[:7]
        score_history = [e for e in score_history if e.get("date", "")[:7] == current_month]
        score_history.sort(key=lambda e: e.get("date", ""))

        avg_score_30d = round(sum(e["score"] for e in score_history) / len(score_history), 1) if score_history else kpis["score"]
        kpis["score_history"] = score_history
        kpis["avg_score_30d"] = avg_score_30d

        existing_months_map: Dict[str, Dict[str, Any]] = {}
        if docs_monthly_history.exists():
            try:
                monthly_prev = json.loads(docs_monthly_history.read_text())
                monthly_prev_list = monthly_prev.get("months", []) if isinstance(monthly_prev, dict) else []
                for item in monthly_prev_list:
                    if isinstance(item, dict) and item.get("month"):
                        existing_months_map[str(item["month"])] = item
            except Exception:
                existing_months_map = {}

        months_map: Dict[str, Dict[str, Any]] = dict(existing_months_map)
        for month_key, raw in performance_history.items():
            if month_key < "2025-04":
                continue
            monthly_kpis = compute_monthly_kpis(raw)
            months_map[month_key] = {
                "month": month_key,
                "avg_flip_days": round(_to_num(raw.get("avg_flip_days")), 1),
                "occupancy_pct": round(_to_num(raw.get("occupancy_pct")), 1),
                "avg_tenure_days": round(_to_num(raw.get("avg_tenure_days")), 1),
                "partial": monthly_kpis["partial"],
                "bonuses": monthly_kpis["bonuses"],
                "penalties": monthly_kpis["penalties"],
                "score": monthly_kpis["score"],
                "note": monthly_kpis["note"],
            }

        current_month = scraped_at[:7]
        months_map[current_month] = {
            "month": current_month,
            "avg_flip_days": round(_to_num(kpis.get("avg_flip_days")), 1),
            "occupancy_pct": round(_to_num(kpis.get("occupancy_pct")), 1),
            "avg_tenure_days": round(_to_num(kpis.get("avg_tenure_days")), 1),
            "partial": False,
            "bonuses": kpis.get("bonuses", []),
            "penalties": kpis.get("penalties", []),
            "score": int(_to_num(kpis.get("score"))),
        }

        monthly_history_months = [
            months_map[m]
            for m in sorted(months_map.keys())
            if isinstance(months_map[m], dict) and m >= "2025-04"
        ]
        monthly_history_payload: Dict[str, Any] = {
            "updated_at": scraped_at,
            "months": monthly_history_months,
        }

        stats_payload: Dict[str, Any] = {
            "scraped_at": scraped_at,
            "rooms": rooms,
            "properties": properties,
            "earnings": _extract_earnings_rows(earnings_payload),
            "kpis": kpis,
        }

        # --- 1. SAVE THE RAW DATA LOCALLY ---
        output_dir = base_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = scraped_at.replace(":", "-") + ".json"
        out_path = output_dir / filename
        latest_path = output_dir / "latest.json"
        stats_path = output_dir / "stats.json"
        out_path.write_text(json.dumps(payload, indent=2))
        latest_path.write_text(json.dumps(payload, indent=2))
        stats_path.write_text(json.dumps(stats_payload, indent=2))
        docs_monthly_history.write_text(json.dumps(monthly_history_payload, indent=2))
        sys.stderr.write(f"# Saved raw data to {out_path}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages-only", action="store_true", help="Skip tasks; summarize messages only")
    args = parser.parse_args()
    try:
        run(messages_only=args.messages_only)
    except requests.exceptions.ConnectionError:
        sys.stderr.write("Network error: could not reach padsplit.com\n")
        sys.exit(1)
    except requests.exceptions.Timeout:
        sys.stderr.write("Request timed out — PadSplit may be slow\n")
        sys.exit(1)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)
