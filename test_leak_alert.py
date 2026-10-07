#!/usr/bin/env python3
"""Dry-run leak alert planner. No live calls or texts."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import leak_alert
from padsplit_scraper import leak_reply
from padsplit_scraper import runtime
from test_leak_reply import NOW
from test_leak_reply import FakeSend
from test_leak_reply import firestore_t5_doc
from test_leak_reply import member_thread
from test_leak_reply import run_process


DON = "+15555550101"
TOM = "+15555550102"
JOE = "+15555550103"
TENANT_A = "+15555550111"
TENANT_B = "+15555550112"


def _env(**extra: str) -> dict:
    base = {
        "CI": "",
        "GITHUB_ACTIONS": "",
        "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
        "PADSPLIT_COLLECTION_ONLY": "0",
        "BLAND_API_KEY": "bland-test",
        "QUO_API_KEY": "quo-test",
        "LEAK_ALERT_DON_E164": DON,
        "LEAK_ALERT_TOM_E164": TOM,
        "LEAK_ALERT_JOE_E164": JOE,
        "LEAK_ALERT_ENABLE": "1",
    }
    base.update(extra)
    return base


def _roster(path: Path, numbers: list[str], slug: str = "leana_6623") -> None:
    path.write_text(json.dumps({slug: numbers}))


def _by_key(entries: list[dict]) -> dict:
    return {entry["key"]: entry for entry in entries}


class FlagTests(unittest.TestCase):
    def test_flag_defaults_off_and_ci_is_noop(self) -> None:
        quiet = {
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
            "PADSPLIT_COLLECTION_ONLY": "0",
        }
        self.assertFalse(runtime.send_enabled("leak_alert", quiet))
        self.assertFalse(leak_alert.live_enabled(quiet))
        enabled = dict(quiet)
        enabled["LEAK_ALERT_ENABLE"] = "1"
        self.assertTrue(leak_alert.live_enabled(enabled))
        ci = dict(enabled)
        ci["CI"] = "true"
        self.assertFalse(leak_alert.live_enabled(ci))
        collected = dict(enabled)
        collected["PADSPLIT_COLLECTION_ONLY"] = "1"
        self.assertFalse(leak_alert.live_enabled(collected))


class PlanTests(unittest.TestCase):
    def test_voice_is_bland_and_joe_text_is_standby_when_bland_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.json"
            _roster(roster, [TENANT_A, TENANT_B])
            plan = leak_alert.build_plan(
                member_thread(),
                now=NOW,
                house_threads=[member_thread()],
                environ=_env(),
                roster_path=roster,
            )
        self.assertIsNotNone(plan)
        assert plan is not None
        encoded = json.dumps(plan.entries)
        leak_alert.assert_plan_has_no_numbers(encoded)
        for secret in (DON, TOM, JOE, TENANT_A, TENANT_B, "bland-test", "quo-test"):
            self.assertNotIn(secret, encoded)
        rows = _by_key(plan.entries)
        incident = "chat-leana:m-leak"
        self.assertEqual(plan.incident, incident)
        for role in ("don", "tom", "joe"):
            voice = rows[f"voice:{incident}:{role}"]
            self.assertEqual(voice["provider"], "bland")
            self.assertEqual(voice["status"], "planned")
            self.assertEqual(voice["max_duration_min"], 1)
            self.assertEqual(voice["voicemail"], "leave_message")
            self.assertEqual(voice["bland"]["voicemail"]["action"], "leave_message")
            self.assertEqual(voice["bland"]["max_duration"], 1)
            self.assertIn("Category wall_ceiling", voice["script"])
            self.assertNotIn("phone_number", voice["bland"])
        joe = rows[f"joe:{incident}"]
        self.assertEqual(joe["status"], "standby")
        self.assertEqual(joe["mode"], "private_1to1")
        self.assertEqual(joe["provider"], "quo")
        self.assertEqual(joe["path"], "/v1/messages")
        tenant_keys = [key for key in rows if key.startswith(f"tenant:{incident}:")]
        self.assertEqual(len(tenant_keys), 2)
        for key in tenant_keys:
            self.assertEqual(rows[key]["mode"], "private_1to1")
            self.assertEqual(rows[key]["status"], "planned")
            self.assertIn("photos", rows[key]["body"])
        self.assertIn(
            f"tenant:{incident}:{leak_alert.recipient_hash(TENANT_A)}",
            rows,
        )
        self.assertNotIn("twilio", encoded.lower())

    def test_joe_falls_back_to_quo_text_without_bland_key(self) -> None:
        plan = leak_alert.build_plan(
            member_thread(),
            now=NOW,
            house_threads=[member_thread()],
            environ=_env(BLAND_API_KEY=""),
            roster_path=None,
        )
        assert plan is not None
        rows = _by_key(plan.entries)
        incident = plan.incident
        self.assertEqual(rows[f"voice:{incident}:joe"]["status"], "missing_credentials")
        self.assertEqual(rows[f"joe:{incident}"]["status"], "planned")
        self.assertEqual(rows[f"joe:{incident}"]["mode"], "private_1to1")

    def test_missing_numbers_have_no_defaults(self) -> None:
        plan = leak_alert.build_plan(
            member_thread(),
            now=NOW,
            house_threads=[member_thread()],
            environ=_env(
                LEAK_ALERT_DON_E164="",
                LEAK_ALERT_TOM_E164="",
                LEAK_ALERT_JOE_E164="",
            ),
        )
        assert plan is not None
        for entry in plan.entries:
            if entry["kind"] == "voice" or entry["key"].startswith("joe:"):
                self.assertEqual(entry["status"], "missing_number")
                self.assertFalse(entry["has_number"])

    def test_photo_already_attached_omits_photo_ask(self) -> None:
        thread = member_thread()
        thread["recent_messages"][0]["attachments"] = [
            {"mediaType": "PICTURE", "deleted": False}
        ]
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.json"
            _roster(roster, [TENANT_A])
            plan = leak_alert.build_plan(
                thread,
                now=NOW,
                house_threads=[thread],
                environ=_env(),
                roster_path=roster,
            )
        assert plan is not None
        body = next(entry["body"] for entry in plan.entries if entry["kind"] == "tenant_text" and entry["status"] == "planned")
        self.assertNotIn("photos", body.lower())
        self.assertIn("water is shut off", body)

    def test_over_cap_fails_closed_without_partial_recipients(self) -> None:
        numbers = [f"+155555501{index:02d}" for index in range(11)]
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.json"
            _roster(roster, numbers)
            plan = leak_alert.build_plan(
                member_thread(),
                now=NOW,
                house_threads=[member_thread()],
                environ=_env(),
                roster_path=roster,
            )
        assert plan is not None
        tenants = [entry for entry in plan.entries if entry["kind"] == "tenant_text"]
        self.assertEqual(len(tenants), 1)
        self.assertEqual(tenants[0]["status"], "fail_closed")
        self.assertEqual(tenants[0]["reason"], "over_cap")
        self.assertEqual(tenants[0]["count"], 11)
        encoded = json.dumps(plan.entries)
        for number in numbers:
            self.assertNotIn(number, encoded)
            self.assertNotIn(leak_alert.recipient_hash(number), encoded)

    def test_host_catchup_skips_tenants_and_still_plans_voice(self) -> None:
        catchup = "In case of a water leak the water is off\nhttps://youtube.com/shorts/SCryjPiyZcs"
        sibling = member_thread(
            chat_id="chat-leana-3",
            room=3,
            text="ok",
            host_texts=[catchup],
            host_created="2026-09-10T14:40:00Z",
        )
        reporter = member_thread()
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.json"
            _roster(roster, [TENANT_A])
            plan = leak_alert.build_plan(
                reporter,
                now=NOW,
                house_threads=[reporter, sibling],
                environ=_env(),
                roster_path=roster,
            )
        assert plan is not None
        tenants = [entry for entry in plan.entries if entry["kind"] == "tenant_text"]
        self.assertEqual(len(tenants), 1)
        self.assertEqual(tenants[0]["status"], "skipped_handled")
        self.assertNotIn(leak_alert.recipient_hash(TENANT_A), tenants[0]["key"])
        voices = [entry for entry in plan.entries if entry["kind"] == "voice"]
        self.assertTrue(all(entry["status"] == "planned" for entry in voices))

    def test_state_records_keys_once_and_never_stores_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.json"
            state_path = Path(tmp) / "leak_alert_state.json"
            _roster(roster, [TENANT_A])
            thread = member_thread()
            plan = leak_alert.build_plan(
                thread,
                now=NOW,
                house_threads=[thread],
                environ=_env(),
                roster_path=roster,
            )
            assert plan is not None
            state = leak_alert.load_state(state_path)
            first = leak_alert.persist_plan(plan, state, now=NOW)
            leak_alert.save_state(state, state_path)
            second = leak_alert.persist_plan(plan, state, now=NOW)
            raw = state_path.read_text()
            leak_alert.assert_plan_has_no_numbers(raw)
            for secret in (DON, TOM, JOE, TENANT_A, "bland-test", "quo-test"):
                self.assertNotIn(secret, raw)
            incident = "chat-leana:m-leak"
            self.assertIn(f"voice:{incident}:don", state["alerts"])
            self.assertIn(f"voice:{incident}:tom", state["alerts"])
            self.assertIn(f"voice:{incident}:joe", state["alerts"])
            self.assertIn(f"joe:{incident}", state["alerts"])
            self.assertEqual(state["alerts"][f"joe:{incident}"]["status"], "standby")
            self.assertIn(f"tenant:{incident}:{leak_alert.recipient_hash(TENANT_A)}", state["alerts"])
            self.assertEqual(state["alerts"][f"voice:{incident}:don"]["max_duration_min"], 1)
            self.assertEqual(state["alerts"][f"voice:{incident}:don"]["voicemail"], "leave_message")
            self.assertTrue(any(entry["status"] == "already_planned" for entry in second))
            self.assertTrue(all(entry["status"] == "planned" or entry["status"] == "standby" for entry in first if entry["kind"] == "voice" or entry["key"].startswith("joe:")))

    def test_module_does_not_call_http(self) -> None:
        source = Path("padsplit_scraper/leak_alert.py").read_text().lower()
        self.assertNotIn("twilio", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("urlopen", source)


class WireTests(unittest.TestCase):
    def test_process_persists_plan_without_sending_alerts(self) -> None:
        fake = FakeSend()
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.json"
            alert_path = Path(tmp) / "leak_alert_state.json"
            _roster(roster, [TENANT_A])
            rows, _ = run_process(
                fake,
                [member_thread()],
                plan_alerts=True,
                alert_enabled=True,
                alert_state_path=alert_path,
                alert_environ=_env(),
                roster_path=roster,
            )
            stored = json.loads(alert_path.read_text())
        self.assertEqual(rows[0]["action"], "sent")
        self.assertEqual(len(fake.sends), 1)
        self.assertIn("alerts", rows[0])
        self.assertTrue(stored["alerts"])
        leak_alert.assert_plan_has_no_numbers(json.dumps(stored))
        self.assertNotIn(TENANT_A, json.dumps(rows[0]["alerts"]))

    def test_ci_does_not_plan_or_send(self) -> None:
        fake = FakeSend()
        with patch.dict(os.environ, {"CI": "true"}, clear=False):
            rows, _ = run_process(
                fake,
                [member_thread()],
                plan_alerts=True,
                alert_enabled=True,
                alert_environ=_env(),
            )
            result = leak_reply.run(
                now=NOW,
                dry_run=False,
                host_messages=[member_thread()],
                leftover_compose_tabs=[],
                send_fn=fake.send,
                fetch_doc=lambda: firestore_t5_doc(),
                alert_environ=_env(),
            )
        self.assertNotIn("alerts", rows[0])
        self.assertEqual(result.action, "skip_ci")
        self.assertEqual(result.sent, 0)

    def test_alert_flag_plans_when_padsplit_send_is_off(self) -> None:
        fake = FakeSend()
        with tempfile.TemporaryDirectory() as tmp:
            roster = Path(tmp) / "roster.json"
            alert_path = Path(tmp) / "leak_alert_state.json"
            state_path = Path(tmp) / "leak_state.json"
            _roster(roster, [TENANT_A])
            with patch.object(leak_reply, "live_send_enabled", return_value=False):
                result = leak_reply.run(
                    now=NOW,
                    dry_run=False,
                    host_messages=[member_thread()],
                    leftover_compose_tabs=[],
                    send_fn=fake.send,
                    post_discord=fake.posts.append,
                    state_path=state_path,
                    fetch_doc=lambda: firestore_t5_doc(),
                    alert_state_path=alert_path,
                    alert_environ=_env(),
                    roster_path=roster,
                )
            raw = alert_path.read_text()
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])
        self.assertEqual(result.results[0]["action"], "would_send")
        self.assertIn("alerts", result.results[0])
        leak_alert.assert_plan_has_no_numbers(raw)
        self.assertIn("voice:chat-leana:m-leak:don", raw)

    def test_dry_run_plans_without_writing_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            alert_path = Path(tmp) / "leak_alert_state.json"
            state_path = Path(tmp) / "leak_state.json"
            with patch.object(leak_reply, "live_send_enabled", return_value=False), patch.object(
                leak_alert, "live_enabled", return_value=True
            ):
                result = leak_reply.run(
                    now=NOW,
                    dry_run=True,
                    host_messages=[member_thread()],
                    leftover_compose_tabs=[],
                    state_path=state_path,
                    fetch_doc=lambda: firestore_t5_doc(),
                    alert_state_path=alert_path,
                    alert_environ=_env(BLAND_API_KEY=""),
                    roster_path=None,
                )
        self.assertFalse(alert_path.exists())
        alerts = result.results[0]["alerts"]
        joe = next(entry for entry in alerts if entry["key"].startswith("joe:"))
        self.assertEqual(joe["status"], "planned")


if __name__ == "__main__":
    unittest.main()
