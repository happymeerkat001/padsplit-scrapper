"""Durable PadSplit notices after a code change.

A change is not done until each affected current occupant has a sent
journal row. The journal stores a salted HMAC, never the code. Shared
doors notify every current occupant of the house. A vacant room notifies
nobody. Retries: three attempts over about ten minutes, then a digit-free
Discord flag.

Gate: CODE_CHANGE_NOTIFY_ENABLE (default off) and CODE_CHANGE_NOTIFY_DRY_RUN
(default on). CI and collection-only never send.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from padsplit_scraper import code_gates
    from padsplit_scraper import lockout_reply
    from padsplit_scraper import partner_members
    from padsplit_scraper import runtime
    from padsplit_scraper.room_interlock import digit_free_label, house_label, room_words
    from padsplit_scraper.sifely_client import match_house_slug
except ModuleNotFoundError:  # python padsplit_scraper/code_change_notify.py
    import code_gates  # type: ignore
    import lockout_reply  # type: ignore
    import partner_members  # type: ignore
    import runtime  # type: ignore
    from room_interlock import digit_free_label, house_label, room_words  # type: ignore
    from sifely_client import match_house_slug  # type: ignore


DRY_RUN_ENV = "CODE_CHANGE_NOTIFY_DRY_RUN"
_RETRY_OFFSETS = (
    timedelta(0),
    timedelta(minutes=4),
    timedelta(minutes=10),
)
_MAX_ATTEMPTS = 3
_MEMORY: Dict[str, str] = {}


def _log(message: str) -> None:
    sys.stderr.write(f"[code-change-notify] {message}\n")


def _stamp(when: datetime) -> str:
    current = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: Any) -> Optional[datetime]:
    return lockout_reply.parse_dt(value) if isinstance(value, str) else None


def state_path(path: Optional[Path] = None, environ: Optional[os._Environ[str]] = None) -> Path:
    if path is not None:
        return path
    return runtime.state_dir(environ) / "code_change_notify.json"


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"jobs": {}, "last_known": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"jobs": {}, "last_known": {}}
    if not isinstance(payload, dict):
        return {"jobs": {}, "last_known": {}}
    payload.setdefault("jobs", {})
    payload.setdefault("last_known", {})
    return payload


def save_state(state: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")


def code_hmac(code: str, salt: str) -> str:
    """Salted HMAC-SHA256. Never a bare hash of a short code."""
    return hmac.new(salt.encode("utf-8"), (code or "").encode("utf-8"), hashlib.sha256).hexdigest()


def salt_for(state: Dict[str, Any], environ: Optional[os._Environ[str]] = None) -> str:
    env = environ if environ is not None else os.environ
    configured = (env.get("CODES_HASH_SALT") or "").strip()
    if configured:
        return configured
    existing = str(state.get("hash_salt") or "").strip()
    if existing:
        return existing
    generated = secrets.token_hex(16)
    state["hash_salt"] = generated
    return generated


def notice_body(role: str, label: str, code: str) -> str:
    """One code only. No wifi and no second code."""
    if role == "room":
        what = "room"
    elif role == "back":
        what = "back door"
    else:
        what = "front door"
    house = digit_free_label(label)
    return f"Your {what} code at {house} has changed. New code: {code}."


def failure_discord(slug: str, role: str, room: str = "") -> str:
    label = house_label(slug)
    if role == "room":
        where = f"room {room_words(room)}"
    elif role == "back":
        where = "back door"
    else:
        where = "front door"
    text = f"{label} {where}: code notice failed"
    if any(ch.isdigit() for ch in text):
        raise RuntimeError("Refusing Discord outbound: message contains digits")
    return text


def _future(row_or_thread_move_in: Any, now: datetime) -> bool:
    parsed = lockout_reply.parse_dt(row_or_thread_move_in) if isinstance(row_or_thread_move_in, str) else None
    if parsed is None:
        return False
    return parsed.date() > now.astimezone(timezone.utc).date()


def _thread_slug(thread: Dict[str, Any]) -> str:
    return match_house_slug(lockout_reply.thread_street(thread)) or ""


def resolve_recipients(
    slug: str,
    role: str,
    room: str,
    threads: Sequence[Dict[str, Any]],
    directory: Any,
    now: datetime,
    *,
    property_id: str = "",
) -> tuple[str, List[str]]:
    """Return (vacant|ok|unresolved|ambiguous, chat ids). No names."""
    if directory is None:
        return "unresolved", []
    prop = str(property_id or "").strip()
    if not prop:
        try:
            refs = list(directory.property_index() or [])
        except Exception:
            return "unresolved", []
        from padsplit_scraper.room_interlock import _property_id

        prop = _property_id(directory, slug, "")
        if not prop and refs:
            return "unresolved", []
    try:
        rows = list(directory.members_for(prop) or [])
    except Exception:
        return "unresolved", []

    chats: List[str] = []
    for thread in threads:
        if _thread_slug(thread) != slug:
            continue
        chat_id = str(thread.get("id") or "").strip()
        if not chat_id:
            continue
        occupancy = thread.get("occupancy") if isinstance(thread.get("occupancy"), dict) else {}
        if _future(occupancy.get("moveInDate") or occupancy.get("move_in_date"), now):
            continue
        thread_room = lockout_reply.thread_room(thread)
        if role == "room" and not partner_members.rooms_match(thread_room, room):
            continue
        try:
            match = partner_members.select_member(
                rows,
                occupancy_id=lockout_reply._thread_occupancy_id(thread),
                room_number=thread_room,
                now=now,
            )
        except Exception:
            return "unresolved", []
        if not match.ok:
            continue
        chats.append(chat_id)
    unique = list(dict.fromkeys(chats))
    if role == "room":
        if len(unique) == 0:
            open_rows = [
                row
                for row in rows
                if isinstance(row, dict)
                and partner_members.rooms_match(row.get("room_number"), room)
                and row.get("is_terminated") is False
                and partner_members.move_out_is_open(row.get("move_out_date"), now)
                and not _future(row.get("move_in_date"), now)
            ]
            if open_rows:
                return "unresolved", []
            return "vacant", []
        if len(unique) > 1:
            return "ambiguous", []
        return "ok", unique
    if not unique:
        return "vacant", []
    return "ok", unique


def _job_key(slug: str, role: str, room: str, digest: str) -> str:
    room_bit = room or "-"
    return f"{slug}|{role}|{room_bit}|{digest[:16]}"


def _enabled_flag(environ: Optional[os._Environ[str]]) -> bool:
    env = environ if environ is not None else os.environ
    for name in runtime.ACTION_FLAGS.get("code_change_notify", ()):
        if runtime.flag_value(env.get(name)) is True:
            return True
    return False


def observe_change(
    *,
    slug: str,
    role: str,
    room: str = "",
    code: str,
    lock_id: str = "",
    threads: Optional[Sequence[Dict[str, Any]]] = None,
    directory: Any = None,
    now: Optional[datetime] = None,
    property_id: str = "",
    state_path: Optional[Path] = None,
    environ: Optional[os._Environ[str]] = None,
    source: str = "",
    recipients: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Record a job. The code stays in process memory, not in the journal."""
    env = environ if environ is not None else os.environ
    if not code or not slug:
        return {"action": "skipped", "reason": "missing code or house"}
    if not code_gates.house_allowed(slug, env):
        _log("skip notice; house is outside CODE_AUTOMATION_TEST_HOUSES")
        return {"action": "skipped_allowlist"}
    if not _enabled_flag(env):
        return {"action": "disabled"}
    current = now or datetime.now(timezone.utc)
    path = state_path if state_path is not None else globals()["state_path"](None, env)
    state = load_state(path)
    salt = salt_for(state, env)
    digest = code_hmac(code, salt)
    key = _job_key(slug, role, str(room or ""), digest)
    _MEMORY[key] = code
    resolution = "ok"
    chats: List[str]
    if recipients is not None:
        chats = [str(item) for item in recipients if str(item).strip()]
        resolution = "vacant" if not chats else "ok"
    else:
        resolution, chats = resolve_recipients(
            slug,
            role,
            str(room or ""),
            list(threads or []),
            directory,
            current,
            property_id=property_id,
        )
    jobs = state.setdefault("jobs", {})
    existing = jobs.get(key) if isinstance(jobs.get(key), dict) else None
    if existing and existing.get("status") == "done":
        save_state(state, path)
        return {"action": "already", "job": key}
    recipient_rows = {}
    if existing:
        prior = existing.get("recipients") if isinstance(existing.get("recipients"), dict) else {}
        recipient_rows.update(prior)
    for chat_id in chats:
        if chat_id not in recipient_rows:
            recipient_rows[chat_id] = {
                "status": "pending",
                "attempts": 0,
                "next_attempt_at": _stamp(current),
            }
    status = "done" if resolution == "vacant" else "pending"
    if resolution in {"unresolved", "ambiguous"}:
        status = "pending"
    jobs[key] = {
        "slug": slug,
        "role": role,
        "room": str(room or ""),
        "lock_id": str(lock_id or ""),
        "code_hmac": digest,
        "created_at": (existing or {}).get("created_at") or _stamp(current),
        "source": source or (existing or {}).get("source") or "",
        "status": status,
        "resolution": resolution,
        "flagged": bool((existing or {}).get("flagged")),
        "recipients": recipient_rows,
    }
    if str(lock_id or ""):
        state.setdefault("last_known", {})[str(lock_id)] = digest
    save_state(state, path)
    _log(f"queued role={role} house={house_label(slug)} recipients={len(chats)} resolution={resolution}")
    return {"action": "queued", "job": key, "recipients": len(chats), "resolution": resolution}


def note_if_changed(
    *,
    slug: str,
    role: str,
    room: str,
    lock_id: str,
    code: str,
    threads: Optional[Sequence[Dict[str, Any]]] = None,
    directory: Any = None,
    now: Optional[datetime] = None,
    property_id: str = "",
    state_path: Optional[Path] = None,
    environ: Optional[os._Environ[str]] = None,
    source: str = "sifely_observed",
) -> Dict[str, Any]:
    """Enqueue when the in-memory code differs from the last stored HMAC."""
    env = environ if environ is not None else os.environ
    if not _enabled_flag(env) or not code or not lock_id:
        return {"action": "skipped"}
    path = state_path if state_path is not None else globals()["state_path"](None, env)
    state = load_state(path)
    salt = salt_for(state, env)
    digest = code_hmac(code, salt)
    known = state.setdefault("last_known", {})
    if str(lock_id) not in known:
        known[str(lock_id)] = digest
        save_state(state, path)
        return {"action": "baseline"}
    if known.get(str(lock_id)) == digest:
        return {"action": "unchanged"}
    return observe_change(
        slug=slug,
        role=role,
        room=room,
        code=code,
        lock_id=lock_id,
        threads=threads,
        directory=directory,
        now=now,
        property_id=property_id,
        state_path=path,
        environ=env,
        source=source,
    )


def _lookup_code(job_key: str, job: Dict[str, Any], code_lookup: Optional[Callable[[Dict[str, Any]], str]]) -> str:
    if code_lookup is not None:
        try:
            found = code_lookup(job) or ""
        except Exception:
            found = ""
        if found:
            return found
    return _MEMORY.get(job_key, "")


def _due(row: Dict[str, Any], now: datetime) -> bool:
    nxt = _parse(str(row.get("next_attempt_at") or ""))
    if nxt is None:
        return True
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return current >= nxt


def _schedule_next(created: datetime, attempts: int) -> datetime:
    index = min(max(attempts, 0), len(_RETRY_OFFSETS) - 1)
    return created + _RETRY_OFFSETS[index]


def drain(
    *,
    now: Optional[datetime] = None,
    state_path: Optional[Path] = None,
    send_fn: Optional[Callable[[str, str], Any]] = None,
    code_lookup: Optional[Callable[[Dict[str, Any]], str]] = None,
    post_discord: Optional[Callable[[str], Any]] = None,
    threads: Optional[Sequence[Dict[str, Any]]] = None,
    directory: Any = None,
    environ: Optional[os._Environ[str]] = None,
) -> Dict[str, Any]:
    """Send pending notices. Dry-run logs a count and does not mark them sent."""
    env = environ if environ is not None else os.environ
    current = now or datetime.now(timezone.utc)
    path = state_path if state_path is not None else globals()["state_path"](None, env)
    if not _enabled_flag(env):
        return {"action": "disabled", "sent": 0, "failed": 0, "would_send": 0}
    live = code_gates.action_live("code_change_notify", DRY_RUN_ENV, env)
    state = load_state(path)
    salt = salt_for(state, env)
    sent = 0
    failed = 0
    would_send = 0
    flags: List[str] = []
    jobs = state.setdefault("jobs", {})
    for key, job in list(jobs.items()):
        if not isinstance(job, dict) or job.get("status") in {"done", "failed"}:
            continue
        if not code_gates.house_allowed(str(job.get("slug") or ""), env):
            continue
        resolution = str(job.get("resolution") or "")
        if resolution in {"unresolved", "ambiguous"} and threads is not None:
            resolution, chats = resolve_recipients(
                str(job.get("slug") or ""),
                str(job.get("role") or ""),
                str(job.get("room") or ""),
                threads,
                directory,
                current,
            )
            job["resolution"] = resolution
            recipients = job.setdefault("recipients", {})
            for chat_id in chats:
                recipients.setdefault(
                    chat_id,
                    {"status": "pending", "attempts": 0, "next_attempt_at": _stamp(current)},
                )
        if job.get("resolution") == "vacant":
            job["status"] = "done"
            continue
        recipients = job.get("recipients") if isinstance(job.get("recipients"), dict) else {}
        created = _parse(str(job.get("created_at") or "")) or current
        pending = False
        for chat_id, row in recipients.items():
            if not isinstance(row, dict):
                continue
            if row.get("status") == "sent":
                continue
            if row.get("status") == "failed":
                continue
            attempts = int(row.get("attempts") or 0)
            if attempts >= _MAX_ATTEMPTS:
                row["status"] = "failed"
                failed += 1
                continue
            if not _due(row, current):
                pending = True
                continue
            code = _lookup_code(key, job, code_lookup)
            if not code or code_hmac(code, salt) != str(job.get("code_hmac") or ""):
                attempts += 1
                row["attempts"] = attempts
                if attempts >= _MAX_ATTEMPTS:
                    row["status"] = "failed"
                    failed += 1
                else:
                    row["next_attempt_at"] = _stamp(_schedule_next(created, attempts))
                    pending = True
                continue
            body = notice_body(str(job.get("role") or ""), house_label(str(job.get("slug") or "")), code)
            if not live:
                would_send += 1
                pending = True
                continue
            try:
                if send_fn is None:
                    raise RuntimeError("send is not wired")
                send_fn(chat_id, body)
            except Exception:
                attempts += 1
                row["attempts"] = attempts
                if attempts >= _MAX_ATTEMPTS:
                    row["status"] = "failed"
                    failed += 1
                    _log(f"notice failed house={house_label(str(job.get('slug') or ''))} role={job.get('role')}")
                else:
                    row["next_attempt_at"] = _stamp(_schedule_next(created, attempts))
                    pending = True
                continue
            row["status"] = "sent"
            row["sent_at"] = _stamp(current)
            sent += 1
            _log(f"sent role={job.get('role')} house={house_label(str(job.get('slug') or ''))}")
        statuses = [row.get("status") for row in recipients.values() if isinstance(row, dict)]
        if statuses and all(item == "sent" for item in statuses):
            job["status"] = "done"
        elif statuses and all(item == "failed" for item in statuses) and not pending:
            job["status"] = "failed"
            if not job.get("flagged"):
                text = failure_discord(str(job.get("slug") or ""), str(job.get("role") or ""), str(job.get("room") or ""))
                flags.append(text)
                job["flagged"] = True
                if post_discord is not None and live:
                    post_discord(text)
                elif post_discord is not None and not live:
                    post_discord(text)
        elif job.get("resolution") == "ambiguous" and not pending and not statuses:
            job["status"] = "failed"
            if not job.get("flagged"):
                text = failure_discord(str(job.get("slug") or ""), str(job.get("role") or ""), str(job.get("room") or ""))
                flags.append(text)
                job["flagged"] = True
                if post_discord is not None:
                    post_discord(text)
    save_state(state, path)
    action = "sent" if live else "dry_run"
    if would_send:
        _log(f"would notify {would_send}")
    return {
        "action": action,
        "sent": sent,
        "failed": failed,
        "would_send": would_send,
        "flags": flags,
    }
