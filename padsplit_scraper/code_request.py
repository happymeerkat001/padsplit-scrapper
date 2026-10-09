"""Deterministic code-request classifier plus an optional Jev audit.

Wifi requests are classified and never answered. Jev runs only when the
fast path is ambiguous, and as a non-blocking audit. It never chooses a
room. The gate JEV_CODE_CLASSIFY_ENABLE defaults off and also needs
AI_GATEWAY_API_KEY. Timeout is 3 seconds. Any error keeps the
deterministic result.

Jev receives message text with digits stripped and no names. The request
asks for zero data retention and no prompt training.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Optional

try:
    from padsplit_scraper import lockout_reply
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python padsplit_scraper/code_request.py
    import lockout_reply  # type: ignore
    import runtime  # type: ignore


JEV_MODEL = "typesafe-ai/jev"
JEV_URL = "https://ai-gateway.vercel.sh/v1/evaluate"
JEV_TIMEOUT_S = 3
_CONFIDENT = 0.6

_WIFI_RE = re.compile(r"(?i)\bwi[\s-]?fi\b|\bwireless\b|\binternet\s+password\b")
_LOCKBOX_RE = re.compile(r"(?i)\block\s*box\b|\blockbox\b")
_ROOM_RE = re.compile(r"(?i)\broom\s+code\b|\bbedroom\s+code\b|\bmy\s+room\s+code\b")
_DOOR_RE = re.compile(r"(?i)\bdoor\s+code\b|\bfront\s+door\b|\bback\s+door\b|\bbuilding\s+code\b")
_GENERIC_RE = re.compile(
    r"(?i)("
    r"\bwhat(?:['’]s| is)\s+my\s+code\b"
    r"|\bforgot\s+(?:my\s+)?code\b"
    r"|\bnew\s+code\b"
    r"|\bcode\s+not\s+working\b"
    r"|\bneed\s+(?:my\s+)?(?:door\s+|room\s+|lockbox\s+)?code\b"
    r")"
)
_DIGIT_RE = re.compile(r"\d+")


@dataclass(frozen=True)
class CodeRequest:
    is_code_request: bool
    kind: str
    mentioned_room: str
    ambiguous: bool
    lockout: bool = False


def strip_for_jev(text: str) -> str:
    """Message text only, digits removed. Names are never added."""
    cleaned = _DIGIT_RE.sub(" ", text or "")
    return " ".join(cleaned.split())


def classify_fast(text: str) -> CodeRequest:
    """Keyword path. Lockout phrases are a door subtype."""
    body = text or ""
    mentioned = lockout_reply.mentioned_room(body)
    lockout = lockout_reply.detect_lockout(body) or lockout_reply.detect_door_fail_followup(body)
    wifi = bool(_WIFI_RE.search(body))
    lockbox = bool(_LOCKBOX_RE.search(body))
    room = bool(_ROOM_RE.search(body))
    door = bool(_DOOR_RE.search(body)) or lockout
    generic = bool(_GENERIC_RE.search(body))
    kinds = []
    if door:
        kinds.append("door")
    if room:
        kinds.append("room")
    if lockbox:
        kinds.append("lockbox")
    if wifi:
        kinds.append("wifi")
    if wifi and len(kinds) > 1:
        return CodeRequest(True, "unknown", mentioned, True, lockout=lockout)
    if wifi and not door and not room and not lockbox:
        return CodeRequest(True, "wifi", mentioned, False, lockout=False)
    if len(kinds) == 1:
        return CodeRequest(True, kinds[0], mentioned, False, lockout=lockout and kinds[0] == "door")
    if len(kinds) > 1:
        return CodeRequest(True, "unknown", mentioned, True, lockout=lockout)
    if generic:
        return CodeRequest(True, "unknown", mentioned, True, lockout=False)
    return CodeRequest(False, "unknown", mentioned, False, lockout=False)


def _questions() -> Dict[str, Any]:
    return {
        "is_code_request": {
            "type": "boolean",
            "instructions": "Is the sender asking for a door, room, or lockbox entry code?",
        },
        "kind": {
            "type": "choice",
            "instructions": "Which entry are they asking for? Do not infer a room number.",
            "criteria": {
                "door": "A building door code",
                "room": "A bedroom lock code",
                "lockbox": "A lockbox code",
                "wifi": "A wifi password",
                "unknown": "Unclear, or not an entry-code request",
            },
        },
    }


def jev_payload(text: str) -> Dict[str, Any]:
    return {
        "model": JEV_MODEL,
        "state": {"message": strip_for_jev(text)},
        "questions": _questions(),
        "providerOptions": {
            "gateway": {
                "zeroDataRetention": True,
                "disallowPromptTraining": True,
            }
        },
    }


def _enabled(environ: Optional[os._Environ[str]]) -> bool:
    env = environ if environ is not None else os.environ
    if runtime.flag_value(env.get("JEV_CODE_CLASSIFY_ENABLE")) is not True:
        return False
    if not (env.get("AI_GATEWAY_API_KEY") or "").strip():
        return False
    if runtime.running_in_ci(env) or runtime.collection_only(env):
        return False
    return True


def _parse_jev(payload: Any, mentioned_room: str) -> Optional[CodeRequest]:
    if not isinstance(payload, dict):
        return None
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        answers = payload
    kind_row = answers.get("kind") if isinstance(answers.get("kind"), dict) else {}
    flag_row = answers.get("is_code_request") if isinstance(answers.get("is_code_request"), dict) else {}
    choice = str(kind_row.get("choice") or "")
    if choice not in {"door", "room", "lockbox", "wifi", "unknown"}:
        return None
    probabilities = kind_row.get("probabilities") if isinstance(kind_row.get("probabilities"), dict) else {}
    try:
        choice_p = float(probabilities.get(choice, 0))
    except (TypeError, ValueError):
        choice_p = 0.0
    if choice_p and choice_p < _CONFIDENT:
        return None
    try:
        probability = float(flag_row.get("probability", 0))
    except (TypeError, ValueError):
        probability = 0.0
    is_request = probability >= _CONFIDENT
    if choice == "unknown":
        is_request = False if probability < _CONFIDENT else is_request
    is_code = choice == "wifi" or (is_request and choice != "unknown")
    return CodeRequest(
        is_code_request=is_code,
        kind=choice,
        mentioned_room=mentioned_room,
        ambiguous=False,
        lockout=False,
    )


class JevClassifier:
    """Pluggable evaluate client. post() performs the HTTP call or a fake."""

    def __init__(
        self,
        *,
        post: Optional[Callable[[Dict[str, Any], float], Any]] = None,
        environ: Optional[os._Environ[str]] = None,
        timeout: float = JEV_TIMEOUT_S,
    ) -> None:
        self._post = post
        self.environ = environ
        self.timeout = timeout
        self.agree = 0
        self.disagree = 0

    def enabled(self) -> bool:
        return _enabled(self.environ)

    def _evaluate(self, text: str) -> Any:
        payload = jev_payload(text)
        if self._post is not None:
            return self._post(payload, self.timeout)
        return _default_post(payload, self.timeout, self.environ)

    def refine(self, text: str, deterministic: CodeRequest) -> CodeRequest:
        """Use Jev only for ambiguous fast-path results. Never changes the room."""
        if not deterministic.ambiguous or not self.enabled():
            return deterministic
        try:
            payload = self._evaluate(text)
        except Exception:
            return deterministic
        parsed = _parse_jev(payload, deterministic.mentioned_room)
        if parsed is None:
            return deterministic
        return replace(parsed, mentioned_room=deterministic.mentioned_room, lockout=deterministic.lockout)

    def audit_async(self, text: str, deterministic: CodeRequest) -> Optional[threading.Thread]:
        """Count agreement. Does not block the reply and does not pick a room."""
        if not self.enabled() or deterministic.ambiguous:
            return None

        def work() -> None:
            try:
                payload = self._evaluate(text)
            except Exception:
                return
            parsed = _parse_jev(payload, deterministic.mentioned_room)
            if parsed is None:
                return
            if parsed.kind == deterministic.kind and parsed.is_code_request == deterministic.is_code_request:
                self.agree += 1
            else:
                self.disagree += 1

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread


def classify(text: str, jev: Optional[JevClassifier] = None) -> CodeRequest:
    deterministic = classify_fast(text)
    if jev is None:
        return deterministic
    if deterministic.ambiguous:
        return jev.refine(text, deterministic)
    jev.audit_async(text, deterministic)
    return deterministic


def _default_post(payload: Dict[str, Any], timeout: float, environ: Optional[os._Environ[str]]) -> Any:
    import requests

    env = environ if environ is not None else os.environ
    key = (env.get("AI_GATEWAY_API_KEY") or "").strip()
    response = requests.post(
        JEV_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()
