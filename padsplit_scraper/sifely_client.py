"""Read-only Sifely Open API client.

Auth header is the raw SIFELY_API_KEY (no Bearer). This module has no
write endpoints. Lock codes stay in memory for the caller and are never
written to disk.

The account can have more locks than one page of 20. Lock list requests
use pageSize 100 and follow total/pages. The public rate limit is about
30 requests per minute; this client stays at 25/min and backs off on 429.
Lock-list cache defaults to 10 minutes. A single lock's passcodes cache
for 60 seconds.
"""

from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests


SIFELY_BASE = "https://cus-openapi.sifely.com"
LOCK_LIST_PATH = "/v3/lock/list"
PASSCODE_LIST_PATH = "/v3/lock/listKeyboardPwd"
PAGE_SIZE = 100
CONNECT_TIMEOUT_S = 3
READ_TIMEOUT_S = 5
DEFAULT_PER_MINUTE = 25
DEFAULT_LOCK_LIST_TTL_S = 600
DEFAULT_PASSCODE_TTL_S = 60
MAX_PAGES = 10

# Digit-free house labels. Aliases match Sifely lock names and streets.
# Field names are Firestore property_codes keys. No code values live here.
HOUSE_LOCK_PROFILES: Dict[str, Dict[str, Any]] = {
    "leana_6623": {
        "label": "Leana",
        "aliases": ("leana", "leanna"),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "sylvia_2516": {
        "label": "Sylvia",
        "aliases": ("sylvia",),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "ridge_oak_10235": {
        "label": "Ridge Oak",
        "aliases": ("ridge oak", "ridgeoak"),
        "has_front": True,
        "has_back": True,
        "front_field": "front_door",
        "back_field": "back_door",
    },
    "pebbleshores_3414": {
        "label": "Pebbleshores",
        "aliases": ("pebbleshores", "pebble shores", "pebble shore"),
        "has_front": True,
        "has_back": True,
        "front_field": "front_back",
        "back_field": "front_back",
    },
    "greenhill_3406": {
        "label": "Greenhill",
        "aliases": ("greenhill", "green hill"),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "parker_4351": {
        "label": "Parker",
        "aliases": ("parker",),
        "has_front": True,
        "has_back": True,
        "front_field": "front_door",
        "back_field": "back_door",
    },
    "pioneer_1404": {
        "label": "Pioneer",
        "aliases": ("pioneer",),
        "has_front": True,
        "has_back": True,
        "front_field": "front_door",
        "back_field": "back_door",
    },
    "burton_5509": {
        "label": "Burton",
        "aliases": ("burton",),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "broken_crest_1025": {
        "label": "Broken Crest",
        "aliases": ("broken crest", "brokencrest"),
        "has_front": True,
        "has_back": False,
        "front_field": "front_door",
        "back_field": "",
    },
    "spanish_moss": {
        "label": "Spanish Moss",
        "aliases": ("spanish moss", "spanishmoss"),
        "has_front": True,
        "has_back": True,
        "front_field": "front_door",
        "back_field": "back_door",
    },
}

_ROOM_IN_LABEL_RE = re.compile(
    r"(?i)(?:(?:room|rm)\s*[:#-]?\s*(\d{1,2})|\br(\d{1,2})\b)"
)
_MS_THRESHOLD = 10 ** 10


class SifelyUnavailable(RuntimeError):
    """Sifely cannot be read. The message must not include secrets."""


@dataclass
class LockMatch:
    lock_id: str
    slug: str
    role: str
    room: str = ""
    alias: str = ""


@dataclass
class Inventory:
    mapped: List[LockMatch] = field(default_factory=list)
    unmapped: int = 0
    ambiguous: int = 0

    @property
    def unmapped_or_ambiguous(self) -> int:
        return self.unmapped + self.ambiguous


@dataclass
class CurrentCode:
    """In-memory selection. status is ok or ambiguous. Never log code."""

    status: str
    code: str = ""
    reason: str = ""


def compact_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _positive_int(raw: Optional[str], default: int) -> int:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def lock_list_ttl_s(environ: Optional[os._Environ[str]] = None) -> int:
    env = environ if environ is not None else os.environ
    return _positive_int(env.get("SIFELY_LOCK_LIST_CACHE_TTL_S"), DEFAULT_LOCK_LIST_TTL_S)


def passcode_ttl_s(environ: Optional[os._Environ[str]] = None) -> int:
    env = environ if environ is not None else os.environ
    specific = (env.get("SIFELY_PASSCODE_CACHE_TTL_S") or "").strip()
    if specific:
        return _positive_int(specific, DEFAULT_PASSCODE_TTL_S)
    fallback = (env.get("SIFELY_CACHE_TTL_S") or "").strip()
    if fallback:
        return _positive_int(fallback, DEFAULT_PASSCODE_TTL_S)
    return DEFAULT_PASSCODE_TTL_S


def per_minute_limit(environ: Optional[os._Environ[str]] = None) -> int:
    env = environ if environ is not None else os.environ
    return _positive_int(env.get("SIFELY_MAX_PER_MINUTE"), DEFAULT_PER_MINUTE)


class RateLimiter:
    """Space calls to about 25 per minute. Sleep advances a monotonic clock."""

    def __init__(
        self,
        per_minute: int = DEFAULT_PER_MINUTE,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.per_minute = max(1, per_minute)
        self.min_interval = 60.0 / float(self.per_minute)
        self.sleep = sleep
        self.clock = clock
        self._next_at = 0.0

    def wait(self) -> None:
        now = self.clock()
        if now < self._next_at:
            self.sleep(self._next_at - now)
            now = self.clock()
        self._next_at = now + self.min_interval


def match_house_slug(text: str) -> Optional[str]:
    """Unique house slug from a street or lock label. None if ambiguous."""
    hay = str(text or "").lower()
    compact = compact_text(text)
    if not hay and not compact:
        return None
    moss = "spanish" in hay and "moss" in hay
    green = "greenhill" in compact or "green hill" in hay
    if moss and green:
        return None
    hits: List[str] = []
    for slug, profile in HOUSE_LOCK_PROFILES.items():
        aliases: Sequence[str] = profile["aliases"]
        if any(alias in hay or compact_text(alias) in compact for alias in aliases):
            hits.append(slug)
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    return None


def classify_sifely_lock(lock: Dict[str, Any]) -> Optional[LockMatch]:
    """Map one lock label. Unmapped or role-unclear labels return None."""
    if not isinstance(lock, dict):
        return None
    alias = str(lock.get("lockAlias") or "")
    name = str(lock.get("lockName") or "")
    label = f"{alias} {name}".strip()
    slug = match_house_slug(label)
    if not slug:
        return None
    lock_id = str(lock.get("lockId") or "")
    if not lock_id:
        return None
    lowered = label.lower()
    has_front = "front" in lowered
    has_back = "back" in lowered
    room_match = _ROOM_IN_LABEL_RE.search(label)
    if has_front and has_back:
        return LockMatch(lock_id=lock_id, slug=slug, role="shared", alias=alias)
    if has_front:
        return LockMatch(lock_id=lock_id, slug=slug, role="front", alias=alias)
    if has_back:
        return LockMatch(lock_id=lock_id, slug=slug, role="back", alias=alias)
    if room_match:
        room = room_match.group(1) or room_match.group(2) or ""
        if room:
            return LockMatch(lock_id=lock_id, slug=slug, role="room", room=str(int(room)), alias=alias)
    return None


def inventory_locks(locks: Sequence[Dict[str, Any]]) -> Inventory:
    """Confident unique matches only. Duplicates are ambiguous, never guessed."""
    found: List[LockMatch] = []
    unmapped = 0
    for lock in locks:
        if not isinstance(lock, dict):
            unmapped += 1
            continue
        match = classify_sifely_lock(lock)
        if match is None:
            unmapped += 1
        else:
            found.append(match)
    groups: Dict[tuple, List[LockMatch]] = {}
    for item in found:
        groups.setdefault((item.slug, item.role, item.room), []).append(item)
    mapped: List[LockMatch] = []
    ambiguous = 0
    for items in groups.values():
        if len(items) == 1:
            mapped.append(items[0])
        else:
            ambiguous += len(items)
    return Inventory(mapped=mapped, unmapped=unmapped, ambiguous=ambiguous)


def find_lock(
    inventory: Sequence[LockMatch],
    slug: str,
    role: str,
    room: str = "",
) -> Optional[LockMatch]:
    matches: List[LockMatch] = []
    room_key = str(int(room)) if str(room).isdigit() else str(room)
    for item in inventory:
        if item.slug != slug:
            continue
        if role == "room":
            item_room = str(int(item.room)) if str(item.room).isdigit() else str(item.room)
            if item.role == "room" and item_room == room_key:
                matches.append(item)
            continue
        if item.role == role or item.role == "shared":
            matches.append(item)
    if len(matches) == 1:
        return matches[0]
    if role in {"front", "back"}:
        exact = [item for item in matches if item.role == role]
        if len(exact) == 1:
            return exact[0]
        shared = [item for item in matches if item.role == "shared"]
        if len(shared) == 1 and not exact:
            return shared[0]
    return None


def codes_field_for(slug: str, role: str, room: str = "") -> str:
    profile = HOUSE_LOCK_PROFILES.get(slug) or {}
    if role == "room":
        digits = re.sub(r"\D", "", str(room or ""))
        return f"r{digits}" if digits else ""
    if role == "lockbox":
        digits = re.sub(r"\D", "", str(room or ""))
        return f"lockbox_{digits}" if digits else ""
    if role == "front":
        return str(profile.get("front_field") or "front_door")
    if role == "back":
        return str(profile.get("back_field") or "back_door")
    if role == "shared":
        return str(profile.get("front_field") or profile.get("back_field") or "front_back")
    return ""


def _as_epoch_seconds(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None
        if number > _MS_THRESHOLD:
            return number / float(10 ** 3)
        return number
    text = str(value).strip()
    if not text or text in {"0", "None"}:
        return None
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        if number <= 0:
            return None
        if number > _MS_THRESHOLD:
            return number / float(10 ** 3)
        return number
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _status_active(item: Dict[str, Any]) -> bool:
    status = item.get("status")
    if status is None and "keyboardPwdStatus" in item:
        status = item.get("keyboardPwdStatus")
    if isinstance(status, str) and status.strip().isdigit():
        status = int(status.strip())
    return status == 1


def _type_permanent(item: Dict[str, Any]) -> bool:
    kind = item.get("keyboardPwdType")
    if isinstance(kind, str) and kind.strip().isdigit():
        kind = int(kind.strip())
    return kind == 2


def _inside_window(item: Dict[str, Any], now: datetime) -> bool:
    start = _as_epoch_seconds(item.get("startDate"))
    end = _as_epoch_seconds(item.get("endDate"))
    if start is None and end is None:
        return False
    moment = now.timestamp()
    if start is not None and moment < start:
        return False
    if end is not None and moment > end:
        return False
    return True


def select_current_code(passcodes: Sequence[Dict[str, Any]], now: datetime) -> CurrentCode:
    """Exactly one active permanent or in-window code. Otherwise ambiguous."""
    candidates: List[str] = []
    for item in passcodes:
        if not isinstance(item, dict):
            continue
        if not _status_active(item):
            continue
        if not (_type_permanent(item) or _inside_window(item, now)):
            continue
        value = item.get("keyboardPwd")
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
        else:
            candidates.append("")
    usable = [item for item in candidates if item]
    if len(candidates) == 1 and len(usable) == 1:
        return CurrentCode(status="ok", code=usable[0], reason="single current passcode")
    if not candidates:
        return CurrentCode(status="ambiguous", reason="no current passcode")
    return CurrentCode(status="ambiguous", reason="more than one current passcode")


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and payload.get("code") in (None, 0, 200, "0", "200"):
        return payload.get("data")
    return payload


def _page_rows(payload: Any) -> tuple[List[Dict[str, Any]], Optional[int], Optional[int]]:
    data = payload if isinstance(payload, dict) else {}
    rows = data.get("list") if isinstance(data, dict) else payload
    if not isinstance(rows, list):
        rows = []
    clean = [row for row in rows if isinstance(row, dict)]
    total = data.get("total") if isinstance(data, dict) else None
    pages = data.get("pages") if isinstance(data, dict) else None
    try:
        total_n = int(total) if total not in (None, "") else None
    except (TypeError, ValueError):
        total_n = None
    try:
        pages_n = int(pages) if pages not in (None, "") else None
    except (TypeError, ValueError):
        pages_n = None
    return clean, total_n, pages_n


class SifelyClient:
    """In-memory cached reader. No disk cache and no write methods."""

    def __init__(
        self,
        api_key: str,
        *,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
        per_minute: Optional[int] = None,
        lock_list_ttl: Optional[int] = None,
        passcode_ttl: Optional[int] = None,
        environ: Optional[os._Environ[str]] = None,
    ) -> None:
        env = environ if environ is not None else os.environ
        self.api_key = (api_key or "").strip()
        self.session = session if session is not None else requests
        self.sleep = sleep
        self.clock = clock
        self.rng = rng
        self.limiter = RateLimiter(
            per_minute if per_minute is not None else per_minute_limit(env),
            sleep=sleep,
            clock=clock,
        )
        self.lock_list_ttl = lock_list_ttl if lock_list_ttl is not None else lock_list_ttl_s(env)
        self.passcode_ttl = passcode_ttl if passcode_ttl is not None else passcode_ttl_s(env)
        self._cache: Dict[str, tuple[float, Any]] = {}

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": self.api_key, "Accept": "application/json"}

    def _jitter_sleep(self) -> None:
        self.sleep(0.05 + (self.rng() * 0.2))

    def _backoff_sleep(self, attempt: int) -> None:
        # attempt is 1-based. Backoff grows and stays under a minute.
        base = float(2 ** attempt)
        self.sleep(base + self.rng())

    def _request(self, method: str, path: str, params: Dict[str, Any]) -> Any:
        if not self.api_key:
            raise SifelyUnavailable("missing SIFELY_API_KEY")
        url = f"{SIFELY_BASE}{path}"
        transient = 0
        rate_tries = 0
        while True:
            self.limiter.wait()
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
                )
            except requests.Timeout as exc:
                if transient >= 1:
                    raise SifelyUnavailable("timeout") from exc
                transient += 1
                self._jitter_sleep()
                continue
            except requests.RequestException as exc:
                raise SifelyUnavailable("request failed") from exc
            status = int(getattr(response, "status_code", 0) or 0)
            if status == 429 and rate_tries < 3:
                rate_tries += 1
                self._backoff_sleep(rate_tries)
                continue
            if status >= 500 and transient < 1:
                transient += 1
                self._jitter_sleep()
                continue
            if status in (401, 403):
                raise SifelyUnavailable("Sifely rejected SIFELY_API_KEY")
            if status >= 400:
                raise SifelyUnavailable(f"Sifely HTTP {status}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise SifelyUnavailable("Sifely returned a non-JSON body") from exc
            return _unwrap(payload)

    def _cached(self, key: str) -> Any:
        hit = self._cache.get(key)
        if not hit:
            return None
        expires, value = hit
        if self.clock() >= expires:
            self._cache.pop(key, None)
            return None
        return value

    def _store(self, key: str, value: Any, ttl: int) -> None:
        self._cache[key] = (self.clock() + float(ttl), value)

    def list_locks(self, *, use_cache: bool = True) -> List[Dict[str, Any]]:
        if use_cache:
            cached = self._cached("locks")
            if cached is not None:
                return list(cached)
        rows = self._collect_pages(
            "POST",
            LOCK_LIST_PATH,
            {},
            lambda row, page, index: str(row.get("lockId") or f"lock-{page}-{index}"),
        )
        sanitized = [_public_lock(row) for row in rows]
        self._store("locks", sanitized, self.lock_list_ttl)
        return list(sanitized)

    def _collect_pages(
        self,
        method: str,
        path: str,
        params: Dict[str, Any],
        identity: Callable[[Dict[str, Any], int, int], str],
    ) -> List[Dict[str, Any]]:
        """Follow pages and total. A short page is not the end when more remain."""
        collected: List[Dict[str, Any]] = []
        seen: set[str] = set()
        page = 1
        while page <= MAX_PAGES:
            query = dict(params)
            query["pageNo"] = str(page)
            query["pageSize"] = str(PAGE_SIZE)
            payload = self._request(method, path, query)
            rows, total, pages = _page_rows(payload)
            if not rows:
                break
            added = 0
            for index, row in enumerate(rows):
                marker = identity(row, page, index)
                if marker in seen:
                    continue
                seen.add(marker)
                collected.append(row)
                added += 1
            if added == 0:
                break
            if pages is not None and page >= pages:
                break
            if total is not None and len(collected) >= total:
                break
            if pages is None and total is None and len(rows) < PAGE_SIZE:
                break
            page += 1
        return collected

    def list_passcodes(self, lock_id: Any, *, use_cache: bool = True) -> List[Dict[str, Any]]:
        key = f"pwd:{lock_id}"
        if use_cache:
            cached = self._cached(key)
            if cached is not None:
                return list(cached)
        rows = self._collect_pages(
            "GET",
            PASSCODE_LIST_PATH,
            {"lockId": lock_id},
            lambda row, page, index: str(
                row.get("keyboardPwdId") or row.get("id") or f"pwd-{page}-{index}"
            ),
        )
        self._store(key, rows, self.passcode_ttl)
        return list(rows)

    def current_code(self, lock_id: Any, now: datetime, *, use_cache: bool = True) -> CurrentCode:
        try:
            rows = self.list_passcodes(lock_id, use_cache=use_cache)
        except SifelyUnavailable:
            raise
        return select_current_code(rows, now)


def _public_lock(item: Dict[str, Any]) -> Dict[str, Any]:
    """Drop PIN-bearing fields such as noKeyPwd."""
    return {
        "lockId": item.get("lockId"),
        "lockAlias": item.get("lockAlias"),
        "lockName": item.get("lockName"),
    }
