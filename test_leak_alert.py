#!/usr/bin/env python3
"""Dry-run leak alert planner. No live calls or texts."""

import base64
import json
import os
import re
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
GROUP_A = "+15555550121"
GROUP_B = "+15555550122"
GROUP_C = "+15555550123"
GROUP_LIST = f"{GROUP_A}, {GROUP_B},{GROUP_C}"
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
        "LEAK_ALERT_GROUP_E164S": GROUP_LIST,
        "LEAK_ALERT_ENABLE": "1",
    }
    base.update(extra)
    return base


def _gid(pk: int) -> str:
    raw = base64.b64encode(f"MessengerOccupancyType:{pk}".encode()).decode()
    return raw.rstrip("=")


def _house(pk: int, **kwargs) -> dict:
    thread = member_thread(**kwargs)
    thread["occupancy"]["id"] = _gid(pk)
    return thread


def _profiles(mapping: dict[str, str]):
    def fetch(pk: str):
        number = mapping.get(str(pk))
        if not number:
            return {}
        return {"phone_number": number}

    return fetch


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
    def test_voice_is_bland_for_don_and_tom_and_group_is_one_quo_post(self) -> None:
        reporter = _house(9001)
        sibling = _house(9002, chat_id="chat-leana-2", room=3, text="ok")
        plan = leak_alert.build_plan(
            reporter,
            now=NOW,
            house_threads=[reporter, sibling],
            environ=_env(),
            profile_fetcher=_profiles({"9001": TENANT_A, "9002": TENANT_B}),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        encoded = json.dumps(plan.entries)
        leak_alert.assert_plan_has_no_numbers(encoded)
        for secret in (DON, TOM, GROUP_A, GROUP_B, GROUP_C, TENANT_A, TENANT_B, "bland-test", "quo-test"):
            self.assertNotIn(secret, encoded)
        self.assertNotIn("9001", encoded)
        self.assertNotIn("9002", encoded)
        rows = _by_key(plan.entries)
        incident = "chat-leana:m-leak"
        self.assertEqual(plan.incident, incident)
        self.assertNotIn(f"voice:{incident}:joe", rows)
        self.assertNotIn(f"joe:{incident}", rows)
        self.assertNotIn(f"ang:{incident}", rows)
        for role in ("don", "tom"):
            voice = rows[f"voice:{incident}:{role}"]
            self.assertEqual(voice["provider"], "bland")
            self.assertEqual(voice["status"], "planned")
            self.assertEqual(voice["max_duration_min"], 1)
            self.assertEqual(voice["voicemail"], "leave_message")
            self.assertEqual(voice["bland"]["voicemail"]["action"], "leave_message")
            self.assertEqual(voice["bland"]["max_duration"], 1)
            self.assertIn("Category wall_ceiling", voice["script"])
            self.assertNotIn("phone_number", voice["bland"])
        group = rows[f"group:{incident}"]
        self.assertEqual(group["status"], "planned")
        self.assertEqual(group["mode"], "group")
        self.assertEqual(group["method"], "POST")
        self.assertEqual(group["provider"], "quo")
        self.assertEqual(group["path"], "/v1/messages")
        self.assertEqual(group["participant_count"], 3)
        self.assertEqual(group["conversation_check"], "skipped")
        self.assertNotIn("to", group)
        tenant_keys = [key for key in rows if key.startswith(f"tenant:{incident}:") and rows[key]["status"] == "planned"]
        self.assertEqual(len(tenant_keys), 2)
        for key in tenant_keys:
            self.assertEqual(rows[key]["mode"], "private_1to1")
            self.assertIn("photos", rows[key]["body"])
        self.assertIn(f"tenant:{incident}:{leak_alert.recipient_hash(TENANT_A)}", rows)
        self.assertNotIn("twilio", encoded.lower())

    def test_group_text_needs_quo_credentials(self) -> None:
        plan = leak_alert.build_plan(
            _house(9001),
            now=NOW,
            house_threads=[_house(9001)],
            environ=_env(QUO_API_KEY=""),
            profile_fetcher=_profiles({"9001": TENANT_A}),
        )
        assert plan is not None
        rows = _by_key(plan.entries)
        incident = plan.incident
        self.assertEqual(rows[f"voice:{incident}:don"]["status"], "planned")
        self.assertEqual(rows[f"group:{incident}"]["status"], "missing_credentials")
        self.assertEqual(rows[f"group:{incident}"]["mode"], "group")
        tenant = next(entry for entry in plan.entries if entry["kind"] == "tenant_text")
        self.assertEqual(tenant["status"], "missing_credentials")

    def test_missing_numbers_have_no_defaults(self) -> None:
        plan = leak_alert.build_plan(
            member_thread(),
            now=NOW,
            house_threads=[member_thread()],
            environ=_env(
                LEAK_ALERT_DON_E164="",
                LEAK_ALERT_TOM_E164="",
                LEAK_ALERT_GROUP_E164S="",
            ),
        )
        assert plan is not None
        for entry in plan.entries:
            if entry["kind"] == "voice":
                self.assertEqual(entry["status"], "missing_number")
                self.assertFalse(entry["has_number"])
        group = next(entry for entry in plan.entries if entry["key"].startswith("group:"))
        self.assertEqual(group["status"], "skipped")
        self.assertEqual(group["reason"], "group_unset")
        source = Path("padsplit_scraper/leak_alert.py").read_text()
        self.assertNotIn("LEAK_ALERT_JOE", source)
        self.assertNotIn("LEAK_ALERT_ANG_E164", source)
        self.assertNotIn("LEAK_TENANT_ROSTER", source)
        self.assertIsNone(re.search(r"\+\d{10,}", source))

    def test_photo_already_attached_omits_photo_ask(self) -> None:
        thread = _house(9001)
        thread["recent_messages"][0]["attachments"] = [
            {"mediaType": "PICTURE", "deleted": False}
        ]
        plan = leak_alert.build_plan(
            thread,
            now=NOW,
            house_threads=[thread],
            environ=_env(),
            profile_fetcher=_profiles({"9001": TENANT_A}),
        )
        assert plan is not None
        body = next(
            entry["body"]
            for entry in plan.entries
            if entry["kind"] == "tenant_text" and entry["status"] == "planned"
        )
        self.assertNotIn("photos", body.lower())
        self.assertIn("water is shut off", body)

    def test_over_cap_fails_closed_without_partial_recipients(self) -> None:
        numbers = [f"+155555501{index:02d}" for index in range(11)]
        threads = []
        mapping = {}
        for index, number in enumerate(numbers):
            pk = 9100 + index
            threads.append(_house(pk, chat_id=f"chat-{index}", room=index + 1, text="ok" if index else "water leaking from the ceiling"))
            mapping[str(pk)] = number
        plan = leak_alert.build_plan(
            threads[0],
            now=NOW,
            house_threads=threads,
            environ=_env(),
            profile_fetcher=_profiles(mapping),
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

    def test_no_phone_records_a_skip_and_sends_nothing(self) -> None:
        plan = leak_alert.build_plan(
            _house(9001),
            now=NOW,
            house_threads=[_house(9001)],
            environ=_env(),
            profile_fetcher=lambda _pk: {},
        )
        assert plan is not None
        tenants = [entry for entry in plan.entries if entry["kind"] == "tenant_text"]
        self.assertEqual(len(tenants), 1)
        self.assertEqual(tenants[0]["status"], "skipped_no_phone")
        self.assertNotIn("phone_number", tenants[0])
        state = {"alerts": {}}
        leak_alert.persist_plan(plan, state, now=NOW)
        self.assertNotIn(tenants[0]["key"], state["alerts"])
        self.assertTrue(any(entry["status"] == "planned" for entry in plan.entries if entry["kind"] == "voice"))

    def test_member_phone_endpoint_uses_occupancy_pk_and_phone_number(self) -> None:
        class FakeResponse:
            status_code = 200

            def json(self):
                return {"phone_number": TENANT_A}

        class FakeSession:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def get(self, url, timeout=None):
                self.urls.append(url)
                return FakeResponse()

        session = FakeSession()
        thread = member_thread()
        thread["occupancy"]["id"] = _gid(9001)
        plan = leak_alert.build_plan(
            thread,
            now=NOW,
            house_threads=[thread],
            environ=_env(),
            profile_fetcher=leak_alert.member_phone_fetcher(session),
        )
        assert plan is not None
        self.assertEqual(
            session.urls,
            ["https://www.padsplit.com/api/host-member-profile/member-phone/9001/"],
        )
        encoded = json.dumps(plan.entries)
        self.assertNotIn(TENANT_A, encoded)
        self.assertIn(
            f"tenant:{plan.incident}:{leak_alert.recipient_hash(TENANT_A)}",
            encoded,
        )
        self.assertEqual(leak_alert.phone_from_profile_payload({"phoneNumber": TENANT_B}), TENANT_B)

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

        def forbid(_pk: str):
            raise AssertionError("profile fetch should not run when the house is already handled")

        plan = leak_alert.build_plan(
            reporter,
            now=NOW,
            house_threads=[reporter, sibling],
            environ=_env(),
            profile_fetcher=forbid,
        )
        assert plan is not None
        tenants = [entry for entry in plan.entries if entry["kind"] == "tenant_text"]
        self.assertEqual(len(tenants), 1)
        self.assertEqual(tenants[0]["status"], "skipped_handled")
        voices = [entry for entry in plan.entries if entry["kind"] == "voice"]
        self.assertEqual([entry["role"] for entry in voices], ["don", "tom"])
        self.assertTrue(all(entry["status"] == "planned" for entry in voices))
        group = next(entry for entry in plan.entries if entry["key"].startswith("group:"))
        self.assertEqual(group["status"], "planned")
        self.assertEqual(group["mode"], "group")

    def test_state_records_keys_once_and_never_stores_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "leak_alert_state.json"
            thread = _house(9001)
            plan = leak_alert.build_plan(
                thread,
                now=NOW,
                house_threads=[thread],
                environ=_env(),
                profile_fetcher=_profiles({"9001": TENANT_A}),
            )
            assert plan is not None
            state = leak_alert.load_state(state_path)
            first = leak_alert.persist_plan(plan, state, now=NOW)
            leak_alert.save_state(state, state_path)
            second = leak_alert.persist_plan(plan, state, now=NOW)
            raw = state_path.read_text()
            leak_alert.assert_plan_has_no_numbers(raw)
            for secret in (DON, TOM, GROUP_A, GROUP_B, GROUP_C, TENANT_A, "bland-test", "quo-test", "9001"):
                self.assertNotIn(secret, raw)
            incident = "chat-leana:m-leak"
            self.assertIn(f"voice:{incident}:don", state["alerts"])
            self.assertIn(f"voice:{incident}:tom", state["alerts"])
            self.assertNotIn(f"voice:{incident}:joe", state["alerts"])
            self.assertNotIn(f"joe:{incident}", state["alerts"])
            self.assertNotIn(f"ang:{incident}", state["alerts"])
            self.assertEqual(state["alerts"][f"group:{incident}"]["status"], "planned")
            self.assertEqual(state["alerts"][f"group:{incident}"]["mode"], "group")
            self.assertIn(f"tenant:{incident}:{leak_alert.recipient_hash(TENANT_A)}", state["alerts"])
            self.assertEqual(state["alerts"][f"voice:{incident}:don"]["max_duration_min"], 1)
            self.assertEqual(state["alerts"][f"voice:{incident}:don"]["voicemail"], "leave_message")
            self.assertTrue(any(entry["status"] == "already_planned" for entry in second))
            self.assertTrue(
                all(
                    entry["status"] == "planned"
                    for entry in first
                    if entry["kind"] == "voice" or entry["key"].startswith("group:")
                )
            )

    def test_module_does_not_send_or_import_a_client(self) -> None:
        source = Path("padsplit_scraper/leak_alert.py").read_text().lower()
        self.assertNotIn("twilio", source)
        self.assertNotIn("requests", source)
        self.assertIn("urlopen", source)
        self.assertIn("/v1/conversations", source)


class GroupConversationTests(unittest.TestCase):
    def _getter(self, pages: list[tuple[int, dict]]):
        calls: list[str] = []

        def fetch(url: str, headers: dict):
            calls.append(url)
            self.assertEqual(headers.get("Authorization"), "quo-test")
            self.assertNotIn("quo-test", url)
            return pages.pop(0)

        fetch.calls = calls  # type: ignore[attr-defined]
        return fetch

    def test_dry_run_matches_participants_without_storing_numbers(self) -> None:
        pages = [
            (200, {"data": [{"id": "CN-other", "participants": [GROUP_A]}], "nextPageToken": "page-2"}),
            (200, {"data": [{"id": "CN-group", "participants": [GROUP_C, GROUP_A, GROUP_B]}], "nextPageToken": None}),
        ]
        getter = self._getter(pages)
        plan = leak_alert.build_plan(
            member_thread(),
            now=NOW,
            house_threads=[member_thread()],
            environ=_env(LEAK_ALERT_GROUP_CONVERSATION_ID="CN-group"),
            dry_run=True,
            conversations_get=getter,
        )
        assert plan is not None
        group = next(entry for entry in plan.entries if entry["key"].startswith("group:"))
        self.assertEqual(group["status"], "planned")
        self.assertEqual(group["conversation_check"], "matched")
        self.assertEqual(len(getter.calls), 2)  # type: ignore[attr-defined]
        self.assertTrue(getter.calls[0].startswith("https://api.quo.com/v1/conversations?"))  # type: ignore[attr-defined]
        self.assertIn("pageToken=page-2", getter.calls[1])  # type: ignore[attr-defined]
        encoded = json.dumps(plan.entries)
        for secret in (GROUP_A, GROUP_B, GROUP_C, "quo-test"):
            self.assertNotIn(secret, encoded)

    def test_mismatch_skips_and_live_plan_does_not_get(self) -> None:
        def forbid(url: str, headers: dict):
            raise AssertionError("conversation check must not run unless dry-run")

        live = leak_alert.build_plan(
            member_thread(),
            now=NOW,
            house_threads=[member_thread()],
            environ=_env(LEAK_ALERT_GROUP_CONVERSATION_ID="CN-group"),
            dry_run=False,
            conversations_get=forbid,
        )
        assert live is not None
        live_group = next(entry for entry in live.entries if entry["key"].startswith("group:"))
        self.assertEqual(live_group["status"], "planned")
        self.assertEqual(live_group["conversation_check"], "not_run")

        def mismatch(url: str, headers: dict):
            return 200, {"data": [{"id": "CN-group", "participants": [GROUP_A]}], "nextPageToken": None}

        dry = leak_alert.build_plan(
            member_thread(),
            now=NOW,
            house_threads=[member_thread()],
            environ=_env(LEAK_ALERT_GROUP_CONVERSATION_ID="CN-group"),
            dry_run=True,
            conversations_get=mismatch,
        )
        assert dry is not None
        group = next(entry for entry in dry.entries if entry["key"].startswith("group:"))
        self.assertEqual(group["status"], "skipped")
        self.assertEqual(group["reason"], "conversation_mismatch")
        self.assertNotIn(GROUP_A, json.dumps(dry.entries))
        state = {"alerts": {}}
        leak_alert.persist_plan(dry, state, now=NOW)
        self.assertNotIn(group["key"], state["alerts"])

    def test_from_line_is_not_part_of_the_group_list(self) -> None:
        plan = leak_alert.build_plan(
            member_thread(),
            now=NOW,
            house_threads=[member_thread()],
            environ=_env(QUO_FROM_NUMBER=GROUP_A),
        )
        assert plan is not None
        group = next(entry for entry in plan.entries if entry["key"].startswith("group:"))
        self.assertEqual(group["participant_count"], 2)
        self.assertNotIn(GROUP_A, json.dumps(plan.entries))


class WireTests(unittest.TestCase):
    def test_process_persists_plan_without_sending_alerts(self) -> None:
        fake = FakeSend()
        thread = _house(9001)
        with tempfile.TemporaryDirectory() as tmp:
            alert_path = Path(tmp) / "leak_alert_state.json"
            rows, _ = run_process(
                fake,
                [thread],
                plan_alerts=True,
                alert_enabled=True,
                alert_state_path=alert_path,
                alert_environ=_env(),
                profile_fetcher=_profiles({"9001": TENANT_A}),
            )
            stored = json.loads(alert_path.read_text())
        self.assertEqual(rows[0]["action"], "sent")
        self.assertEqual(len(fake.sends), 1)
        self.assertIn("alerts", rows[0])
        self.assertTrue(stored["alerts"])
        leak_alert.assert_plan_has_no_numbers(json.dumps(stored))
        encoded = json.dumps(rows[0]["alerts"])
        self.assertNotIn(TENANT_A, encoded)
        self.assertNotIn(GROUP_A, encoded)
        self.assertIn("group:chat-leana:m-leak", json.dumps(stored))

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
        thread = _house(9001)
        with tempfile.TemporaryDirectory() as tmp:
            alert_path = Path(tmp) / "leak_alert_state.json"
            state_path = Path(tmp) / "leak_state.json"
            with patch.object(leak_reply, "live_send_enabled", return_value=False):
                result = leak_reply.run(
                    now=NOW,
                    dry_run=False,
                    host_messages=[thread],
                    leftover_compose_tabs=[],
                    send_fn=fake.send,
                    post_discord=fake.posts.append,
                    state_path=state_path,
                    fetch_doc=lambda: firestore_t5_doc(),
                    alert_state_path=alert_path,
                    alert_environ=_env(),
                    profile_fetcher=_profiles({"9001": TENANT_A}),
                )
            raw = alert_path.read_text()
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])
        self.assertEqual(result.results[0]["action"], "would_send")
        self.assertIn("alerts", result.results[0])
        leak_alert.assert_plan_has_no_numbers(raw)
        self.assertIn("voice:chat-leana:m-leak:don", raw)
        self.assertIn("group:chat-leana:m-leak", raw)
        self.assertNotIn("joe:", raw)
        self.assertNotIn("ang:", raw)

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
                )
        self.assertFalse(alert_path.exists())
        alerts = result.results[0]["alerts"]
        group = next(entry for entry in alerts if entry["key"].startswith("group:"))
        self.assertEqual(group["status"], "planned")
        self.assertEqual(group["mode"], "group")
        self.assertEqual(group["conversation_check"], "skipped")
        voices = [entry for entry in alerts if entry["kind"] == "voice"]
        self.assertEqual({entry["role"] for entry in voices}, {"don", "tom"})
        self.assertTrue(all(entry["status"] == "missing_credentials" for entry in voices))
        tenants = [entry for entry in alerts if entry["kind"] == "tenant_text"]
        self.assertEqual(tenants[0]["status"], "skipped_no_phone")

    def test_session_is_used_when_no_fetcher_is_injected(self) -> None:
        class FakeResponse:
            status_code = 200

            def json(self):
                return {"phone_number": TENANT_A}

        class FakeSession:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def get(self, url, timeout=None):
                self.urls.append(url)
                return FakeResponse()

        fake = FakeSend()
        session = FakeSession()
        thread = _house(9001)
        rows, _ = run_process(
            fake,
            [thread],
            plan_alerts=True,
            alert_enabled=False,
            alert_environ=_env(),
            session=session,
        )
        self.assertEqual(
            session.urls,
            ["https://www.padsplit.com/api/host-member-profile/member-phone/9001/"],
        )
        encoded = json.dumps(rows[0]["alerts"])
        self.assertNotIn(TENANT_A, encoded)
        self.assertIn(leak_alert.recipient_hash(TENANT_A), encoded)


if __name__ == "__main__":
    unittest.main()
