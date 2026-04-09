import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv
import anthropic

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
BASE_URL = "https://www.padsplit.com"
LOGIN_URL = f"{BASE_URL}/api/auth/login"
GRAPHQL_URL = f"{BASE_URL}/api/graphql/"
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
    # Load environment variables here so they are ready for Padsplit AND MiniMax
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
        allowed_methods=["GET", "POST"],
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


# ==========================================
# MAIN EXECUTION
# ==========================================
def run() -> None:
    creds = load_credentials() # This also loads the .env file!
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

        sys.stderr.write("Fetching tasks...\n")
        tasks = fetch_tasks(session, creds)
        scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        payload = {
            "scraped_at": scraped_at,
            "messages": messages,
            "tasks": tasks,
        }

        # --- 1. SAVE THE RAW DATA LOCALLY ---
        output_dir = base_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = scraped_at.replace(":", "-") + ".json"
        out_path = output_dir / filename
        latest_path = output_dir / "latest.json"
        out_path.write_text(json.dumps(payload, indent=2))
        latest_path.write_text(json.dumps(payload, indent=2))
        sys.stderr.write(f"# Saved raw data to {out_path}\n")

        # --- 2. SEND THE DATA TO MINIMAX AI ---
        sys.stderr.write("Sending data to MiniMax AI for processing...\n")
        
        # Initialize client (it will use the keys loaded in load_credentials)
        client = anthropic.Anthropic()
        
        # Convert our scraped dictionary into a string so the AI can read it
        payload_string = json.dumps(payload) 

        message = client.messages.create(
            model="MiniMax-M2.5", 
            max_tokens=2000, # Increased so the AI has room to write a longer answer
            messages=[
                {
                    "role": "user", 
                    "content": f"Here is the latest data scraped from Padsplit. Please summarize the most urgent messages and any open tasks:\n\n{payload_string}"
                }
            ]
        )

        # --- 3. PRINT THE AI'S RESPONSE ---
        print("\n" + "="*50)
        for block in message.content:
            if block.type == "text":
                print(f"AI Response:\n{block.text}")
        print("="*50 + "\n")


if __name__ == "__main__":
    try:
        run()
    except requests.exceptions.ConnectionError:
        sys.stderr.write("Network error: could not reach padsplit.com\n")
        sys.exit(1)
    except requests.exceptions.Timeout:
        sys.stderr.write("Request timed out — PadSplit may be slow\n")
        sys.exit(1)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)