"""Deterministic code-request classifier plus an optional Jev audit.

Wifi requests are classified and never answered. Jev runs when the fast
path is ambiguous, or when it is not a request but the text is
code-adjacent (a way in, send it again). The call waits at most 3
seconds, then keeps the fast-path result. It never chooses a room.

The HTTP call matches AI SDK experimental_evaluate: POST
https://ai-gateway.vercel.sh/v1/evaluate with model typesafe-ai/jev.
Chat completions cannot run this model. The gate
JEV_CODE_CLASSIFY_ENABLE defaults off and also needs AI_GATEWAY_API_KEY.
CI and collection-only do not call. Failures log a reason only.

Jev receives message text with digits stripped and no names. The request
asks for zero data retention and no prompt training.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Optional, Sequence

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
_SMOKE_TEXT = "is there a way in"

_WIFI_RE = re.compile(r"(?i)\bwi[\s-]?fi\b|\bwireless\b|\binternet\s+password\b")
_LOCKBOX_RE = re.compile(r"(?i)\block\s*box\b|\blockbox\b")
_ROOM_RE = re.compile(r"(?i)\broom\s+code\b|\bbedroom\s+code\b|\bmy\s+room\s+code\b")
_DOOR_RE = re.compile(r"(?i)\bdoor\s+code\b|\bfront\s+door\b|\bback\s+door\b|\bbuilding\s+code\b")
# Typo forms (cod, coed) sit beside code/codigo. Apostrophes are removed before matching.
_CODE_WORD = r"(?:codes?|codigos?|coed|cod)"
_CODE_ASK_RE = re.compile(
    rf"(?i)("
    rf"\b(?:what|whats|wat|wats|cual|cuales)\b.{{0,40}}\b{_CODE_WORD}\b"
    rf"|\b(?:send|give|text|share|need|get|pass)\b.{{0,40}}\b{_CODE_WORD}\b"
    rf"|\b(?:forgot|forget|forgotten|olvido|olvide)\b.{{0,40}}\b{_CODE_WORD}\b"
    rf"|\bse\s+me\s+olvido\b"
    rf"|\blost\b.{{0,24}}\b(?:my\s+)?(?:key|keys)\b"
    rf"|\bnew\s+{_CODE_WORD}\b"
    rf"|\b{_CODE_WORD}\s*(?:pls|please)\b"
    rf"|\b{_CODE_WORD}\s*\?"
    rf"|^\s*{_CODE_WORD}\s*\??\s*$"
    rf"|\b(?:the|my|el|un|our)\s+{_CODE_WORD}\b"
    rf")"
)
# A failed lock, keypad, battery, or an inability to open the door is a lockout.
_ENTRY_FAIL_RE = re.compile(
    r"(?i)("
    r"\blockd\s*out\b"
    r"|\bloked\s*out\b"
    r"|\blocked\s*out\b"
    r"|\blockout\b"
    r"|\bcan(?:t|not)\s+get\s+inn?\b"
    r"|\bcan(?:t|not)\s+open\b"
    r"|\bno\s+puedo\s+entrar\b"
    r"|\bestoy\s+afuera\b"
    r"|\b(?:code|cod|coed|codigo|keypad|lock|door)\b.{0,40}\b(?:isnt|not|doesnt|dont|wont|dead|died|broken)\b"
    r"|\bbattery\b.{0,40}\b(?:lock|keypad|door)\b"
    r"|\b(?:lock|keypad|door)\b.{0,40}\bbattery\b"
    r")"
)
# "who changed the code?" is not a request for the code.
_NOT_REQUEST_RE = re.compile(
    r"(?i)("
    r"\bwho\s+(?:changed|reset|updated|set|gave|has|did)\b"
    r"|\bwhy\s+(?:did|was|is|would)\b.{0,40}\b(?:code|codigo|cod|coed|lock)\b"
    r"|\bwhen\s+(?:did|was)\b.{0,40}\b(?:code|codigo)\b.{0,20}\bchang"
    r")"
)
_THANKS_ONLY_RE = re.compile(r"(?i)^\s*thanks?!?\s*[!.]*\s*$|^\s*thank\s+you\b[!.]*\s*$")
# Not a fast-path hit, but close enough that Jev should look: "way in", "send it again".
_CODE_ADJACENT_RE = re.compile(
    r"(?i)("
    r"\bway\s+in\b"
    r"|\b(?:get|come|let)\s+(?:me\s+)?in\b"
    r"|\bsend\s+it\b"
    r"|\bdoor\b"
    r"|\block\b"
    r"|\bkeypad\b"
    r"|\bkeys?\b"
    r"|\bcod(?:e|igo)?\b"
    r"|\bcoed\b"
    r"|\bentrar\b"
    r"|\bafuera\b"
    r")"
)
_DIGIT_RE = re.compile(r"\d+")
_APOSTROPHE_RE = re.compile(r"[''`´’]")


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


def _fold(text: str) -> str:
    """Lowercase, strip accents, and drop apostrophes so isn't and isnt match."""
    normalized = unicodedata.normalize("NFKD", text or "")
    without_marks = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return _APOSTROPHE_RE.sub("", without_marks)


def classify_fast(text: str) -> CodeRequest:
    """Keyword path. Any code ask or lockout is a request. Lock failures are lockouts.

    A bare ask ("what's my code", "code?", "I forgot my code") stays ambiguous
    so the reply can send this member's room code and the front door when Jev
    is off. Wifi is classified and is not answered on this path.
    """
    body = text or ""
    folded = _fold(body)
    mentioned = lockout_reply.mentioned_room(body)
    if _THANKS_ONLY_RE.search(folded) or _NOT_REQUEST_RE.search(folded):
        return CodeRequest(False, "unknown", mentioned, False, lockout=False)
    wifi = bool(_WIFI_RE.search(folded))
    lockbox = bool(_LOCKBOX_RE.search(folded))
    room = bool(_ROOM_RE.search(folded))
    legacy_lockout = lockout_reply.detect_lockout(body) or lockout_reply.detect_door_fail_followup(body)
    entry_fail = bool(_ENTRY_FAIL_RE.search(folded))
    lockout = legacy_lockout or entry_fail
    door = bool(_DOOR_RE.search(folded)) or lockout
    generic = bool(_CODE_ASK_RE.search(folded))
    kinds = []
    if door:
        kinds.append("door")
    if room:
        kinds.append("room")
    if lockbox:
        kinds.append("lockbox")
    if wifi:
        kinds.append("wifi")
    if wifi and not door and not room and not lockbox and not generic:
        return CodeRequest(True, "wifi", mentioned, False, lockout=False)
    if wifi and (generic or len(kinds) > 1):
        return CodeRequest(True, "unknown", mentioned, True, lockout=lockout)
    if len(kinds) == 1:
        return CodeRequest(True, kinds[0], mentioned, False, lockout=lockout and kinds[0] == "door")
    if len(kinds) > 1:
        return CodeRequest(True, "unknown", mentioned, True, lockout=lockout)
    if generic:
        return CodeRequest(True, "unknown", mentioned, True, lockout=False)
    return CodeRequest(False, "unknown", mentioned, False, lockout=False)


def code_adjacent(text: str) -> bool:
    """True when a non-request still talks about entry, a lock, or sending it."""
    return bool(_CODE_ADJACENT_RE.search(_fold(text)))


def should_consult_jev(fast: CodeRequest, text: str) -> bool:
    """Ambiguous asks, or a miss that is still about getting in."""
    if fast.ambiguous:
        return True
    return (not fast.is_code_request) and code_adjacent(text)


def _questions() -> Dict[str, Any]:
    return {
        "is_code_request": {
            "type": "boolean",
            "instructions": "Is the sender asking for a door, room, or lockbox entry code, or for a way in?",
            "criteria": {
                "true": "They want an entry code, a door opened, or the last code sent again.",
                "false": "They are not asking for an entry code.",
            },
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
    """Same body experimental_evaluate posts to /v1/evaluate. state is a string."""
    return {
        "model": JEV_MODEL,
        "state": strip_for_jev(text),
        "questions": _questions(),
        "providerOptions": {
            "gateway": {
                "zeroDataRetention": True,
                "disallowPromptTraining": True,
            }
        },
    }


class JevError(Exception):
    """Safe failure. reason is a short token and never includes message text."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _log_jev(reason: str) -> None:
    sys.stderr.write(f"[code-request] jev skipped: {reason}\n")


def _safe_reason(exc: BaseException) -> str:
    if isinstance(exc, JevError):
        return exc.reason
    if "timeout" in type(exc).__name__.lower():
        return "timeout"
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return f"http {status}"
    return "request failed"


def _env(environ: Optional[os._Environ[str]]) -> os._Environ[str]:
    return environ if environ is not None else os.environ


def _blocked_reason(environ: Optional[os._Environ[str]]) -> Optional[str]:
    """None when Jev may run. A reason when the flag is on but another gate blocks.

    An unset flag stays silent. That is the default-off path.
    """
    env = _env(environ)
    if runtime.flag_value(env.get("JEV_CODE_CLASSIFY_ENABLE")) is not True:
        return None
    if not (env.get("AI_GATEWAY_API_KEY") or "").strip():
        return "missing AI_GATEWAY_API_KEY"
    if runtime.running_in_ci(env):
        return "ci"
    if runtime.collection_only(env):
        return "collection-only"
    return None


def _enabled(environ: Optional[os._Environ[str]]) -> bool:
    env = _env(environ)
    if runtime.flag_value(env.get("JEV_CODE_CLASSIFY_ENABLE")) is not True:
        return False
    return _blocked_reason(env) is None


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
        """Wait at most 3 seconds. Never changes the room. On failure, keep the fast path."""
        if not should_consult_jev(deterministic, text):
            return deterministic
        if not self.enabled():
            reason = _blocked_reason(self.environ)
            if reason:
                _log_jev(reason)
            return deterministic
        try:
            payload = self._evaluate(text)
        except Exception as exc:
            _log_jev(_safe_reason(exc))
            return deterministic
        parsed = _parse_jev(payload, deterministic.mentioned_room)
        if parsed is None:
            _log_jev("unparsed response")
            return deterministic
        return replace(parsed, mentioned_room=deterministic.mentioned_room, lockout=deterministic.lockout)

    def audit_async(self, text: str, deterministic: CodeRequest) -> Optional[threading.Thread]:
        """Count agreement. Does not block the reply and does not pick a room."""
        if not self.enabled() or not deterministic.is_code_request or deterministic.ambiguous:
            return None

        def work() -> None:
            try:
                payload = self._evaluate(text)
            except Exception as exc:
                _log_jev(_safe_reason(exc))
                return
            parsed = _parse_jev(payload, deterministic.mentioned_room)
            if parsed is None:
                _log_jev("unparsed response")
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
    if should_consult_jev(deterministic, text):
        return jev.refine(text, deterministic)
    if deterministic.is_code_request:
        jev.audit_async(text, deterministic)
    return deterministic


def _default_post(payload: Dict[str, Any], timeout: float, environ: Optional[os._Environ[str]]) -> Any:
    """POST /v1/evaluate. The wall clock stops at timeout even if the socket lingers."""
    import requests

    env = _env(environ)
    key = (env.get("AI_GATEWAY_API_KEY") or "").strip()
    if not key:
        raise JevError("missing AI_GATEWAY_API_KEY")

    def once() -> Any:
        try:
            response = requests.post(
                JEV_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise JevError("timeout") from exc
        except requests.RequestException as exc:
            raise JevError("request failed") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status >= 400:
            raise JevError(f"http {status}")
        try:
            return response.json()
        except ValueError as exc:
            raise JevError("non-json") from exc

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(once).result(timeout=timeout)
    except FuturesTimeout as exc:
        raise JevError("timeout") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def jev_smoke(environ: Optional[os._Environ[str]] = None) -> int:
    """One harmless evaluate call. Prints the parsed flags, never the key or the text."""
    env = _env(environ)
    if not (env.get("AI_GATEWAY_API_KEY") or "").strip():
        sys.stdout.write("jev smoke: missing AI_GATEWAY_API_KEY\n")
        return 2
    try:
        payload = _default_post(jev_payload(_SMOKE_TEXT), JEV_TIMEOUT_S, env)
    except Exception as exc:
        sys.stdout.write(f"jev smoke: failed ({_safe_reason(exc)})\n")
        return 1
    parsed = _parse_jev(payload, "")
    if parsed is None:
        sys.stdout.write("jev smoke: unparsed\n")
        return 1
    sys.stdout.write(
        "jev smoke: "
        f"is_code_request={parsed.is_code_request} "
        f"kind={parsed.kind} "
        f"ambiguous={parsed.ambiguous} "
        f"lockout={parsed.lockout}\n"
    )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Code-request classifier")
    parser.add_argument(
        "--jev-smoke",
        action="store_true",
        help="Send one harmless evaluate request and print the parsed flags",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.jev_smoke:
        parser.print_help(sys.stderr)
        return 2
    return jev_smoke()


if __name__ == "__main__":
    raise SystemExit(main())
