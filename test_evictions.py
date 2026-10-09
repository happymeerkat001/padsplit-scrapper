#!/usr/bin/env python3
"""Offline eviction-package tests. No live PadSplit or Discord calls."""

from __future__ import annotations

import base64
import io
import json
import re
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from padsplit_scraper import evictions
from padsplit_scraper import partner_members
from padsplit_scraper import runtime


ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
NAME = "ZqxPat Member"
LOCK_CODE = "949381"
MESSAGE = "member says the door code is 949381 and please wait"
BALANCE = "99123.45"
HOUSE = "100 Example Lane"
ROOM = "2"


def live_env(**extra: str) -> dict:
    env = {
        "CI": "",
        "GITHUB_ACTIONS": "",
        "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
        "PADSPLIT_COLLECTION_ONLY": "0",
        "EVICTIONS_ENABLE": "1",
    }
    env.update(extra)
    return env


def dry_env(**extra: str) -> dict:
    env = {"CI": "", "GITHUB_ACTIONS": "", "EVICTIONS_ENABLE": "0"}
    env.update(extra)
    return env


def member(**overrides: object) -> dict:
    row = {
        "occupancy_id": "555",
        "house_address": HOUSE,
        "room_number": ROOM,
        "is_terminated": False,
        "occupancy_status": "active",
        "finance_status": "Behind",
        "is_on_payment_plan": False,
        "payment_plan_end_date": None,
        "balance": BALANCE,
        "move_in_date": "2024-03-01",
        "first_name": NAME,
        "last_name": "Hidden",
        "name": NAME,
        "lock_code": LOCK_CODE,
        "door_code": LOCK_CODE,
        "message": MESSAGE,
        "phone": "214-555-0199",
    }
    row.update(overrides)
    return row


def pdf_text(data: bytes) -> str:
    chunks = []
    text = data.decode("latin1")
    for match in re.finditer(r"stream\n(.*?)endstream", text, re.S):
        raw = match.group(1).strip().encode("latin1")
        if raw.endswith(b"~>"):
            raw = base64.a85decode(raw, adobe=True)
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        chunks.append(raw.decode("latin1", errors="ignore"))
    blob = "\n".join(chunks)
    parts = re.findall(r"\((?:\\.|[^\\)])*\)", blob)
    texts = []
    for part in parts:
        inner = part[1:-1]
        inner = inner.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        texts.append(inner)
    return "\n".join(texts)


class EvictionDecisionTests(unittest.TestCase):
    def test_trigger_statuses_and_terminated_flag(self) -> None:
        behind = evictions.build_case(
            member(),
            triggers=evictions.DEFAULT_TRIGGERS,
            notice=evictions.notice_on(NOW),
            slack=2,
        )
        self.assertIsNotNone(behind)
        assert behind is not None
        self.assertEqual(behind.status, "Behind")
        self.assertEqual(behind.vacate_date, "2026-10-14")
        self.assertEqual(behind.notice_date, "2026-10-09")

        flagged = evictions.build_case(
            member(finance_status="current", is_terminated=True),
            triggers=evictions.DEFAULT_TRIGGERS,
            notice=evictions.notice_on(NOW),
            slack=2,
        )
        self.assertIsNotNone(flagged)
        assert flagged is not None
        self.assertEqual(flagged.status, "terminated")

        both = evictions.build_case(
            member(is_terminated=True, finance_status="Behind"),
            triggers=evictions.DEFAULT_TRIGGERS,
            notice=evictions.notice_on(NOW),
            slack=2,
        )
        self.assertIsNotNone(both)
        assert both is not None
        self.assertEqual(both.status, "terminated")

        self.assertIsNone(
            evictions.build_case(
                member(finance_status="current", is_terminated=False),
                triggers=evictions.DEFAULT_TRIGGERS,
                notice=evictions.notice_on(NOW),
                slack=2,
            )
        )

    def test_payment_plan_suppresses(self) -> None:
        for overrides in (
            {"is_on_payment_plan": True, "finance_status": "Behind"},
            {"is_on_payment_plan": True, "is_terminated": True, "finance_status": "terminated"},
            {"is_on_payment_plan": "yes", "finance_status": "Behind"},
        ):
            self.assertIsNone(
                evictions.build_case(
                    member(**overrides),
                    triggers=evictions.DEFAULT_TRIGGERS,
                    notice=evictions.notice_on(NOW),
                    slack=2,
                )
            )

    def test_custom_trigger_list(self) -> None:
        self.assertIsNone(
            evictions.case_status(member(finance_status="Behind"), ("terminated",))
        )
        self.assertEqual(
            evictions.case_status(member(is_terminated=True, finance_status="Behind"), ("terminated",)),
            "terminated",
        )

    def test_vacate_slack_override(self) -> None:
        self.assertEqual(evictions.slack_days({"EVICTIONS_VACATE_SLACK_DAYS": "0"}), 0)
        notice = evictions.notice_on(NOW)
        self.assertEqual(evictions.vacate_on(notice, 0).isoformat(), "2026-10-12")
        self.assertEqual(evictions.slack_days({}), 2)


class EvictionRunTests(unittest.TestCase):
    def _paths(self, tmp: str) -> dict:
        root = Path(tmp)
        return {
            "state_path": root / "evictions_state.json",
            "dry_log_path": root / "evictions_dryrun.jsonl",
            "output_dir": root / "pdfs",
        }

    def test_each_trigger_builds_a_package_when_live(self) -> None:
        calls = []

        def post(text: str, pdf: Path) -> None:
            calls.append((text, pdf.read_bytes()))

        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            result = evictions.process(
                [
                    member(occupancy_id="1", finance_status="Behind", is_terminated=False),
                    member(occupancy_id="2", finance_status="current", is_terminated=True),
                ],
                environ=live_env(),
                now=NOW,
                post_fn=post,
                **paths,
            )
        self.assertEqual(result.action, "posted")
        self.assertEqual(len(calls), 2)
        statuses = {text.split("—")[1].strip() for text, _blob in calls}
        self.assertEqual(statuses, {"Behind", "terminated"})
        for text, blob in calls:
            self.assertIn(evictions.MANUAL_LINE, text)
            self.assertIn("2026-10-14", text)
            self.assertNotIn(BALANCE, text)
            self.assertNotIn(NAME, text)
            self.assertNotIn(LOCK_CODE, text)
            self.assertNotIn(MESSAGE, text)
            extracted = pdf_text(blob)
            self.assertIn(HOUSE, extracted)
            self.assertIn(ROOM, extracted)
            self.assertIn("2026-10-09", extracted)
            self.assertIn("2026-10-14", extracted)
            self.assertIn(BALANCE, extracted)
            self.assertIn("2024-03-01", extracted)
            self.assertIn(evictions.ENTITY, extracted)
            self.assertIn(evictions.SIGNER, extracted)
            self.assertIn("Case ledger and timeline", extracted)
            self.assertNotIn(NAME, extracted)
            self.assertNotIn(LOCK_CODE, extracted)
            self.assertNotIn(MESSAGE, extracted)
            self.assertNotIn("214-555-0199", extracted)

    def test_dedupe_and_worsen(self) -> None:
        calls = []

        def post(text: str, _pdf: Path) -> None:
            calls.append(text)

        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            kwargs = dict(environ=live_env(), now=NOW, post_fn=post, **paths)
            first = evictions.process([member()], **kwargs)
            second = evictions.process([member()], **kwargs)
            self.assertEqual(first.action, "posted")
            self.assertEqual(len(calls), 1)
            self.assertEqual(second.action, "skipped")
            self.assertEqual(len(calls), 1)
            worse = evictions.process(
                [member(is_terminated=True, finance_status="Behind")],
                **kwargs,
            )
            self.assertEqual(worse.action, "posted")
            self.assertEqual(len(calls), 2)
            self.assertIn("terminated", calls[1])
            quiet = evictions.process([member(finance_status="Behind", is_terminated=False)], **kwargs)
            self.assertEqual(quiet.action, "skipped")
            self.assertEqual(len(calls), 2)
            saved = json.loads(paths["state_path"].read_text())
            blob = json.dumps(saved)
            self.assertNotIn(BALANCE, blob)
            self.assertNotIn(NAME, blob)
            self.assertNotIn(LOCK_CODE, blob)
            self.assertTrue(saved["cases"]["555+Behind"]["posted"])
            self.assertTrue(saved["cases"]["555+terminated"]["posted"])

    def test_gate_off_writes_dry_run_and_does_not_post(self) -> None:
        def post(_text: str, _pdf: Path) -> None:
            raise AssertionError("discord must not be called when the gate is off")

        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            result = evictions.process(
                [member()],
                environ=dry_env(),
                now=NOW,
                post_fn=post,
                **paths,
            )
            self.assertEqual(result.action, "dry_run")
            log = paths["dry_log_path"].read_text()
            self.assertIn(evictions.MANUAL_LINE, log)
            self.assertIn(HOUSE, log)
            self.assertIn("2026-10-14", log)
            self.assertIn(".pdf", log)
            self.assertNotIn(BALANCE, log)
            self.assertNotIn(NAME, log)
            self.assertNotIn(LOCK_CODE, log)
            self.assertNotIn(MESSAGE, log)
            pdfs = list(paths["output_dir"].glob("*.pdf"))
            self.assertEqual(len(pdfs), 1)
            self.assertIn(BALANCE, pdf_text(pdfs[0].read_bytes()))
            again = evictions.process([member()], environ=dry_env(), now=NOW, post_fn=post, **paths)
            self.assertEqual(again.action, "noop")
            self.assertEqual(len(paths["dry_log_path"].read_text().splitlines()), 1)
            state = json.loads(paths["state_path"].read_text())
            self.assertFalse(state["cases"]["555+Behind"]["posted"])
            self.assertNotIn(BALANCE, paths["state_path"].read_text())

    def test_collection_only_and_ci_do_not_post(self) -> None:
        def post(_text: str, _pdf: Path) -> None:
            raise AssertionError("discord must not be called")

        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            collected = live_env(PADSPLIT_COLLECTION_ONLY="1")
            self.assertFalse(runtime.send_enabled("evictions", collected))
            result = evictions.process([member()], environ=collected, now=NOW, post_fn=post, **paths)
            self.assertEqual(result.action, "dry_run")
            ci = evictions.run(
                members=[member()],
                environ={"CI": "true", "EVICTIONS_ENABLE": "1", "PADSPLIT_ENABLE_ACTION_HOOKS": "1"},
                now=NOW,
                post_fn=post,
                **paths,
            )
            self.assertEqual(ci.action, "skip_ci")
            self.assertEqual(ci.posts, [])
            self.assertEqual(list(paths["output_dir"].glob("*.pdf")), [paths["output_dir"] / "555-Behind.pdf"])
            with patch.dict("os.environ", {"CI": "true"}):
                self.assertEqual(evictions.main([]), 0)

    def test_discord_payload_has_pdf_and_no_balance_in_text(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            seen = {}

            def http_post(url, **kwargs):
                seen["url"] = url
                seen["data"] = kwargs.get("data")
                seen["files"] = kwargs.get("files")
                response = type("Response", (), {"status_code": 200})()
                return response

            with patch("padsplit_scraper.evictions.requests.post", side_effect=AssertionError("raw requests")):
                result = evictions.process(
                    [member()],
                    environ=live_env(
                        DISCORD_EVICTIONS_CHANNEL_ID="123",
                        DISCORD_BOT_TOKEN="bot-token",
                        DISCORD_EVICTIONS_WEBHOOK_URL="https://discord.example/webhook",
                    ),
                    now=NOW,
                    http_post=http_post,
                    **paths,
                )
        self.assertEqual(result.action, "posted")
        self.assertIn("/channels/123/messages", seen["url"])
        payload = json.loads(seen["data"]["payload_json"])
        self.assertIn(evictions.MANUAL_LINE, payload["content"])
        self.assertNotIn(BALANCE, payload["content"])
        self.assertNotIn(NAME, payload["content"])
        self.assertNotIn(LOCK_CODE, json.dumps(seen["data"]))
        filename, blob, mime = seen["files"]["files[0]"]
        self.assertEqual(filename, "notice-to-vacate.pdf")
        self.assertEqual(mime, "application/pdf")
        self.assertIn(BALANCE, pdf_text(blob))
        self.assertNotIn("https://discord.example/webhook", seen["url"])

    def test_members_fetch_failure_skips_without_discord(self) -> None:
        def post(_text: str, _pdf: Path) -> None:
            raise AssertionError("discord")

        def request_fn(_url, _params):
            raise partner_members.MembersRequestError("request failed")

        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = evictions.run(
                    members=None,
                    session=object(),
                    creds={},
                    request_fn=request_fn,
                    environ=live_env(),
                    now=NOW,
                    post_fn=post,
                    **paths,
                )
        self.assertEqual(result.action, "skipped")
        self.assertIn("members fetch failed", stderr.getvalue())
        self.assertNotIn(NAME, stderr.getvalue())
        self.assertNotIn(BALANCE, stderr.getvalue())

    def test_one_property_fetch_failure_still_packages_the_other(self) -> None:
        def request_fn(url, params):
            if "properties" in url:
                return {
                    "results": [
                        {"id": 1, "address": "1 Broken Street"},
                        {"id": 2, "address": {"street1": HOUSE, "city": "Dallas", "state": "TX", "zip": "75201"}},
                    ]
                }
            if str((params or {}).get("property_id")) == "1":
                raise partner_members.MembersRequestError("request failed")
            return {
                "next": None,
                "results": [
                    {
                        "occupancy_id": "777",
                        "room_number": ROOM,
                        "is_terminated": True,
                        "finance_status": "terminated",
                        "is_on_payment_plan": False,
                        "move_in_date": "2024-03-01",
                        "balance": BALANCE,
                        "first_name": NAME,
                        "lock_code": LOCK_CODE,
                        "message": MESSAGE,
                    }
                ],
            }

        calls = []
        with TemporaryDirectory() as tmp:
            paths = self._paths(tmp)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = evictions.run(
                    members=None,
                    session=object(),
                    creds={},
                    request_fn=request_fn,
                    environ=live_env(),
                    now=NOW,
                    post_fn=lambda text, _pdf: calls.append(text),
                    **paths,
                )
        self.assertEqual(result.action, "posted")
        self.assertEqual(len(calls), 1)
        self.assertIn(HOUSE, calls[0])
        self.assertIn("Dallas", calls[0])
        self.assertNotIn(NAME, calls[0])
        self.assertNotIn(BALANCE, calls[0])
        self.assertNotIn(LOCK_CODE, calls[0])
        self.assertIn("continuing", stderr.getvalue())
        self.assertNotIn(BALANCE, stderr.getvalue())

    def test_preview_prints_post_text_without_balance(self) -> None:
        with TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                text = evictions.build_preview(
                    environ=dry_env(),
                    now=NOW,
                    output_dir=Path(tmp),
                )
                code = evictions.main(["--preview"])
            self.assertEqual(
                text,
                "TEST 100 Example Lane, Room 2 — terminated — vacate 2026-10-14. "
                "package attached; mailing and county filing are manual.",
            )
            pdf = Path(tmp) / "preview-notice.pdf"
            extracted = pdf_text(pdf.read_bytes())
            self.assertIn("TEST 100 Example Lane", extracted)
            self.assertIn("250.00", extracted)
            self.assertIn(evictions.ENTITY, extracted)
            self.assertNotIn("Hidden Preview Name", extracted)
            self.assertNotIn("949381", extracted)
            self.assertNotIn("member message must not leak", extracted)
            self.assertEqual(code, 0)
            posted = stdout.getvalue().strip()
            self.assertIn("package attached; mailing and county filing are manual.", posted)
            self.assertNotIn("250.00", posted)
            self.assertNotIn("Hidden Preview Name", posted)

    def test_repo_wiring(self) -> None:
        template = (ROOT / "templates" / "notice_to_vacate.txt").read_text()
        self.assertIn(evictions.ENTITY, template)
        self.assertIn(evictions.SIGNER, template)
        self.assertNotIn("phone", template.lower())
        self.assertNotRegex(template, r"\d{3}[-.)]\d{3}")
        morning = (ROOT / "run_morning.sh").read_text()
        self.assertLess(morning.index("scraper.py"), morning.index("evictions.py"))
        add_block = morning.split('git -C "$WORKSPACE" add', 1)[1].split("if git", 1)[0]
        self.assertNotIn("evictions", add_block)
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertIn("padsplit_scraper/output/evictions/", gitignore)
        self.assertIn("reportlab", (ROOT / "padsplit_scraper" / "requirements.txt").read_text())
        self.assertIn("evictions", runtime.ACTION_FLAGS)
        self.assertFalse(runtime.send_enabled("evictions", dry_env()))
        self.assertTrue(runtime.send_enabled("evictions", live_env()))


if __name__ == "__main__":
    unittest.main()
