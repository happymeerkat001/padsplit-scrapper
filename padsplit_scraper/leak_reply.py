#!/usr/bin/env python3
"""PadSplit host water-leak auto-reply (Ang GO / Chief).

When a current member reports a water leak at 100% precision, SEND a
shut-off pack on that PadSplit thread (Quo + water-key YouTube + host
shut-off copy) and emit a digit-free WATER_KEY_ORDER event on Discord
#ai-automations for Cart. No Amazon purchase in this scraper.

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

from dotenv import load_dotenv

try:
    from padsplit_scraper import lock_codes
    from padsplit_scraper import lockout_reply
    from padsplit_scraper import new_booking
    from padsplit_scraper.scraper import create_session, load_credentials, login
except ModuleNotFoundError:  # python3 padsplit_scraper/leak_reply.py
    import lock_codes  # type: ignore
    import lockout_reply  # type: ignore
    import new_booking  # type: ignore
    from scraper import create_session, load_credentials, login  # type: ignore


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
STATE_PATH = ROOT_DIR / "logs" / "leak_reply_state.json"

LOOKBACK = timedelta(hours=36)
IDEMPOTENCY_WINDOW = timedelta(hours=24)

# Member-thread only. Same Quo field line as lockout / live fleet templates.
QUO_FIELD_PHONE = lockout_reply.JOE_FIELD_PHONE
WATER_KEY_YOUTUBE_URL = "https://youtube.com/shorts/SCryjPiyZcs"
LEAK_PACK_MARKER = "Sorry about the water leak — please shut the water off now"
WATER_KEY_ORDER_MARKER = "WATER_KEY_ORDER"

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

# Current member leak reports. Bare “leak” without water/flood/fixture
# context is not 100% and must not fire.
_CURRENT_LEAK_RE = re.compile(
    r"(?i)("
    r"\bwater\s+leak(?:ing)?\b"
    r"|\bleak(?:ing)?\s+water\b"
    r"|\bpipe\s+leak(?:ing)?\b"
    r"|\bleak(?:ing)?\s+pipe\b"
    r"|\b(?:sink|faucet|toilet|shower|tub|ceiling|hose|spigot|"
    r"bathroom|kitchen)\s+(?:is\s+|has\s+(?:a\s+)?)?leak(?:ing|s)?\b"
    r"|\bleak(?:ing)?\s+(?:sink|faucet|toilet|shower|tub|ceiling|"
    r"pipe|hose|spigot)\b"
    r"|\bslow\s+leak\b"
    r"|\bleak(?:ing)?\s+(?:in|from|under|behind|near|at|by)\b"
    r"|\b(?:there(?:['’]s| is| are)|we have|i (?:have|found|see|saw)|"
    r"it(?:['’]s| is))\s+(?:a\s+|an\s+)?(?:water\s+)?leak(?:ing)?\b"
    r"|\b(?:is|are)\s+leak(?:ing)?\b"
    r"|\b(?:is|are)\s+flood(?:ing|ed)\b"
    r"|\bflooding\b"
    r"|\bflooded\b"
    r")",
)

# Host reminder / historical / non-water chatter. Stripped before the
# current-leak re-check so “was leaking … toilet has a slow leak now”
# can still fire.
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
    r")",
)


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
    return bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))


def live_send_enabled() -> bool:
    """Default off until Mac .env sets LEAK_REPLY_ENABLE. CI must not send."""
    if running_in_ci():
        return False
    flag = (os.getenv("LEAK_REPLY_ENABLE") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


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
    """Drop leading house numbers so Discord stays digit-free."""
    return _LEADING_HOUSE_NUM_RE.sub("", lockout_reply.normalize_text(street)).strip()


def detect_leak(text: str) -> bool:
    """True only at 100% current member leak / flood language."""
    raw = text or ""
    if _HOST_BLAST_RE.search(raw) and len(raw) > 280:
        return False
    stripped = _LEAK_EXCLUDE_RE.sub(" ", raw)
    return bool(_CURRENT_LEAK_RE.search(stripped))


def format_leak_body() -> str:
    """PadSplit host body. No lock codes, no SSN, no Amazon."""
    return "\n".join(
        [
            f"{LEAK_PACK_MARKER}.",
            "",
            f"Call/text Quo: {QUO_FIELD_PHONE}",
            "",
            "The water shut-off box is between the water meter and the house.",
            "1. Use the water key to turn OFF the water immediately.",
            "2. Keep the water OFF except a brief turn-on if you need drinking water, then OFF again.",
            "",
            "How to use a water key:",
            WATER_KEY_YOUTUBE_URL,
            "",
            "We’ll send someone out ASAP. If you’re unsure, call/text the number above.",
        ]
    )


def discord_water_key_order_text(
    *,
    house_label: str = "",
    street: str = "",
    room: str = "",
    chat_id: str = "",
) -> str:
    """Digit-free Cart event. No codes. Chat id / room digits are spelled."""
    house = lockout_reply.normalize_text(house_label) or "unknown"
    addr = street_label_for_discord(street)
    room_token = encode_digits_for_discord(room) if room else "unknown"
    chat_token = encode_digits_for_discord(chat_id) if chat_id else "unknown"
    parts = [
        WATER_KEY_ORDER_MARKER,
        f"house={house}",
    ]
    if addr:
        parts.append(f"addr={addr}")
    parts.extend(
        [
            f"room={room_token}",
            f"chat={chat_token}",
        ]
    )
    text = " ".join(parts)
    return lock_codes.assert_discord_outbound_safe(text)


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"threads": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"threads": {}}
    if not isinstance(payload, dict):
        return {"threads": {}}
    payload.setdefault("threads", {})
    if not isinstance(payload["threads"], dict):
        payload["threads"] = {}
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
    house = lockout_reply.match_house(street, member_text)
    room = lockout_reply.resolve_room(thread, member_text)
    return Decision(
        action="send",
        reason="100 percent current member leak",
        certainty=100,
        chat_id=chat_id,
        house_label=house.label,
        street=street,
        room=room.room or "",
        send_body=format_leak_body(),
        discord=discord_water_key_order_text(
            house_label=house.label,
            street=street,
            room=room.room or "",
            chat_id=chat_id,
        ),
    )


def post_automations_discord(
    text: str,
    *,
    token: Optional[str] = None,
    channel: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return lockout_reply.post_automations_discord(text, token=token, channel=channel)


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
) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    state = state if state is not None else load_state(state_path)
    results: List[Dict[str, Any]] = []

    for thread in threads:
        decision = decide(thread, now=now, state=state)
        row = {
            "chat_id": decision.chat_id,
            "action": decision.action,
            "reason": decision.reason,
            "certainty": decision.certainty,
            "house_label": decision.house_label,
            "room": decision.room,
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
        if decision.discord:
            if post_discord is not None:
                post_discord(decision.discord)
            else:
                try:
                    post_automations_discord(decision.discord)
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
    )
    if leftover_compose_tabs is None and not dry_run:
        new_booking.save_leftover_compose_tabs(tabs, leftover_tabs_path)

    sent = sum(1 for row in rows if row.get("action") == "sent")
    posts = [str(row["discord"]) for row in rows if row.get("discord") and row.get("action") == "sent"]
    for text in posts:
        lock_codes.assert_discord_outbound_safe(text)
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
