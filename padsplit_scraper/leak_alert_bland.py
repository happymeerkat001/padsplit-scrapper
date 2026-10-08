#!/usr/bin/env python3
"""Bland leak-alert sender, poller, and dry-run preview.

Live HTTP is off unless LEAK_ALERT_ENABLE is on and LEAK_ALERT_DRY_RUN is
explicitly off. CI and collection-only never call Bland or Quo. Phone
numbers stay in memory for the request body and are not written to state,
logs, or Discord.

The Quo group-result post lives here, behind ``post_group_result_line``,
so a parallel Quo text sender can land without renaming leak_alert helpers.

Preview (no HTTP):

    python -m padsplit_scraper.leak_alert_bland --preview
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

try:
    from padsplit_scraper import leak_alert
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python -m from a flat path
    import leak_alert  # type: ignore
    import runtime  # type: ignore


ROOT_DIR = Path(__file__).resolve().parent.parent
PREVIEW_PATH = ROOT_DIR / "logs" / "leak_alert_bland_preview.txt"
CALL_LOG_PATH = ROOT_DIR / "logs" / "leak_alert_calls.jsonl"
RESULT_LOG_PATH = ROOT_DIR / "logs" / "leak_alert_result.jsonl"
WEBHOOK_PLACEHOLDER = "https://us-central1-padsplit-scrapper.cloudfunctions.net/leak_alert_bland"
PREVIEW_DON = "+15555550101"
PREVIEW_TOM = "+15555550102"
PREVIEW_INCIDENT = "preview-incident:m1"
PREVIEW_HOUSE = "100 Example Lane"
PREVIEW_ROOM = "2"
PREVIEW_CATEGORY = "pipe"
FAILURE_RETRY = timedelta(hours=24)
QUO_MESSAGES_URL = "https://api.quo.com/v1/messages"
QUO_API_VERSION = "2026-03-30"
CALL_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,80}")

_IMPL_PATH = ROOT_DIR / "functions" / "leak_alert_bland.py"
_spec = importlib.util.spec_from_file_location("leak_alert_bland_impl", _IMPL_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"missing Bland helper at {_IMPL_PATH}")
_impl = importlib.util.module_from_spec(_spec)
sys.modules["leak_alert_bland_impl"] = _impl
_spec.loader.exec_module(_impl)

verify_signature = _impl.verify_signature
bearer_matches = _impl.bearer_matches
handle_webhook = _impl.handle_webhook
handle_confirm = _impl.handle_confirm
build_call_body = _impl.build_call_body
build_record = _impl.build_record
format_result_line = _impl.format_result_line
classify_outcome = _impl.classify_outcome
MemoryCallStore = _impl.MemoryCallStore
FirestoreCallStore = _impl.FirestoreCallStore
BLAND_CALLS_URL = _impl.BLAND_CALLS_URL
ANALYSIS_SCHEMA = _impl.ANALYSIS_SCHEMA
STALE_AFTER = _impl.STALE_AFTER


def dry_run_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Default on. Live HTTP requires an explicit off value."""
    env = environ if environ is not None else os.environ
    parsed = runtime.flag_value(env.get("LEAK_ALERT_DRY_RUN"))
    if parsed is None:
        return True
    return parsed


def calls_live(environ: Optional[Mapping[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    if not runtime.send_enabled("leak_alert", env):  # type: ignore[arg-type]
        return False
    return not dry_run_enabled(env)


def result_post_live(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Quo result line. Own flag, still blocked by dry-run, CI, and leak-alert enable."""
    env = environ if environ is not None else os.environ
    if not calls_live(env):
        return False
    return runtime.flag_value(env.get("LEAK_ALERT_RESULT_POST_ENABLE")) is True


def mask_e164(number: str) -> str:
    """Keep the leading plus and the last two digits. Preview output only."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) < 2:
        return ""
    return "+" + ("x" * (len(digits) - 2)) + digits[-2:]


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    encoded = json.dumps(dict(row), sort_keys=True)
    leak_alert.assert_plan_has_no_numbers(encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded + "\n")


def _voice_key(incident: str, role: str) -> str:
    return f"voice:{incident}:{role}"


def _parse_voice_key(key: str) -> Optional[tuple]:
    if not key.startswith("voice:"):
        return None
    for role in ("don", "tom"):
        suffix = f":{role}"
        if key.endswith(suffix):
            incident = key[len("voice:"): -len(suffix)]
            if incident:
                return incident, role
    return None


def _parse_time(value: Any) -> Optional[datetime]:
    return _impl.parse_time(value)


def default_bland_post(
    body: Mapping[str, Any],
    api_key: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    opener: Optional[Callable[..., Any]] = None,
) -> tuple:
    """POST /v1/calls. Raises before any socket when the live gate is closed."""
    env = environ if environ is not None else os.environ
    if not calls_live(env):
        raise RuntimeError("leak alert Bland calls are not live")
    data = json.dumps(dict(body)).encode("utf-8")
    request = urllib.request.Request(
        BLAND_CALLS_URL,
        data=data,
        headers={
            "Authorization": _bearer(api_key),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    status, payload = _read_json(request, opener)
    return status, payload


def default_bland_get(
    call_id: str,
    api_key: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    opener: Optional[Callable[..., Any]] = None,
) -> tuple:
    """GET /v1/calls/{id}. The id is restricted to a token so the URL cannot wander."""
    env = environ if environ is not None else os.environ
    if not calls_live(env):
        raise RuntimeError("leak alert Bland polls are not live")
    if not CALL_ID_RE.fullmatch(call_id or ""):
        return 0, {}
    request = urllib.request.Request(
        f"https://api.bland.ai/v1/calls/{call_id}",
        headers={"Authorization": _bearer(api_key)},
        method="GET",
    )
    return _read_json(request, opener)


def default_quo_post(
    payload: Mapping[str, Any],
    api_key: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    opener: Optional[Callable[..., Any]] = None,
) -> int:
    """POST one Quo group message. The payload is not logged."""
    env = environ if environ is not None else os.environ
    if not result_post_live(env):
        raise RuntimeError("leak alert result posts are not live")
    request = urllib.request.Request(
        QUO_MESSAGES_URL,
        data=json.dumps(dict(payload)).encode("utf-8"),
        headers={
            "Authorization": api_key,
            "Quo-Api-Version": QUO_API_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    status, _payload = _read_json(request, opener)
    return status


def _bearer(api_key: str) -> str:
    key = (api_key or "").strip()
    if key.lower().startswith("bearer "):
        return key
    return f"Bearer {key}"


def _read_json(request: urllib.request.Request, opener: Optional[Callable[..., Any]]) -> tuple:
    fetch = opener or urllib.request.urlopen
    try:
        with fetch(request, timeout=30) as response:
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


def _within_retry_block(prior: Mapping[str, Any], now: datetime) -> bool:
    if str(prior.get("status") or "") != "failed":
        return False
    failed_at = _parse_time(prior.get("failed_at"))
    if failed_at is None:
        return True
    return now.astimezone(timezone.utc) - failed_at < FAILURE_RETRY


def place_plan_calls(
    plan: Any,
    state: Dict[str, Any],
    *,
    now: datetime,
    environ: Mapping[str, str],
    poster: Optional[Callable[..., tuple]] = None,
    store: Any = None,
    call_log: Path = CALL_LOG_PATH,
) -> List[Dict[str, Any]]:
    """Place Don and Tom calls, or log a dry-run line. Never stores the phone."""
    notes: List[Dict[str, Any]] = []
    if not runtime.send_enabled("leak_alert", environ):  # type: ignore[arg-type]
        return notes
    alerts = state.setdefault("alerts", {})
    dry = dry_run_enabled(environ)
    api_key = str(environ.get("BLAND_API_KEY") or "").strip()
    webhook_url = str(environ.get("LEAK_ALERT_BLAND_WEBHOOK_URL") or "").strip()
    webhook_secret = str(environ.get("BLAND_WEBHOOK_SECRET") or "").strip()
    citation = str(environ.get("LEAK_ALERT_BLAND_CITATION_SCHEMA_ID") or "").strip()
    for entry in plan.entries:
        if entry.get("kind") != "voice":
            continue
        role = str(entry.get("role") or "")
        if role not in leak_alert.VOICE_ROLES:
            continue
        key = str(entry.get("key") or _voice_key(plan.incident, role))
        prior = alerts.get(key)
        if not isinstance(prior, dict):
            notes.append({"role": role, "status": "skipped", "reason": "not_planned"})
            continue
        if prior.get("status") == "placed" and prior.get("call_id"):
            notes.append({"role": role, "status": "already_placed", "call_id": prior.get("call_id")})
            continue
        if _within_retry_block(prior, now):
            notes.append({"role": role, "status": "skipped", "reason": "recent_failure"})
            continue
        phone = leak_alert.normalize_e164(str(environ.get(leak_alert.ROLE_ENV[role]) or ""))
        if not phone or not api_key:
            notes.append({"role": role, "status": "skipped", "reason": "not_configured"})
            continue
        if dry:
            if not prior.get("dry_logged_at"):
                _append_jsonl(call_log, {
                    "dry_run": True,
                    "kind": "bland_call",
                    "role": role,
                    "incident": plan.incident,
                    "would_place": True,
                })
                prior["dry_logged_at"] = _stamp(now)
            notes.append({"role": role, "status": "dry_run"})
            continue
        script = str(entry.get("script") or "")
        body = build_call_body(
            phone_number=phone,
            script=script,
            incident_id=plan.incident,
            role=role,
            webhook_url=webhook_url,
            webhook_secret=webhook_secret,
            citation_schema_id=citation,
        )
        send = poster or (lambda payload, key: default_bland_post(payload, key, environ=environ))
        try:
            status, payload = send(body, api_key)
        except Exception:
            status, payload = 0, {}
        call_id = str(payload.get("call_id") or "") if isinstance(payload, dict) else ""
        if status not in (200, 201) or not call_id:
            prior["status"] = "failed"
            prior["failed_at"] = _stamp(now)
            prior.pop("call_id", None)
            notes.append({"role": role, "status": "failed"})
            continue
        prior["status"] = "placed"
        prior["call_id"] = call_id
        prior["placed_at"] = _stamp(now)
        prior["role"] = role
        prior.pop("failed_at", None)
        if store is not None:
            try:
                store.put_pending(call_id, {
                    "call_id": call_id,
                    "incident_id": plan.incident,
                    "role": role,
                    "placed_at": prior["placed_at"],
                })
            except Exception:
                prior["pending_stored"] = False
        notes.append({"role": role, "status": "placed", "call_id": call_id})
    leak_alert.assert_plan_has_no_numbers(json.dumps(notes))
    leak_alert.assert_plan_has_no_numbers(json.dumps(state))
    return notes


def poll_open_calls(
    state: Dict[str, Any],
    *,
    now: datetime,
    environ: Mapping[str, str],
    getter: Optional[Callable[..., tuple]] = None,
    store: Any = None,
    skip_call_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """GET calls placed on an earlier run when no webhook record is stored."""
    notes: List[Dict[str, Any]] = []
    if not calls_live(environ):
        return notes
    alerts = state.get("alerts")
    if not isinstance(alerts, dict):
        return notes
    api_key = str(environ.get("BLAND_API_KEY") or "").strip()
    if not api_key:
        return notes
    skip = set(skip_call_ids or [])
    fetch = getter or (lambda call_id, key: default_bland_get(call_id, key, environ=environ))
    for key, prior in list(alerts.items()):
        parsed = _parse_voice_key(str(key))
        if parsed is None or not isinstance(prior, dict):
            continue
        incident, role = parsed
        call_id = str(prior.get("call_id") or "")
        if prior.get("status") != "placed" or not call_id or call_id in skip:
            continue
        if prior.get("result_recorded"):
            continue
        if store is not None:
            try:
                existing = store.get_record(incident, role)
            except Exception:
                existing = None
            if isinstance(existing, dict) and str(existing.get("call_id") or "") == call_id:
                prior["result_recorded"] = True
                prior["outcome"] = existing.get("outcome")
                notes.append({"role": role, "status": "webhook_record", "call_id": call_id})
                continue
        try:
            status, payload = fetch(call_id, api_key)
        except Exception:
            notes.append({"role": role, "status": "poll_failed", "call_id": call_id})
            continue
        if status != 200 or not isinstance(payload, dict):
            notes.append({"role": role, "status": "poll_failed", "call_id": call_id})
            continue
        payload = dict(payload)
        payload.setdefault("call_id", call_id)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        payload["metadata"] = {
            "incident_id": str(metadata.get("incident_id") or incident),
            "role": str(metadata.get("role") or role),
        }
        pending = None
        if store is not None:
            try:
                pending = store.get_pending(call_id)
            except Exception:
                pending = None
        record = build_record(
            payload,
            incident_id=incident,
            role=role,
            source="poll",
            now=now,
            pending=pending,
        )
        if record is None:
            notes.append({"role": role, "status": "in_progress", "call_id": call_id})
            continue
        line = format_result_line(role, record["outcome"], record["confirmed"], record["eta_minutes"])
        if store is not None:
            store.put_record(incident, role, record)
            store.enqueue(call_id, {
                "call_id": call_id,
                "incident_id": incident,
                "role": role,
                "outcome": record["outcome"],
                "line": line,
                "enqueued_at": _stamp(now),
                "posted": False,
            })
        else:
            state.setdefault("result_queue", {})[call_id] = {
                "call_id": call_id,
                "line": line,
                "role": role,
                "outcome": record["outcome"],
            }
        prior["result_recorded"] = True
        prior["outcome"] = record["outcome"]
        notes.append({"role": role, "status": "polled", "call_id": call_id, "line": line})
    leak_alert.assert_plan_has_no_numbers(json.dumps(notes))
    leak_alert.assert_plan_has_no_numbers(json.dumps(state))
    return notes


def post_group_result_line(
    line: str,
    *,
    environ: Mapping[str, str],
    poster: Optional[Callable[..., int]] = None,
    dry_run: bool = False,
    result_log: Path = RESULT_LOG_PATH,
) -> Dict[str, Any]:
    """One Quo group line. Dry-run writes the line and does not POST.

    ``poster`` is ``(payload, api_key) -> http status`` so another Quo module
    can own the transport later without changing leak_alert.py.
    """
    leak_alert.assert_plan_has_no_numbers(line)
    if dry_run or dry_run_enabled(environ):
        _append_jsonl(result_log, {"dry_run": True, "line": line, "kind": "quo_group_result"})
        return {"status": "would_send", "line": line}
    if not result_post_live(environ):
        return {"status": "disabled", "line": line}
    numbers = leak_alert.parse_group_e164s(str(environ.get(leak_alert.GROUP_ENV) or ""))
    from_raw = str(environ.get("QUO_FROM_NUMBER") or environ.get("FIELD_MMS_QUO_FROM") or "")
    from_number = leak_alert.normalize_e164(from_raw) if from_raw.strip() else ""
    if from_number:
        numbers = [number for number in numbers if number != from_number]
    api_key = str(environ.get("QUO_API_KEY") or "").strip()
    if not numbers or not from_number or not api_key or len(numbers) > leak_alert.TENANT_CAP:
        return {"status": "skipped", "line": line}
    payload = {"content": line, "from": from_number, "to": numbers}
    send = poster or (lambda payload, key: default_quo_post(payload, key, environ=environ))
    try:
        status = int(send(payload, api_key))
    except Exception:
        return {"status": "failed", "line": line}
    if status not in (200, 201, 202):
        return {"status": "failed", "line": line}
    return {"status": "posted", "line": line}


def drain_result_queue(
    state: Dict[str, Any],
    *,
    now: datetime,
    environ: Mapping[str, str],
    store: Any = None,
    quo_post: Optional[Callable[..., int]] = None,
    result_log: Path = RESULT_LOG_PATH,
) -> List[Dict[str, Any]]:
    """Post or dry-log each queued line once per call_id."""
    notes: List[Dict[str, Any]] = []
    if not runtime.send_enabled("leak_alert", environ):  # type: ignore[arg-type]
        return notes
    results = state.setdefault("results", {})
    rows: List[Dict[str, Any]] = []
    if store is not None:
        try:
            rows.extend(store.list_queue())
        except Exception:
            rows = []
    local_queue = state.get("result_queue")
    if isinstance(local_queue, dict):
        rows.extend(dict(row) for row in local_queue.values() if isinstance(row, dict))
    dry = dry_run_enabled(environ)
    flag_on = runtime.flag_value(environ.get("LEAK_ALERT_RESULT_POST_ENABLE")) is True
    for row in rows:
        call_id = str(row.get("call_id") or "")
        line = str(row.get("line") or "")
        if not call_id or not line:
            continue
        prior = results.get(call_id) if isinstance(results.get(call_id), dict) else {}
        if row.get("posted") or prior.get("posted"):
            continue
        if dry:
            if not flag_on or prior.get("logged"):
                continue
            outcome = post_group_result_line(line, environ=environ, dry_run=True, result_log=result_log)
            results[call_id] = {"logged": True, "line": line, "logged_at": _stamp(now)}
            notes.append({"call_id": call_id, "status": outcome["status"], "line": line})
            continue
        if not result_post_live(environ):
            continue
        outcome = post_group_result_line(
            line,
            environ=environ,
            poster=quo_post,
            dry_run=False,
            result_log=result_log,
        )
        if outcome["status"] != "posted":
            notes.append({"call_id": call_id, "status": outcome["status"], "line": line})
            continue
        results[call_id] = {"posted": True, "line": line, "posted_at": _stamp(now)}
        if store is not None:
            try:
                store.mark_queue_posted(call_id, results[call_id]["posted_at"])
            except Exception:
                pass
        notes.append({"call_id": call_id, "status": "posted", "line": line})
    leak_alert.assert_plan_has_no_numbers(json.dumps(notes))
    leak_alert.assert_plan_has_no_numbers(json.dumps(state))
    return notes


def advance_plans(
    plans: Sequence[Any],
    state: Dict[str, Any],
    *,
    now: datetime,
    environ: Optional[Mapping[str, str]] = None,
    poster: Optional[Callable[..., tuple]] = None,
    getter: Optional[Callable[..., tuple]] = None,
    quo_post: Optional[Callable[..., int]] = None,
    store: Any = None,
    call_log: Path = CALL_LOG_PATH,
    result_log: Path = RESULT_LOG_PATH,
) -> List[Dict[str, Any]]:
    """Poll earlier calls, drain result lines, then place this run's voice calls."""
    env: Mapping[str, str] = environ if environ is not None else os.environ
    if not runtime.send_enabled("leak_alert", env):  # type: ignore[arg-type]
        return []
    notes: List[Dict[str, Any]] = []
    active_store = store
    if active_store is None and calls_live(env):
        active_store = firestore_store_or_none()
    if calls_live(env):
        notes.extend(poll_open_calls(
            state,
            now=now,
            environ=env,
            getter=getter,
            store=active_store,
        ))
    notes.extend(drain_result_queue(
        state,
        now=now,
        environ=env,
        store=active_store,
        quo_post=quo_post,
        result_log=result_log,
    ))
    for plan in plans:
        notes.extend(place_plan_calls(
            plan,
            state,
            now=now,
            environ=env,
            poster=poster,
            store=active_store,
            call_log=call_log,
        ))
    leak_alert.assert_plan_has_no_numbers(json.dumps(notes))
    return notes


def firestore_store_or_none() -> Any:
    """Admin client when Mac credentials exist. Never required for dry-run."""
    try:
        from padsplit_scraper.persist import _firestore_client_or_none
    except ModuleNotFoundError:
        try:
            from persist import _firestore_client_or_none  # type: ignore
        except ModuleNotFoundError:
            return None
    client = _firestore_client_or_none()
    if client is None:
        return None
    return FirestoreCallStore(client)


def render_preview() -> str:
    """Exact call bodies with fake numbers masked to the last two digits."""
    script = leak_alert.fixed_script(PREVIEW_HOUSE, PREVIEW_ROOM, PREVIEW_CATEGORY)
    blocks = [
        "Leak alert Bland preview",
        "Dry-run only. No Bland or Quo HTTP.",
        f"Incident: {PREVIEW_INCIDENT}",
        f"House: {PREVIEW_HOUSE}",
        f"Room: {PREVIEW_ROOM}",
        f"Category: {PREVIEW_CATEGORY}",
        "phone_number is masked to the last 2 digits. The live POST sends the env E.164.",
        "authorization and the tool bearer are placeholders. No API key is included.",
        f"Live webhook URL comes from LEAK_ALERT_BLAND_WEBHOOK_URL. Placeholder: {WEBHOOK_PLACEHOLDER}",
        "",
    ]
    for role, number in (("don", PREVIEW_DON), ("tom", PREVIEW_TOM)):
        body = build_call_body(
            phone_number=mask_e164(number),
            script=script,
            incident_id=PREVIEW_INCIDENT,
            role=role,
            webhook_url=WEBHOOK_PLACEHOLDER,
            webhook_secret="<BLAND_WEBHOOK_SECRET>",
        )
        blocks.append(f"{role} POST {BLAND_CALLS_URL}")
        blocks.append("authorization: Bearer <BLAND_API_KEY>")
        blocks.append("content-type: application/json")
        blocks.append(json.dumps(body, indent=2))
        blocks.append("")
    blocks.append("Quo group result lines (not sent)")
    for role in ("don", "tom"):
        blocks.append(format_result_line(role, "answered", True, 20))
        blocks.append(format_result_line(role, "answered", False, None))
        blocks.append(format_result_line(role, "voicemail", None, None))
        blocks.append(format_result_line(role, "no_answer", None, None))
    blocks.append("")
    text = "\n".join(blocks)
    if leak_alert._E164_LEAK_RE.search(text) or PREVIEW_DON in text or PREVIEW_TOM in text:
        raise RuntimeError("preview leaked a full number")
    return text if text.endswith("\n") else text + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bland leak-alert preview. Does not call Bland or Quo.")
    parser.add_argument("--preview", action="store_true", help="Print the dry-run call bodies and result lines")
    parser.add_argument("--out", default="", help="Preview file path (default logs/leak_alert_bland_preview.txt)")
    args = parser.parse_args(argv)
    if not args.preview:
        parser.error("pass --preview; this command does not place calls")
    text = render_preview()
    path = Path(args.out) if args.out else PREVIEW_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
