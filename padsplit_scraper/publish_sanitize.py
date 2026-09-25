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
``comment``) are scanned for access keywords, matched on word boundaries:

    door, room, gate, lockbox, keypad, code, pin, wifi / wi-fi, ssid,
    password, passcode, lock, key, combo, combination, access, entry,
    unlock, pw, pwd, network

``key`` does not match ``keyboard`` or ``monkey``. ``lock`` does not match
``clock``, ``locked``, or ``lockbox`` (``lockbox`` is its own keyword).

A secret token is:

* a 3–16 digit run, or
* a password-like token: 4–32 characters with both a letter and a digit, or
  (only beside password / passcode / pin / wifi / ssid / pw / pwd) a letter
  token of 8+ characters, or a short CamelCase / underscored / hyphenated
  token, or
* beside ``pw`` / ``pwd`` only, a following letter token of 3+ characters
  (``pw xyz``). ``network``, ``lock``, ``key``, ``combo``, ``access``,
  ``entry``, and ``unlock`` pair with digit runs and mixed letter-digit
  tokens, not with ordinary words.

The keyword and token must sit within 48 characters (32 for letter-only
password tokens). The whole span covering both, including the short glue
between them, is replaced with ``[code hidden, see ops page]``. Prose with a
keyword and no nearby token is left alone, so "the door is broken" and
"Room 4 needs paint" stay. One- and two-digit room labels stay. Dates
(``YYYY-MM-DD``, ``MM/DD/YYYY``, month names, "in 2024"), ISO timestamps
(including fractional seconds), colon times (``10:30``), ``$`` prices, phone
numbers, and ``http(s)`` URLs are not tokens.

Message body fields (``text``, ``body``) and any other non-allowlisted string
also lose a standalone 4–8 digit run that is not one of those protected
forms (``use 424242 to get in``). The cleaner still limits keyword redaction
to the free-text field list above. Bare-number redaction follows the checker's
allowlist so a sanitized snapshot can pass the full-string scan.

A 4–5 digit run that begins a street address is not a bare number. It stays
only when a street suffix is the next word, or the next word after one or
two name words. ``way`` is not a suffix. Other suffixes (case-insensitive,
optional period): st, street, ave, avenue, rd, road, dr, drive, ln, lane,
blvd, boulevard, ct, court, pl, place, pkwy, parkway, cir, circle, trl,
trail, hwy, highway, loop.

``st`` and ``ct`` count only as abbreviations, not as the start of a longer
word or an ellipsis. They match ``st.`` / ``ct.`` when the period is followed
by whitespace or the end of the string, or ``st`` / ``ct`` at the end of a
line (end of the string or just before a newline). ``1234 main st`` and
``1234 main st. tomorrow`` stay. ``1234 main st tomorrow`` does not, because
``st`` is mid-sentence with no period. ``st...`` does not either: the first
period is followed by another period, not whitespace or the end, so an
ellipsis is not treated as ``st.``. A comma after ``st`` does not count.

A name word is not a function or direction-instruction word: a, an, the, on,
in, to, at, of, by, for, from, with, into, onto, and, or, but, then, left,
right, first, second, third, next, turn, go, straight, after, before, off,
out, up, down, over, under, use, punch, enter. Compass letters N, S, E, and
W (optional period) are name words. ``1234 main st`` and ``1234 N Oak Dr.``
keep the number. ``use 1234 on the way in``, ``punch 1234 first st...``, and
``use 1234 then left on elm dr`` do not. A 6–8 digit run before a suffix is
still removed. The keyword rule still wins, so ``door code 1234`` and
``door code 1234 main st`` are redacted.

The replacement text itself is masked before either scan. The word ``code``
inside ``[code hidden, see ops page]`` is not a keyword, and a second pass
does not flag or rewrite the placeholder.

Edge cases, accepted on purpose:

* A zip or other 4–8 digit run in message text that is not a street number
  is redacted. The same digits in ``street1``, ``street2``, ``zip``, or
  ``address`` stay because those keys are allowlisted.
* A 4-digit year with date wording (``in 2024``, ``2026-09-01``) stays. A
  bare year with no date wording in message text can be redacted; it is
  indistinguishable from a 4-digit code.
* ``10:30`` stays. ``1430`` with no colon can be redacted.
* ``(214) 555-0100``, ``214-555-0100``, and ``+1 214 555 0100`` stay,
  including the last four digits.
* A 3+ digit run next to "room" is treated as a code, so a street number in
  that window can be redacted even when a suffix follows.

Bare-number allowlist (key name, unless noted as a path). Keyword+token
rules still apply on these strings. The list is only ids, timestamps, counts,
room numbers, prices, phones, address parts, and url/media fields:

    id, pk, property_id, occupancy_id, user_id, role_id, psproperty_id,
    created, modified, scraped_at, seenAt, update_status_at, started_at,
    finished_at, run_scraped_at, moveInDate, moveOutDate, move_out_date,
    room_number, roomNumber, room_status, moveout_photos_count,
    days_to_complete, days_in_current_status,
    base_price, last_room_price, new_price, recommended_price,
    metro_area_average_price, zip, street1, street2, full_street, address,
    phone, phone_number, mobile,
    picture, preferredPicture, url, cover, filename
    paths ending in attachments[].location or media[].location

Ticket ``location`` is not allowlisted. Attachment and media locations are
file paths whose ids are not door codes.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Sequence, Tuple

TEXT_FIELDS = frozenset({"text", "body", "details", "description", "comment"})
MESSAGE_BODY_FIELDS = frozenset({"text", "body"})
REDACTION = "[code hidden, see ops page]"
DIGIT_WINDOW = 48
PASSWORD_WINDOW = 32

# Longer forms first so "lockbox" wins over "lock" and "pwd" wins over "pw".
_KEYWORD_PARTS = (
    "lockboxes",
    "lockbox",
    "keypads",
    "keypad",
    "passcodes",
    "passcode",
    "passwords",
    "password",
    "combinations",
    "combination",
    "combos",
    "combo",
    "wi-fi",
    "wifi",
    "ssids",
    "ssid",
    "networks",
    "network",
    "doors",
    "door",
    "gates",
    "gate",
    "rooms",
    "room",
    "codes",
    "code",
    "pins",
    "pin",
    "unlocks",
    "unlock",
    "locks",
    "lock",
    "entries",
    "entry",
    "access",
    "keys",
    "key",
    "pwds",
    "pwd",
    "pw",
)
_PASSWORD_PARTS = (
    "passcodes",
    "passcode",
    "passwords",
    "password",
    "wi-fi",
    "wifi",
    "ssids",
    "ssid",
    "pins",
    "pin",
    "pwds",
    "pwd",
    "pw",
)
_KEYWORD_RE = re.compile(r"(?i)\b(?:%s)\b" % "|".join(_KEYWORD_PARTS))
_PASSWORD_KEYWORD_RE = re.compile(r"(?i)\b(?:%s)\b" % "|".join(_PASSWORD_PARTS))
_DATE_RE = re.compile(
    r"(?i)(?<!\d)(?:"
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|(?:in|since|year|during|from|until|before|after|on)\s+(?:19|20)\d{2}"
    r")(?!\d)"
)
_TIME_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?!\d)")
_PRICE_RE = re.compile(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\$\s?\d+(?:\.\d{2})?")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_DIGIT_RE = re.compile(r"(?<!\d)\d{3,16}(?!\d)")
_BARE_DIGIT_RE = re.compile(r"(?<!\d)\d{4,8}(?!\d)")
# Longer suffixes first so "street" wins over "st" and "drive" wins over "dr".
# "way" is omitted on purpose: "on the way" is not an address.
_STREET_SUFFIX_PARTS = (
    "boulevard",
    "parkway",
    "highway",
    "street",
    "avenue",
    "circle",
    "trail",
    "court",
    "place",
    "drive",
    "pkwy",
    "blvd",
    "lane",
    "road",
    "loop",
    "hwy",
    "trl",
    "cir",
    "ave",
    "rd",
    "dr",
    "ln",
    "pl",
)
# Not name words. Compass N/S/E/W are absent so they still count.
_STREET_FUNCTION_WORDS = (
    "first",
    "second",
    "third",
    "straight",
    "before",
    "after",
    "enter",
    "punch",
    "right",
    "under",
    "left",
    "then",
    "into",
    "onto",
    "from",
    "with",
    "over",
    "down",
    "next",
    "turn",
    "and",
    "the",
    "for",
    "but",
    "off",
    "out",
    "use",
    "on",
    "in",
    "to",
    "at",
    "of",
    "by",
    "or",
    "an",
    "up",
    "go",
    "a",
)
# st/ct only: "st." / "ct." then whitespace or end, or the abbreviation at
# end of line. A following period that continues ("st...") is not a match.
_STREET_SHORT_SUFFIX = r"(?:st|ct)(?:\.(?=\s|$)|(?=\r?\n|$))"
_STREET_NAME_WORD = r"(?!(?:%s)\b)[A-Za-z][A-Za-z0-9'-]*\.?" % "|".join(_STREET_FUNCTION_WORDS)
_STREET_SUFFIX = r"(?:(?:%s)\b\.?|%s)" % ("|".join(_STREET_SUFFIX_PARTS), _STREET_SHORT_SUFFIX)
_STREET_ADDRESS_RE = re.compile(
    r"(?i)(?<!\d)(?P<number>\d{4,5})(?!\d)(?:\s+%s){0,2}\s+%s"
    % (_STREET_NAME_WORD, _STREET_SUFFIX)
)
_MIXED_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)"
    r"[A-Za-z0-9][A-Za-z0-9@#$!%*_.\-]{3,31}(?![A-Za-z0-9])"
)
_LETTER_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9_\-]{7,31}(?![A-Za-z0-9])")
_CAMEL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][a-z0-9]*[A-Z][A-Za-z0-9_\-]{1,28}(?![A-Za-z0-9])"
)
_SMASHED_RE = re.compile(r"(?i)\b(?:%s)[\s:=\-#]*\d{3,16}\b" % "|".join(_KEYWORD_PARTS))
_PW_TOKEN_RE = re.compile(
    r"(?i)\b(?:pwds|pwd|pw)\b[\s:=\-#]*[A-Za-z][A-Za-z0-9_\-]{2,31}"
)

# Bare 4–8 digit runs are legitimate in these keys. Keyword+token still applies.
BARE_NUMBER_KEY_ALLOWLIST = frozenset(
    {
        "id",
        "pk",
        "property_id",
        "occupancy_id",
        "user_id",
        "role_id",
        "psproperty_id",
        "created",
        "modified",
        "scraped_at",
        "seenAt",
        "update_status_at",
        "started_at",
        "finished_at",
        "run_scraped_at",
        "moveInDate",
        "moveOutDate",
        "move_out_date",
        "room_number",
        "roomNumber",
        "room_status",
        "moveout_photos_count",
        "days_to_complete",
        "days_in_current_status",
        "base_price",
        "last_room_price",
        "new_price",
        "recommended_price",
        "metro_area_average_price",
        "zip",
        "street1",
        "street2",
        "full_street",
        "address",
        "phone",
        "phone_number",
        "mobile",
        "picture",
        "preferredPicture",
        "url",
        "cover",
        "filename",
    }
)
_BARE_NUMBER_PATH_ALLOWLIST = (
    re.compile(r"attachments\[\]\.location$"),
    re.compile(r"media\[\]\.location$"),
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


def redact_sensitive_text(text: str, *, bare_numbers: bool = False, keywords: bool = True) -> str:
    """Replace keyword-plus-token phrases with ``[code hidden, see ops page]``.

    When ``bare_numbers`` is true, also replace a standalone 4–8 digit run
    that is not a date, time, price, phone number, URL, or 4–5 digit street
    number. ``keywords`` stays on for the free-text field list and off when
    a structural field only needs bare-number handling.
    """
    if not isinstance(text, str) or not text:
        return text
    spans: List[Span] = list(_sensitive_spans(text)) if keywords else []
    if bare_numbers:
        spans = _merged(spans + _bare_number_spans(text), text)
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


def bare_number_allowed(key: str, path: str) -> bool:
    """True when a 4–8 digit run in this field is structural, not a code."""
    if key in BARE_NUMBER_KEY_ALLOWLIST:
        return True
    collapsed = _collapse_indexes(path)
    return any(pattern.search(collapsed) for pattern in _BARE_NUMBER_PATH_ALLOWLIST)


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
    bare = sum(1 for item in violations if item.kind == "bare_number")
    lines = [
        f"path: {label}",
        f"room_code_keys: {room}",
        f"sensitive_texts: {sensitive}",
        f"bare_numbers: {bare}",
    ]
    for kind, path, count in collapsed_counts(violations):
        lines.append(f"{kind} {path} {count}")
    return "\n".join(lines) + "\n"


def _sanitize_inplace(node: Any, path: str = "$") -> None:
    if isinstance(node, dict):
        node.pop("room_code", None)
        for key, value in list(node.items()):
            child = f"{path}.{key}"
            if isinstance(value, str):
                node[key] = _redact_field(key, child, value)
            else:
                _sanitize_inplace(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _sanitize_inplace(item, f"{path}[{index}]")


def _redact_field(key: str, path: str, value: str) -> str:
    """Keyword redaction stays on the free-text field list.

    Bare 4–8 digit runs are removed from every string the checker would
    flag, which is every string whose key or path is not allowlisted.
    Message bodies are in that set (``text``, ``body``).
    """
    keywords = key in TEXT_FIELDS
    bare = not bare_number_allowed(key, path)
    if not keywords and not bare:
        return value
    if keywords:
        return redact_sensitive_text(value, bare_numbers=bare, keywords=True)
    if bare and _bare_number_spans(value):
        return redact_sensitive_text(value, bare_numbers=True, keywords=False)
    return value


def _collect_violations(node: Any, path: str, found: List[Violation]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key == "room_code":
                found.append(Violation("room_code", child))
            if isinstance(value, str):
                _collect_string_violations(key, child, value, found)
            else:
                _collect_violations(value, child, found)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _collect_violations(item, f"{path}[{index}]", found)
    elif isinstance(node, str):
        _collect_string_violations("", path, node, found)


def _collect_string_violations(key: str, path: str, value: str, found: List[Violation]) -> None:
    """Keyword+token on every string. Bare numbers except the allowlist."""
    if _sensitive_spans(value):
        found.append(Violation("sensitive_text", path))
    if not bare_number_allowed(key, path) and _bare_number_spans(value):
        found.append(Violation("bare_number", path))


def _protected_spans(text: str) -> List[Span]:
    patterns = (_DATE_RE, _TIME_RE, _PRICE_RE, _PHONE_RE, _URL_RE)
    return _merged(
        [(match.start(), match.end()) for pattern in patterns for match in pattern.finditer(text)],
        text,
    )


def _mask_redactions(text: str) -> str:
    """Hide the placeholder so its word ``code`` is not a keyword."""
    if REDACTION not in text:
        return text
    return text.replace(REDACTION, " " * len(REDACTION))


def _street_number_spans(text: str) -> List[Span]:
    """4–5 digit runs that begin a street address. Keyword rules ignore these."""
    return [(match.start("number"), match.end("number")) for match in _STREET_ADDRESS_RE.finditer(text)]


def _bare_number_spans(text: str) -> List[Span]:
    masked = _mask_redactions(text)
    protected = _protected_spans(masked)
    street = _street_number_spans(masked)
    return [
        (match.start(), match.end())
        for match in _BARE_DIGIT_RE.finditer(masked)
        if not _overlaps(match.start(), match.end(), protected)
        and not _overlaps(match.start(), match.end(), street)
    ]


def _sensitive_spans(text: str) -> List[Span]:
    masked = _mask_redactions(text)
    protected = _protected_spans(masked)
    keywords = [match for match in _KEYWORD_RE.finditer(masked)]
    tokens: List[_Token] = []
    for match in _DIGIT_RE.finditer(masked):
        if not _overlaps(match.start(), match.end(), protected):
            tokens.append(_Token(match.start(), match.end(), False, DIGIT_WINDOW))
    for match in _MIXED_TOKEN_RE.finditer(masked):
        if not _overlaps(match.start(), match.end(), protected):
            tokens.append(_Token(match.start(), match.end(), False, DIGIT_WINDOW))
    for match in list(_LETTER_TOKEN_RE.finditer(masked)) + list(_CAMEL_TOKEN_RE.finditer(masked)):
        word = match.group()
        if word.lower() in _PROSE_WORDS or _KEYWORD_RE.fullmatch(word):
            continue
        if not _overlaps(match.start(), match.end(), protected):
            tokens.append(_Token(match.start(), match.end(), True, PASSWORD_WINDOW))

    spans: List[Span] = []
    for match in _SMASHED_RE.finditer(masked):
        if not _overlaps(match.start(), match.end(), protected):
            spans.append((match.start(), match.end()))
    for match in _PW_TOKEN_RE.finditer(masked):
        token = re.search(r"[A-Za-z][A-Za-z0-9_\-]{2,31}$", match.group())
        if token and token.group().lower() in _PROSE_WORDS:
            continue
        if token and token.group().lower() in {"the", "and", "for", "you", "your", "this", "that", "with", "from"}:
            continue
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
