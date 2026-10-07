#!/usr/bin/env python3
"""Dry-run planner for leak alerts. No HTTP.

Voice entries are shaped for Bland (fixed script, max_duration 1 minute,
voicemail leave_message) for Don, Tom, and Joe. Joe falls back to one
private Quo text when Bland is not configured. Tenants are one private
Quo text each. More than 10 recipients fails closed and stores nothing
for that incident's tenant list.

Numbers come from LEAK_ALERT_DON_E164, LEAK_ALERT_TOM_E164,
LEAK_ALERT_JOE_E164, and LEAK_TENANT_ROSTER_PATH. This module has no
default numbers. Plans, logs, and logs/leak_alert_state.json never
include a phone number or API key.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:
    from padsplit_scraper import leak_reply
    from padsplit_scraper import lockout_reply
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/leak_alert.py
    import leak_reply  # type: ignore
    import lockout_reply  # type: ignore
    import runtime  # type: ignore


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT_DIR / "logs" / "leak_alert_state.json"

TENANT_CAP = 10
BLAND_MAX_DURATION_MIN = 1
BLAND_VOICEMAIL_ACTION = "leave_message"
SCRIPT_ID = "leak_alert_v1"
BLAND_CALLS_PATH = "/v1/calls"
QUO_MESSAGES_PATH = "/v1/messages"

ROLE_ENV = {
    "don": "LEAK_ALERT_DON_E164",
    "tom": "LEAK_ALERT_TOM_E164",
    "joe": "LEAK_ALERT_JOE_E164",
}
VOICE_ROLES = ("don", "tom", "joe")
PERSIST_STATUSES = {"planned", "standby", "skipped_handled"}

_E164_LEAK_RE = re.compile(r"\+\d{10,15}")
_PHONE_CHARS_RE = re.compile(r"\D")


class CorruptAlertState(RuntimeError):
    """Alert state file is unreadable. Refuse to record new plans."""


@dataclass
class AlertPlan:
    incident: str
    entries: List[Dict[str, Any]] = field(default_factory=list)


def live_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Default off. CI and collection-only never enable alerts."""
    if environ is None:
        return runtime.send_enabled("leak_alert")
    return runtime.send_enabled("leak_alert", environ)  # type: ignore[arg-type]


def normalize_e164(value: str) -> str:
    """Return E.164 or empty. Callers must not log the result."""
    digits = _PHONE_CHARS_RE.sub("", value or "")
    if len(digits) == 10:
        digits = "1" + digits
    if len(digits) < 11 or len(digits) > 15:
        return ""
    return "+" + digits


def recipient_hash(e164: str) -> str:
    return hashlib.sha256(e164.encode("utf-8")).hexdigest()[:16]


def leak_category(text: str) -> str:
    """Short label for the script. Does not copy the member message."""
    lowered = (text or "").lower()
    if "flood" in lowered:
        return "flooding"
    if "water main" in lowered or "main water" in lowered or re.search(r"\bmain\s+(?:water\s+)?(?:line|pipe)", lowered):
        return "main"
    if "wall" in lowered or "ceiling" in lowered:
        return "wall_ceiling"
    if "pipe" in lowered:
        return "pipe"
    return "water"


def fixed_script(house: str, room: str, category: str) -> str:
    house_label = house or "the house"
    room_label = room or "unknown"
    return (
        f"Water emergency at {house_label}, room {room_label}. "
        f"Category {category}. "
        "The water is being shut off. "
        "This is an automated notice."
    )


def tenant_body(house: str, room: str, *, ask_photos: bool) -> str:
    house_label = house or "the house"
    room_bit = f" room {room}" if room else ""
    body = (
        f"{house_label}{room_bit}: water is shut off for a leak. "
        "Turn it on only briefly for drinking water, then leave it off."
    )
    if ask_photos:
        body += " Please reply with photos of the leak."
    return body


def message_has_picture(message: Mapping[str, Any]) -> bool:
    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("deleted"):
            continue
        if attachment.get("mediaType") == "PICTURE":
            return True
    return False


def bland_voice_spec(script: str) -> Dict[str, Any]:
    """Bland call shape. phone_number is applied at send time from env and is not stored."""
    return {
        "provider": "bland",
        "path": BLAND_CALLS_PATH,
        "max_duration": BLAND_MAX_DURATION_MIN,
        "task": script,
        "first_sentence": script,
        "block_interruptions": True,
        "voicemail": {"action": BLAND_VOICEMAIL_ACTION, "message": script},
    }


def quo_private_spec(content: str) -> Dict[str, Any]:
    """One Quo recipient per POST. from/to are applied at send time and are not stored."""
    return {
        "provider": "quo",
        "method": "POST",
        "path": QUO_MESSAGES_PATH,
        "mode": "private_1to1",
        "content": content,
    }


def assert_plan_has_no_numbers(payload: str) -> None:
    if _E164_LEAK_RE.search(payload or ""):
        raise RuntimeError("refusing alert output that contains a phone number")
    lowered = (payload or "").lower()
    if "api_key" in lowered or "authorization" in lowered:
        raise RuntimeError("refusing alert output that contains a credential")


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"alerts": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise CorruptAlertState(f"corrupt leak alert state at {path}") from exc
    if not isinstance(payload, dict):
        raise CorruptAlertState(f"corrupt leak alert state at {path}: not an object")
    alerts = payload.setdefault("alerts", {})
    if not isinstance(alerts, dict):
        raise CorruptAlertState(f"corrupt leak alert state at {path}: alerts is not an object")
    encoded = json.dumps(payload)
    assert_plan_has_no_numbers(encoded)
    return payload


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    encoded = json.dumps(state, indent=2) + "\n"
    assert_plan_has_no_numbers(encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded)


def _roster_path(environ: Mapping[str, str], explicit: Optional[Path]) -> Optional[Path]:
    if explicit is not None:
        return explicit
    raw = (environ.get("LEAK_TENANT_ROSTER_PATH") or "").strip()
    if not raw:
        return None
    return Path(raw)


def load_roster(path: Path) -> Dict[str, List[str]]:
    """House slug to normalized numbers. Caller must not log the lists."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("corrupt roster") from exc
    if not isinstance(payload, dict):
        raise ValueError("corrupt roster")
    roster: Dict[str, List[str]] = {}
    for slug, numbers in payload.items():
        if not isinstance(numbers, list):
            continue
        cleaned: List[str] = []
        seen: set[str] = set()
        for item in numbers:
            normalized = normalize_e164(str(item))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(normalized)
        roster[str(slug)] = cleaned
    return roster


def _voice_status(has_number: bool, bland_ready: bool) -> str:
    if not has_number:
        return "missing_number"
    if not bland_ready:
        return "missing_credentials"
    return "planned"


def _tenant_entries(
    *,
    incident: str,
    house_slug: str,
    house_label: str,
    room: str,
    ask_photos: bool,
    environ: Mapping[str, str],
    roster_path: Optional[Path],
    quo_ready: bool,
) -> List[Dict[str, Any]]:
    path = _roster_path(environ, roster_path)
    base = {
        "kind": "tenant_text",
        "provider": "quo",
        "mode": "private_1to1",
        "path": QUO_MESSAGES_PATH,
    }
    if path is None or not path.exists():
        return [{
            **base,
            "key": f"tenant:{incident}:missing_roster",
            "status": "missing_roster",
        }]
    try:
        roster = load_roster(path)
    except ValueError:
        return [{
            **base,
            "key": f"tenant:{incident}:fail_closed",
            "status": "fail_closed",
            "reason": "corrupt_roster",
        }]
    if not house_slug:
        return [{
            **base,
            "key": f"tenant:{incident}:fail_closed",
            "status": "fail_closed",
            "reason": "house_unknown",
        }]
    numbers = roster.get(house_slug) or []
    if not numbers:
        return [{
            **base,
            "key": f"tenant:{incident}:missing_roster",
            "status": "missing_roster",
            "reason": "house_not_in_roster",
        }]
    if len(numbers) > TENANT_CAP:
        return [{
            **base,
            "key": f"tenant:{incident}:fail_closed",
            "status": "fail_closed",
            "reason": "over_cap",
            "count": len(numbers),
            "cap": TENANT_CAP,
        }]
    body = tenant_body(house_label, room, ask_photos=ask_photos)
    status = "planned" if quo_ready else "missing_credentials"
    entries: List[Dict[str, Any]] = []
    for number in numbers:
        entries.append({
            **base,
            **quo_private_spec(body),
            "key": f"tenant:{incident}:{recipient_hash(number)}",
            "status": status,
            "ask_photos": ask_photos,
            "body": body,
        })
    return entries


def build_plan(
    thread: Dict[str, Any],
    *,
    now: datetime,
    house_threads: Sequence[Dict[str, Any]],
    environ: Optional[Mapping[str, str]] = None,
    roster_path: Optional[Path] = None,
) -> Optional[AlertPlan]:
    """Plan one incident. Returns None when there is no current member leak."""
    env: Mapping[str, str] = environ if environ is not None else os.environ
    if not lockout_reply.current_occupant(thread, now):
        return None
    leak_message = leak_reply.recent_member_leak(thread, now=now)
    if leak_message is None:
        return None
    chat_id = str(thread.get("id") or "")
    message_id = str(leak_message.get("id") or "")
    if not chat_id or not message_id:
        return None
    incident = f"{chat_id}:{message_id}"
    member_text = lockout_reply.message_text(leak_message)
    street = lockout_reply.thread_street(thread)
    house = lockout_reply.match_house(street, "")
    room = lockout_reply.resolve_room(thread, member_text).room or ""
    category = leak_category(member_text)
    script = fixed_script(house.label, room, category)
    ask_photos = not message_has_picture(leak_message)
    report_at = lockout_reply.parse_dt(leak_message.get("created"))
    handled = bool(
        report_at
        and leak_reply.house_host_handled(
            thread,
            house_threads,
            report_at,
            now=now,
        )
    )
    bland_ready = bool((env.get("BLAND_API_KEY") or "").strip())
    quo_ready = bool((env.get("QUO_API_KEY") or "").strip())
    voice_spec = bland_voice_spec(script)

    entries: List[Dict[str, Any]] = []
    number_present = {role: bool(normalize_e164(env.get(ROLE_ENV[role]) or "")) for role in VOICE_ROLES}
    for role in VOICE_ROLES:
        status = _voice_status(number_present[role], bland_ready)
        entries.append({
            "key": f"voice:{incident}:{role}",
            "kind": "voice",
            "role": role,
            "provider": "bland",
            "status": status,
            "has_number": number_present[role],
            "script_id": SCRIPT_ID,
            "script": script,
            "max_duration_min": BLAND_MAX_DURATION_MIN,
            "voicemail": BLAND_VOICEMAIL_ACTION,
            "bland": voice_spec,
        })

    joe_voice_planned = number_present["joe"] and bland_ready
    if joe_voice_planned:
        joe_status = "standby"
    elif not number_present["joe"]:
        joe_status = "missing_number"
    elif not quo_ready:
        joe_status = "missing_credentials"
    else:
        joe_status = "planned"
    entries.append({
        "key": f"joe:{incident}",
        "kind": "quo_text",
        "role": "joe",
        "status": joe_status,
        "has_number": number_present["joe"],
        "script": script,
        **quo_private_spec(script),
    })

    if handled:
        entries.append({
            "key": f"tenant:{incident}:skipped_handled",
            "kind": "tenant_text",
            "provider": "quo",
            "mode": "private_1to1",
            "status": "skipped_handled",
            "reason": "host leak pack already at house",
        })
    else:
        entries.extend(
            _tenant_entries(
                incident=incident,
                house_slug=house.slug,
                house_label=house.label,
                room=room,
                ask_photos=ask_photos,
                environ=env,
                roster_path=roster_path,
                quo_ready=quo_ready,
            )
        )
    encoded = json.dumps(entries)
    assert_plan_has_no_numbers(encoded)
    return AlertPlan(incident=incident, entries=entries)


def persist_plan(
    plan: AlertPlan,
    state: Dict[str, Any],
    *,
    now: datetime,
) -> List[Dict[str, Any]]:
    """Record actionable keys. Missing config and over-cap results are not stored."""
    alerts = state.setdefault("alerts", {})
    if not isinstance(alerts, dict):
        raise CorruptAlertState("alerts is not an object")
    stamped = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    visible: List[Dict[str, Any]] = []
    for entry in plan.entries:
        row = dict(entry)
        key = str(entry.get("key") or "")
        status = str(entry.get("status") or "")
        if status not in PERSIST_STATUSES or not key:
            visible.append(row)
            continue
        if key in alerts:
            row["status"] = "already_planned"
            visible.append(row)
            continue
        alerts[key] = {
            "status": status,
            "planned_at": stamped,
            "kind": entry.get("kind"),
            "provider": entry.get("provider"),
            "role": entry.get("role"),
            "mode": entry.get("mode"),
            "max_duration_min": entry.get("max_duration_min"),
            "voicemail": entry.get("voicemail"),
        }
        visible.append(row)
    assert_plan_has_no_numbers(json.dumps(state))
    return visible
