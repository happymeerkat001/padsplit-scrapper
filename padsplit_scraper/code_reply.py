"""Occupancy-verified Sifely code replies and the per-house report.

Sifely is the source of truth. A reply uses the Sifely code. On a digest
mismatch the automations channel gets a digit-free flag, and Firestore
property_codes is updated only when CODES_DIGEST_SYNC_ENABLE is on
(default off, which means dry-run "would update").

``python -m padsplit_scraper.code_reply --once`` reads the PadSplit message
list only (no full scrape) and drains pending code-change notices. A Mac
launchd or cron job can call it every 5 to 10 minutes. This repo does not
install that schedule. CI must not send.

``python -m padsplit_scraper.code_reply --house-report [--house SLUG] [--live]``
prints counts only. The default is an offline fixture. ``--live`` is
read-only and spaces Sifely calls through the client rate limit.

First live enablement should set CODE_AUTOMATION_TEST_HOUSES=spanish_moss.
Spanish Moss is Ang's own house and has no PadSplit tenants.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from padsplit_scraper import code_change_notify
    from padsplit_scraper import code_gates
    from padsplit_scraper import code_request
    from padsplit_scraper import codes_history
    from padsplit_scraper import lockout_reply
    from padsplit_scraper import new_booking
    from padsplit_scraper import partner_members
    from padsplit_scraper import runtime
    from padsplit_scraper.room_interlock import (
        Allowed,
        assert_room_action_allowed,
        house_label,
        room_words,
    )
    from padsplit_scraper.sifely_client import (
        HOUSE_LOCK_PROFILES,
        CurrentCode,
        Inventory,
        SifelyUnavailable,
        codes_field_for,
        find_lock,
        inventory_locks,
        match_house_slug,
    )
except ModuleNotFoundError:  # python -m padsplit_scraper.code_reply
    import code_change_notify  # type: ignore
    import code_gates  # type: ignore
    import code_request  # type: ignore
    import codes_history  # type: ignore
    import lockout_reply  # type: ignore
    import new_booking  # type: ignore
    import partner_members  # type: ignore
    import runtime  # type: ignore
    from room_interlock import Allowed, assert_room_action_allowed, house_label, room_words  # type: ignore
    from sifely_client import (  # type: ignore
        HOUSE_LOCK_PROFILES,
        CurrentCode,
        Inventory,
        SifelyUnavailable,
        codes_field_for,
        find_lock,
        inventory_locks,
        match_house_slug,
    )


DRY_RUN_ENV = "CODE_REPLY_DRY_RUN"
# Labeled fakes for the offline house report. Not lock codes.
FIXTURE_FRONT = "FAKE-CODE-A"
FIXTURE_ROOM = "FAKE-CODE-B"
FIXTURE_DIGEST_ROOM = "FAKE-CODE-C"
FIXTURE_BACK = "FAKE-CODE-D"


def _log(message: str) -> None:
    sys.stderr.write(f"[code-reply] {message}\n")


def _no_digits(text: str) -> str:
    if any(ch.isdigit() for ch in text or ""):
        raise RuntimeError("Refusing Discord outbound: message contains digits")
    return text


def needs_tap_text(slug: str, room: str) -> str:
    return _no_digits(
        f"{house_label(slug)} room {room_words(room)}: code request needs a tap"
    )


def missing_code_text(slug: str) -> str:
    return _no_digits(f"{house_label(slug)}: missing code")


def mismatch_text(slug: str, role: str, room: str = "") -> str:
    label = house_label(slug)
    if role == "room":
        where = f"room {room_words(room)}"
    elif role == "back":
        where = "back door"
    elif role == "lockbox":
        where = f"room {room_words(room)} lockbox"
    else:
        where = "front door"
    return _no_digits(f"{label} {where}: digest mismatch, digest updated to match Sifely")


@dataclass
class RoomResolution:
    ok: bool
    slug: str = ""
    label: str = ""
    room: str = ""
    reason: str = ""
    text_conflict: bool = False
    send_room_code: bool = False
    send_door_codes: bool = False
    property_id: str = ""


@dataclass
class RoleCode:
    role: str
    field: str
    code: str = ""
    source: str = "missing"
    lock_id: str = ""


def house_from_thread(thread: Dict[str, Any], directory: Any = None) -> Optional[tuple[str, str, str]]:
    """House slug from the street or property id. Member text is ignored."""
    street = lockout_reply.thread_street(thread)
    slug = match_house_slug(street)
    prop = lockout_reply._explicit_property_id(thread)
    if slug:
        return slug, house_label(slug), prop
    if directory is None or not prop:
        return None
    try:
        refs = list(directory.property_index() or [])
    except Exception:
        return None
    for ref in refs:
        if not isinstance(ref, dict) or str(ref.get("id") or "") != prop:
            continue
        found = match_house_slug(str(ref.get("street") or ""))
        if found:
            return found, house_label(found), prop
    return None


def resolve_member_room(
    thread: Dict[str, Any],
    directory: Any,
    now: datetime,
    *,
    member_text: str = "",
) -> RoomResolution:
    """Room comes from occupancy, confirmed by partner_members.select_member."""
    occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
    user = occupancy.get("user") if isinstance(occupancy.get("user"), dict) else {}
    if not occupancy or not user:
        return RoomResolution(False, reason="occupancy missing")
    room = lockout_reply.thread_room(thread)
    if not room:
        return RoomResolution(False, reason="occupancy room missing")
    house = house_from_thread(thread, directory)
    if not house:
        return RoomResolution(False, reason="house unknown")
    slug, label, prop = house
    if directory is None:
        return RoomResolution(False, slug=slug, label=label, room=room, reason="partner members unavailable")
    try:
        rows, detail = lockout_reply._member_rows_for_thread(directory, thread)
    except Exception:
        return RoomResolution(False, slug=slug, label=label, room=room, reason="partner members unavailable")
    if detail:
        return RoomResolution(False, slug=slug, label=label, room=room, reason=detail)
    occ_id = lockout_reply._thread_occupancy_id(thread)
    match = partner_members.select_member(
        rows,
        occupancy_id=occ_id,
        room_number=room,
        now=now,
    )
    if not match.ok:
        return RoomResolution(False, slug=slug, label=label, room=room, reason=match.detail)
    if occ_id:
        matched = [
            row
            for row in rows
            if isinstance(row, dict) and partner_members.occupancy_ids_match(row.get("occupancy_id"), occ_id)
        ]
    else:
        matched = [
            row
            for row in rows
            if isinstance(row, dict)
            and partner_members.rooms_match(row.get("room_number"), room)
            and partner_members.move_out_is_open(row.get("move_out_date"), now)
        ]
    if len(matched) != 1:
        reason = "multiple occupants" if len(matched) > 1 else "no match"
        return RoomResolution(False, slug=slug, label=label, room=room, reason=reason)
    row = matched[0]
    if not partner_members.rooms_match(row.get("room_number"), room):
        return RoomResolution(False, slug=slug, label=label, room=room, reason="occupancy room mismatch")
    if not partner_members.move_out_is_open(row.get("move_out_date"), now):
        return RoomResolution(False, slug=slug, label=label, room=room, reason="moved out")
    open_rows = [
        item
        for item in rows
        if isinstance(item, dict)
        and partner_members.rooms_match(item.get("room_number"), room)
        and item.get("is_terminated") is False
        and partner_members.move_out_is_open(item.get("move_out_date"), now)
    ]
    if len(open_rows) != 1:
        return RoomResolution(False, slug=slug, label=label, room=room, reason="multiple occupants")
    spoken = lockout_reply.mentioned_room(member_text)
    conflict = bool(spoken) and not partner_members.rooms_match(spoken, room)
    return RoomResolution(
        ok=True,
        slug=slug,
        label=label,
        room=str(room),
        reason="ok",
        text_conflict=conflict,
        send_room_code=not conflict,
        send_door_codes=True,
        property_id=prop,
    )


def _role_label(role: str) -> str:
    if role == "back":
        return "Back door code"
    if role == "room":
        return "Room code"
    if role == "lockbox":
        return "Lockbox code"
    return "Front door code"


def format_code_reply(label: str, room: str, parts: Sequence[RoleCode]) -> str:
    lines = [f"Entry for {label} Rm {room}:"]
    for part in parts:
        if part.code:
            lines.append(f"{_role_label(part.role)}: {part.code}")
    return "\n".join(lines)


def _digest_value(doc: Dict[str, Any], field_name: str) -> str:
    if not field_name or not isinstance(doc, dict):
        return ""
    value = doc.get(field_name)
    if isinstance(value, str):
        return value.strip()
    return ""


def _select_role(
    slug: str,
    role: str,
    room: str,
    inventory: Inventory,
    passcodes_for: Callable[[str], CurrentCode],
    digest: Dict[str, Any],
    *,
    sifely_down: bool,
) -> RoleCode:
    field_name = codes_field_for(slug, role, room)
    lock = None if sifely_down else find_lock(inventory.mapped, slug, role, room)
    if lock is not None and not sifely_down:
        try:
            selected = passcodes_for(lock.lock_id)
        except SifelyUnavailable:
            selected = None
        if selected is None:
            value = _digest_value(digest, field_name)
            if value:
                return RoleCode(role, field_name, value, "digest", lock.lock_id)
            return RoleCode(role, field_name, "", "missing", lock.lock_id)
        if selected.status != "ok":
            return RoleCode(role, field_name, "", "ambiguous", lock.lock_id)
        return RoleCode(role, field_name, selected.code, "sifely", lock.lock_id)
    if field_name:
        value = _digest_value(digest, field_name)
        if value:
            return RoleCode(role, field_name, value, "digest")
        return RoleCode(role, field_name, "", "missing")
    return RoleCode(role, "", "", "missing")


def roles_for(kind: str, resolution: RoomResolution) -> List[str]:
    if kind == "wifi" or not resolution.ok:
        return []
    if kind == "room":
        if resolution.send_room_code:
            return ["room"]
        if resolution.send_door_codes:
            return ["front", "back"]
        return []
    if kind == "lockbox":
        if resolution.send_room_code:
            return ["lockbox"]
        if resolution.send_door_codes:
            return ["front", "back"]
        return []
    if kind == "door":
        return ["front", "back"] if resolution.send_door_codes else []
    # Ambiguous ask: this member's room code and the front door only.
    if kind == "unknown":
        roles: List[str] = []
        if resolution.send_room_code:
            roles.append("room")
        if resolution.send_door_codes:
            roles.append("front")
        return roles
    return []


def _dedupe(parts: Sequence[RoleCode]) -> List[RoleCode]:
    seen = set()
    out: List[RoleCode] = []
    for part in parts:
        marker = part.lock_id or part.field or part.role
        if marker in seen:
            continue
        seen.add(marker)
        out.append(part)
    return out


def load_reply_state(path: Any) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {"replied": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"replied": {}}
    if not isinstance(payload, dict):
        return {"replied": {}}
    payload.setdefault("replied", {})
    return payload


def save_reply_state(state: Dict[str, Any], path: Any) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def _already(state: Dict[str, Any], chat_id: str, message_id: str) -> bool:
    rows = (state.get("replied") or {}).get(chat_id) or []
    return message_id in {str(item) for item in rows}


def _remember(state: Dict[str, Any], chat_id: str, message_id: str) -> None:
    replied = state.setdefault("replied", {})
    rows = [str(item) for item in (replied.get(chat_id) or [])]
    if message_id not in rows:
        rows.append(message_id)
    replied[chat_id] = rows[-50:]


@dataclass
class SyncResult:
    action: str
    discord: str = ""


def sync_digest_field(
    db: Any,
    *,
    slug: str,
    field_name: str,
    code: str,
    source: str,
    role: str,
    room: str,
    now: datetime,
    enabled: bool,
    written: set[tuple[str, str]],
) -> SyncResult:
    """Equality compare. Write only a confident Sifely code, once per field."""
    if source != "sifely" or not code or not field_name:
        return SyncResult("skipped")
    key = (slug, field_name)
    if key in written:
        return SyncResult("already")
    doc_ref = db.collection(codes_history.CODES_COLLECTION).document(slug)
    snap = doc_ref.get()
    exists = bool(getattr(snap, "exists", True))
    current = snap.to_dict() if exists and hasattr(snap, "to_dict") else {}
    if not isinstance(current, dict):
        current = {}
    existing = current.get(field_name)
    if isinstance(existing, str) and existing == code:
        written.add(key)
        return SyncResult("equal")
    flag = mismatch_text(slug, role, room)
    if not enabled:
        return SyncResult("would_update", flag)
    merged = dict(current)
    merged[field_name] = code
    merged["updatedAt"] = now.astimezone(timezone.utc).isoformat()
    doc_ref.set({field_name: code, "updatedAt": merged["updatedAt"]}, merge=True)
    fields = codes_history.canonicalize_fields(merged)
    doc_ref.collection(codes_history.VERSIONS_COLLECTION).document().set(
        {
            "fields": fields,
            "contentHash": codes_history.content_hash(fields),
            "createdAt": now,
            "expireAt": now + timedelta(days=codes_history.RETENTION_DAYS),
            "source": "sifely_sync",
        }
    )
    written.add(key)
    return SyncResult("updated", flag)


def process_code_requests(
    threads: Sequence[Dict[str, Any]],
    *,
    now: datetime,
    directory: Any,
    state: Optional[Dict[str, Any]] = None,
    inventory: Optional[Inventory] = None,
    passcodes_for: Optional[Callable[[str], CurrentCode]] = None,
    digest_for: Optional[Callable[[str], Dict[str, Any]]] = None,
    sifely_down: bool = False,
    send_fn: Optional[Callable[[str, str], Any]] = None,
    post_discord: Optional[Callable[[str], Any]] = None,
    sync_db: Any = None,
    sync_enabled: bool = False,
    send_enabled: bool = False,
    dry_run: bool = True,
    max_sends: Optional[int] = None,
    max_age: Optional[timedelta] = None,
    jev: Optional[code_request.JevClassifier] = None,
    environ: Optional[os._Environ[str]] = None,
    notify_state: Any = None,
) -> List[Dict[str, Any]]:
    """Classify, resolve, and maybe send. Does not log reply bodies."""
    env = environ if environ is not None else os.environ
    current_state = state if state is not None else {"replied": {}}
    age = lockout_reply.lockout_max_age() if max_age is None else max_age
    cap = lockout_reply.lockout_max_sends() if max_sends is None else max_sends
    sent = 0
    written: set[tuple[str, str]] = set()
    results: List[Dict[str, Any]] = []
    locks = inventory if inventory is not None else Inventory()

    def lookup_digest(slug: str) -> Dict[str, Any]:
        if digest_for is None:
            return {}
        try:
            doc = digest_for(slug) or {}
        except Exception:
            return {}
        return doc if isinstance(doc, dict) else {}

    def lookup_code(lock_id: str) -> CurrentCode:
        if passcodes_for is None:
            raise SifelyUnavailable("no passcode reader")
        return passcodes_for(lock_id)

    for thread in threads:
        chat_id = str(thread.get("id") or "")
        picked = _newest_request(thread, now=now, max_age=age, jev=jev)
        if picked is None:
            continue
        message, request = picked
        message_id = lockout_reply.message_key(message)
        created = lockout_reply.parse_dt(message.get("created"))
        row: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "kind": request.kind,
            "action": "skip",
            "reason": "",
            "source": "",
        }
        if request.kind == "wifi":
            row["action"] = "skip_wifi"
            row["reason"] = "wifi is out of scope"
            results.append(row)
            continue
        if not request.is_code_request:
            row["action"] = "skip"
            row["reason"] = "not a code request"
            results.append(row)
            continue
        if not chat_id or not message_id:
            row["reason"] = "missing ids"
            results.append(row)
            continue
        if _already(current_state, chat_id, message_id):
            row["action"] = "already_sent"
            row["reason"] = "already replied to this message"
            results.append(row)
            continue
        if created is not None and lockout_reply.hand_replied_after(thread, created):
            row["action"] = "already_answered"
            row["reason"] = "non-member replied after the request"
            results.append(row)
            continue
        if lockout_reply.message_is_stale(message, now=now, max_age=age):
            row["action"] = "stale"
            row["reason"] = "message older than max age"
            results.append(row)
            continue
        if sent >= cap:
            row["action"] = "max_sends"
            row["reason"] = "per-run send cap"
            results.append(row)
            continue
        resolution = resolve_member_room(
            thread,
            directory,
            now,
            member_text=lockout_reply.message_text(message),
        )
        if not resolution.ok:
            row["action"] = "no_send"
            row["reason"] = resolution.reason
            results.append(row)
            continue
        if not code_gates.house_allowed(resolution.slug, env):
            row["action"] = "skipped_allowlist"
            row["reason"] = "house is outside the test allowlist"
            results.append(row)
            continue
        if resolution.text_conflict:
            text = needs_tap_text(resolution.slug, resolution.room)
            row["discord"] = text
            if post_discord is not None:
                post_discord(text)
        digest = lookup_digest(resolution.slug)
        parts = _dedupe(
            [
                _select_role(
                    resolution.slug,
                    role,
                    resolution.room,
                    locks,
                    lookup_code,
                    digest,
                    sifely_down=sifely_down,
                )
                for role in roles_for(request.kind, resolution)
            ]
        )
        sendable = [part for part in parts if part.code]
        if not sendable:
            text = missing_code_text(resolution.slug)
            row["action"] = "missing_code"
            row["reason"] = "no code for this request"
            row["discord"] = text
            if post_discord is not None:
                post_discord(text)
            results.append(row)
            continue
        sources = sorted({part.source for part in sendable})
        row["source"] = ",".join(sources)
        body = format_code_reply(resolution.label, resolution.room, sendable)
        if not send_enabled or dry_run:
            row["action"] = "would_send"
            row["reason"] = "dry-run"
            results.append(row)
            continue
        try:
            if send_fn is None:
                raise RuntimeError("send is not wired")
            send_fn(chat_id, body)
        except Exception:
            row["action"] = "send_failed"
            row["reason"] = "send failed"
            results.append(row)
            continue
        _remember(current_state, chat_id, message_id)
        sent += 1
        row["action"] = "sent"
        row["reason"] = "sent"
        _observe_sifely_parts(
            sendable,
            resolution,
            threads,
            directory,
            now,
            notify_state,
            env,
        )
        if sync_db is not None:
            for part in sendable:
                outcome = sync_digest_field(
                    sync_db,
                    slug=resolution.slug,
                    field_name=part.field,
                    code=part.code,
                    source=part.source,
                    role=part.role,
                    room=resolution.room,
                    now=now,
                    enabled=sync_enabled,
                    written=written,
                )
                if outcome.discord:
                    row.setdefault("discord_flags", []).append(outcome.discord)
                    if post_discord is not None:
                        post_discord(outcome.discord)
                row["sync"] = outcome.action
        results.append(row)
    return results


def _observe_sifely_parts(
    parts: Sequence[RoleCode],
    resolution: RoomResolution,
    threads: Sequence[Dict[str, Any]],
    directory: Any,
    now: datetime,
    notify_state: Any,
    env: os._Environ[str],
) -> None:
    for part in parts:
        if part.source != "sifely" or not part.code or not part.lock_id:
            continue
        try:
            code_change_notify.note_if_changed(
                slug=resolution.slug,
                role=part.role if part.role != "shared" else "front",
                room=resolution.room if part.role == "room" else "",
                lock_id=part.lock_id,
                code=part.code,
                threads=threads,
                directory=directory,
                now=now,
                property_id=resolution.property_id,
                state_path=notify_state,
                environ=env,
                source="code_reply",
            )
        except Exception:
            _log("code notice observe skipped")


def _observe_inventory_changes(
    inventory: Inventory,
    passcodes_for: Callable[[str], CurrentCode],
    *,
    threads: Sequence[Dict[str, Any]],
    directory: Any,
    now: datetime,
    notify_state: Any,
    env: os._Environ[str],
) -> None:
    """Compare each mapped lock with the last HMAC. The first sight is a baseline."""
    if not code_change_notify._enabled_flag(env):
        return
    for item in inventory.mapped:
        if not code_gates.house_allowed(item.slug, env):
            continue
        try:
            selected = passcodes_for(item.lock_id)
        except Exception:
            continue
        if selected.status != "ok" or not selected.code:
            continue
        role = "front" if item.role == "shared" else item.role
        room = item.room if item.role == "room" else ""
        try:
            code_change_notify.note_if_changed(
                slug=item.slug,
                role=role,
                room=room,
                lock_id=item.lock_id,
                code=selected.code,
                threads=threads,
                directory=directory,
                now=now,
                state_path=notify_state,
                environ=env,
                source="sifely_observed",
            )
        except Exception:
            _log("code notice observe skipped")


def _newest_request(
    thread: Dict[str, Any],
    *,
    now: datetime,
    max_age: timedelta,
    jev: Optional[code_request.JevClassifier],
) -> Optional[tuple[Dict[str, Any], code_request.CodeRequest]]:
    newest: Optional[Dict[str, Any]] = None
    newest_at: Optional[datetime] = None
    for message in lockout_reply.iter_thread_messages(thread):
        if message.get("deleted") or not lockout_reply.is_member_message(thread, message):
            continue
        created = lockout_reply.parse_dt(message.get("created"))
        if created is None or (now - created) > lockout_reply.LOOKBACK:
            continue
        fast = code_request.classify_fast(lockout_reply.message_text(message))
        if not fast.is_code_request:
            continue
        if newest_at is None or created > newest_at:
            newest = message
            newest_at = created
    if newest is None:
        return None
    request = code_request.classify(lockout_reply.message_text(newest), jev)
    return newest, request


@dataclass
class HouseReport:
    slug: str
    reachable: bool
    front: int = 0
    back: int = 0
    room_locks: int = 0
    unmapped: int = 0
    occupants: int = 0
    occupied_mapped: int = 0
    match: int = 0
    mismatch: int = 0
    missing: int = 0
    would_update: int = 0
    allowed: int = 0
    refused: int = 0
    digest_unavailable: bool = False


def format_house_report(row: HouseReport) -> str:
    lines = [
        f"house: {row.slug}",
        f"sifely reachable: {'y' if row.reachable else 'n'}",
        f"sifely locks mapped: front {row.front} back {row.back} room {row.room_locks}",
        f"unmapped/ambiguous lock count: {row.unmapped}",
        f"rooms with a current occupant: {row.occupants}",
        f"rooms with occupant and mapped sifely room lock: {row.occupied_mapped}",
    ]
    if row.digest_unavailable:
        lines.append("digest compare: unavailable (no Firestore admin creds)")
    else:
        lines.extend(
            [
                f"digest match: {row.match}",
                f"digest mismatch: {row.mismatch}",
                f"digest missing: {row.missing}",
                f"would update {row.would_update} rooms",
            ]
        )
    lines.extend(
        [
            f"interlock allowed: {row.allowed}",
            f"interlock refused: {row.refused}",
        ]
    )
    return "\n".join(lines)


def _count_roles(matches: Sequence[Any], slug: str) -> tuple[int, int, int]:
    front = back = rooms = 0
    for item in matches:
        if item.slug != slug:
            continue
        if item.role == "front":
            front += 1
        elif item.role == "back":
            back += 1
        elif item.role == "shared":
            front += 1
            back += 1
        elif item.role == "room":
            rooms += 1
    return front, back, rooms


def _live_occupants(rows: Sequence[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("is_terminated") is not False:
            continue
        if not partner_members.move_out_is_open(row.get("move_out_date"), now):
            continue
        move_in = lockout_reply.parse_dt(row.get("move_in_date")) if isinstance(row.get("move_in_date"), str) else None
        if move_in is not None and move_in.date() > now.astimezone(timezone.utc).date():
            continue
        found.append(row)
    return found


def build_house_report(
    slug: str,
    *,
    now: datetime,
    locks: Sequence[Dict[str, Any]],
    reachable: bool,
    passcodes_for: Optional[Callable[[str], CurrentCode]] = None,
    digest: Optional[Dict[str, Any]] = None,
    digest_unavailable: bool = False,
    directory: Any = None,
    property_id: str = "",
    sifely_down: bool = False,
) -> HouseReport:
    inventory = inventory_locks(locks)
    mine = [item for item in inventory.mapped if item.slug == slug]
    front, back, rooms = _count_roles(mine, slug)
    house_locks = [lock for lock in locks if match_house_slug(_lock_label(lock)) == slug]
    house_inventory = inventory_locks(house_locks)
    row = HouseReport(
        slug=slug,
        reachable=reachable and not sifely_down,
        front=front,
        back=back,
        room_locks=rooms,
        unmapped=house_inventory.unmapped_or_ambiguous,
        digest_unavailable=digest_unavailable,
    )
    member_rows: List[Dict[str, Any]] = []
    if directory is not None and property_id:
        try:
            member_rows = list(directory.members_for(property_id) or [])
        except Exception:
            member_rows = []
    occupants = _live_occupants(member_rows, now)
    row.occupants = len({str(item.get("room_number")) for item in occupants})
    mapped_rooms = {item.room for item in mine if item.role == "room"}
    occupied_rooms = {str(item.get("room_number")) for item in occupants}
    row.occupied_mapped = len(mapped_rooms & occupied_rooms)
    if not digest_unavailable and digest is not None and passcodes_for is not None:
        for item in mine:
            field_name = codes_field_for(slug, "front" if item.role == "shared" else item.role, item.room)
            if not field_name:
                continue
            if sifely_down:
                continue
            try:
                selected = passcodes_for(item.lock_id)
            except SifelyUnavailable:
                row.reachable = False
                continue
            if selected.status != "ok":
                continue
            existing = _digest_value(digest, field_name)
            if not existing:
                row.missing += 1
                row.would_update += 1
            elif existing == selected.code:
                row.match += 1
            else:
                row.mismatch += 1
                row.would_update += 1
    room_numbers = set(mapped_rooms) | {str(item.get("room_number")) for item in member_rows if item.get("room_number") not in (None, "")}
    for room in sorted(room_numbers, key=lambda value: int(value) if str(value).isdigit() else 0):
        if directory is None or not property_id:
            continue
        verdict = assert_room_action_allowed(
            slug,
            room,
            "rotate",
            directory=directory,
            now=now,
            property_id=property_id,
        )
        if isinstance(verdict, Allowed):
            row.allowed += 1
        else:
            row.refused += 1
    return row


def _lock_label(lock: Dict[str, Any]) -> str:
    return f"{lock.get('lockAlias') or ''} {lock.get('lockName') or ''}"


class _MemoryDirectory:
    def __init__(
        self,
        rows: Dict[str, Sequence[Dict[str, Any]]] | Sequence[Dict[str, Any]],
        refs: Sequence[Dict[str, str]],
        as_of: Any = None,
    ) -> None:
        if isinstance(rows, dict):
            self._by_property = {key: list(value) for key, value in rows.items()}
        else:
            self._by_property = {"": list(rows)}
        self._refs = list(refs)
        self.as_of = as_of

    def members_for(self, property_id: str) -> List[Dict[str, Any]]:
        if property_id in self._by_property:
            return list(self._by_property[property_id])
        if "" in self._by_property:
            return list(self._by_property[""])
        return []

    def property_index(self) -> List[Dict[str, str]]:
        return list(self._refs)


def offline_fixture(now: datetime) -> tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], Dict[str, Dict[str, Any]], _MemoryDirectory, Dict[str, str]]:
    """In-memory report fixture. Codes are labeled fakes."""
    locks = [
        {"lockId": "L-front", "lockAlias": "Broken Crest front door", "lockName": ""},
        {"lockId": "L-room", "lockAlias": "Broken Crest room 1", "lockName": ""},
        {"lockId": "L-amb", "lockAlias": "Broken Crest room 2", "lockName": ""},
        {"lockId": "L-vague", "lockAlias": "Broken Crest keypad", "lockName": ""},
        {"lockId": "L-back", "lockAlias": "Spanish Moss back door", "lockName": ""},
    ]
    permanent = {"status": 1, "keyboardPwdType": 2}
    passcodes = {
        "L-front": [{**permanent, "keyboardPwd": FIXTURE_FRONT}],
        "L-room": [{**permanent, "keyboardPwd": FIXTURE_ROOM}],
        "L-amb": [
            {**permanent, "keyboardPwd": FIXTURE_FRONT, "keyboardPwdId": "a"},
            {**permanent, "keyboardPwd": FIXTURE_ROOM, "keyboardPwdId": "b"},
        ],
        "L-back": [{**permanent, "keyboardPwd": FIXTURE_BACK}],
    }
    digests = {
        "broken_crest_1025": {"front_door": FIXTURE_FRONT, "r1": FIXTURE_DIGEST_ROOM},
        "spanish_moss": {"back_door": FIXTURE_BACK},
    }
    crest_rows = [
        {
            "occupancy_id": "occ-live",
            "room_number": "1",
            "move_out_date": None,
            "move_in_date": None,
            "is_terminated": False,
        },
        {
            "occupancy_id": "occ-ended",
            "room_number": "4",
            "move_out_date": None,
            "is_terminated": True,
        },
    ]
    directory = _MemoryDirectory(
        {"bc": crest_rows, "sm": []},
        [
            {"id": "bc", "street": "Broken Crest"},
            {"id": "sm", "street": "Spanish Moss"},
        ],
    )
    props = {"broken_crest_1025": "bc", "spanish_moss": "sm"}
    del now
    return locks, passcodes, digests, directory, props


def offline_house_reports(now: datetime, house: str = "") -> List[HouseReport]:
    locks, passcodes, digests, directory, props = offline_fixture(now)

    def reader(lock_id: str) -> CurrentCode:
        from padsplit_scraper.sifely_client import select_current_code

        return select_current_code(passcodes.get(lock_id) or [], now)

    slugs = [house] if house else ["broken_crest_1025", "spanish_moss"]
    reports = []
    for slug in slugs:
        if slug not in HOUSE_LOCK_PROFILES:
            continue
        reports.append(
            build_house_report(
                slug,
                now=now,
                locks=locks,
                reachable=True,
                passcodes_for=reader,
                digest=digests.get(slug) or {},
                directory=directory,
                property_id=props.get(slug, ""),
            )
        )
    return reports


def render_reports(rows: Sequence[HouseReport]) -> str:
    return "\n".join(format_house_report(row) for row in rows)


def run_house_report(
    *,
    house: str = "",
    live: bool = False,
    now: Optional[datetime] = None,
    environ: Optional[os._Environ[str]] = None,
    client: Any = None,
    directory: Any = None,
    digest_for: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> tuple[str, int]:
    """Print counts. Exit 0 unless the house slug is unknown."""
    current = now or datetime.now(timezone.utc)
    slug = (house or "").strip()
    if slug and slug not in HOUSE_LOCK_PROFILES:
        return f"unknown house: {slug}", 2
    if not live:
        rows = offline_house_reports(current, slug)
        if slug and not rows:
            return f"unknown house: {slug}", 2
        return render_reports(rows), 0
    env = environ if environ is not None else os.environ
    reader = client
    reachable = True
    locks: List[Dict[str, Any]] = []
    if reader is None:
        key = (env.get("SIFELY_API_KEY") or "").strip()
        if not key:
            reachable = False
        else:
            from padsplit_scraper.sifely_client import SifelyClient

            reader = SifelyClient(key, environ=env)
    if reader is not None and reachable:
        try:
            locks = list(reader.list_locks())
        except SifelyUnavailable:
            reachable = False
            locks = []

    def passcodes_for(lock_id: str) -> CurrentCode:
        if reader is None:
            raise SifelyUnavailable("no client")
        return reader.current_code(lock_id, current)

    digest_unavailable = False
    if digest_for is None:
        digest_unavailable = _firestore_unavailable(env)

        def digest_for(slug_name: str) -> Dict[str, Any]:
            if digest_unavailable:
                return {}
            return lockout_reply.fetch_property_codes(slug_name)

    slugs = [slug] if slug else list(HOUSE_LOCK_PROFILES)
    blocks = []
    for item in slugs:
        prop = ""
        if directory is not None:
            from padsplit_scraper.room_interlock import _property_id

            prop = _property_id(directory, item, "")
        digest = {} if digest_unavailable else (digest_for(item) or {})
        blocks.append(
            build_house_report(
                item,
                now=current,
                locks=locks,
                reachable=reachable,
                passcodes_for=passcodes_for if reachable else None,
                digest=digest,
                digest_unavailable=digest_unavailable,
                directory=directory,
                property_id=prop,
                sifely_down=not reachable,
            )
        )
    return render_reports(blocks), 0


def _firestore_unavailable(env: os._Environ[str]) -> bool:
    if (env.get("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip():
        return False
    if (env.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip():
        return False
    return True


def run_once(
    *,
    now: Optional[datetime] = None,
    threads: Optional[Sequence[Dict[str, Any]]] = None,
    directory: Any = None,
    environ: Optional[os._Environ[str]] = None,
    send_fn: Optional[Callable[[str, str], Any]] = None,
    post_discord: Optional[Callable[[str], Any]] = None,
    state_path: Any = None,
    notify_path: Any = None,
    inventory: Optional[Inventory] = None,
    passcodes_for: Optional[Callable[[str], CurrentCode]] = None,
    digest_for: Optional[Callable[[str], Dict[str, Any]]] = None,
    sync_db: Any = None,
    sifely_down: bool = False,
    jev: Optional[code_request.JevClassifier] = None,
) -> Dict[str, Any]:
    """Lightweight pass: message decisions plus the notice drain."""
    env = environ if environ is not None else os.environ
    current = now or datetime.now(timezone.utc)
    live = code_gates.action_live("code_reply", DRY_RUN_ENV, env)
    dry = not live
    path = state_path if state_path is not None else runtime.state_dir(env) / "code_reply_state.json"
    state = load_reply_state(path)
    rows = process_code_requests(
        list(threads or []),
        now=current,
        directory=directory,
        state=state,
        inventory=inventory,
        passcodes_for=passcodes_for,
        digest_for=digest_for,
        sifely_down=sifely_down,
        send_fn=send_fn,
        post_discord=post_discord,
        sync_db=sync_db,
        sync_enabled=runtime.send_enabled("codes_digest_sync", env),
        send_enabled=live,
        dry_run=dry,
        jev=jev,
        environ=env,
        notify_state=notify_path,
    )
    if live:
        save_reply_state(state, path)
    if inventory is not None and passcodes_for is not None:
        _observe_inventory_changes(
            inventory,
            passcodes_for,
            threads=list(threads or []),
            directory=directory,
            now=current,
            notify_state=notify_path,
            env=env,
        )
    drained = code_change_notify.drain(
        now=current,
        state_path=notify_path,
        send_fn=send_fn,
        post_discord=post_discord,
        threads=list(threads or []),
        directory=directory,
        environ=env,
    )
    return {"action": "ok", "results": rows, "notices": drained}


def _run_live_once() -> int:
    """One PadSplit session: messages, members, optional Sifely sweep, then drain."""
    from padsplit_scraper.scraper import create_session, fetch_messages, load_credentials, login
    from padsplit_scraper.sifely_client import SifelyClient

    env = os.environ
    current = datetime.now(timezone.utc)
    creds = load_credentials()
    notify_live = code_gates.action_live("code_change_notify", code_change_notify.DRY_RUN_ENV, env)
    tabs = new_booking.load_leftover_compose_tabs()
    with create_session() as session:
        login(session, creds["email"], creds["password"], force=False)
        threads = list(fetch_messages(session, creds))
        directory = partner_members.MemberDirectory(session, creds)

        def send_fn(chat_id: str, body: str) -> Any:
            return new_booking.send_host_message(
                session,
                creds,
                chat_id,
                body,
                leftover_compose_tabs=tabs,
            )

        inventory = None
        passcodes_for = None
        if notify_live:
            key = (env.get("SIFELY_API_KEY") or "").strip()
            if key:
                client = SifelyClient(key, environ=env)
                try:
                    inventory = inventory_locks(client.list_locks())
                except SifelyUnavailable:
                    _log("Sifely lock list unavailable")
                    inventory = None

                def passcodes_for(lock_id: str, reader: Any = client) -> CurrentCode:
                    return reader.current_code(lock_id, current)

        sync_db = None
        digest_for = None
        if (env.get("FIREBASE_SERVICE_ACCOUNT_JSON") or env.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip():
            digest_for = lockout_reply.fetch_property_codes
            if runtime.send_enabled("codes_digest_sync", env):
                from padsplit_scraper.persist import _firestore_client_or_none

                sync_db = _firestore_client_or_none()
        run_once(
            now=current,
            threads=threads,
            directory=directory,
            environ=env,
            send_fn=send_fn,
            post_discord=lockout_reply.post_automations_discord,
            inventory=inventory,
            passcodes_for=passcodes_for,
            digest_for=digest_for,
            sync_db=sync_db,
            jev=code_request.JevClassifier(environ=env),
        )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sifely-backed code replies and the house report")
    parser.add_argument("--once", action="store_true", help="Read the message list once and drain notices")
    parser.add_argument("--house-report", action="store_true", help="Print per-house counts")
    parser.add_argument("--house", default="", help="Limit the report to one house slug")
    parser.add_argument("--live", action="store_true", help="Read-only Sifely, members, and Firestore")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.once and args.house_report:
        sys.stderr.write("choose one of --once or --house-report\n")
        return 2
    if args.house_report:
        text, code = run_house_report(house=args.house, live=args.live)
        sys.stdout.write(text + ("\n" if text else ""))
        return code
    if not args.once:
        parser.print_help(sys.stderr)
        return 2
    live = code_gates.action_live("code_reply", DRY_RUN_ENV)
    notify_live = code_gates.action_live("code_change_notify", code_change_notify.DRY_RUN_ENV)
    if not live and not notify_live and not code_gates.dry_run_flag(DRY_RUN_ENV):
        _log("disabled (CODE_REPLY_ENABLE or CI)")
        code_change_notify.drain()
        return 0
    if not live and not notify_live:
        # Dry-run stays local. Tests call run_once with threads.
        run_once(threads=[])
        return 0
    try:
        return _run_live_once()
    except Exception:
        _log("message list unavailable")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
