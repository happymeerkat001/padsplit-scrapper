#!/usr/bin/env python3
"""Monthly PadSplit SEO / vacancy advice pack (1st, America/Chicago).

Prefers a live host/rooms + occupancy refresh. Falls back to on-disk stats
only when live fetch fails, and says so in the report. Never changes prices,
Instant Book, or bookings. CI must not Discord-post.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

try:
    from padsplit_scraper import scraper
    from padsplit_scraper.kpis import _room_property_label, _to_num
    from padsplit_scraper.occupancy import (
        CHICAGO,
        _normalize_room,
        _normalize_street,
        compute_occupancy,
    )
except ModuleNotFoundError:  # python3 padsplit_scraper/seo_monthly.py
    import scraper  # type: ignore
    from kpis import _room_property_label, _to_num  # type: ignore
    from occupancy import (  # type: ignore
        CHICAGO,
        _normalize_room,
        _normalize_street,
        compute_occupancy,
    )


CT = CHICAGO
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
LOG_DIR = ROOT_DIR / "logs"
LAUNCH_AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCHD_LABEL = "com.padsplit.seo-monthly"

DISCORD_API_BASE = "https://discord.com/api/v10"
# Liaison Ops #ai-tasks-temp (same channel as field MMS / task digest).
DISCORD_TASKS_CHANNEL_ID = "1540475874955231343"
DEFAULT_TIMEOUT = (10, 30)
DISCORD_MESSAGE_LIMIT = 2000

LISTED_STATUSES = {"listed"}
PRICE_HIGH = "High"
PRICE_VERY_HIGH = "Very High"
PRICE_STATUS_MAP = {
    "high": PRICE_HIGH,
    "very-high": PRICE_VERY_HIGH,
    "very_high": PRICE_VERY_HIGH,
    "veryhigh": PRICE_VERY_HIGH,
    "too-high": PRICE_VERY_HIGH,
    "too_high": PRICE_VERY_HIGH,
    "toohigh": PRICE_VERY_HIGH,
}
# Standing locks — already on / skip. Do not recommend turning these on.
STANDING_LOCKS = (
    "10% promo is already on",
    "Most $0 move-in specials are already on",
    "Instant Book = skip (do not enable; do not recommend)",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:front\s+)?(?:door|room|gate|lock|entry|garage)\s+codes?\b[^.\n]*"),
    re.compile(r"(?i)\bcodes?\s*[:#]\s*\S+"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"(?i)\b(?:ssn|social\s+security)\b[^.\n]*"),
    re.compile(r"(?i)\b(?:id|driver(?:'s)?\s+license|passport)\s+photos?\b[^.\n]*"),
)
_CINDY_RE = re.compile(r"(?i)\bcindy\b")


@dataclass
class DataBundle:
    rooms: List[Dict[str, Any]]
    occupancy_rooms: List[Dict[str, Any]]
    source: str  # live | stale_fallback
    live_fetch_failed: bool = False
    fallback_reason: str = ""
    scraped_at: str = ""


@dataclass
class StaleRoom:
    property_label: str
    room_number: Any
    days_listed: Optional[float]
    days_vacant: Optional[int]
    listed: bool
    vacant: bool
    occupant_present: Optional[bool]
    rent_ready: Optional[bool]
    seo_eligible: Optional[bool]
    base_price: float
    recommended_price: Optional[float]
    price_status: Optional[str]
    price_status_raw: str
    cover_missing: bool
    landscape_detectable: bool
    move_out_photos: Optional[int]
    presence_conflict: bool
    room_id: Any = None
    property_id: Any = None


@dataclass
class AdvicePack:
    month_label: str
    generated_at: str
    source: str
    live_fetch_failed: bool
    fallback_reason: str
    stale_rooms: List[StaleRoom] = field(default_factory=list)
    pricing_outliers: List[StaleRoom] = field(default_factory=list)
    photo_gaps: List[str] = field(default_factory=list)
    landscape_note: str = ""
    markdown: str = ""
    joe_lines: List[str] = field(default_factory=list)


def load_environment() -> None:
    load_dotenv(ENV_PATH)


def running_in_ci() -> bool:
    return bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))


def posting_allowed(*, ci: Optional[bool] = None) -> bool:
    """CI must never post. Live post is the Mac LaunchAgent only."""
    if running_in_ci() if ci is None else ci:
        return False
    flag = (os.getenv("SEO_MONTHLY_DISCORD_ENABLE") or "").strip().lower()
    if flag in {"0", "false", "no"}:
        return False
    if sys.platform == "darwin":
        return True
    return flag in {"1", "true", "yes"}


def sanitize_text(text: str) -> str:
    cleaned = text or ""
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned


def joe_mention() -> str:
    user_id = (os.getenv("DISCORD_JOE_USER_ID") or "").strip()
    if user_id.isdigit():
        return f"<@{user_id}>"
    return "@Joe"


def normalize_price_status(value: Any) -> Optional[str]:
    raw = str(value or "").strip().lower().replace(" ", "-")
    return PRICE_STATUS_MAP.get(raw)


def _room_key(address: Any, room_number: Any) -> Tuple[str, Optional[str]]:
    if isinstance(address, dict):
        street = address.get("full_street") or address.get("street1") or ""
    else:
        street = address or ""
    return (_normalize_street(street), _normalize_room(room_number))


def _occupancy_index(rows: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, Optional[str]], Dict[str, Any]]:
    index: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        index[_room_key(row.get("address"), row.get("room_number"))] = row
    return index


def _has_cover(room: Dict[str, Any]) -> bool:
    cover = room.get("cover")
    if not isinstance(cover, dict):
        return False
    location = str(cover.get("location") or cover.get("url") or "").strip()
    return bool(location)


def _cover_has_orientation(room: Dict[str, Any]) -> bool:
    cover = room.get("cover") if isinstance(room.get("cover"), dict) else {}
    for key in ("orientation", "aspect", "aspect_ratio", "width", "height", "is_landscape"):
        if cover.get(key) not in (None, ""):
            return True
    return False


def listed_or_vacant_over_14(
    rooms: Sequence[Dict[str, Any]],
    occupancy_rooms: Sequence[Dict[str, Any]],
) -> List[StaleRoom]:
    """Listed-status or presence-vacant rooms older than 14 Chicago days."""
    occ = _occupancy_index(occupancy_rooms)
    seen: set[Tuple[str, Optional[str]]] = set()
    rows: List[StaleRoom] = []

    def add(room: Dict[str, Any], occ_row: Optional[Dict[str, Any]], *, listed: bool, vacant: bool) -> None:
        key = _room_key(
            room.get("address") or room.get("property") or (occ_row or {}).get("address"),
            room.get("room_number") if room.get("room_number") not in (None, "") else (occ_row or {}).get("room_number"),
        )
        if key in seen:
            return
        days_listed = _to_num(room.get("days_in_current_status")) if listed else 0.0
        days_vacant_raw = (occ_row or {}).get("days_vacant")
        days_vacant = int(days_vacant_raw) if isinstance(days_vacant_raw, (int, float)) and not isinstance(days_vacant_raw, bool) else None
        over_listed = listed and days_listed > 14
        over_vacant = vacant and days_vacant is not None and days_vacant > 14
        if not (over_listed or over_vacant):
            return
        seen.add(key)
        occupant_present = (occ_row or {}).get("occupant_present")
        presence_conflict = bool(listed and occupant_present is True)
        label = _room_property_label(room) if room.get("address") or room.get("property_address") else str((occ_row or {}).get("address") or "Unknown Property")
        rec_raw = room.get("recommended_price")
        recommended = _to_num(rec_raw) if rec_raw not in (None, "") else None
        has_listing = bool(room.get("id") or room.get("detailed_status") or room.get("cover") is not None)
        rows.append(
            StaleRoom(
                property_label=label,
                room_number=room.get("room_number") if room.get("room_number") not in (None, "") else (occ_row or {}).get("room_number"),
                days_listed=round(days_listed, 1) if listed else None,
                days_vacant=days_vacant,
                listed=listed,
                vacant=vacant,
                occupant_present=occupant_present if occ_row is not None else None,
                rent_ready=(occ_row or {}).get("rent_ready"),
                seo_eligible=(occ_row or {}).get("seo_eligible"),
                base_price=_to_num(room.get("base_price") or room.get("last_room_price")),
                recommended_price=recommended,
                price_status=normalize_price_status(room.get("recommended_price_status")),
                price_status_raw=str(room.get("recommended_price_status") or ""),
                cover_missing=(not _has_cover(room)) if has_listing else False,
                landscape_detectable=_cover_has_orientation(room) if has_listing else False,
                move_out_photos=(occ_row or {}).get("move_out_photos"),
                presence_conflict=presence_conflict,
                room_id=room.get("id"),
                property_id=room.get("psproperty_id") or room.get("property_id") or (occ_row or {}).get("property_id"),
            )
        )

    for room in rooms:
        if not isinstance(room, dict):
            continue
        listed = str(room.get("detailed_status") or "").lower() in LISTED_STATUSES
        occ_row = occ.get(_room_key(room.get("address"), room.get("room_number")))
        vacant = bool((occ_row or {}).get("vacant")) if occ_row else False
        add(room, occ_row, listed=listed, vacant=vacant)

    for occ_row in occupancy_rooms:
        if not isinstance(occ_row, dict):
            continue
        add({}, occ_row, listed=False, vacant=bool(occ_row.get("vacant")))

    rows.sort(
        key=lambda row: (
            -(row.days_listed or 0),
            -(row.days_vacant or 0),
            row.property_label,
            str(row.room_number or ""),
        )
    )
    return rows


def pricing_outliers(rooms: Sequence[StaleRoom]) -> List[StaleRoom]:
    """High / Very High Pricing Analysis only. Never a blind −$1 sweep."""
    flagged = [row for row in rooms if row.price_status in {PRICE_HIGH, PRICE_VERY_HIGH}]
    flagged.sort(key=lambda row: (0 if row.price_status == PRICE_VERY_HIGH else 1, row.property_label))
    return flagged


def listing_photo_gaps(rooms: Sequence[StaleRoom]) -> Tuple[List[str], str]:
    gaps: List[str] = []
    landscape_seen = False
    for row in rooms:
        label = _room_label(row)
        if row.cover_missing:
            gaps.append(f"{label} — missing listing cover photo")
        if row.vacant and row.move_out_photos == 0:
            gaps.append(f"{label} — missing empty/turn photos (move_out_photos=0)")
        if row.landscape_detectable:
            landscape_seen = True
    if landscape_seen:
        note = "Cover orientation fields were present on at least one listing; check landscape vs portrait in host UI."
    else:
        note = (
            "Landscape vs portrait cover is not detectable from the current listing payload "
            "(cover has a path only; no orientation/aspect field)."
        )
    return gaps, note


def _room_label(row: StaleRoom) -> str:
    number = row.room_number
    room_bit = f"Rm {number}" if number not in (None, "") else "Rm ?"
    return f"{row.property_label} {room_bit}"


def _days_phrase(row: StaleRoom) -> str:
    parts: List[str] = []
    if row.listed and row.days_listed is not None:
        parts.append(f"listed {int(round(row.days_listed))}d")
    if row.vacant and row.days_vacant is not None:
        parts.append(f"vacant {row.days_vacant}d")
    return " / ".join(parts) if parts else "stale"


def build_advice_pack(bundle: DataBundle, now: datetime) -> AdvicePack:
    current = now.astimezone(CT) if now.tzinfo else now.replace(tzinfo=timezone_utc()).astimezone(CT)
    stale = listed_or_vacant_over_14(bundle.rooms, bundle.occupancy_rooms)
    outliers = pricing_outliers(stale)
    gaps, landscape_note = listing_photo_gaps(stale)
    pack = AdvicePack(
        month_label=current.strftime("%Y-%m"),
        generated_at=current.strftime("%Y-%m-%d %H:%M %Z"),
        source=bundle.source,
        live_fetch_failed=bundle.live_fetch_failed,
        fallback_reason=bundle.fallback_reason,
        stale_rooms=stale,
        pricing_outliers=outliers,
        photo_gaps=gaps,
        landscape_note=landscape_note,
    )
    pack.markdown = render_markdown(pack)
    pack.joe_lines = render_joe_lines(pack)
    return pack


def timezone_utc():
    from datetime import timezone

    return timezone.utc


def render_markdown(pack: AdvicePack) -> str:
    if pack.live_fetch_failed:
        source_line = (
            f"**Data source:** STALE FALLBACK (`docs/data` / scraper output) — "
            f"live host/rooms fetch failed: {pack.fallback_reason or 'unknown error'}"
        )
    elif pack.source == "live":
        source_line = "**Data source:** LIVE partner rooms + occupancy (messages/tasks this run)"
    else:
        source_line = f"**Data source:** {pack.source}"

    lines = [
        "# Monthly PadSplit SEO / vacancy advice",
        "",
        "**For:** Chief → Ang",
        f"**Month:** {pack.month_label}",
        f"**Generated:** {pack.generated_at}",
        source_line,
        "",
        "## Standing locks (do not change)",
        "",
    ]
    for lock in STANDING_LOCKS:
        lines.append(f"- {lock}")
    lines.extend(
        [
            "",
            "This pack never changes prices, Instant Book, or booking approvals.",
            "Do not recommend Instant Book. Do not apply a blind −$1 across the board.",
            "",
            "## Rooms listed / vacant >14 days",
            "",
        ]
    )
    if not pack.stale_rooms:
        lines.append("None this month.")
    else:
        for row in pack.stale_rooms:
            extra: List[str] = []
            if row.presence_conflict:
                extra.append("occupancy says occupant still present — do not treat as empty")
            if row.rent_ready is False:
                extra.append("not rent-ready")
            if row.seo_eligible is True:
                extra.append("seo_eligible")
            suffix = f" — {'; '.join(extra)}" if extra else ""
            lines.append(f"- {_room_label(row)} — {_days_phrase(row)}{suffix}")

    lines.extend(["", "## Pricing Analysis outliers (High / Very High only)", ""])
    lines.append("Only PadSplit Pricing Analysis **High** / **Very High**. No across-the-board −$1.")
    lines.append("Review in host UI only — no auto price change.")
    lines.append("")
    if not pack.pricing_outliers:
        lines.append("No High / Very High outliers on listed/vacant >14d rooms.")
    else:
        for row in pack.pricing_outliers:
            rec = f"${row.recommended_price:.0f}" if row.recommended_price is not None else "n/a"
            current = f"${row.base_price:.0f}" if row.base_price else "n/a"
            lines.append(
                f"- {_room_label(row)} — {row.price_status} — listed {current} vs recommended {rec} (review only)"
            )

    lines.extend(["", "## Cover-photo / listing gaps", ""])
    if pack.photo_gaps:
        for gap in pack.photo_gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("No missing cover or empty/turn photos detected on the >14d set.")
    lines.append(f"- {pack.landscape_note}")

    lines.extend(
        [
            "",
            "## Discord #ai-tasks-temp (@Joe only; do not post as Ang)",
            "",
        ]
    )
    return sanitize_text("\n".join(lines).rstrip() + "\n")


def render_joe_lines(pack: AdvicePack) -> List[str]:
    mention = joe_mention()
    if pack.stale_rooms:
        room_bits = ", ".join(
            f"{_short_street(row.property_label)} {_room_num(row)}" for row in pack.stale_rooms[:8]
        )
        if len(pack.stale_rooms) > 8:
            room_bits += f" (+{len(pack.stale_rooms) - 8} more)"
        stale_line = f"{len(pack.stale_rooms)} listed/vacant >14d: {room_bits}"
    else:
        stale_line = "no rooms listed/vacant >14d"

    if pack.pricing_outliers:
        price_bits = ", ".join(
            f"{_short_street(row.property_label)} {_room_num(row)} {row.price_status}"
            for row in pack.pricing_outliers[:6]
        )
        price_line = f"Pricing High/Very High: {price_bits}"
    else:
        price_line = "no High/Very High pricing outliers"

    if pack.photo_gaps:
        photo_line = f"{len(pack.photo_gaps)} cover/turn photo gap(s)"
    else:
        photo_line = "no cover/turn photo gaps flagged"

    source_bit = "live stats" if pack.source == "live" and not pack.live_fetch_failed else "STALE fallback"
    text = (
        f"{mention} monthly vacancy SEO ({pack.month_label}, {source_bit}): "
        f"{stale_line}. {price_line}. {photo_line}. "
        f"Locks: 10% promo + $0 move-in already on; Instant Book skip. No auto price changes."
    )
    text = sanitize_text(text)
    if _CINDY_RE.search(text):
        text = _CINDY_RE.sub("staff", text)
    if len(text) > DISCORD_MESSAGE_LIMIT:
        text = text[: DISCORD_MESSAGE_LIMIT - 1] + "…"
    return [text]


def _short_street(label: str) -> str:
    street = (label or "").split(",")[0].strip()
    return street or "Unknown"


def _room_num(row: StaleRoom) -> str:
    return f"Rm {row.room_number}" if row.room_number not in (None, "") else "Rm ?"


def attach_discord_section(markdown: str, joe_lines: Sequence[str], *, posted: bool, reason: str) -> str:
    header = "**Posted to #ai-tasks-temp:**" if posted else f"**Would post ({reason}):**"
    block = "\n".join([header, "", *[f"> {line}" for line in joe_lines]])
    return sanitize_text(markdown.rstrip() + "\n" + block + "\n")


def fetch_live_bundle(now: datetime) -> DataBundle:
    creds = scraper.load_credentials()
    with scraper.create_session() as session:
        scraper.login(session, creds["email"], creds["password"], force=False)
        rooms = scraper.fetch_rooms(session, creds)
        messages = scraper.fetch_messages(session, creds)
        tasks = scraper.fetch_tasks(session, creds)
    occupancy = compute_occupancy(messages, tasks, now)
    return DataBundle(
        rooms=list(rooms or []),
        occupancy_rooms=list((occupancy or {}).get("rooms") or []),
        source="live",
        scraped_at=str((occupancy or {}).get("scraped_at") or ""),
    )


def load_stale_bundle() -> DataBundle:
    stats = _load_first_json(
        [
            ROOT_DIR / "padsplit_scraper" / "output" / "stats.json",
            ROOT_DIR / "docs" / "data" / "stats.json",
        ]
    )
    occupancy = _load_first_json(
        [
            ROOT_DIR / "padsplit_scraper" / "output" / "occupancy.json",
            ROOT_DIR / "docs" / "data" / "occupancy.json",
        ]
    )
    rooms = list((stats or {}).get("rooms") or [])
    occ_rooms = list((occupancy or {}).get("rooms") or [])
    if not rooms and not occ_rooms:
        raise RuntimeError("No stale stats.json / occupancy.json rooms available")
    return DataBundle(
        rooms=rooms,
        occupancy_rooms=occ_rooms,
        source="stale_fallback",
        scraped_at=str((stats or {}).get("scraped_at") or (occupancy or {}).get("scraped_at") or ""),
    )


def _load_first_json(paths: Sequence[Path]) -> Optional[Dict[str, Any]]:
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def collect_bundle(
    now: datetime,
    *,
    live_fetcher: Optional[Callable[[datetime], DataBundle]] = None,
    stale_loader: Optional[Callable[[], DataBundle]] = None,
    stale_only: bool = False,
) -> DataBundle:
    if stale_only:
        bundle = (stale_loader or load_stale_bundle)()
        bundle.source = "stale_fallback"
        bundle.live_fetch_failed = True
        bundle.fallback_reason = bundle.fallback_reason or "stale-only flag (live fetch skipped)"
        return bundle
    try:
        return (live_fetcher or fetch_live_bundle)(now)
    except Exception as exc:
        sys.stderr.write(f"[seo-monthly] live PadSplit fetch failed; falling back to on-disk stats: {exc}\n")
        try:
            bundle = (stale_loader or load_stale_bundle)()
        except Exception as fallback_exc:
            raise RuntimeError(f"live fetch failed ({exc}); stale fallback failed ({fallback_exc})") from fallback_exc
        bundle.source = "stale_fallback"
        bundle.live_fetch_failed = True
        bundle.fallback_reason = str(exc)
        return bundle


def report_path(now: datetime, directory: Path = LOG_DIR) -> Path:
    current = now.astimezone(CT) if now.tzinfo else now.replace(tzinfo=timezone_utc()).astimezone(CT)
    return directory / f"seo-monthly-{current.strftime('%Y-%m')}.md"


def write_report(markdown: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown if markdown.endswith("\n") else markdown + "\n")
    return path


def post_joe_discord(lines: Sequence[str], *, token: Optional[str] = None, channel: Optional[str] = None) -> None:
    token = (token or os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    channel = (channel or os.getenv("DISCORD_TASKS_CHANNEL_ID") or DISCORD_TASKS_CHANNEL_ID).strip()
    if not token or not channel:
        raise RuntimeError("Discord Ops bot not wired (missing DISCORD_BOT_TOKEN or channel)")
    text = "\n".join(lines).strip()
    if not text:
        return
    if _CINDY_RE.search(text):
        raise RuntimeError("Refusing Discord post: Cindy must not be mentioned")
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "padsplit-scraper (https://github.com/happymeerkat001/padsplit-scrapper, 1.0)",
    }
    response = requests.post(
        f"{DISCORD_API_BASE}/channels/{channel}/messages",
        headers=headers,
        json={"content": text[:DISCORD_MESSAGE_LIMIT]},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()


def build_launchd_plist(workspace: Path = ROOT_DIR) -> Dict[str, Any]:
    logs = workspace / "logs"
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": ["/bin/zsh", str(workspace / "run_seo_monthly.sh")],
        "WorkingDirectory": str(workspace),
        "StartCalendarInterval": {"Day": 1, "Hour": 9, "Minute": 0},
        "StandardOutPath": str(logs / "seo-monthly.stdout.log"),
        "StandardErrorPath": str(logs / "seo-monthly.stderr.log"),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/usr/bin:/bin", "TZ": "America/Chicago"},
    }


def install_launchd(workspace: Path = ROOT_DIR) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent install is Mac-only")
    LAUNCH_AGENT_DIR.mkdir(parents=True, exist_ok=True)
    (workspace / "logs").mkdir(parents=True, exist_ok=True)
    path = LAUNCH_AGENT_DIR / f"{LAUNCHD_LABEL}.plist"
    path.write_bytes(plistlib.dumps(build_launchd_plist(workspace)))
    subprocess.run(["launchctl", "unload", str(path)], check=False, capture_output=True)
    result = subprocess.run(["launchctl", "load", str(path)], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "launchctl load failed").strip()
        raise RuntimeError(f"Failed to load {path}: {err}")
    return path


def run_pack(
    *,
    now: Optional[datetime] = None,
    live_fetcher: Optional[Callable[[datetime], DataBundle]] = None,
    stale_loader: Optional[Callable[[], DataBundle]] = None,
    poster: Optional[Callable[[Sequence[str]], None]] = None,
    output_dir: Optional[Path] = None,
    stale_only: bool = False,
    dry_run: bool = False,
    ci: Optional[bool] = None,
) -> AdvicePack:
    current = (now or datetime.now(CT)).astimezone(CT)
    bundle = collect_bundle(
        current,
        live_fetcher=live_fetcher,
        stale_loader=stale_loader,
        stale_only=stale_only,
    )
    pack = build_advice_pack(bundle, current)
    in_ci = running_in_ci() if ci is None else ci
    token_wired = bool((os.getenv("DISCORD_BOT_TOKEN") or "").strip())
    # Injected poster is for tests. Platform gate still applies in production.
    can_post = (not in_ci) and (not dry_run) and (posting_allowed(ci=False) or poster is not None)
    posted = False
    if in_ci:
        reason = "CI must not Discord-post"
    elif dry_run:
        reason = "dry-run"
    elif not can_post:
        reason = "posting gated (Mac LaunchAgent / SEO_MONTHLY_DISCORD_ENABLE)"
    elif not token_wired and poster is None:
        reason = "Ops bot not wired (DISCORD_BOT_TOKEN missing)"
    else:
        reason = "ready"
    if can_post and (token_wired or poster is not None):
        send = poster or post_joe_discord
        send(pack.joe_lines)
        posted = True
        reason = "posted via PadSplit Ops bot"
    markdown = attach_discord_section(pack.markdown, pack.joe_lines, posted=posted, reason=reason)
    pack.markdown = markdown
    path = write_report(markdown, report_path(current, output_dir or LOG_DIR))
    sys.stderr.write(f"[seo-monthly] wrote {path} source={pack.source} posted={posted}\n")
    print(markdown)
    return pack


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Monthly PadSplit SEO / vacancy advice (1st, 9:00am CT)")
    parser.add_argument("--dry-run", action="store_true", help="Build the pack; do not Discord-post")
    parser.add_argument("--stale-only", action="store_true", help="Skip live login; use on-disk stats (called out)")
    parser.add_argument("--install-launchd", action="store_true", help="Install 9:00am CT on the 1st LaunchAgent")
    parser.add_argument("--output-dir", default=str(LOG_DIR), help="Directory for seo-monthly-YYYY-MM.md")
    args = parser.parse_args(argv)
    load_environment()
    if args.install_launchd:
        path = install_launchd()
        print(f"Installed {path}")
        print("Not live until Ang merges + this Mac pulls. First fire is the next 1st at 9:00am CT.")
        print("Chief Grok Bot cron fallback stays until this LaunchAgent is loaded.")
        return 0
    if running_in_ci() and not args.dry_run:
        sys.stderr.write("[seo-monthly] skip_ci: GitHub Actions / CI must not Discord-post\n")
        run_pack(stale_only=True, dry_run=True, ci=True, output_dir=Path(args.output_dir))
        return 0
    run_pack(
        stale_only=args.stale_only,
        dry_run=args.dry_run or running_in_ci(),
        output_dir=Path(args.output_dir),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
