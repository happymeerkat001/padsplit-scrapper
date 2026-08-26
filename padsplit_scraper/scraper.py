import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

try:
    from padsplit_scraper.kpis import (
        _extract_earnings_rows,
        _parse_iso,
        _to_num,
        compute_kpis,
        compute_monthly_kpis,
    )
except ModuleNotFoundError:  # Support the cron entry point: python3 padsplit_scraper/scraper.py
    from kpis import _extract_earnings_rows, _parse_iso, _to_num, compute_kpis, compute_monthly_kpis

try:
    from padsplit_scraper.occupancy import compute_occupancy
except ModuleNotFoundError:  # Support the cron entry point: python3 padsplit_scraper/scraper.py
    from occupancy import compute_occupancy

try:
    from padsplit_scraper.persist import (
        _build_monthly_history_payload,
        _build_run_status,
        _build_stats_payload,
        _load_json_if_exists,
        _load_score_history,
        _monthly_history_path,
        _persist_latest_payload,
        _persist_occupancy_payload,
        _stats_output_path,
        _write_json,
    )
except ModuleNotFoundError:  # Support the cron entry point: python3 padsplit_scraper/scraper.py
    from persist import (
        _build_monthly_history_payload,
        _build_run_status,
        _build_stats_payload,
        _load_json_if_exists,
        _load_score_history,
        _monthly_history_path,
        _persist_latest_payload,
        _persist_occupancy_payload,
        _stats_output_path,
        _write_json,
    )


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
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DOCS_DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"

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
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)
    email = os.getenv("PADSPLIT_EMAIL")
    password = os.getenv("PADSPLIT_PASSWORD")
    if not email or not password:
        sys.exit("Missing PADSPLIT_EMAIL or PADSPLIT_PASSWORD in environment or root .env")
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


class ScrapePhaseError(RuntimeError):
    def __init__(
        self,
        phase: str,
        message: str,
        *,
        endpoint: Optional[str] = None,
        original: Optional[BaseException] = None,
    ) -> None:
        self.phase = phase
        self.endpoint = endpoint
        self.original = original
        detail = f"{phase} failed"
        if endpoint:
            detail += f" ({endpoint})"
        detail += f": {message}"
        super().__init__(detail)


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
        login_fn(session, creds["email"], creds["password"], force=False)
        resp = session.request(method, url, **kwargs)
    if resp.status_code in (401, 403):
        login_fn(session, creds["email"], creds["password"], force=True)
        resp = session.request(method, url, **kwargs)
        if resp.status_code in (401, 403):
            raise RuntimeError("Session could not be refreshed — check credentials")
    return resp


def login(session: requests.Session, email: str, password: str, force: bool = False) -> None:
    payload = {
        "email": email,
        "password": password,
        "mfa_code": "",
        "force_login": force,
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



def _describe_exception(exc: BaseException) -> str:
    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        if response is not None:
            return f"HTTP {response.status_code}"
        return "HTTP request failed"
    return str(exc) or exc.__class__.__name__


def _run_phase(label: str, phase: str, fn):
    sys.stderr.write(f"{label}\n")
    try:
        return fn()
    except ScrapePhaseError:
        raise
    except requests.exceptions.RequestException as exc:
        endpoint = None
        request = getattr(exc, "request", None)
        if request is not None:
            endpoint = getattr(request, "url", None)
        raise ScrapePhaseError(phase, _describe_exception(exc), endpoint=endpoint, original=exc) from exc
    except RuntimeError as exc:
        raise ScrapePhaseError(phase, str(exc), original=exc) from exc


def _enrich_recent_threads(session: requests.Session, creds: Dict[str, str], messages: List[Dict]) -> None:
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
        thread["recent_messages"] = _run_phase(
            "Fetching recent thread context...",
            "message_context",
            lambda: fetch_thread_messages(session, creds, chat_id),
        )





def run(messages_only: bool = False) -> int:
    creds = load_credentials()
    mode = "messages_only" if messages_only else "full"

    with create_session() as session:
        _run_phase(
            "Logging in to Padsplit...",
            "login",
            lambda: login(session, creds["email"], creds["password"], force=False),
        )

        messages = _run_phase("Fetching messages...", "messages", lambda: fetch_messages(session, creds))
        _enrich_recent_threads(session, creds, messages)

        scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload: Dict[str, Any] = {"scraped_at": scraped_at, "messages": messages}
        out_path = _persist_latest_payload(payload, scraped_at=scraped_at)
        tasks_for_kpis: Dict[str, List[Dict[str, Any]]] = {}

        if messages_only:
            run_status = _build_run_status(state="ok", mode=mode, run_scraped_at=scraped_at)
            _persist_latest_payload(payload, scraped_at=scraped_at, run_status=run_status, write_timestamped=True)
            sys.stderr.write(f"# Saved raw data to {out_path}\n")
            return 0

        fetched_tasks = _run_phase("Fetching tasks...", "tasks", lambda: fetch_tasks(session, creds))
        payload["tasks"] = fetched_tasks
        tasks_for_kpis = fetched_tasks
        out_path = _persist_latest_payload(payload, scraped_at=scraped_at, write_timestamped=True)

        try:
            occupancy_payload = compute_occupancy(messages, fetched_tasks, datetime.now(timezone.utc))
            _persist_occupancy_payload(occupancy_payload)
        except Exception as exc:
            sys.stderr.write(f"# Occupancy derivation failed; continuing scrape: {exc}\n")

        try:
            rooms = _run_phase("Fetching room stats...", "room_stats", lambda: fetch_rooms(session, creds))
            properties = _run_phase(
                "Fetching property stats...",
                "property_stats",
                lambda: fetch_properties_stats(session, creds),
            )
            earnings_payload = _run_phase(
                "Fetching earnings stats...",
                "earnings_stats",
                lambda: fetch_earnings(session, creds),
            )
            kpis = compute_kpis(
                rooms,
                properties,
                earnings_payload,
                tasks_for_kpis,
                datetime.now(timezone.utc),
            )
            score_history = _load_score_history()
            today = scraped_at[:10]
            score_history = [entry for entry in score_history if entry.get("date") != today]
            score_history.append({"date": today, "score": kpis["score"]})
            current_month = today[:7]
            score_history = [entry for entry in score_history if entry.get("date", "")[:7] == current_month]
            score_history.sort(key=lambda entry: entry.get("date", ""))
            avg_score_30d = (
                round(sum(entry["score"] for entry in score_history) / len(score_history), 1)
                if score_history
                else kpis["score"]
            )
            kpis["score_history"] = score_history
            kpis["avg_score_30d"] = avg_score_30d
            performance_history = _run_phase(
                "Fetching performance history stats...",
                "performance_history",
                lambda: fetch_performance_history(session, creds),
            )
        except ScrapePhaseError as exc:
            run_status = _build_run_status(
                state="degraded",
                mode=mode,
                failed_phase=exc.phase,
                error_type=exc.original.__class__.__name__ if exc.original else exc.__class__.__name__,
                error_message=str(exc),
                fallback_used=_stats_output_path().exists(),
                run_scraped_at=scraped_at,
            )
            _persist_latest_payload(payload, scraped_at=scraped_at, run_status=run_status, write_timestamped=True)

            fallback_stats = _load_json_if_exists(_stats_output_path())
            if fallback_stats is not None:
                fallback_stats["run_status"] = run_status
                _write_json(_stats_output_path(), fallback_stats)
                sys.stderr.write(f"# Degraded stats run; re-used prior stats from {_stats_output_path()}\n")
            else:
                sys.stderr.write(f"# Degraded stats run; no prior stats fallback at {_stats_output_path()}\n")

            sys.stderr.write(f"{exc}\n")
            sys.stderr.write(f"# Saved raw data to {out_path}\n")
            return 0

        run_status = _build_run_status(state="ok", mode=mode, run_scraped_at=scraped_at)
        _persist_latest_payload(payload, scraped_at=scraped_at, run_status=run_status, write_timestamped=True)
        stats_payload = _build_stats_payload(
            scraped_at=scraped_at,
            rooms=rooms,
            properties=properties,
            earnings_payload=earnings_payload,
            kpis=kpis,
            run_status=run_status,
        )
        monthly_history_payload = _build_monthly_history_payload(
            performance_history=performance_history,
            kpis=kpis,
            scraped_at=scraped_at,
        )
        _write_json(_stats_output_path(), stats_payload)
        _write_json(_monthly_history_path(), monthly_history_payload)
        sys.stderr.write(f"# Saved raw data to {out_path}\n")
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages-only", action="store_true", help="Skip tasks; summarize messages only")
    args = parser.parse_args(argv)
    try:
        return run(messages_only=args.messages_only)
    except ScrapePhaseError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
