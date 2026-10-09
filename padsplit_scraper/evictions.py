#!/usr/bin/env python3
"""Collections / terminated members -> a notice-to-vacate package.

Default off. ``EVICTIONS_ENABLE`` follows the runtime action-flag pattern
(also ``PADSPLIT_SEND_EVICTIONS``). Off writes the would-post text and the
PDF path to ``logs/evictions_dryrun.jsonl`` and still builds the PDF. CI is
a no-op: no fetch, no PDF, no Discord.

Discord text is house, room, status, vacate date, and a manual-mailing line.
It never includes balance, member names, lock codes, or member messages.
Balance is rendered only inside the gitignored PDF.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

import requests

try:
    from padsplit_scraper import partner_members, runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/evictions.py
    import partner_members  # type: ignore
    import runtime  # type: ignore


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "templates" / "notice_to_vacate.txt"
OUTPUT_DIR = ROOT / "padsplit_scraper" / "output" / "evictions"
STATE_PATH = ROOT / "logs" / "evictions_state.json"
DRY_LOG_PATH = ROOT / "logs" / "evictions_dryrun.jsonl"
CHICAGO = ZoneInfo("America/Chicago")
DISCORD_API = "https://discord.com/api/v10"
DEFAULT_TRIGGERS = ("terminated", "Behind")
NOTICE_DAYS = 3
DEFAULT_SLACK_DAYS = 2
ENTITY = "Li Real Estate LLC / Liaison Ventures Management"
SIGNER = "[Authorized signer]"
MANUAL_LINE = "package attached; mailing and county filing are manual."
INTERNAL_HEADER = "INTERNAL - not for tenant"
REVIEWER_NOTES = (
    "Draft package for review. This form is a Texas-style placeholder. "
    "Confirm the wording before any mailing. This software does not mail this notice "
    "and does not file with the county.",
    "That vacate date is three days after the date of this notice, plus the configured "
    "mailing slack. Count the date of this notice as the mail date only after a person "
    "actually mails it.",
    "Mailing and county filing are manual.",
)

# Higher rank is worse. A new package is posted only when the rank rises.
STATUS_RANK = {
    "behind": 10,
    "terminated": 20,
}
UNKNOWN_STATUS_RANK = 15

DROPPED_KEYS = (
    "first_name",
    "last_name",
    "name",
    "full_name",
    "phone",
    "email",
    "lock_code",
    "door_code",
    "code",
    "message",
    "body",
    "member_message",
)

HttpPost = Callable[..., Any]
PostFn = Callable[[str, Path], Any]


class EvictionsStateError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


@dataclass
class Case:
    occupancy_id: str
    house: str
    room: str
    status: str
    balance: str
    move_in_date: str
    notice_date: str
    vacate_date: str
    secrets: List[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"Case(occupancy_id={self.occupancy_id!r}, room={self.room!r}, "
            f"status={self.status!r})"
        )


@dataclass
class RunResult:
    action: str
    posts: List[str] = field(default_factory=list)
    pdfs: List[str] = field(default_factory=list)
    skipped: int = 0


def _log(message: str) -> None:
    sys.stderr.write(f"[evictions] {message}\n")


def trigger_statuses(environ: Dict[str, str]) -> Sequence[str]:
    raw = (environ.get("EVICTIONS_TRIGGER_STATUSES") or "").strip()
    if not raw:
        return DEFAULT_TRIGGERS
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return tuple(parts) or DEFAULT_TRIGGERS


def slack_days(environ: Dict[str, str]) -> int:
    raw = (environ.get("EVICTIONS_VACATE_SLACK_DAYS") or "").strip()
    if not raw:
        return DEFAULT_SLACK_DAYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SLACK_DAYS
    if value < 0:
        return DEFAULT_SLACK_DAYS
    return value


def status_rank(status: str) -> int:
    return STATUS_RANK.get(status.strip().lower(), UNKNOWN_STATUS_RANK)


def _match_trigger(value: str, triggers: Sequence[str]) -> Optional[str]:
    text = value.strip().lower()
    if not text:
        return None
    for trigger in triggers:
        if trigger.strip().lower() == text:
            return trigger.strip()
    return None


def _on_payment_plan(member: Dict[str, Any]) -> bool:
    value = member.get("is_on_payment_plan")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value in (1,):
        return True
    return False


def case_status(member: Dict[str, Any], triggers: Sequence[str]) -> Optional[str]:
    """Worst matching trigger, or None when a payment plan suppresses the row.

    ``is_terminated`` true counts as the configured ``terminated`` status.
    """
    if _on_payment_plan(member):
        return None
    found: List[str] = []
    if member.get("is_terminated") is True:
        matched = _match_trigger("terminated", triggers)
        if matched:
            found.append(matched)
    for key in ("finance_status", "occupancy_status"):
        value = member.get(key)
        if isinstance(value, str):
            matched = _match_trigger(value, triggers)
            if matched and matched not in found:
                found.append(matched)
    if not found:
        return None
    return max(found, key=lambda item: (status_rank(item), item.lower()))


def notice_on(now: datetime) -> date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(CHICAGO).date()


def vacate_on(notice: date, slack: int) -> date:
    return notice + timedelta(days=NOTICE_DAYS + slack)


def _dropped_secrets(member: Dict[str, Any]) -> List[str]:
    secrets: List[str] = []
    for key in DROPPED_KEYS:
        value = member.get(key)
        if isinstance(value, str):
            text = value.strip()
            if len(text) >= 3:
                secrets.append(text)
    return secrets


def format_balance(value: Any) -> str:
    """Currency for the PDF. Empty stays 'not on file'. Unparsed text is kept."""
    if value in (None, ""):
        return "not on file"
    text = str(value).strip()
    if not text or text.lower() == "not on file":
        return "not on file"
    cleaned = text.replace("$", "").replace(",", "").strip()
    negative = cleaned.startswith("-")
    if negative:
        cleaned = cleaned[1:].strip()
    try:
        amount = float(cleaned)
    except ValueError:
        return text
    if negative:
        amount = -amount
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def build_case(
    member: Dict[str, Any],
    *,
    triggers: Sequence[str],
    notice: date,
    slack: int,
) -> Optional[Case]:
    status = case_status(member, triggers)
    if not status:
        return None
    occupancy_id = str(member.get("occupancy_id") or "").strip()
    if not occupancy_id:
        return None
    house = str(member.get("house_address") or member.get("house") or "").strip() or "unknown house"
    room = str(member.get("room_number") or member.get("room") or "").strip() or "unknown"
    move_in = member.get("move_in_date")
    move_in_text = "" if move_in in (None, "") else str(move_in).strip()
    return Case(
        occupancy_id=occupancy_id,
        house=house,
        room=room,
        status=status,
        balance=format_balance(member.get("balance")),
        move_in_date=move_in_text or "not on file",
        notice_date=notice.isoformat(),
        vacate_date=vacate_on(notice, slack).isoformat(),
        secrets=_dropped_secrets(member),
    )


def post_text(case: Case) -> str:
    room = case.room
    if room.lower().startswith("room "):
        room_label = room
    else:
        room_label = f"Room {room}"
    return (
        f"{case.house}, {room_label} — {case.status} — vacate {case.vacate_date}. "
        f"{MANUAL_LINE}"
    )


def fill_template(template: str, case: Case) -> str:
    values = {
        "house_address": case.house,
        "room": case.room,
        "notice_date": case.notice_date,
        "vacate_date": case.vacate_date,
        "balance": case.balance,
        "move_in_date": case.move_in_date,
    }
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text or "}}" in text:
        raise RuntimeError("notice template has an unfilled placeholder")
    return text


def ledger_text(case: Case, slack: int) -> str:
    notice = date.fromisoformat(case.notice_date)
    three_day = (notice + timedelta(days=NOTICE_DAYS)).isoformat()
    lines = [
        INTERNAL_HEADER,
        "",
        "Case ledger and timeline",
        f"House: {case.house}",
        f"Room: {case.room}",
        f"Occupancy: {case.occupancy_id}",
        f"Status: {case.status}",
        f"Move-in: {case.move_in_date}",
        f"Notice / mail date: {case.notice_date}",
        f"Three-day period ends: {three_day}",
        f"Slack days: {slack}",
        f"Vacate date: {case.vacate_date}",
        f"Balance: {case.balance}",
        "Payment plan: no",
        f"Entity: {ENTITY}",
        f"Signer: {SIGNER}",
        "Timeline: move-in, then this notice date, then the vacate date.",
        "",
        "Reviewer notes:",
        *REVIEWER_NOTES,
    ]
    return "\n".join(lines) + "\n"


def _reject(blob: str, secrets: Sequence[str], *, balance: str, allow_balance: bool) -> None:
    for secret in secrets:
        if secret and secret in blob:
            raise RuntimeError("refusing text that contains a member identifier")
    if not allow_balance and balance and balance not in {"", "not on file"} and balance in blob:
        raise RuntimeError("refusing text that contains a balance")


def assert_outbound_safe(case: Case, post: str, log_line: str, package_text: str) -> None:
    _reject(post, case.secrets, balance=case.balance, allow_balance=False)
    _reject(log_line, case.secrets, balance=case.balance, allow_balance=False)
    _reject(package_text, case.secrets, balance=case.balance, allow_balance=True)


def _draw_block(canvas: Any, text: str, *, x: float, y: float, max_width: float, leading: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    font = "Times-Roman"
    size = 11
    canvas.setFont(font, size)
    for paragraph in text.split("\n"):
        if y < 72:
            canvas.showPage()
            canvas.setFont(font, size)
            y = 720
        if not paragraph:
            y -= leading
            continue
        if stringWidth(paragraph, font, size) <= max_width:
            canvas.drawString(x, y, paragraph)
            y -= leading
            continue
        line = ""
        for word in paragraph.split(" "):
            trial = word if not line else f"{line} {word}"
            if stringWidth(trial, font, size) <= max_width:
                line = trial
                continue
            if line:
                canvas.drawString(x, y, line)
                y -= leading
            line = word
        if line:
            canvas.drawString(x, y, line)
            y -= leading
    return y


def write_package_pdf(path: Path, notice: str, ledger: str) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    pdf = canvas.Canvas(str(temp), pagesize=letter)
    width, _height = letter
    _draw_block(pdf, notice, x=72, y=720, max_width=width - 144, leading=15)
    pdf.showPage()
    _draw_block(pdf, ledger, x=72, y=720, max_width=width - 144, leading=16)
    pdf.save()
    temp.replace(path)


def _safe_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    cleaned = cleaned.strip("-") or "case"
    return cleaned[:80]


def pdf_path_for(case: Case, output_dir: Path) -> Path:
    name = f"{_safe_token(case.occupancy_id)}-{_safe_token(case.status)}.pdf"
    return output_dir / name


def case_key(case: Case) -> str:
    return f"{case.occupancy_id}+{case.status}"


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "cases": {}}
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EvictionsStateError("corrupt state") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), dict):
        raise EvictionsStateError("corrupt state")
    return payload


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, indent=2) + "\n")
    temp.replace(path)


def _posted_rank(cases: Dict[str, Any], occupancy_id: str) -> Optional[int]:
    ranks: List[int] = []
    for row in cases.values():
        if not isinstance(row, dict):
            continue
        if str(row.get("occupancy_id") or "") != occupancy_id:
            continue
        if not row.get("posted"):
            continue
        ranks.append(status_rank(str(row.get("status") or "")))
    if not ranks:
        return None
    return max(ranks)


def plan_action(state: Dict[str, Any], case: Case, *, live: bool) -> str:
    """``post``, ``dry_log``, ``refresh``, or ``skip``.

    Skip when this occupancy was already posted at this status or at a worse
    one. Dry-run does not set ``posted``, so a later live run can still send.
    """
    cases = state.get("cases") or {}
    rank = status_rank(case.status)
    posted = _posted_rank(cases, case.occupancy_id)
    if posted is not None and rank <= posted:
        return "skip"
    row = cases.get(case_key(case))
    if not isinstance(row, dict):
        row = {}
    if live:
        return "post"
    if row.get("dry_logged"):
        return "refresh"
    return "dry_log"


def _public_log(case: Case, text: str, pdf: Path) -> Dict[str, str]:
    return {
        "occupancy_id": case.occupancy_id,
        "house": case.house,
        "room": case.room,
        "status": case.status,
        "notice_date": case.notice_date,
        "vacate_date": case.vacate_date,
        "text": text,
        "pdf": str(pdf),
    }


def append_dry_log(path: Path, record: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def discord_delivery(text: str, pdf_path: Path, environ: Dict[str, str]) -> Dict[str, Any]:
    """Bot channel when configured, otherwise the webhook. Does not send."""
    channel = (environ.get("DISCORD_EVICTIONS_CHANNEL_ID") or "").strip()
    token = (environ.get("DISCORD_BOT_TOKEN") or "").strip()
    webhook = (environ.get("DISCORD_EVICTIONS_WEBHOOK_URL") or "").strip()
    blob = pdf_path.read_bytes()
    filename = "notice-to-vacate.pdf"
    if channel and token:
        return {
            "kind": "bot",
            "url": f"{DISCORD_API}/channels/{channel}/messages",
            "headers": {"Authorization": f"Bot {token}"},
            "data": {"payload_json": json.dumps({"content": text})},
            "files": {"files[0]": (filename, blob, "application/pdf")},
        }
    if webhook:
        return {
            "kind": "webhook",
            "url": webhook,
            "headers": {},
            "data": {"content": text},
            "files": {"file": (filename, blob, "application/pdf")},
        }
    return {"kind": "unconfigured"}


def post_discord(
    text: str,
    pdf_path: Path,
    *,
    environ: Dict[str, str],
    http_post: Optional[HttpPost] = None,
) -> str:
    delivery = discord_delivery(text, pdf_path, environ)
    kind = str(delivery.get("kind") or "")
    if kind == "unconfigured":
        raise RuntimeError("discord unconfigured")
    sender = http_post or requests.post
    response = sender(
        delivery["url"],
        headers=delivery["headers"],
        data=delivery["data"],
        files=delivery["files"],
        timeout=30,
    )
    status = int(getattr(response, "status_code", 200) or 200)
    if status >= 400:
        raise RuntimeError("discord post failed")
    return kind


def _remember(state: Dict[str, Any], case: Case, pdf: Path, *, posted: bool, dry_logged: bool) -> None:
    cases = state.setdefault("cases", {})
    key = case_key(case)
    prior = cases.get(key) if isinstance(cases.get(key), dict) else {}
    cases[key] = {
        "occupancy_id": case.occupancy_id,
        "status": case.status,
        "notice_date": case.notice_date,
        "vacate_date": case.vacate_date,
        "pdf": str(pdf),
        "posted": posted or bool(prior.get("posted")),
        "dry_logged": dry_logged or bool(prior.get("dry_logged")),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _stamp_state(path: Path, case: Case, pdf: Path, *, posted: bool, dry_logged: bool) -> None:
    state = load_state(path)
    _remember(state, case, pdf, posted=posted, dry_logged=dry_logged)
    save_state(path, state)


def process(
    members: Sequence[Dict[str, Any]],
    *,
    environ: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
    state_path: Path = STATE_PATH,
    dry_log_path: Path = DRY_LOG_PATH,
    output_dir: Path = OUTPUT_DIR,
    template_path: Path = TEMPLATE_PATH,
    post_fn: Optional[PostFn] = None,
    http_post: Optional[HttpPost] = None,
) -> RunResult:
    env = dict(environ) if environ is not None else dict(os.environ)
    if runtime.running_in_ci(env):  # type: ignore[arg-type]
        return RunResult(action="skip_ci")
    live = runtime.send_enabled("evictions", env)  # type: ignore[arg-type]
    try:
        state = load_state(state_path)
    except EvictionsStateError:
        _log("corrupt state; skipping")
        return RunResult(action="skipped")
    template = template_path.read_text(encoding="utf-8")
    if ENTITY not in template or SIGNER not in template:
        raise RuntimeError("notice template is missing the entity or signer placeholder")
    current = now or datetime.now(timezone.utc)
    notice = notice_on(current)
    triggers = trigger_statuses(env)
    slack = slack_days(env)
    posts: List[str] = []
    pdfs: List[str] = []
    skipped = 0
    acted = 0
    for member in members:
        if not isinstance(member, dict):
            continue
        case = build_case(member, triggers=triggers, notice=notice, slack=slack)
        if case is None:
            continue
        action = plan_action(state, case, live=live)
        if action == "skip":
            skipped += 1
            continue
        text = post_text(case)
        notice_body = fill_template(template, case)
        ledger = ledger_text(case, slack)
        record = _public_log(case, text, pdf_path_for(case, output_dir))
        log_line = json.dumps(record, sort_keys=True)
        try:
            assert_outbound_safe(case, text, log_line, notice_body + "\n" + ledger)
        except RuntimeError:
            _log("skipped one case (outbound check)")
            skipped += 1
            continue
        pdf = pdf_path_for(case, output_dir)
        write_package_pdf(pdf, notice_body, ledger)
        pdfs.append(str(pdf))
        if action == "refresh":
            continue
        if action == "dry_log":
            append_dry_log(dry_log_path, record)
            _remember(state, case, pdf, posted=False, dry_logged=True)
            save_state(state_path, state)
            posts.append(text)
            acted += 1
            continue
        try:
            if post_fn is not None:
                post_fn(text, pdf)
            else:
                post_discord(text, pdf, environ=env, http_post=http_post)
        except Exception:
            _log("discord post failed; will retry this case")
            skipped += 1
            continue
        _remember(state, case, pdf, posted=True, dry_logged=bool((state.get("cases") or {}).get(case_key(case), {}).get("dry_logged")))
        save_state(state_path, state)
        posts.append(text)
        acted += 1
    if acted:
        return RunResult(action="posted" if live else "dry_run", posts=posts, pdfs=pdfs, skipped=skipped)
    if skipped:
        return RunResult(action="skipped", posts=posts, pdfs=pdfs, skipped=skipped)
    return RunResult(action="noop", posts=posts, pdfs=pdfs, skipped=skipped)


def load_live_members(
    session: Any,
    creds: Optional[Dict[str, str]],
    *,
    request_fn: Optional[partner_members.RequestFn] = None,
) -> List[Dict[str, Any]]:
    """Property address plus eviction rows. Balance stays on the in-memory row."""
    refs = partner_members.fetch_property_index(session, creds, request_fn=request_fn)
    rows: List[Dict[str, Any]] = []
    for ref in refs:
        property_id = str(ref.get("id") or "").strip()
        if not property_id:
            continue
        try:
            members = partner_members.fetch_eviction_members(
                session,
                creds,
                property_id,
                request_fn=request_fn,
            )
        except partner_members.MembersRequestError:
            _log("members fetch failed (request failed); continuing")
            continue
        address = partner_members.house_address(ref)
        for member in members:
            if not isinstance(member, dict):
                continue
            row = dict(member)
            row["house_address"] = address
            row["property_id"] = property_id
            rows.append(row)
    return rows


def run(
    *,
    members: Optional[Sequence[Dict[str, Any]]] = None,
    environ: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
    state_path: Path = STATE_PATH,
    dry_log_path: Path = DRY_LOG_PATH,
    output_dir: Path = OUTPUT_DIR,
    template_path: Path = TEMPLATE_PATH,
    post_fn: Optional[PostFn] = None,
    http_post: Optional[HttpPost] = None,
    session: Any = None,
    creds: Optional[Dict[str, str]] = None,
    request_fn: Optional[partner_members.RequestFn] = None,
) -> RunResult:
    env = dict(environ) if environ is not None else dict(os.environ)
    if runtime.running_in_ci(env):  # type: ignore[arg-type]
        return RunResult(action="skip_ci")
    selected = members
    if selected is None:
        try:
            from padsplit_scraper.scraper import create_session, load_credentials, login
        except ModuleNotFoundError:
            from scraper import create_session, load_credentials, login  # type: ignore
        try:
            if session is None:
                creds = creds or load_credentials()
                with create_session() as owned:
                    login(owned, creds["email"], creds["password"], force=False)
                    selected = load_live_members(owned, creds, request_fn=request_fn)
            else:
                selected = load_live_members(session, creds, request_fn=request_fn)
        except partner_members.MembersRequestError:
            _log("members fetch failed (request failed); skipping")
            return RunResult(action="skipped")
        except Exception as exc:
            detail = "auth failed" if partner_members._auth_failure(exc) else "request failed"
            _log(f"members fetch failed ({detail}); skipping")
            return RunResult(action="skipped")
    return process(
        selected,
        environ=env,
        now=now,
        state_path=state_path,
        dry_log_path=dry_log_path,
        output_dir=output_dir,
        template_path=template_path,
        post_fn=post_fn,
        http_post=http_post,
    )


def preview_member() -> Dict[str, Any]:
    return {
        "occupancy_id": "preview",
        "house_address": "TEST 100 Example Lane",
        "room_number": "2",
        "is_terminated": True,
        "finance_status": "terminated",
        "is_on_payment_plan": False,
        "balance": "250.00",
        "move_in_date": "2024-03-01",
        "first_name": "Hidden Preview Name",
        "lock_code": "949381",
        "message": "member message must not leak",
    }


def build_preview(
    *,
    environ: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
    output_dir: Path = OUTPUT_DIR,
    template_path: Path = TEMPLATE_PATH,
) -> str:
    """Sample package for a fake member. No network, no ledger, no Discord."""
    env = dict(os.environ) if environ is None else dict(environ)
    env["EVICTIONS_ENABLE"] = "0"
    current = now or datetime.now(timezone.utc)
    case = build_case(
        preview_member(),
        triggers=trigger_statuses(env),
        notice=notice_on(current),
        slack=slack_days(env),
    )
    if case is None:
        raise RuntimeError("preview member did not trigger")
    template = template_path.read_text(encoding="utf-8")
    text = post_text(case)
    notice = fill_template(template, case)
    ledger = ledger_text(case, slack_days(env))
    assert_outbound_safe(case, text, text, notice + "\n" + ledger)
    path = output_dir / "preview-notice.pdf"
    write_package_pdf(path, notice, ledger)
    sys.stderr.write(f"pdf: {path}\n")
    return text


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build PadSplit eviction packages")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Write a sample PDF for a fake member and print the Discord text",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.preview:
        sys.stdout.write(build_preview() + "\n")
        return 0
    if runtime.running_in_ci():
        return 0
    try:
        run()
    except EvictionsStateError:
        _log("corrupt state; skipping")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
