#!/usr/bin/env python3
"""Generate human-reviewed PadSplit draft replies.

Reads padsplit_scraper/output/latest.json, selects recent tenant-led threads,
generates Claude draft replies, and writes padsplit_scraper/output/drafts.json.
The script never sends PadSplit messages.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "padsplit_scraper" / "output"
LATEST_PATH = OUTPUT_DIR / "latest.json"
DRAFTS_PATH = OUTPUT_DIR / "drafts.json"
STATE_PATH = OUTPUT_DIR / "drafted_messages.json"
TEMPLATE_DIR = BASE_DIR / "templates"
LOCAL_TZ = ZoneInfo("America/Chicago")
RECENT_HOURS = 48
STATE_RETENTION_DAYS = 30
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
PADSPLIT_THREAD_URL = "https://www.padsplit.com/host/communication/{chat_id}"

PII_PATTERNS = [
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\d)"), "[phone]"),
]

CATEGORY_TEMPLATES = {
    "maintenance": "maintenance_ack.md",
    "payment": "payment_extension.md",
    "move-in/out": "move_in_welcome.md",
    "general": "general_response.md",
    "complaint": "general_response.md",
    "emergency": "emergency_protocol.md",
}


def load_environment() -> None:
    load_dotenv(BASE_DIR / ".env")


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize(value: Optional[str]) -> str:
    return " ".join((value or "").split())


def mask_pii(value: str) -> str:
    masked = value
    for pattern, replacement in PII_PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def save_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def sender_name(sender: Dict) -> str:
    return normalize(sender.get("displayName") or f"{sender.get('firstName') or ''} {sender.get('lastName') or ''}")


def occupant_names(thread: Dict) -> set[str]:
    user = ((thread.get("occupancy") or {}).get("user") or {})
    values = {
        normalize(thread.get("title")).lower(),
        normalize(user.get("displayName")).lower(),
        normalize(f"{user.get('firstName') or ''} {user.get('lastName') or ''}").lower(),
    }
    return {value for value in values if value}


def is_tenant_message(thread: Dict, message: Dict) -> bool:
    sender = message.get("sender") or {}
    if sender.get("roleId") == "A_0":
        return True
    if sender.get("roleId") == "A_1":
        return False
    return sender_name(sender).lower() in occupant_names(thread)


def property_address(thread: Dict) -> str:
    address = ((thread.get("property") or {}).get("address") or {})
    city = ((address.get("city") or {}).get("name") or "").strip()
    state = (((address.get("city") or {}).get("state") or {}).get("name") or "").strip()
    parts = [address.get("street1"), address.get("street2"), city, state, address.get("zip")]
    return ", ".join(normalize(part) for part in parts if normalize(part))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "default"


def scope_matches(thread: Dict, filters: List[str]) -> bool:
    if not filters:
        return True
    user = ((thread.get("occupancy") or {}).get("user") or {})
    room = ((thread.get("occupancy") or {}).get("room") or {})
    haystack = " ".join(
        normalize(str(value)).lower()
        for value in [
            thread.get("id"),
            thread.get("title"),
            user.get("pk"),
            user.get("firstName"),
            user.get("lastName"),
            user.get("displayName"),
            room.get("pk"),
            room.get("roomNumber"),
            property_address(thread),
            slugify(property_address(thread)),
        ]
        if value is not None
    )
    return any(item in haystack for item in filters)


def classify_message(text: str, thread: Dict) -> Tuple[str, str]:
    lowered = text.lower()
    status_blob = " ".join(
        json.dumps(value, default=str).lower()
        for value in (thread.get("ticketStatus"), thread.get("paymentExtensionStatus"), thread.get("bookingStatus"))
        if value
    )
    combined = f"{lowered} {status_blob}"

    critical_terms = [
        "fire",
        "flood",
        "gas leak",
        "break in",
        "break-in",
        "locked out",
        "no heat",
        "no ac",
        "no a/c",
        "sparking",
        "electrical shock",
        "emergency",
        "unsafe",
    ]
    high_terms = ["leak", "ac", "a/c", "air", "heat", "toilet", "plumbing", "mold", "pest", "roaches", "bed bug"]
    if any(term in combined for term in critical_terms):
        return "emergency", "critical"
    if any(term in combined for term in ["payment", "pay", "extension", "late fee", "balance", "dues", "rent"]):
        return "payment", "high" if "extension" in combined or "late" in combined else "medium"
    if any(term in combined for term in ["move in", "move-in", "move out", "move-out", "key", "access code", "checkout"]):
        return "move-in/out", "medium"
    if any(term in combined for term in high_terms) or "ticket" in combined:
        return "maintenance", "high" if any(term in combined for term in high_terms[:8]) else "medium"
    if any(term in combined for term in ["complaint", "angry", "upset", "unacceptable", "not fair", "noise", "dirty"]):
        return "complaint", "medium"
    return "general", "low"


def load_template(path: Path, fallback: str = "") -> str:
    return path.read_text().strip() if path.exists() else fallback


def load_property_sop(thread: Dict) -> Tuple[str, str]:
    address = property_address(thread)
    candidates = [
        TEMPLATE_DIR / "properties" / f"{slugify(address)}.md",
        TEMPLATE_DIR / "properties" / f"{slugify((address.split(',') or [''])[0])}.md",
        TEMPLATE_DIR / "properties" / "default.md",
    ]
    for path in candidates:
        if path.exists():
            return path.name, path.read_text().strip()
    return "default.md", "No property-specific SOP found."


def format_history(thread: Dict) -> str:
    messages = sorted(
        thread.get("recent_messages") or [],
        key=lambda item: parse_dt(item.get("created")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    lines = []
    for message in messages[-10:]:
        text = mask_pii(normalize(message.get("text")))
        if not text or message.get("deleted"):
            continue
        created = parse_dt(message.get("created"))
        created_label = iso_utc(created) if created else "unknown time"
        role = "Tenant" if is_tenant_message(thread, message) else "Manager"
        lines.append(f"- {created_label} {role} ({sender_name(message.get('sender') or {})}): {text}")
    return "\n".join(lines) or "- No usable message history."


def build_prompt(thread: Dict, message: Dict, category: str) -> Tuple[str, str, str, str]:
    system_base = load_template(TEMPLATE_DIR / "system_prompt.md")
    gold_examples = load_template(TEMPLATE_DIR / "gold_examples.md")
    template_file = CATEGORY_TEMPLATES.get(category, "general_response.md")
    category_sop = load_template(TEMPLATE_DIR / "categories" / template_file)
    property_sop_name, property_sop = load_property_sop(thread)
    occupancy = thread.get("occupancy") or {}
    room_number = (occupancy.get("room") or {}).get("roomNumber")
    tenant_name = thread.get("title") or sender_name(message.get("sender") or {}) or "Unknown tenant"
    context = {
        "tenant_name": tenant_name,
        "room_number": room_number,
        "property": property_address(thread),
        "move_in_date": occupancy.get("moveInDate"),
        "move_out_date": occupancy.get("moveOutDate"),
        "thread_url": PADSPLIT_THREAD_URL.format(chat_id=thread.get("id")),
    }
    system_prompt = "\n\n".join(
        [
            system_base,
            "Category SOP:\n" + category_sop,
            "Property SOP:\n" + property_sop,
            "Gold Examples:\n" + gold_examples,
        ]
    )
    inbound = mask_pii(normalize(message.get("text")))
    user_prompt = "\n\n".join(
        [
            "Tenant context:\n" + json.dumps(context, indent=2),
            "Conversation history:\n" + format_history(thread),
            "Draft a reply to this latest inbound tenant message:",
            f"<tenant_message>{inbound}</tenant_message>",
        ]
    )
    return system_prompt, user_prompt, template_file, property_sop_name


def call_claude(system_prompt: str, user_prompt: str) -> str:
    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY")
    model = (os.getenv("ANTHROPIC_MODEL") or "claude-3-5-haiku-latest").strip()
    body = json.dumps(
        {
            "model": model,
            "max_tokens": 500,
            "temperature": 0.2,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
    ).encode()
    for attempt in range(3):
        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read())
            parts = payload.get("content") or []
            text = "".join(part.get("text", "") for part in parts if part.get("type") == "text").strip()
            if not text:
                raise RuntimeError(f"Claude returned no text: {payload}")
            return text
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 529) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Claude API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Claude request failed: {exc}") from exc
    raise RuntimeError("Claude API retries exhausted")


def template_fallback(thread: Dict, message: Dict, category: str) -> str:
    tenant_name = (thread.get("title") or sender_name(message.get("sender") or {}) or "there").split()[0]
    if category == "emergency":
        return f"Hi {tenant_name}, I'm sorry this is happening. If anyone is in immediate danger, please call 911 first. Please send the exact location and any photos or video here so I can prioritize next steps."
    if category == "maintenance":
        return f"Hi {tenant_name}, thank you for letting me know. Can you please send a photo or short video and confirm exactly where this is happening so I can review and follow up with next steps?"
    if category == "payment":
        return f"Hi {tenant_name}, thank you for reaching out. Please keep the request and any details here in PadSplit so I can review the account context and follow up correctly."
    return f"Hi {tenant_name}, thank you for the message. I'll review this and follow up with the next steps."


def load_state() -> Dict:
    state = load_json(STATE_PATH, {"drafted": {}})
    if not isinstance(state, dict):
        return {"drafted": {}}
    state.setdefault("drafted", {})
    return state


def prune_state(state: Dict, now: datetime) -> Dict:
    cutoff = now - timedelta(days=STATE_RETENTION_DAYS)
    drafted = {}
    for message_id, meta in (state.get("drafted") or {}).items():
        drafted_at = parse_dt((meta or {}).get("drafted_at"))
        if drafted_at and drafted_at >= cutoff:
            drafted[message_id] = meta
    state["drafted"] = drafted
    return state


def already_handled(state: Dict, message_id: str, last_message_at: str) -> bool:
    meta = (state.get("drafted") or {}).get(message_id)
    if not meta:
        return False
    action = meta.get("user_action") or "drafted"
    return action in {"drafted", "approved", "ignored"} and meta.get("last_message_at") == last_message_at


def select_candidates(payload: Dict, state: Dict, scope_filter: str) -> List[Tuple[Dict, Dict, str, str]]:
    scraped_at = parse_dt(payload.get("scraped_at")) or utc_now()
    cutoff = scraped_at - timedelta(hours=RECENT_HOURS)
    filters = [item.strip().lower() for item in scope_filter.split(",") if item.strip()]
    candidates: List[Tuple[Dict, Dict, str, str]] = []

    for thread in payload.get("messages") or []:
        if not scope_matches(thread, filters):
            continue
        recent = thread.get("recent_messages")
        if not recent:
            continue
        usable = sorted(
            [m for m in recent if normalize(m.get("text")) and not m.get("deleted")],
            key=lambda item: parse_dt(item.get("created")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        if not usable:
            continue
        last_message = usable[0]
        if not is_tenant_message(thread, last_message):
            continue
        created = parse_dt(last_message.get("created"))
        if not created or created < cutoff:
            continue
        message_id = last_message.get("id")
        chat_id = thread.get("id")
        if not message_id or not chat_id:
            print(f"Skipping malformed thread without required IDs: {thread.get('title')}", file=sys.stderr)
            continue
        last_message_at = iso_utc(created)
        if already_handled(state, message_id, last_message_at):
            continue
        candidates.append((thread, last_message, message_id, last_message_at))

    candidates.sort(key=lambda item: parse_dt(item[1].get("created")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return candidates


def send_critical_slack_alert(draft: Dict) -> None:
    try:
        from padsplit_scraper.slack_notifier import post_slack_message

        text = (
            "@channel Critical PadSplit tenant message\n"
            f"Tenant: {draft['tenant_name']} | Room: {draft.get('room_number') or '?'}\n"
            f"Property: {draft.get('property') or 'Unknown'}\n"
            f"Thread: {PADSPLIT_THREAD_URL.format(chat_id=draft['chat_id'])}\n"
            f"Message: {draft['inbound_message']}"
        )
        post_slack_message(text)
    except Exception as exc:
        print(f"Critical Slack alert failed: {exc}", file=sys.stderr)


def make_draft(thread: Dict, message: Dict, message_id: str, last_message_at: str, use_ai: bool) -> Dict:
    category, urgency = classify_message(message.get("text") or "", thread)
    system_prompt, user_prompt, template_file, property_sop_name = build_prompt(thread, message, category)
    draft_reply = call_claude(system_prompt, user_prompt) if use_ai else template_fallback(thread, message, category)
    occupancy = thread.get("occupancy") or {}
    draft = {
        "id": f"draft_{uuid.uuid4().hex[:12]}",
        "chat_id": thread.get("id"),
        "message_id": message_id,
        "tenant_name": thread.get("title") or sender_name(message.get("sender") or {}) or "Unknown tenant",
        "room_number": (occupancy.get("room") or {}).get("roomNumber"),
        "property": property_address(thread),
        "category": category,
        "urgency": urgency,
        "inbound_message": mask_pii(normalize(message.get("text"))),
        "inbound_at": last_message_at,
        "draft_reply": draft_reply.strip(),
        "template_used": template_file,
        "property_sop": property_sop_name,
        "status": "pending",
        "thread_url": PADSPLIT_THREAD_URL.format(chat_id=thread.get("id")),
        "generated_at": iso_utc(utc_now()),
    }
    if not draft["chat_id"] or not draft["message_id"]:
        raise RuntimeError("Draft missing required chat_id or message_id")
    return draft


def update_state(state: Dict, draft: Dict) -> None:
    state.setdefault("drafted", {})[draft["message_id"]] = {
        "drafted_at": draft["generated_at"],
        "draft_id": draft["id"],
        "last_message_at": draft["inbound_at"],
        "user_action": "drafted",
    }


def print_stdout(drafts: Iterable[Dict]) -> None:
    for draft in drafts:
        print(f"\n[{draft['urgency'].upper()}] {draft['tenant_name']} - {draft['category']}")
        print(f"{draft.get('property') or 'Unknown property'} | Room {draft.get('room_number') or '?'}")
        print(draft["draft_reply"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PadSplit draft replies without sending messages.")
    parser.add_argument("--template-only", action="store_true", help="Skip Claude and generate template fallback drafts.")
    parser.add_argument("--stdout", action="store_true", help="Also print drafts to stdout.")
    args = parser.parse_args()

    load_environment()
    if not LATEST_PATH.exists():
        raise RuntimeError(f"Missing latest.json: {LATEST_PATH}")

    now = utc_now()
    payload = load_json(LATEST_PATH, {})
    state = prune_state(load_state(), now)
    candidates = select_candidates(payload, state, os.getenv("SCOPE_FILTER", ""))
    use_ai = not args.template_only
    drafts = []

    for thread, message, message_id, last_message_at in candidates:
        draft = make_draft(thread, message, message_id, last_message_at, use_ai=use_ai)
        drafts.append(draft)
        update_state(state, draft)
        if draft["urgency"] == "critical":
            send_critical_slack_alert(draft)

    output = {"generated_at": iso_utc(now), "drafts": drafts}
    save_json(DRAFTS_PATH, output)
    save_json(STATE_PATH, state)

    if args.stdout or args.template_only:
        print_stdout(drafts)
    print(f"Generated {len(drafts)} draft replies.")


if __name__ == "__main__":
    main()
