#!/usr/bin/env python3
"""Planner for leak alerts. This module does not place calls or send texts.

Live Bland calls, the webhook record, and the Quo result line are in
padsplit_scraper/leak_alert_bland.py. LEAK_ALERT_ENABLE defaults off and
LEAK_ALERT_DRY_RUN defaults on, so nothing is called until both gates open.

Voice entries are shaped for Bland (fixed script, max_duration 1 minute,
voicemail leave_message) for Don and Tom only. Don, Tom, and Ang share one
Quo group post (not a 1:1 to Ang). Tenants are one private Quo text each.
More than 10 tenant numbers, or more than 10 group participants, fails
closed and stores nothing for that list.

Don and Tom numbers come only from LEAK_ALERT_DON_E164 and
LEAK_ALERT_TOM_E164. The group comes only from LEAK_ALERT_GROUP_E164S, a
comma list of participants excluding the from-line. There are no defaults.
If that list is unset, the group entry is a skip.

Quo v1 cannot send by conversation id. POST /v1/messages takes content,
from, and to (the participant list, max 10). GET /v1/messages addresses a
group thread by that same participant set. Optional
LEAK_ALERT_GROUP_CONVERSATION_ID is a dry-run check only: GET
/v1/conversations confirms the list matches that conversation's
participants. Numbers are not logged. The check does not run on a live plan.

Tenant numbers are read at incident time from the host member profile,
for current members of the leaking house only, using the scraper's
authenticated session. They stay in memory. Plans, logs, Discord, and
logs/leak_alert_state.json store a recipient hash, never the number.
If a profile has no phone, that member is skipped and nothing is sent.

Phone source (host SPA, ShowPhoneToHostStore.loadPhoneNumber):
  GET {apiUrl}/api/host-member-profile/member-phone/{occupancy.pk}/
  JSON field phone_number (the SPA camelizes this to phoneNumber).
occupancy.pk is the numeric id on the host occupant-profile route. Messenger
threads store it as the GraphQL id MessengerOccupancyType:{pk}. The occupant
profile GraphQL selection also has user.phone, bundled with email and
screening fields; this planner does not query that.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

try:
    from padsplit_scraper import leak_reply
    from padsplit_scraper import lockout_reply
    from padsplit_scraper import new_booking
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/leak_alert.py
    import leak_reply  # type: ignore
    import lockout_reply  # type: ignore
    import new_booking  # type: ignore
    import runtime  # type: ignore


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT_DIR / "logs" / "leak_alert_state.json"

PADSPLIT_API_ROOT = "https://www.padsplit.com/api"
MEMBER_PHONE_PATH = "/host-member-profile/member-phone/{occupancy_pk}/"

TENANT_CAP = 10
BLAND_MAX_DURATION_MIN = 1
BLAND_VOICEMAIL_ACTION = "leave_message"
SCRIPT_ID = "leak_alert_v1"
BLAND_CALLS_PATH = "/v1/calls"
QUO_API_BASE = "https://api.quo.com"
QUO_MESSAGES_PATH = "/v1/messages"
QUO_CONVERSATIONS_PATH = "/v1/conversations"
CONVERSATION_PAGE_SIZE = 50
CONVERSATION_PAGE_CAP = 20

ROLE_ENV = {
    "don": "LEAK_ALERT_DON_E164",
    "tom": "LEAK_ALERT_TOM_E164",
}
GROUP_ENV = "LEAK_ALERT_GROUP_E164S"
GROUP_CONVERSATION_ENV = "LEAK_ALERT_GROUP_CONVERSATION_ID"
VOICE_ROLES = ("don", "tom")
PERSIST_STATUSES = {"planned", "skipped_handled"}

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
        "Tenant has been told to shut off the water. "
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


def parse_group_e164s(raw: str) -> List[str]:
    """Comma or whitespace list. Empty when unset. Never log the result."""
    numbers: List[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\s]+", raw or ""):
        number = normalize_e164(part)
        if not number or number in seen:
            continue
        seen.add(number)
        numbers.append(number)
    return numbers


def quo_group_spec(content: str, participant_count: int) -> Dict[str, Any]:
    """One group POST. The participant list is applied at send time and is not stored."""
    return {
        "provider": "quo",
        "method": "POST",
        "path": QUO_MESSAGES_PATH,
        "mode": "group",
        "participant_count": participant_count,
        "content": content,
    }


def _conversations_url(page_token: str = "") -> str:
    query = {"maxResults": str(CONVERSATION_PAGE_SIZE)}
    if page_token:
        query["pageToken"] = page_token
    return f"{QUO_API_BASE}{QUO_CONVERSATIONS_PATH}?{urllib.parse.urlencode(query)}"


def default_quo_get(url: str, headers: Mapping[str, str]) -> tuple[int, Dict[str, Any]]:
    """GET helper for the dry-run conversation check. The body is not logged."""
    request = urllib.request.Request(url, headers={str(k): str(v) for k, v in headers.items()}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            status = int(response.status)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code is None:
            return 0, {}
        reader = getattr(exc, "read", None)
        raw = b""
        if callable(reader):
            try:
                raw = reader()
            except Exception:
                raw = b""
        status = int(code)
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except Exception:
        payload = {}
    return status, payload if isinstance(payload, dict) else {}


def _participant_set(values: Sequence[Any]) -> set[str]:
    found: set[str] = set()
    for value in values:
        number = normalize_e164(str(value or ""))
        if number:
            found.add(number)
    return found


def check_group_conversation(
    expected: Sequence[str],
    conversation_id: str,
    *,
    api_key: str,
    getter=None,
) -> str:
    """Dry-run only. Return matched, mismatch, not_found, or check_failed.

    Does not return or log phone numbers.
    """
    fetch = getter or default_quo_get
    headers = {"Authorization": api_key}
    page_token = ""
    wanted = str(conversation_id or "").strip()
    expected_set = _participant_set(expected)
    for _page in range(CONVERSATION_PAGE_CAP):
        try:
            status, payload = fetch(_conversations_url(page_token), headers)
        except Exception:
            return "check_failed"
        if int(status or 0) != 200 or not isinstance(payload, dict):
            return "check_failed"
        rows = payload.get("data")
        if not isinstance(rows, list):
            return "check_failed"
        for row in rows:
            if not isinstance(row, dict) or str(row.get("id") or "") != wanted:
                continue
            actual = row.get("participants")
            if not isinstance(actual, list):
                return "mismatch"
            if _participant_set(actual) == expected_set:
                return "matched"
            return "mismatch"
        token = payload.get("nextPageToken")
        if not token:
            return "not_found"
        page_token = str(token)
    return "not_found"


def _group_entry(
    *,
    incident: str,
    script: str,
    environ: Mapping[str, str],
    quo_ready: bool,
    dry_run: bool,
    conversations_get,
) -> Dict[str, Any]:
    raw = environ.get(GROUP_ENV)
    base = {
        "key": f"group:{incident}",
        "kind": "quo_group",
        "provider": "quo",
        "method": "POST",
        "path": QUO_MESSAGES_PATH,
        "mode": "group",
    }
    if raw is None or not str(raw).strip():
        return {**base, "status": "skipped", "reason": "group_unset"}
    numbers = parse_group_e164s(str(raw))
    from_raw = (environ.get("QUO_FROM_NUMBER") or environ.get("FIELD_MMS_QUO_FROM") or "").strip()
    from_number = normalize_e164(from_raw) if from_raw else ""
    if from_number:
        numbers = [number for number in numbers if number != from_number]
    if not numbers:
        return {**base, "status": "skipped", "reason": "group_unset"}
    if len(numbers) > TENANT_CAP:
        return {
            **base,
            "status": "fail_closed",
            "reason": "over_cap",
            "count": len(numbers),
            "cap": TENANT_CAP,
        }
    if not quo_ready:
        return {
            **base,
            **quo_group_spec(script, len(numbers)),
            "status": "missing_credentials",
        }
    entry = {
        **base,
        **quo_group_spec(script, len(numbers)),
        "status": "planned",
    }
    conversation_id = str(environ.get(GROUP_CONVERSATION_ENV) or "").strip()
    if not conversation_id:
        entry["conversation_check"] = "skipped"
        return entry
    if not dry_run:
        entry["conversation_check"] = "not_run"
        return entry
    api_key = str(environ.get("QUO_API_KEY") or "").strip()
    check = check_group_conversation(
        numbers,
        conversation_id,
        api_key=api_key,
        getter=conversations_get,
    )
    entry["conversation_check"] = check
    if check != "matched":
        entry["status"] = "skipped"
        entry["reason"] = f"conversation_{check}"
    return entry


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


def _configured_status(has_number: bool, provider_ready: bool) -> str:
    if not has_number:
        return "missing_number"
    if not provider_ready:
        return "missing_credentials"
    return "planned"


def occupancy_pk(thread: Mapping[str, Any]) -> Optional[str]:
    """Numeric occupancy.pk. Messenger stores it inside the GraphQL id."""
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    raw_pk = occupancy.get("pk")
    if raw_pk is not None and str(raw_pk).strip().isdigit():
        return str(raw_pk).strip()
    decoded = new_booking.occupancy_pk_from_gid(occupancy.get("id"))
    if decoded is None:
        return None
    return str(decoded)


def phone_from_profile_payload(payload: Any) -> str:
    """Read phone_number from a member-phone response. Empty when absent."""
    if isinstance(payload, str):
        return normalize_e164(payload)
    if not isinstance(payload, dict):
        return ""
    for key in ("phone_number", "phoneNumber", "phone"):
        raw = payload.get(key)
        if raw:
            return normalize_e164(str(raw))
    return ""


def member_phone_fetcher(session: Any, api_root: str = PADSPLIT_API_ROOT):
    """GET the host member-phone endpoint. The number is not logged."""

    def fetch(pk: str) -> Dict[str, Any]:
        occupancy_pk_value = str(pk or "").strip()
        if not occupancy_pk_value.isdigit():
            return {}
        url = f"{api_root.rstrip('/')}{MEMBER_PHONE_PATH.format(occupancy_pk=occupancy_pk_value)}"
        try:
            response = session.get(url, timeout=30)
        except Exception:
            return {}
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            return {}
        try:
            payload = response.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    return fetch


def _house_member_phones(
    source: Mapping[str, Any],
    house_threads: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    profile_fetcher,
) -> tuple[List[str], int]:
    """In-memory E.164 list for current members of this house, plus a skip count."""
    phones: List[str] = []
    seen_pk: set[str] = set()
    seen_phone: set[str] = set()
    missing = 0
    for thread in house_threads:
        if not isinstance(thread, dict):
            continue
        if not lockout_reply.current_occupant(thread, now):
            continue
        if not leak_reply.same_house(source, thread):  # type: ignore[arg-type]
            continue
        pk = occupancy_pk(thread)
        if not pk or pk in seen_pk:
            if not pk:
                missing += 1
            continue
        seen_pk.add(pk)
        if profile_fetcher is None:
            missing += 1
            continue
        try:
            payload = profile_fetcher(pk)
        except Exception:
            payload = {}
        number = phone_from_profile_payload(payload)
        if not number:
            missing += 1
            continue
        if number in seen_phone:
            continue
        seen_phone.add(number)
        phones.append(number)
    return phones, missing


def _tenant_entries(
    *,
    incident: str,
    house_label: str,
    room: str,
    ask_photos: bool,
    phones: Sequence[str],
    missing: int,
    quo_ready: bool,
) -> List[Dict[str, Any]]:
    base = {
        "kind": "tenant_text",
        "provider": "quo",
        "mode": "private_1to1",
        "path": QUO_MESSAGES_PATH,
    }
    if len(phones) > TENANT_CAP:
        return [{
            **base,
            "key": f"tenant:{incident}:fail_closed",
            "status": "fail_closed",
            "reason": "over_cap",
            "count": len(phones),
            "cap": TENANT_CAP,
        }]
    entries: List[Dict[str, Any]] = []
    if phones:
        body = tenant_body(house_label, room, ask_photos=ask_photos)
        status = "planned" if quo_ready else "missing_credentials"
        for number in phones:
            entries.append({
                **base,
                **quo_private_spec(body),
                "key": f"tenant:{incident}:{recipient_hash(number)}",
                "status": status,
                "ask_photos": ask_photos,
                "body": body,
            })
    if not phones:
        entries.append({
            **base,
            "key": f"tenant:{incident}:skipped_no_phone",
            "status": "skipped_no_phone",
            "reason": "no_phone",
        })
    elif missing:
        entries.append({
            **base,
            "key": f"tenant:{incident}:skipped_no_phone",
            "status": "skipped_no_phone",
            "reason": "no_phone",
            "skipped_count": missing,
        })
    return entries


def build_plan(
    thread: Dict[str, Any],
    *,
    now: datetime,
    house_threads: Sequence[Dict[str, Any]],
    environ: Optional[Mapping[str, str]] = None,
    profile_fetcher=None,
    dry_run: bool = False,
    conversations_get=None,
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
        status = _configured_status(number_present[role], bland_ready)
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

    entries.append(
        _group_entry(
            incident=incident,
            script=script,
            environ=env,
            quo_ready=quo_ready,
            dry_run=dry_run,
            conversations_get=conversations_get,
        )
    )

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
        phones, missing = _house_member_phones(
            thread,
            house_threads,
            now=now,
            profile_fetcher=profile_fetcher,
        )
        entries.extend(
            _tenant_entries(
                incident=incident,
                house_label=house.label,
                room=room,
                ask_photos=ask_photos,
                phones=phones,
                missing=missing,
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
