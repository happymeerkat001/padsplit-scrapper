"""Sanitize snapshots before they can be published or committed.

``sanitize_published_snapshot`` is applied in the persist write path, before
``padsplit_scraper/output/latest.json`` is written. CI copies that file to
``docs/data/latest.json``. Stripping here means codes never land in ``docs/``
or in the scrape commit.

No private ``room_code`` sidecar is written. Nothing in this repo reads task
``room_code`` from a snapshot. Lockout replies load Firestore property fields
(``r{n}`` / ``lockbox_{n}``), not this file. ``output/latest.json`` stays
gitignored. ``scrape.yml`` adds only ``docs/data/latest.json`` and
``docs/data/occupancy.json``. Timestamped ``output/202*.json`` files are
sanitized too and are gitignored.

Heuristic
---------
``room_code`` keys are removed everywhere. ``has_reused_code`` is kept.

Free-text fields (``text``, ``body``, ``details``, ``description``,
``comment``) are scanned for access keywords:

    door, room, gate, lockbox, keypad, code, pin, wifi / wi-fi, ssid,
    password, passcode

A secret token is:

* a 3–16 digit run, or
* a password-like token: 4–32 characters with both a letter and a digit, or
  (only beside password / passcode / pin / wifi / ssid) a letter token of 8+
  characters, or a short CamelCase / underscored / hyphenated token.

The keyword and token must sit within 48 characters (32 for letter-only
password tokens). The whole span covering both, including the short glue
between them, is replaced with ``[redacted]``. Prose with a keyword and no
nearby token is left alone, so "the door is broken" and "Room 4 needs paint"
stay. One- and two-digit room labels stay. Dates (``YYYY-MM-DD``,
``MM/DD/YYYY``, month names, "in 2024"), ``$`` prices, and phone numbers
(``(214) 555-0100``, ``214-555-0100``, ``+1 214 555 0100``) are not tokens.

A 3+ digit run next to "room" is treated as a code, so a street number in
that window can be redacted. That is intentional over-redaction.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Sequence, Tuple

TEXT_FIELDS = frozenset({"text", "body", "details", "description", "comment"})
REDACTION = "[redacted]"
DIGIT_WINDOW = 48
PASSWORD_WINDOW = 32

_KEYWORD_RE = re.compile(
    r"(?i)\b(?:lockboxes|lockbox|keypads|keypad|passcodes|passcode|passwords|password|"
    r"wi-fi|wifi|ssids|ssid|doors|door|gates|gate|rooms|room|codes|code|pins|pin)\b"
)
_PASSWORD_KEYWORD_RE = re.compile(
    r"(?i)\b(?:passcodes|passcode|passwords|password|wi-fi|wifi|ssids|ssid|pins|pin)\b"
)
_DATE_RE = re.compile(
    r"(?i)(?<!\d)(?:"
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|(?:in|since|year|during|from|until|before|after|on)\s+(?:19|20)\d{2}"
    r")(?!\d)"
)
_PRICE_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\$\s?\d+(?:\.\d{2})?")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"
)
_DIGIT_RE = re.compile(r"(?<!\d)\d{3,16}(?!\d)")
_MIXED_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)"
    r"[A-Za-z0-9][A-Za-z0-9@#$!%*_.\-]{3,31}(?![A-Za-z0-9])"
)
_LETTER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_\-]{7,31}(?![A-Za-z0-9])")
_CAMEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][a-z0-9]*[A-Z][A-Za-z0-9_\-]{1,28}(?![A-Za-z0-9])"
)
_SMASHED_RE = re.compile(
    r"(?i)\b(?:lockboxes|lockbox|keypads|keypad|passcodes|passcode|passwords|password|"
    r"wi-fi|wifi|ssids|ssid|doors|door|gates|gate|rooms|room|codes|code|pins|pin)"
    r"[\s:=\-#]*\d{3,16}\b"
)
_PROSE_WORDS = frozenset(
    {
        "tomorrow",
        "yesterday",
        "internet",
        "password",
        "passwords",
        "passcode",
        "passcodes",
        "network",
        "networks",
        "connection",
        "broken",
        "working",
        "hallway",
        "kitchen",
        "bathroom",
        "bedroom",
        "please",
        "thanks",
        "something",
        "anything",
        "nothing",
        "already",
        "because",
        "through",
        "should",
        "would",
        "could",
        "member",
        "tenant",
        "someone",
        "anyone",
        "everyone",
        "another",
        "problem",
        "maintenance",
        "schedule",
        "scheduled",
        "appointment",
        "without",
        "within",
        "before",
        "after",
        "during",
        "available",
        "replaced",
        "replacement",
        "flooding",
        "emergency",
        "messages",
        "message",
        "outside",
        "inside",
        "contact",
        "calling",
        "unlocked",
        "morning",
        "evening",
        "afternoon",
        "tonight",
    }
)

Span = Tuple[int, int]


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    password_only: bool
    window: int


@dataclass(frozen=True)
class Violation:
    """A published-snapshot problem. ``path`` never includes the value."""

    kind: str
    path: str


def redact_sensitive_text(text: str) -> str:
    """Replace keyword-plus-token phrases with ``[redacted]``."""
    if not isinstance(text, str) or not text:
        return text
    spans = _sensitive_spans(text)
    if not spans:
        return text
    pieces: List[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        pieces.append(REDACTION)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def sanitize_published_snapshot(payload: Any) -> Any:
    """Deep-copy ``payload``, drop ``room_code``, and redact free text.

    The caller's object is not modified, so in-memory occupancy and KPI
    calculations can still see the scrape that just finished.
    """
    cloned = copy.deepcopy(payload)
    _sanitize_inplace(cloned)
    return cloned


def find_violations(payload: Any) -> List[Violation]:
    """Return room_code keys and still-sensitive free-text paths. No values."""
    found: List[Violation] = []
    _collect_violations(payload, "$", found)
    return found


def collapsed_counts(violations: Sequence[Violation]) -> List[Tuple[str, str, int]]:
    """Aggregate violations as ``(kind, path-with-[] , count)`` sorted."""
    counts = {}
    for item in violations:
        key = (item.kind, _collapse_indexes(item.path))
        counts[key] = counts.get(key, 0) + 1
    return [(kind, path, counts[(kind, path)]) for kind, path in sorted(counts)]


def format_violation_report(label: str, violations: Sequence[Violation]) -> str:
    """Counts and paths only. Safe to print for a real snapshot."""
    room = sum(1 for item in violations if item.kind == "room_code")
    sensitive = sum(1 for item in violations if item.kind == "sensitive_text")
    lines = [
        f"path: {label}",
        f"room_code_keys: {room}",
        f"sensitive_texts: {sensitive}",
    ]
    for kind, path, count in collapsed_counts(violations):
        lines.append(f"{kind} {path} {count}")
    return "\n".join(lines) + "\n"


def _sanitize_inplace(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("room_code", None)
        for key, value in list(node.items()):
            if key in TEXT_FIELDS and isinstance(value, str):
                node[key] = redact_sensitive_text(value)
            else:
                _sanitize_inplace(value)
    elif isinstance(node, list):
        for item in node:
            _sanitize_inplace(item)


def _collect_violations(node: Any, path: str, found: List[Violation]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key == "room_code":
                found.append(Violation("room_code", child))
            if key in TEXT_FIELDS and isinstance(value, str) and _sensitive_spans(value):
                found.append(Violation("sensitive_text", child))
            _collect_violations(value, child, found)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _collect_violations(item, f"{path}[{index}]", found)


def _sensitive_spans(text: str) -> List[Span]:
    protected = _merged(
        [(match.start(), match.end()) for pattern in (_DATE_RE, _PRICE_RE, _PHONE_RE) for match in pattern.finditer(text)],
        text,
    )
    keywords = [match for match in _KEYWORD_RE.finditer(text)]
    tokens: List[_Token] = []
    for match in _DIGIT_RE.finditer(text):
        if not _overlaps(match.start(), match.end(), protected):
            tokens.append(_Token(match.start(), match.end(), False, DIGIT_WINDOW))
    for match in _MIXED_TOKEN_RE.finditer(text):
        if not _overlaps(match.start(), match.end(), protected):
            tokens.append(_Token(match.start(), match.end(), False, DIGIT_WINDOW))
    for match in list(_LETTER_TOKEN_RE.finditer(text)) + list(_CAMEL_TOKEN_RE.finditer(text)):
        word = match.group()
        if word.lower() in _PROSE_WORDS or _KEYWORD_RE.fullmatch(word):
            continue
        if not _overlaps(match.start(), match.end(), protected):
            tokens.append(_Token(match.start(), match.end(), True, PASSWORD_WINDOW))

    spans: List[Span] = []
    for match in _SMASHED_RE.finditer(text):
        if not _overlaps(match.start(), match.end(), protected):
            spans.append((match.start(), match.end()))
    for keyword in keywords:
        keyword_is_password = bool(_PASSWORD_KEYWORD_RE.fullmatch(keyword.group()))
        for token in tokens:
            if token.password_only and not keyword_is_password:
                continue
            if token.end <= keyword.start():
                gap = keyword.start() - token.end
                if gap <= token.window:
                    spans.append((token.start, keyword.end()))
            elif keyword.end() <= token.start:
                gap = token.start - keyword.end()
                if gap <= token.window:
                    spans.append((keyword.start(), token.end))
            else:
                spans.append((min(keyword.start(), token.start), max(keyword.end(), token.end)))
    return _merged(spans, text)


def _overlaps(start: int, end: int, spans: Sequence[Span]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _merged(spans: Iterable[Span], text: str) -> List[Span]:
    ordered = sorted(spans)
    if not ordered:
        return []
    merged: List[List[int]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        gap = text[merged[-1][1] : start]
        if start <= merged[-1][1] or gap.strip() == "":
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _collapse_indexes(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path)
