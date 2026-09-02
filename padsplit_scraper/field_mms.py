#!/usr/bin/env python3
"""Daily Don-field group MMS from the existing PadSplit scraper.

6:00am CT and 7:00pm CT, every day including weekends. One group MMS per
window when PadSplit host messages and/or Discord #ai-tasks-temp have
something to say. Skip when both sources are empty. Never a 1:1 to Don.
Never send from GitHub Actions / CI.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

try:
    from padsplit_scraper import scraper
except ModuleNotFoundError:  # python3 padsplit_scraper/field_mms.py
    import scraper  # type: ignore


CT = ZoneInfo("America/Chicago")
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
STATE_PATH = ROOT_DIR / "logs" / "field_mms_sent.json"
LAUNCH_AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_LABEL = "com.padsplit.field-mms"

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_GUILD_ID = "1540475742104719380"
DISCORD_TASKS_CHANNEL_ID = "1540475874955231343"
DEFAULT_TIMEOUT = (10, 30)
LOOKBACK = timedelta(hours=18)
SMS_CHAR_LIMIT = 1500
FIELD_MMS_CHAT_NAME_DEFAULT = "Don Field"

# Thread owner (From). Recipients are always the three field numbers together.
ANG_VOICE_PHONE = "+14696267260"
DAD_PHONE = "+19452413070"
JOE_PHONE = "+14693732048"
DON_PHONE = "+12147798338"
DON_WRONG_PHONE = "+12144541768"
GROUP_RECIPIENTS = (DAD_PHONE, JOE_PHONE, DON_PHONE)

HOST_STAFF_ROLE = "A_1"
TENANT_ROLE = "A_0"
SKIP_MESSAGE_TYPES = {"CHAT_OPENED"}

# Redact lock / Wi-Fi / SSN / password / ID-photo material from SMS.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:front\s+)?(?:door|room|gate|lock|entry|garage)\s+codes?\b[^.\n]*"),
    re.compile(r"(?i)\b(?:lock|door|room|gate)\s+codes?\s+(?:for\s+[^.\n]+?\s+)?(?:is|:|#)\s*\S+"),
    re.compile(r"(?i)\bcodes?\s*[:#]\s*\S+"),
    re.compile(r"(?i)\b(?:wifi|wi-fi|ssid)\b[^.\n]*"),
    re.compile(r"(?i)\bpasswords?\s*[:#]?\s*\S+"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"(?i)\b(?:ssn|social\s+security)\b[^.\n]*"),
    re.compile(r"(?i)\b(?:id|driver(?:'s)?\s+license|passport)\s+photos?\b[^.\n]*"),
    re.compile(r"(?i)\b(?:attachment|photo|image)\s*:\s*\S+"),
)
_LOCK_CODE_LIKE = re.compile(
    r"(?i)(?:"
    r"\b(?:lock|door|room|gate|entry)\s*codes?\b"
    r"|\bcodes?\s*[:#]\s*\d{3,}"
    r"|\b\d{3}-\d{2}-\d{4}\b"
    r"|\b(?:wifi|wi-fi)\s+(?:name|ssid|password|pass)\b"
    r")"
)
_TASK_DIGEST_RE = re.compile(r"Tasks Digest", re.I)
_NO_TASKS_RE = re.compile(r"No open or pending tasks", re.I)
_TASK_LINE_RE = re.compile(
    r"^\[(?P<bucket>Requests|Open)\]\s*(?:\(Room\s+(?P<room>[^)]+)\))?\s*(?P<why>.*)$",
    re.I,
)


@dataclass
class Window:
    date: str
    hour: int

    @property
    def id(self) -> str:
        return f"{self.date}-{self.hour:02d}"


@dataclass
class SendPlan:
    action: str  # send | skip_empty | skip_duplicate | skip_ci
    window_id: str
    body: str = ""
    host_lines: List[str] = field(default_factory=list)
    task_lines: List[str] = field(default_factory=list)
    recipients: Sequence[str] = GROUP_RECIPIENTS
    thread_owner: str = ANG_VOICE_PHONE


def load_environment() -> None:
    load_dotenv(ENV_PATH)


def running_in_ci() -> bool:
    return bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))


def sending_allowed() -> bool:
    """CI must never send. Live send is the Mac scraper launchd job only."""
    if running_in_ci():
        return False
    flag = (os.getenv("FIELD_MMS_ENABLE") or "").strip().lower()
    if flag in {"0", "false", "no"}:
        return False
    if sys.platform == "darwin":
        return True
    return flag in {"1", "true", "yes"}


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10:
        digits = "1" + digits
    if not digits:
        return ""
    return "+" + digits


def assert_group_recipients(recipients: Sequence[str]) -> List[str]:
    normalized = [normalize_phone(item) for item in recipients]
    if DON_WRONG_PHONE in normalized:
        raise RuntimeError("Refusing send: wrong Don number")
    if normalize_phone(DON_PHONE) not in normalized:
        raise RuntimeError("Refusing send: Don is missing from the group")
    required = {DAD_PHONE, JOE_PHONE, DON_PHONE}
    have = set(normalized)
    if not required.issubset(have):
        raise RuntimeError("Refusing send: group MMS only (Dad + Joe + Don); never solo Don")
    extra = have - required - {ANG_VOICE_PHONE}
    if extra:
        raise RuntimeError(f"Refusing send: unexpected group numbers: {sorted(extra)}")
    if have == {DON_PHONE} or (len(have) == 1 and DON_PHONE in have):
        raise RuntimeError("Refusing send: never a 1:1 to Don")
    return [DAD_PHONE, JOE_PHONE, DON_PHONE]


def window_for(now: Optional[datetime] = None) -> Window:
    current = (now or datetime.now(CT)).astimezone(CT)
    if current.hour < 6:
        day = current.date() - timedelta(days=1)
        return Window(date=day.isoformat(), hour=19)
    if current.hour < 19:
        return Window(date=current.date().isoformat(), hour=6)
    return Window(date=current.date().isoformat(), hour=19)


def _parse_created(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(CT)


def normalize_whitespace(value: Optional[str]) -> str:
    return " ".join((value or "").split())


def sanitize_sms(text: str) -> str:
    cleaned = text or ""
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    if _LOCK_CODE_LIKE.search(cleaned):
        cleaned = _LOCK_CODE_LIKE.sub("[redacted]", cleaned)
    lines = [" ".join(line.split()) for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def contains_lock_code_like(text: str) -> bool:
    return bool(_LOCK_CODE_LIKE.search(text or ""))


def format_house(thread: Dict[str, Any]) -> str:
    address = (thread.get("property") or {}).get("address") or {}
    street = normalize_whitespace(address.get("street1"))
    return street or normalize_whitespace(thread.get("title")) or "Unknown"


def format_room(thread: Dict[str, Any]) -> str:
    room_number = ((thread.get("occupancy") or {}).get("room") or {}).get("roomNumber")
    if room_number in (None, ""):
        return "?"
    return str(room_number)


def _sender_name(person: Dict[str, Any]) -> str:
    display = normalize_whitespace(person.get("displayName")).lower()
    full = normalize_whitespace(
        f"{person.get('firstName') or ''} {person.get('lastName') or ''}"
    ).lower()
    return display or full


def _occupant_names(thread: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    title = normalize_whitespace(thread.get("title")).lower()
    if title:
        names.add(title)
    occupancy_user = (thread.get("occupancy") or {}).get("user") or {}
    for key in ("displayName",):
        value = normalize_whitespace(occupancy_user.get(key)).lower()
        if value:
            names.add(value)
    full = normalize_whitespace(
        f"{occupancy_user.get('firstName') or ''} {occupancy_user.get('lastName') or ''}"
    ).lower()
    if full:
        names.add(full)
    return {name for name in names if name}


def is_tenant_item(thread: Dict[str, Any], message: Dict[str, Any]) -> bool:
    sender = message.get("sender") or {}
    role = str(sender.get("roleId") or "")
    if role == HOST_STAFF_ROLE:
        ticket = ((message.get("ticketStatus") or {}).get("ticket") or {})
        author = ticket.get("author") or {}
        if author and _sender_name(author) in _occupant_names(thread):
            status = str(
                ticket.get("status")
                or (message.get("ticketStatus") or {}).get("status")
                or ""
            ).upper()
            return status in {"SUBMITTED", "OPEN", "REQUESTS", "PENDING"}
        return False
    if role == TENANT_ROLE:
        return True
    sender_name = _sender_name(sender)
    occupants = _occupant_names(thread)
    if sender_name and sender_name in occupants:
        return True
    ticket = ((message.get("ticketStatus") or {}).get("ticket") or {})
    author = ticket.get("author") or {}
    return bool(author and _sender_name(author) in occupants)


def message_why(message: Dict[str, Any]) -> str:
    text = normalize_whitespace(message.get("text"))
    if text:
        return text
    mtype = str(message.get("messageType") or "")
    if mtype in SKIP_MESSAGE_TYPES:
        return ""
    ticket = ((message.get("ticketStatus") or {}).get("ticket") or {})
    if ticket:
        details = normalize_whitespace(ticket.get("details"))
        category = normalize_whitespace((ticket.get("category") or "").replace("_", " "))
        status = normalize_whitespace(
            ticket.get("status") or (message.get("ticketStatus") or {}).get("status")
        )
        parts = [part for part in (category, details, status) if part]
        return " ".join(parts) or "ticket update"
    extra = message.get("extra") or {}
    if "PAYMENT_EXTENSION" in mtype:
        return "payment extension"
    if mtype in {"OPEN_REQUEST", "CHANGE_MOVE_IN_REQUEST"}:
        new_date = extra.get("newMoveInDate") if isinstance(extra, dict) else None
        return f"move-in request {new_date}".strip() if new_date else "open request"
    if mtype == "MOVE_OUT_PHOTOS":
        return "move-out photos uploaded"
    if mtype == "BOOKING_STATUS":
        booking = message.get("bookingStatus") or {}
        status = normalize_whitespace(str(booking.get("status") or ""))
        return f"booking status {status}".strip() if status else "booking status update"
    return ""


def _iter_thread_messages(thread: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    recent = thread.get("recent_messages") or []
    if recent:
        yield from recent
        return
    last = thread.get("lastMessage")
    if last:
        yield last


def summarize_host_messages(
    threads: Sequence[Dict[str, Any]],
    *,
    since: datetime,
) -> List[str]:
    lines: List[str] = []
    seen_threads: Set[str] = set()
    for thread in threads:
        thread_id = str(thread.get("id") or "")
        newest: Optional[Dict[str, Any]] = None
        newest_at: Optional[datetime] = None
        for message in _iter_thread_messages(thread):
            if message.get("deleted"):
                continue
            created = _parse_created(message.get("created"))
            if created is None or created < since:
                continue
            if not is_tenant_item(thread, message):
                continue
            why = sanitize_sms(message_why(message))
            if not why or why == "[redacted]":
                continue
            if newest_at is None or created > newest_at:
                newest = message
                newest_at = created
        if not newest:
            continue
        key = thread_id or f"{format_house(thread)}|{format_room(thread)}"
        if key in seen_threads:
            continue
        seen_threads.add(key)
        why = sanitize_sms(message_why(newest))
        house = format_house(thread)
        room = format_room(thread)
        line = sanitize_sms(f"{house} Rm {room} — {why}")
        if line and not contains_lock_code_like(line):
            lines.append(line)
    return lines


def extract_open_task_lines(content: str) -> List[str]:
    if not content:
        return []
    if _NO_TASKS_RE.search(content) and not _TASK_LINE_RE.search(content):
        return []
    lines: List[str] = []
    address = ""
    for raw in content.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        task = _TASK_LINE_RE.match(stripped)
        if task:
            room = (task.group("room") or "").strip()
            why = sanitize_sms(task.group("why") or "")
            bucket = task.group("bucket")
            house = address or "Unknown"
            room_bit = f" Rm {room}" if room else ""
            line = sanitize_sms(f"{house}{room_bit} — [{bucket}] {why}".rstrip())
            if line and not contains_lock_code_like(line):
                lines.append(line)
            continue
        if stripped.endswith(":") and not stripped.startswith("[") and not stripped.startswith("Total"):
            address = stripped[:-1].strip()
    return lines


def digest_discord_open_tasks(messages: Sequence[Dict[str, Any]]) -> List[str]:
    for message in messages:
        content = str(message.get("content") or "")
        if _TASK_DIGEST_RE.search(content):
            return extract_open_task_lines(content)
    collected: List[str] = []
    for message in messages:
        collected.extend(extract_open_task_lines(str(message.get("content") or "")))
    return collected


def build_mms_body(host_lines: Sequence[str], task_lines: Sequence[str]) -> str:
    sections: List[str] = []
    if host_lines:
        sections.append("PadSplit:\n" + "\n".join(host_lines))
    if task_lines:
        sections.append("Open tasks:\n" + "\n".join(task_lines))
    body = sanitize_sms("\n\n".join(sections))
    if contains_lock_code_like(body):
        body = sanitize_sms(body)
    if len(body) > SMS_CHAR_LIMIT:
        body = body[: SMS_CHAR_LIMIT - 14].rstrip() + "… [truncated]"
    return body


def plan_send(
    host_lines: Sequence[str],
    task_lines: Sequence[str],
    window: Window,
    sent_windows: Set[str],
    *,
    ci: Optional[bool] = None,
) -> SendPlan:
    in_ci = running_in_ci() if ci is None else ci
    recipients = assert_group_recipients(GROUP_RECIPIENTS)
    if in_ci:
        return SendPlan(
            action="skip_ci",
            window_id=window.id,
            host_lines=list(host_lines),
            task_lines=list(task_lines),
            recipients=recipients,
        )
    if not host_lines and not task_lines:
        return SendPlan(action="skip_empty", window_id=window.id, recipients=recipients)
    if window.id in sent_windows:
        return SendPlan(
            action="skip_duplicate",
            window_id=window.id,
            host_lines=list(host_lines),
            task_lines=list(task_lines),
            recipients=recipients,
        )
    body = build_mms_body(host_lines, task_lines)
    if not body or contains_lock_code_like(body):
        return SendPlan(action="skip_empty", window_id=window.id, recipients=recipients)
    return SendPlan(
        action="send",
        window_id=window.id,
        body=body,
        host_lines=list(host_lines),
        task_lines=list(task_lines),
        recipients=recipients,
    )


def load_sent_windows(path: Path = STATE_PATH) -> Set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(payload, dict):
        values = payload.get("sent_windows") or []
    elif isinstance(payload, list):
        values = payload
    else:
        return set()
    return {str(item) for item in values}


def save_sent_window(window_id: str, path: Path = STATE_PATH) -> None:
    windows = load_sent_windows(path)
    windows.add(window_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = sorted(windows)[-14:]
    path.write_text(json.dumps({"sent_windows": keep}, indent=2) + "\n")


def fetch_host_threads() -> List[Dict[str, Any]]:
    creds = scraper.load_credentials()
    with scraper.create_session() as session:
        scraper.login(session, creds["email"], creds["password"], force=False)
        threads = scraper.fetch_messages(session, creds)
        scraper._enrich_recent_threads(session, creds, threads)
        return threads


def load_latest_json_threads() -> List[Dict[str, Any]]:
    candidates = [
        ROOT_DIR / "padsplit_scraper" / "output" / "latest.json",
        ROOT_DIR / "docs" / "data" / "latest.json",
    ]
    for path in candidates:
        if path.exists():
            payload = json.loads(path.read_text())
            return list(payload.get("messages") or [])
    return []


def collect_host_lines(since: datetime) -> List[str]:
    threads: List[Dict[str, Any]] = []
    try:
        threads = fetch_host_threads()
    except Exception as exc:
        sys.stderr.write(f"[field-mms] live PadSplit fetch failed; trying latest.json: {exc}\n")
        try:
            threads = load_latest_json_threads()
        except Exception as fallback_exc:
            sys.stderr.write(f"[field-mms] latest.json fallback failed: {fallback_exc}\n")
            return []
    return summarize_host_messages(threads, since=since)


def fetch_discord_channel_messages(token: str, since: datetime) -> List[Dict[str, Any]]:
    headers = {"Authorization": f"Bot {token}"}
    params = {"limit": 50}
    response = requests.get(
        f"{DISCORD_API_BASE}/channels/{DISCORD_TASKS_CHANNEL_ID}/messages",
        headers=headers,
        params=params,
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    messages = response.json() or []
    kept: List[Dict[str, Any]] = []
    for message in messages:
        created = _parse_created(message.get("timestamp") or message.get("edited_timestamp"))
        if created is not None and created < since - timedelta(days=2):
            continue
        kept.append(message)
    return kept


def collect_task_lines(since: datetime) -> List[str]:
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        sys.stderr.write("[field-mms] DISCORD_BOT_TOKEN missing; Discord source is empty\n")
        return []
    try:
        messages = fetch_discord_channel_messages(token, since)
    except Exception as exc:
        sys.stderr.write(f"[field-mms] Discord #ai-tasks-temp fetch failed: {exc}\n")
        return []
    return digest_discord_open_tasks(messages)


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def send_via_messages_chat(body: str, chat_name: str) -> None:
    """Send one group MMS via the existing Mac Messages thread (Voice as owner)."""
    with __import__("tempfile").NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(body)
        body_path = handle.name
    try:
        os.chmod(body_path, 0o600)
        escaped_chat = _escape_applescript(chat_name)
        escaped_path = _escape_applescript(body_path)
        script = (
            f'set msg to do shell script "cat " & quoted form of "{escaped_path}"\n'
            f'tell application "Messages"\n'
            f'    send msg to chat "{escaped_chat}"\n'
            f"end tell\n"
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "osascript failed").strip()
            raise RuntimeError(f"Messages.app group send failed: {err}")
    finally:
        try:
            os.unlink(body_path)
        except OSError:
            pass


def send_group_mms(body: str, recipients: Sequence[str] = GROUP_RECIPIENTS) -> None:
    if not sending_allowed():
        raise RuntimeError("CI / non-Mac must not send MMS")
    checked = assert_group_recipients(recipients)
    if normalize_phone(DON_PHONE) not in {normalize_phone(item) for item in checked}:
        raise RuntimeError("Refusing send: Don number must be 214-779-8338")
    custom = (os.getenv("FIELD_MMS_SEND_COMMAND") or "").strip()
    if custom:
        import shlex

        command = shlex.split(custom) + list(checked)
        result = subprocess.run(command, input=body, text=True, check=False, capture_output=True)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "send command failed").strip()
            raise RuntimeError(f"FIELD_MMS_SEND_COMMAND failed: {err}")
        return
    chat_name = (os.getenv("FIELD_MMS_CHAT_NAME") or FIELD_MMS_CHAT_NAME_DEFAULT).strip()
    send_via_messages_chat(body, chat_name)


def build_launchd_plist(workspace: Path = ROOT_DIR) -> Dict[str, Any]:
    logs = workspace / "logs"
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/bin/zsh", str(workspace / "run_field_mms.sh")],
        "WorkingDirectory": str(workspace),
        "StartCalendarInterval": [
            {"Hour": 6, "Minute": 0},
            {"Hour": 19, "Minute": 0},
        ],
        "StandardOutPath": str(logs / "field-mms.stdout.log"),
        "StandardErrorPath": str(logs / "field-mms.stderr.log"),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
    }


def install_launchd(workspace: Path = ROOT_DIR) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent install is Mac-only")
    LAUNCH_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    (workspace / "logs").mkdir(parents=True, exist_ok=True)
    path = LAUNCH_AGENT_DIR / f"{LAUNCHD_LABEL}.plist"
    path.write_bytes(plistlib.dumps(build_launchd_plist(workspace)))
    subprocess.run(["launchctl", "unload", str(path)], check=False, capture_output=True)
    result = subprocess.run(["launchctl", "load", str(path)], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "launchctl load failed").strip()
        raise RuntimeError(f"Failed to load {path}: {err}")
    return path


def run_window(
    *,
    now: Optional[datetime] = None,
    host_fetcher: Optional[Callable[[datetime], List[str]]] = None,
    task_fetcher: Optional[Callable[[datetime], List[str]]] = None,
    sender: Optional[Callable[[str, Sequence[str]], None]] = None,
    state_path: Path = STATE_PATH,
    ci: Optional[bool] = None,
    dry_run: bool = False,
) -> SendPlan:
    current = (now or datetime.now(CT)).astimezone(CT)
    window = window_for(current)
    since = current - LOOKBACK
    host_lines = (host_fetcher or collect_host_lines)(since)
    task_lines = (task_fetcher or collect_task_lines)(since)
    sent = load_sent_windows(state_path)
    plan = plan_send(host_lines, task_lines, window, sent, ci=ci)
    if plan.action != "send":
        sys.stderr.write(f"[field-mms] {plan.action} window={plan.window_id}\n")
        return plan
    if dry_run:
        sys.stderr.write(f"[field-mms] dry-run window={plan.window_id}\n")
        print(plan.body)
        return plan
    send = sender or send_group_mms
    send(plan.body, plan.recipients)
    save_sent_window(plan.window_id, state_path)
    sys.stderr.write(f"[field-mms] sent window={plan.window_id} recipients={list(plan.recipients)}\n")
    return plan


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Daily Don-field group MMS (existing scraper)")
    parser.add_argument("--dry-run", action="store_true", help="Build the body; do not send")
    parser.add_argument("--install-launchd", action="store_true", help="Install 6am/7pm CT LaunchAgent on this Mac")
    args = parser.parse_args(argv)
    load_environment()
    if args.install_launchd:
        path = install_launchd()
        print(f"Installed {path}")
        return 0
    if running_in_ci() and not args.dry_run:
        sys.stderr.write("[field-mms] skip_ci: GitHub Actions / CI must not send MMS\n")
        return 0
    plan = run_window(dry_run=args.dry_run)
    if plan.action == "send" or args.dry_run:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
