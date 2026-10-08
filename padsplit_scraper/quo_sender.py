#!/usr/bin/env python3
"""Quo SMS sender for leak alerts. Default is dry-run. This module does not place calls.

Live HTTP runs only when ``runtime.send_enabled("leak_alert")`` is true and
``LEAK_ALERT_DRY_RUN`` is explicitly off. CI and collection-only stay no-ops.
There is no Bearer prefix. The body is SMS text only: ``content``, ``from``,
``to`` (1-10 E.164 numbers). Two or more ``to`` numbers are one group thread.

At-most-once: the state key (``group:{incident}`` or ``tenant:{incident}:{hash}``)
is set to ``sending`` and flushed with an atomic replace before POST. A crash
after that claim does not send again, including when the POST never left the
process. Timeouts are not retried (the server may already have accepted).
429 and 5xx retry up to 3 attempts with backoff. 4xx does not retry.
Codes 0206400 (unapproved or unregistered) and 0204403 (daily cap) are marked
failed immediately and never retried.

Phone numbers stay in memory for the request. State and logs store the hash
only. ``--preview`` prints sample copy and masked digits; it never sends.
``preview_call_script`` is a hook for the voice PR and is not a call script.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

try:
    from padsplit_scraper import leak_alert
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/quo_sender.py
    import leak_alert  # type: ignore
    import runtime  # type: ignore


ROOT_DIR = Path(__file__).resolve().parent.parent
PREVIEW_PATH = ROOT_DIR / "logs" / "leak_alert_preview.txt"

QUO_MESSAGES_URL = f"{leak_alert.QUO_API_BASE}{leak_alert.QUO_MESSAGES_PATH}"
QUO_API_VERSION = "2026-03-30"
QUO_SUCCESS_STATUSES = {200, 201, 202}
DEFAULT_TIMEOUT = 30.0
MAX_ATTEMPTS = 3

CODE_UNREGISTERED = "0206400"
CODE_DAILY_CAP = "0204403"
TERMINAL_CODES = {
    CODE_UNREGISTERED: "unapproved or unregistered",
    CODE_DAILY_CAP: "daily cap",
}
CLAIMABLE_STATUSES = {"planned"}

# Reserved fictional range +15555550100 through +15555550199. Preview only.
PREVIEW_HOUSE = "Sample House"
PREVIEW_ROOM = "2"
PREVIEW_CATEGORY = "water"
PREVIEW_FROM = "+15555550100"
PREVIEW_GROUP = ("+15555550101", "+15555550102", "+15555550103")
PREVIEW_TENANTS = ("+15555550110", "+15555550111")

_DIGIT_RE = re.compile(r"\D")


@dataclass
class Outbound:
    """In-memory send. ``to`` is never written to state or logs."""

    key: str
    content: str
    to: List[str]
    kind: str
    mode: str


def _log(message: str) -> None:
    text = message or ""
    if leak_alert._E164_LEAK_RE.search(text):
        text = "log suppressed: phone number"
    lowered = text.lower()
    if "api_key" in lowered or "authorization" in lowered:
        text = "log suppressed: credential"
    sys.stderr.write(f"[leak-alert] {text}\n")


def dry_run_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Default on. Only an explicit false flag allows live HTTP."""
    env = os.environ if environ is None else environ
    parsed = runtime.flag_value(env.get("LEAK_ALERT_DRY_RUN"))
    if parsed is None:
        return True
    return parsed


def http_allowed(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only for a non-CI, non-dry-run leak_alert send."""
    env = os.environ if environ is None else environ
    if runtime.running_in_ci(env):  # type: ignore[arg-type]
        return False
    if not runtime.send_enabled("leak_alert", env):  # type: ignore[arg-type]
        return False
    if dry_run_enabled(env):
        return False
    return True


def mask_e164(number: str) -> str:
    """Last two digits only. Preview and logs use this, never the full number."""
    digits = _DIGIT_RE.sub("", number or "")
    if len(digits) < 2:
        return "***"
    return f"***{digits[-2:]}"


def _water_key_url() -> str:
    try:
        from padsplit_scraper import leak_reply
    except ModuleNotFoundError:
        import leak_reply  # type: ignore
    return str(leak_reply.WATER_KEY_YOUTUBE_URL)


def outbound_tenant_text(body: str) -> str:
    """Planner tenant copy plus the house shutoff steps. No codes, names, or amounts."""
    text = (body or "").strip()
    parts = [text] if text else []
    lowered = text.lower()
    if "shut-off box" not in lowered and "shut off box" not in lowered:
        parts.append(
            "The water shut-off box is between the water meter and the house. "
            "Use the water key to turn the water OFF immediately."
        )
    if "service alert" not in lowered:
        parts.append("This is a service alert about a leak at your house.")
    youtube = _water_key_url()
    if youtube and youtube not in text:
        parts.append(f"How to use a water key:\n{youtube}")
    outbound = "\n\n".join(part for part in parts if part).strip()
    _assert_safe_copy(outbound)
    return outbound


def _assert_safe_copy(text: str) -> None:
    leak_alert.assert_plan_has_no_numbers(text)
    lowered = (text or "").lower()
    for banned in ("lock code", "wifi", "wi-fi", "password", "ssn"):
        if banned in lowered:
            raise RuntimeError("refusing leak alert copy")


def preview_call_script() -> str:
    """Hook for the voice PR. This is not a Bland script and must stay empty of one."""
    return "(call script is owned by the voice PR and is not part of this text preview)"


def _flag_label(environ: Mapping[str, str], name: str) -> str:
    parsed = runtime.flag_value(environ.get(name))
    if parsed is True:
        return "on"
    if parsed is False:
        return "off"
    return "off (unset)"


def gate_lines(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    env = os.environ if environ is None else environ
    dry = runtime.flag_value(env.get("LEAK_ALERT_DRY_RUN"))
    if dry is None:
        dry_label = "on (default)"
    elif dry:
        dry_label = "on"
    else:
        dry_label = "off"
    send = runtime.send_enabled("leak_alert", env)  # type: ignore[arg-type]
    return [
        f"runtime.send_enabled(leak_alert): {'on' if send else 'off'}",
        f"LEAK_ALERT_ENABLE: {_flag_label(env, 'LEAK_ALERT_ENABLE')}",
        f"PADSPLIT_SEND_LEAK_ALERT: {_flag_label(env, 'PADSPLIT_SEND_LEAK_ALERT')}",
        f"LEAK_ALERT_DRY_RUN: {dry_label}",
        f"CI: {'on' if runtime.running_in_ci(env) else 'off'}",  # type: ignore[arg-type]
        f"collection_only: {'on' if runtime.collection_only(env) else 'off'}",  # type: ignore[arg-type]
        "HTTP: off (preview does not send)",
    ]


def render_preview(environ: Optional[Mapping[str, str]] = None) -> str:
    """Exact group and tenant texts for a fake incident. Numbers are masked."""
    env = os.environ if environ is None else environ
    group_text = leak_alert.fixed_script(PREVIEW_HOUSE, PREVIEW_ROOM, PREVIEW_CATEGORY)
    tenant_text = outbound_tenant_text(
        leak_alert.tenant_body(PREVIEW_HOUSE, PREVIEW_ROOM, ask_photos=True)
    )
    lines = [
        "leak alert Quo preview",
        "incident: sample-house:sample-leak",
        "house: Sample House",
        "room: 2",
        "http: not sent",
        "",
        "gates:",
        *[f"  {line}" for line in gate_lines(env)],
        "",
        "group text:",
        group_text,
        "",
        f"group from: {mask_e164(PREVIEW_FROM)}",
        "group to: " + ", ".join(mask_e164(number) for number in PREVIEW_GROUP),
        "",
        "tenant text:",
        tenant_text,
        "",
        f"tenant from: {mask_e164(PREVIEW_FROM)}",
        *[f"tenant to: {mask_e164(number)}" for number in PREVIEW_TENANTS],
        "",
        "call_script:",
        "  hook: padsplit_scraper.quo_sender.preview_call_script",
        f"  {preview_call_script()}",
        "",
    ]
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    _assert_safe_copy(text)
    for number in (PREVIEW_FROM, *PREVIEW_GROUP, *PREVIEW_TENANTS):
        if number in text:
            raise RuntimeError("preview leaked a sample number")
    return text


def write_preview(text: str, path: Optional[Path] = None) -> Path:
    target = PREVIEW_PATH if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def error_code(payload: Any) -> str:
    """Quo error code only. The rest of the body is not logged."""
    if not isinstance(payload, dict):
        return ""
    for key in ("code", "errorCode", "error_code"):
        value = payload.get(key)
        if value:
            return str(value).strip()
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("code", "errorCode", "error_code"):
            if error.get(key):
                return str(error[key]).strip()
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        for key in ("code", "errorCode", "error_code"):
            if errors[0].get(key):
                return str(errors[0][key]).strip()
    return ""


def backoff_seconds(attempt: int) -> float:
    return min(4.0, 0.5 * (2 ** (max(attempt, 1) - 1)))


def _raw_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    return key


def default_post(
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout: float,
) -> tuple[int, Dict[str, Any]]:
    """POST helper. Callers must not log the body or the response."""
    data = json.dumps(dict(payload)).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={str(key): str(value) for key, value in headers.items()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        reader = getattr(exc, "read", None)
        raw = b""
        if callable(reader):
            try:
                raw = reader()
            except Exception:
                raw = b""
        status = int(exc.code)
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except Exception:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def post_text(
    *,
    content: str,
    from_number: str,
    to_numbers: Sequence[str],
    api_key: str,
    http_post: Optional[Callable[..., Any]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = MAX_ATTEMPTS,
) -> Dict[str, Any]:
    """POST one SMS. 0206400, 0204403, other 4xx, and timeouts are not retried."""
    poster = http_post or default_post
    pause = sleeper or time.sleep
    key = _raw_key(api_key)
    if not key:
        _log("Quo send failed: key missing; marked failed; not retrying")
        return {"status": "failed", "reason": "missing_key", "attempts": 0}
    headers = {
        "Authorization": key,
        "Quo-Api-Version": QUO_API_VERSION,
        "Content-Type": "application/json",
    }
    body = {
        "content": content,
        "from": from_number,
        "to": list(to_numbers),
    }
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        try:
            status, payload = poster(
                QUO_MESSAGES_URL,
                headers=headers,
                payload=body,
                timeout=timeout,
            )
        except (TimeoutError, urllib.error.URLError):
            _log("Quo send timed out; marked failed; not retrying")
            return {"status": "failed", "reason": "timeout", "attempts": attempts}
        except Exception:
            _log("Quo send failed: transport error; marked failed; not retrying")
            return {"status": "failed", "reason": "transport", "attempts": attempts}
        status = int(status or 0)
        code = error_code(payload)
        if code in TERMINAL_CODES:
            _log(
                f"Quo send failed: {TERMINAL_CODES[code]} ({code}); "
                "marked failed; not retrying"
            )
            return {"status": "failed", "reason": code, "attempts": attempts}
        if status in QUO_SUCCESS_STATUSES:
            return {"status": "sent", "reason": "", "attempts": attempts}
        if status == 429 or status >= 500:
            if attempt >= max_attempts:
                _log(f"Quo send failed: HTTP {status}; retries exhausted; marked failed")
                return {"status": "failed", "reason": f"http_{status}", "attempts": attempts}
            pause(backoff_seconds(attempt))
            continue
        _log(f"Quo send failed: HTTP {status}; marked failed; not retrying")
        return {"status": "failed", "reason": f"http_{status}", "attempts": attempts}
    _log("Quo send failed: retries exhausted; marked failed")
    return {"status": "failed", "reason": "retries_exhausted", "attempts": attempts}


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_save_state(state: Dict[str, Any], path: Path) -> None:
    """Replace the state file. The payload is checked before the rename."""
    encoded = json.dumps(state, indent=2) + "\n"
    leak_alert.assert_plan_has_no_numbers(encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def claim_key(
    state: Dict[str, Any],
    key: str,
    *,
    now: datetime,
    kind: str,
    mode: str,
    path: Path,
) -> bool:
    """Record ``sending`` on disk before POST. False when the key is already claimed."""
    alerts = state.setdefault("alerts", {})
    if not isinstance(alerts, dict):
        raise leak_alert.CorruptAlertState("alerts is not an object")
    stamp = _stamp(now)
    row = alerts.get(key)
    if row is None:
        alerts[key] = {
            "status": "sending",
            "claimed_at": stamp,
            "kind": kind,
            "provider": "quo",
            "mode": mode,
        }
        atomic_save_state(state, path)
        return True
    if not isinstance(row, dict):
        return False
    if str(row.get("status") or "") not in CLAIMABLE_STATUSES:
        return False
    row["status"] = "sending"
    row["claimed_at"] = stamp
    if not row.get("kind"):
        row["kind"] = kind
    if not row.get("provider"):
        row["provider"] = "quo"
    if not row.get("mode"):
        row["mode"] = mode
    atomic_save_state(state, path)
    return True


def _finish(state: Dict[str, Any], key: str, outcome: Mapping[str, Any], now: datetime) -> None:
    alerts = state.setdefault("alerts", {})
    row = alerts.get(key)
    if not isinstance(row, dict):
        return
    row["status"] = outcome.get("status") or "failed"
    row["finished_at"] = _stamp(now)
    reason = str(outcome.get("reason") or "")
    if reason:
        row["reason"] = reason
    else:
        row.pop("reason", None)


def resolve_from_number(environ: Mapping[str, str]) -> str:
    raw = (environ.get("QUO_FROM_NUMBER") or environ.get("FIELD_MMS_QUO_FROM") or "").strip()
    return leak_alert.normalize_e164(raw) if raw else ""


def resolve_group_numbers(environ: Mapping[str, str], from_number: str) -> List[str]:
    raw = str(environ.get(leak_alert.GROUP_ENV) or "")
    numbers = leak_alert.parse_group_e164s(raw)
    sender = leak_alert.normalize_e164(from_number)
    if not sender:
        return numbers
    return [number for number in numbers if number != sender]


def resolve_tenant_numbers(
    thread: Optional[Mapping[str, Any]],
    house_threads: Optional[Sequence[Mapping[str, Any]]],
    *,
    now: datetime,
    profile_fetcher,
) -> List[str]:
    """Live member phones. The list is not stored."""
    if thread is None:
        return []
    phones, _missing = leak_alert._house_member_phones(
        thread,
        list(house_threads or []),
        now=now,
        profile_fetcher=profile_fetcher,
    )
    return phones


def build_outbounds(
    plan: leak_alert.AlertPlan,
    *,
    from_number: str,
    group_numbers: Sequence[str],
    tenant_numbers: Sequence[str],
) -> List[Outbound]:
    sender = leak_alert.normalize_e164(from_number)
    group: List[str] = []
    seen_group: set[str] = set()
    for number in group_numbers:
        normalized = leak_alert.normalize_e164(number)
        if not normalized or normalized == sender or normalized in seen_group:
            continue
        seen_group.add(normalized)
        group.append(normalized)
    tenants: List[str] = []
    seen_tenant: set[str] = set()
    for number in tenant_numbers:
        normalized = leak_alert.normalize_e164(number)
        if not normalized or normalized == sender or normalized in seen_tenant:
            continue
        seen_tenant.add(normalized)
        tenants.append(normalized)
    by_hash = {leak_alert.recipient_hash(number): number for number in tenants}
    items: List[Outbound] = []
    for entry in plan.entries:
        if str(entry.get("status") or "") != "planned":
            continue
        kind = str(entry.get("kind") or "")
        key = str(entry.get("key") or "")
        if kind == "quo_group":
            items.append(
                Outbound(
                    key=key,
                    content=str(entry.get("content") or "").strip(),
                    to=list(group),
                    kind="quo_group",
                    mode="group",
                )
            )
            continue
        if kind != "tenant_text" or entry.get("mode") != "private_1to1":
            continue
        prefix = f"tenant:{plan.incident}:"
        digest = key[len(prefix):] if key.startswith(prefix) else ""
        number = by_hash.get(digest, "")
        items.append(
            Outbound(
                key=key,
                content=outbound_tenant_text(str(entry.get("body") or entry.get("content") or "")),
                to=[number] if number else [],
                kind="tenant_text",
                mode="private_1to1",
            )
        )
    return items


def _invalid_reason(item: Outbound) -> str:
    if not item.key:
        return "missing_key"
    if not item.content:
        return "missing_content"
    if not item.to:
        return "missing_target"
    if len(item.to) > leak_alert.TENANT_CAP:
        return "over_cap"
    return ""


def deliver(
    plan: leak_alert.AlertPlan,
    *,
    state: Dict[str, Any],
    state_path: Path,
    now: datetime,
    from_number: str,
    group_numbers: Sequence[str],
    tenant_numbers: Sequence[str],
    api_key: str,
    http_post: Optional[Callable[..., Any]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    """Claim each planned Quo key, then POST. Caller must already have passed the gate."""
    results: List[Dict[str, Any]] = []
    for item in build_outbounds(
        plan,
        from_number=from_number,
        group_numbers=group_numbers,
        tenant_numbers=tenant_numbers,
    ):
        reason = _invalid_reason(item)
        if reason:
            _log(f"Quo send skipped: {reason}")
            results.append({"key": item.key, "status": "skipped", "reason": reason})
            continue
        if not claim_key(
            state,
            item.key,
            now=now,
            kind=item.kind,
            mode=item.mode,
            path=state_path,
        ):
            results.append({"key": item.key, "status": "already_claimed", "reason": ""})
            continue
        outcome = post_text(
            content=item.content,
            from_number=from_number,
            to_numbers=item.to,
            api_key=api_key,
            http_post=http_post,
            sleeper=sleeper,
            timeout=timeout,
        )
        _finish(state, item.key, outcome, now)
        atomic_save_state(state, state_path)
        results.append(
            {
                "key": item.key,
                "status": outcome["status"],
                "reason": outcome["reason"],
            }
        )
    leak_alert.assert_plan_has_no_numbers(json.dumps(results))
    leak_alert.assert_plan_has_no_numbers(json.dumps(state))
    return results


def maybe_deliver(
    plan: leak_alert.AlertPlan,
    *,
    state: Dict[str, Any],
    state_path: Path,
    now: datetime,
    environ: Optional[Mapping[str, str]] = None,
    thread: Optional[Mapping[str, Any]] = None,
    house_threads: Optional[Sequence[Mapping[str, Any]]] = None,
    profile_fetcher=None,
    group_numbers: Optional[Sequence[str]] = None,
    tenant_numbers: Optional[Sequence[str]] = None,
    http_post: Optional[Callable[..., Any]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
) -> List[Dict[str, Any]]:
    """Send only when the leak_alert gate is open and dry-run is explicitly off."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    if not http_allowed(env):
        return []
    api_key = _raw_key(str(env.get("QUO_API_KEY") or ""))
    from_number = resolve_from_number(env)
    if not api_key or not from_number:
        _log("Quo send skipped: from-number or key missing")
        return []
    if group_numbers is None:
        group_numbers = resolve_group_numbers(env, from_number)
    if tenant_numbers is None:
        try:
            tenant_numbers = resolve_tenant_numbers(
                thread,
                house_threads,
                now=now,
                profile_fetcher=profile_fetcher,
            )
        except Exception:
            _log("Quo tenant lookup failed; tenant texts skipped")
            tenant_numbers = []
    return deliver(
        plan,
        state=state,
        state_path=state_path,
        now=now,
        from_number=from_number,
        group_numbers=group_numbers,
        tenant_numbers=tenant_numbers,
        api_key=api_key,
        http_post=http_post,
        sleeper=sleeper,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Leak-alert Quo SMS preview (no send)")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Print the sample group and tenant texts and write logs/leak_alert_preview.txt",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.preview:
        parser.error("pass --preview; this command does not send")
    text = render_preview()
    path = write_preview(text)
    sys.stdout.write(text)
    _log(f"preview written to {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
