"""Pure Bland leak-alert webhook and call-body helpers.

No network, no Firebase import, no phone logging. The Mac sender and the
Cloud Function both call this module. Webhook HMAC is SHA-256 over the raw
body, hex-encoded in ``X-Webhook-Signature``:

https://docs.bland.ai/tutorials/webhook-signing

Bland's published signer does not include a timestamp. When a timestamp is
present (header or payload ``end_at`` / ``created_at``), values older than
24 hours are rejected.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

BLAND_CALLS_URL = "https://api.bland.ai/v1/calls"
BLAND_MAX_DURATION_MIN = 2
VOICEMAIL_ACTION = "leave_message"
VOICEMAIL_CLOSING = "Check the Quo group text for details."
SUMMARY_PROMPT = (
    "One sentence: whether they confirmed they are going to the leak, "
    "and the ETA in minutes if they gave one. Do not include phone numbers."
)
ANALYSIS_SCHEMA = {
    "confirmed_going": "boolean",
    "eta_minutes": "number",
}
DISPOSITIONS = ["confirmed_going", "not_going", "voicemail", "no_answer", "failed"]
ROLES = ("don", "tom")
ROLE_LABEL = {"don": "Don", "tom": "Tom"}
STALE_AFTER = timedelta(hours=24)
FUTURE_SKEW = timedelta(minutes=5)
SUMMARY_MAX = 240
TRANSCRIPT_MAX = 180
PENDING_COLLECTION = "leak_alert_pending"
CALLS_COLLECTION = "leak_alert_calls"
QUEUE_COLLECTION = "leak_alert_result_queue"
TIMESTAMP_HEADERS = ("X-Webhook-Timestamp", "X-Bland-Timestamp")

_E164_RE = re.compile(r"\+\d{10,15}")
_LONG_DIGITS_RE = re.compile(r"\d{4,}")


@dataclass
class HandleResult:
    status: int
    error: str = ""
    record: Optional[Dict[str, Any]] = None
    line: str = ""
    enqueued: bool = False
    ignored: bool = False


@dataclass
class MemoryCallStore:
    """In-memory stand-in for the admin Firestore collections."""

    pending: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    records: Dict[tuple, Dict[str, Any]] = field(default_factory=dict)
    queue: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get_pending(self, call_id: str) -> Optional[Dict[str, Any]]:
        row = self.pending.get(call_id)
        return dict(row) if row else None

    def put_pending(self, call_id: str, doc: Mapping[str, Any]) -> None:
        self.pending[call_id] = dict(doc)

    def incident_is_pending(self, incident_id: str) -> bool:
        return any(row.get("incident_id") == incident_id for row in self.pending.values())

    def get_record(self, incident_id: str, role: str) -> Optional[Dict[str, Any]]:
        row = self.records.get((incident_id, role))
        return dict(row) if row else None

    def put_record(self, incident_id: str, role: str, doc: Mapping[str, Any]) -> None:
        self.records[(incident_id, role)] = dict(doc)

    def enqueue(self, call_id: str, doc: Mapping[str, Any]) -> bool:
        if call_id in self.queue:
            return False
        self.queue[call_id] = dict(doc)
        return True

    def get_queue(self, call_id: str) -> Optional[Dict[str, Any]]:
        row = self.queue.get(call_id)
        return dict(row) if row else None

    def list_queue(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.queue.values()]

    def mark_queue_posted(self, call_id: str, posted_at: str) -> None:
        row = self.queue.get(call_id)
        if row is None:
            return
        row["posted"] = True
        row["posted_at"] = posted_at


class FirestoreCallStore:
    """Admin Firestore adapter. The client is injected so this module stays import-light."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def get_pending(self, call_id: str) -> Optional[Dict[str, Any]]:
        return _snap_dict(self.client.collection(PENDING_COLLECTION).document(safe_doc_id(call_id)).get())

    def put_pending(self, call_id: str, doc: Mapping[str, Any]) -> None:
        self.client.collection(PENDING_COLLECTION).document(safe_doc_id(call_id)).set(dict(doc))

    def incident_is_pending(self, incident_id: str) -> bool:
        query = (
            self.client.collection(PENDING_COLLECTION)
            .where("incident_id", "==", incident_id)
            .limit(1)
        )
        return any(True for _ in query.stream())

    def get_record(self, incident_id: str, role: str) -> Optional[Dict[str, Any]]:
        return _snap_dict(_record_ref(self.client, incident_id, role).get())

    def put_record(self, incident_id: str, role: str, doc: Mapping[str, Any]) -> None:
        _record_ref(self.client, incident_id, role).set(dict(doc))

    def enqueue(self, call_id: str, doc: Mapping[str, Any]) -> bool:
        ref = self.client.collection(QUEUE_COLLECTION).document(safe_doc_id(call_id))
        try:
            ref.create(dict(doc))
        except Exception as exc:
            text = str(exc).lower()
            code = getattr(exc, "code", None)
            if code == 6 or "already exists" in text or "alreadyexists" in text:
                return False
            raise
        return True

    def get_queue(self, call_id: str) -> Optional[Dict[str, Any]]:
        return _snap_dict(self.client.collection(QUEUE_COLLECTION).document(safe_doc_id(call_id)).get())

    def list_queue(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for snap in self.client.collection(QUEUE_COLLECTION).stream():
            data = snap.to_dict() or {}
            if "call_id" not in data:
                data["call_id"] = snap.id
            rows.append(data)
        return rows

    def mark_queue_posted(self, call_id: str, posted_at: str) -> None:
        self.client.collection(QUEUE_COLLECTION).document(safe_doc_id(call_id)).set(
            {"posted": True, "posted_at": posted_at},
            merge=True,
        )


def safe_doc_id(value: str) -> str:
    text = str(value or "").strip().replace("/", "_")
    if text in {"", ".", ".."}:
        return "_"
    if text.startswith("__") and text.endswith("__"):
        text = "id_" + text.strip("_")
    return text[:700]


def verify_signature(secret: str, raw: bytes, signature: Optional[str]) -> bool:
    """Constant-time check of Bland's hex HMAC-SHA256 over the raw body."""
    if not secret or signature is None or not str(signature).strip():
        return False
    provided = str(signature).strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1].strip()
    try:
        provided_bytes = provided.encode("ascii")
    except UnicodeEncodeError:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw or b"", hashlib.sha256).hexdigest().encode("ascii")
    if len(provided_bytes) != len(expected):
        return False
    return hmac.compare_digest(expected, provided_bytes)


def bearer_matches(header: Optional[str], secret: str) -> bool:
    """Constant-time compare for the confirm_dispatch tool header."""
    if not secret or header is None or not str(header).strip():
        return False
    token = str(header).strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    try:
        token_bytes = token.encode("ascii")
        secret_bytes = secret.encode("ascii")
    except UnicodeEncodeError:
        return False
    if len(token_bytes) != len(secret_bytes):
        return False
    return hmac.compare_digest(secret_bytes, token_bytes)


def header_value(headers: Optional[Mapping[str, str]], name: str) -> str:
    if not headers:
        return ""
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value or "")
    return ""


def parse_time(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp = stamp / 1000.0
        try:
            return datetime.fromtimestamp(stamp, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_time(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stale_timestamp(
    payload: Mapping[str, Any],
    headers: Optional[Mapping[str, str]],
    now: datetime,
) -> bool:
    """Reject when Bland included a timestamp and it is outside the window."""
    current = now.astimezone(timezone.utc)
    for name in TIMESTAMP_HEADERS:
        raw = header_value(headers, name)
        if not raw:
            continue
        stamped = parse_time(raw)
        if stamped is None or stamped < current - STALE_AFTER or stamped > current + FUTURE_SKEW:
            return True
    for key in ("end_at", "created_at"):
        stamped = parse_time(payload.get(key))
        if stamped is None:
            continue
        if stamped < current - STALE_AFTER or stamped > current + FUTURE_SKEW:
            return True
    return False


def strip_phones(text: str) -> str:
    cleaned = _E164_RE.sub("", text or "")
    cleaned = _LONG_DIGITS_RE.sub("", cleaned)
    return " ".join(cleaned.split())


def voicemail_message(script: str) -> str:
    """Leave-message only. first_sentence and task stay the shared planner script."""
    return f"{script.rstrip()} {VOICEMAIL_CLOSING}"


def task_for(script: str) -> str:
    return (
        f"{script} "
        "Ask if they are going to the house. "
        "If yes, ask how many minutes until they arrive. "
        "Use confirm_dispatch with confirmed_going true or false and eta_minutes when they answer. "
        "Then end the call. Do not mention codes or phone numbers."
    )


def confirm_tool(webhook_url: str, secret: str) -> Dict[str, Any]:
    url = webhook_url.rstrip("/") + "/confirm"
    return {
        "name": "confirm_dispatch",
        "description": "Record whether the person is going to the leak and their ETA in minutes.",
        "speech": "Thanks, I have that.",
        "url": url,
        "method": "POST",
        "headers": {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        "timeout": 8000,
        "input_schema": {
            "type": "object",
            "example": {"confirmed_going": True, "eta_minutes": 20},
            "properties": {
                "confirmed_going": {"type": "boolean"},
                "eta_minutes": {"type": "number"},
            },
            "required": ["confirmed_going"],
        },
        "body": {
            "call_id": "{{call_id}}",
            "incident_id": "{{incident_id}}",
            "role": "{{role}}",
            "confirmed_going": "{{input.confirmed_going}}",
            "eta_minutes": "{{input.eta_minutes}}",
        },
    }


def build_call_body(
    *,
    phone_number: str,
    script: str,
    incident_id: str,
    role: str,
    webhook_url: str = "",
    webhook_secret: str = "",
    citation_schema_id: str = "",
) -> Dict[str, Any]:
    """POST /v1/calls body. Caller must not log it: it can contain a phone and the tool secret."""
    body: Dict[str, Any] = {
        "phone_number": phone_number,
        "task": task_for(script),
        "first_sentence": script,
        "max_duration": BLAND_MAX_DURATION_MIN,
        "block_interruptions": False,
        "record": False,
        "voicemail": {"action": VOICEMAIL_ACTION, "message": voicemail_message(script)},
        "summary_prompt": SUMMARY_PROMPT,
        "analysis_schema": dict(ANALYSIS_SCHEMA),
        "dispositions": list(DISPOSITIONS),
        "metadata": {"incident_id": incident_id, "role": role},
        "request_data": {"incident_id": incident_id, "role": role},
    }
    url = (webhook_url or "").strip()
    if url.startswith("https://"):
        body["webhook"] = url
        secret = (webhook_secret or "").strip()
        if secret:
            body["tools"] = [confirm_tool(url, secret)]
    schema_id = (citation_schema_id or "").strip()
    if schema_id:
        body["citation_schema_ids"] = [schema_id]
    return body


def format_result_line(
    role: str,
    outcome: str,
    confirmed: Optional[bool],
    eta_minutes: Optional[int],
) -> str:
    label = ROLE_LABEL.get(role, role)
    if outcome == "voicemail":
        return f"{label}: voicemail"
    if outcome == "no_answer":
        return f"{label}: no answer"
    if outcome == "failed":
        return f"{label}: failed"
    if outcome == "answered" and confirmed is True:
        if eta_minutes is None:
            return f"{label}: going"
        return f"{label}: going, ETA {eta_minutes}m"
    if outcome == "answered" and confirmed is False:
        return f"{label}: not going"
    if outcome == "answered":
        return f"{label}: answered"
    return f"{label}: {outcome}"


def classify_outcome(payload: Mapping[str, Any]) -> Optional[str]:
    """Map a Bland call object to answered, voicemail, no_answer, or failed.

    Returns None when the payload is a mid-call stream event or still in progress.
    """
    status = str(payload.get("status") or "").strip().lower().replace("_", "-")
    answered = str(payload.get("answered_by") or "").strip().lower().replace("_", "-")
    disposition = str(payload.get("disposition_tag") or "").strip().lower()
    category = str(payload.get("category") or "").strip().lower()
    if category and not status and payload.get("completed") is not True:
        return None
    if payload.get("event_type") == "citations" and not status:
        return None
    if status in {"failed", "canceled", "cancelled", "unknown"}:
        return "failed"
    if status in {"no-answer", "busy"} or answered == "no-answer" or disposition in {"no_answer", "no-answer", "busy"}:
        return "no_answer"
    if answered == "voicemail" or disposition == "voicemail":
        return "voicemail"
    if status in {"queued", "in-progress", "started", "ringing", "allocated"}:
        return None
    if status == "completed" or payload.get("completed") is True:
        return "answered"
    return None


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
    return None


def _as_eta(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        minutes = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        minutes = int(value.strip())
    else:
        return None
    if minutes < 0 or minutes > 24 * 60:
        return None
    return minutes


def _from_citations(payload: Mapping[str, Any]) -> tuple[Optional[bool], Optional[int]]:
    confirmed = None
    eta = None
    rows = payload.get("citations")
    if not isinstance(rows, list):
        return None, None
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("variable_name") or row.get("name") or "").strip().lower()
        value = row.get("value")
        if name in {"confirmed_going", "confirmed"}:
            confirmed = _as_bool(value)
        elif name in {"eta_minutes", "eta"}:
            eta = _as_eta(value)
    return confirmed, eta


def extract_confirmation(
    payload: Mapping[str, Any],
    pending: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[bool], Optional[int]]:
    confirmed = None
    eta = None
    analysis = payload.get("analysis")
    if isinstance(analysis, dict):
        confirmed = _as_bool(analysis.get("confirmed_going"))
        eta = _as_eta(analysis.get("eta_minutes"))
    cited_confirmed, cited_eta = _from_citations(payload)
    if confirmed is None:
        confirmed = cited_confirmed
    if eta is None:
        eta = cited_eta
    variables = payload.get("variables")
    if isinstance(variables, dict):
        nested = variables.get("input") if isinstance(variables.get("input"), dict) else {}
        if confirmed is None:
            confirmed = _as_bool(variables.get("confirmed_going"))
        if confirmed is None:
            confirmed = _as_bool(nested.get("confirmed_going"))
        if eta is None:
            eta = _as_eta(variables.get("eta_minutes"))
        if eta is None:
            eta = _as_eta(nested.get("eta_minutes"))
    disposition = str(payload.get("disposition_tag") or "").strip().lower()
    if confirmed is None and disposition == "confirmed_going":
        confirmed = True
    if confirmed is None and disposition == "not_going":
        confirmed = False
    if pending:
        if confirmed is None:
            confirmed = _as_bool(pending.get("confirmed_going"))
        if eta is None:
            eta = _as_eta(pending.get("eta_minutes"))
    return confirmed, eta


def _summary_text(payload: Mapping[str, Any]) -> str:
    summary = strip_phones(str(payload.get("summary") or ""))
    if summary:
        return summary[:SUMMARY_MAX]
    transcript = str(payload.get("concatenated_transcript") or "")
    if not transcript or len(transcript) > TRANSCRIPT_MAX or _E164_RE.search(transcript):
        return ""
    short = strip_phones(transcript)
    if not short or _LONG_DIGITS_RE.search(transcript):
        return ""
    return short[:SUMMARY_MAX]


def build_record(
    payload: Mapping[str, Any],
    *,
    incident_id: str,
    role: str,
    source: str,
    now: datetime,
    pending: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    outcome = classify_outcome(payload)
    if outcome is None:
        return None
    confirmed, eta = extract_confirmation(payload, pending)
    if outcome != "answered":
        confirmed = None
        eta = None
    elif confirmed is not True:
        eta = None
    call_id = str(payload.get("call_id") or payload.get("c_id") or "")
    record = {
        "outcome": outcome,
        "confirmed": confirmed,
        "eta_minutes": eta,
        "summary": _summary_text(payload),
        "call_id": call_id,
        "role": role,
        "incident_id": incident_id,
        "source": source,
        "recorded_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if _E164_RE.search(json.dumps(record)):
        record["summary"] = ""
    return record


def _queue_doc(record: Mapping[str, Any], line: str, now: datetime) -> Dict[str, Any]:
    return {
        "call_id": record["call_id"],
        "incident_id": record["incident_id"],
        "role": record["role"],
        "outcome": record["outcome"],
        "line": line,
        "enqueued_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "posted": False,
    }


def handle_webhook(
    raw: bytes,
    signature: Optional[str],
    *,
    secret: str,
    store: Any,
    now: datetime,
    headers: Optional[Mapping[str, str]] = None,
    dry_run: bool = False,
    result_log: Optional[List[Dict[str, Any]]] = None,
) -> HandleResult:
    """Verify, require a pending call, write the record, enqueue one Quo line.

    ``result_log`` collects the would-send line for dry-run tests. This function
    does not call Quo or Bland.
    """
    if signature is None or not str(signature).strip():
        return HandleResult(401, "missing_signature")
    if not verify_signature(secret, raw, signature):
        return HandleResult(401, "invalid_signature")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeError, ValueError):
        return HandleResult(400, "bad_payload")
    if not isinstance(payload, dict):
        return HandleResult(400, "bad_payload")
    if stale_timestamp(payload, headers, now):
        return HandleResult(401, "stale_timestamp")
    outcome = classify_outcome(payload)
    if outcome is None:
        return HandleResult(200, ignored=True)
    call_id = str(payload.get("call_id") or payload.get("c_id") or "").strip()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    incident_id = str(metadata.get("incident_id") or "").strip()
    role = str(metadata.get("role") or "").strip().lower()
    if not call_id or not incident_id or role not in ROLES:
        return HandleResult(404, "unknown_call")
    pending = store.get_pending(call_id)
    if not pending:
        return HandleResult(404, "unknown_call")
    if str(pending.get("incident_id") or "") != incident_id or not store.incident_is_pending(incident_id):
        return HandleResult(404, "unknown_incident")
    if str(pending.get("role") or "") != role:
        return HandleResult(404, "unknown_role")
    existing = store.get_record(incident_id, role)
    if isinstance(existing, dict) and str(existing.get("call_id") or "") == call_id:
        line = format_result_line(
            role,
            str(existing.get("outcome") or ""),
            existing.get("confirmed") if isinstance(existing.get("confirmed"), bool) else None,
            existing.get("eta_minutes") if isinstance(existing.get("eta_minutes"), int) else None,
        )
        return HandleResult(200, record=existing, line=line, enqueued=False)
    record = build_record(
        payload,
        incident_id=incident_id,
        role=role,
        source="webhook",
        now=now,
        pending=pending,
    )
    if record is None:
        return HandleResult(200, ignored=True)
    line = format_result_line(role, record["outcome"], record["confirmed"], record["eta_minutes"])
    store.put_record(incident_id, role, record)
    enqueued = store.enqueue(call_id, _queue_doc(record, line, now))
    if dry_run and result_log is not None:
        result_log.append({
            "dry_run": True,
            "line": line,
            "call_id": call_id,
            "outcome": record["outcome"],
            "role": role,
        })
    return HandleResult(200, record=record, line=line, enqueued=enqueued)


def handle_confirm(
    raw: bytes,
    authorization: Optional[str],
    *,
    secret: str,
    store: Any,
    now: datetime,
) -> HandleResult:
    """Mid-call confirm_dispatch. Bearer secret, pending call required. No Quo post."""
    if not bearer_matches(authorization, secret):
        return HandleResult(401, "unauthorized")
    try:
        payload = json.loads((raw or b"{}").decode("utf-8") or "{}")
    except (UnicodeError, ValueError):
        return HandleResult(400, "bad_payload")
    if not isinstance(payload, dict):
        return HandleResult(400, "bad_payload")
    call_id = str(payload.get("call_id") or "").strip()
    incident_id = str(payload.get("incident_id") or "").strip()
    role = str(payload.get("role") or "").strip().lower()
    pending = store.get_pending(call_id) if call_id else None
    if not pending or not store.incident_is_pending(incident_id):
        return HandleResult(404, "unknown_call")
    if str(pending.get("incident_id") or "") != incident_id or str(pending.get("role") or "") != role:
        return HandleResult(404, "unknown_incident")
    if role not in ROLES:
        return HandleResult(404, "unknown_role")
    updated = dict(pending)
    confirmed = _as_bool(payload.get("confirmed_going"))
    eta = _as_eta(payload.get("eta_minutes"))
    if confirmed is not None:
        updated["confirmed_going"] = confirmed
    if eta is not None and confirmed is True:
        updated["eta_minutes"] = eta
    updated["confirmed_at"] = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.put_pending(call_id, updated)
    return HandleResult(200)


def _snap_dict(snap: Any) -> Optional[Dict[str, Any]]:
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or None
    return dict(data) if isinstance(data, dict) else None


def _record_ref(client: Any, incident_id: str, role: str) -> Any:
    return (
        client.collection(CALLS_COLLECTION)
        .document(safe_doc_id(incident_id))
        .collection("roles")
        .document(role)
    )
