#!/usr/bin/env python3
"""Offline tests for Sifely code replies, the interlock, and change notices.

Labeled fakes only. No network.
"""

from __future__ import annotations

import io
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from padsplit_scraper import code_change_notify
from padsplit_scraper import code_reply
from padsplit_scraper import code_request
from padsplit_scraper import lock_codes
from padsplit_scraper import lockout_reply
from padsplit_scraper import partner_members
from padsplit_scraper import runtime
from padsplit_scraper.room_interlock import Allowed, Refused, assert_room_action_allowed
from padsplit_scraper.sifely_client import (
    PAGE_SIZE,
    CurrentCode,
    Inventory,
    SifelyClient,
    SifelyUnavailable,
    inventory_locks,
    select_current_code,
)


NOW = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)
CREATED = "2026-10-09T14:40:00Z"
FAKE_A = "FAKE-CODE-A"
FAKE_B = "FAKE-CODE-B"
FAKE_C = "FAKE-CODE-C"
ROOT = Path(__file__).resolve().parent
NEW_MODULES = (
    "sifely_client.py",
    "code_request.py",
    "room_interlock.py",
    "code_reply.py",
    "code_change_notify.py",
    "code_gates.py",
)


class Directory:
    def __init__(self, rows, *, as_of=None, error: BaseException | None = None, property_id: str = "bc"):
        self.rows = rows
        self.as_of = as_of
        self.error = error
        self.property_id = property_id

    def members_for(self, property_id: str):
        if self.error is not None:
            raise self.error
        return list(self.rows)

    def property_index(self):
        return [{"id": self.property_id, "street": "Broken Crest"}]


def live_row(**overrides) -> dict:
    row = {
        "occupancy_id": "occ-1",
        "room_number": "2",
        "move_out_date": None,
        "move_in_date": None,
        "is_terminated": False,
    }
    row.update(overrides)
    return row


def thread(
    text: str,
    *,
    room: str = "2",
    street: str = "Broken Crest Drive",
    occ: str = "occ-1",
    chat: str = "chat-1",
    created: str = CREATED,
    extra: list | None = None,
    move_out: str | None = None,
    move_in: str | None = None,
) -> dict:
    messages = [
        {
            "id": "m-1",
            "created": created,
            "text": text,
            "sender": {"roleId": "A_0"},
        }
    ]
    messages.extend(extra or [])
    return {
        "id": chat,
        "property": {"id": "bc", "address": {"street1": street}},
        "occupancy": {
            "id": occ,
            "moveInDate": move_in,
            "moveOutDate": move_out,
            "user": {"id": "user-1"},
            "room": {"roomNumber": room},
        },
        "recent_messages": messages,
    }


def crest_inventory() -> Inventory:
    return inventory_locks(
        [
            {"lockId": "LF", "lockAlias": "Broken Crest front door", "lockName": ""},
            {"lockId": "LR", "lockAlias": "Broken Crest room 2", "lockName": ""},
        ]
    )


def codes_for(lock_id: str) -> CurrentCode:
    if lock_id == "LF":
        return CurrentCode("ok", FAKE_A, "single")
    if lock_id == "LR":
        return CurrentCode("ok", FAKE_B, "single")
    return CurrentCode("ambiguous", "", "missing")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class FakeResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls = []

    def request(self, method, url, headers=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "timeout": timeout})
        return self.handler(self.calls[-1])


def enable_env(**extra: str) -> dict:
    env = {
        "CI": "",
        "GITHUB_ACTIONS": "",
        "PADSPLIT_COLLECTION_ONLY": "0",
        "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
    }
    env.update(extra)
    return env


class PinLiteralScannerTests(unittest.TestCase):
    def test_new_modules_have_no_pin_literals(self) -> None:
        pattern = re.compile(r"\b\d{4,8}\b")
        for name in NEW_MODULES:
            text = (ROOT / "padsplit_scraper" / name).read_text()
            for lineno, line in enumerate(text.splitlines(), 1):
                if "FAKE-CODE" in line:
                    continue
                match = pattern.search(line)
                self.assertIsNone(match, f"{name}:{lineno}")


class SifelyClientTests(unittest.TestCase):
    def test_pagination_follows_pages_past_twenty(self) -> None:
        def handler(call):
            self.assertEqual(call["params"]["pageSize"], str(PAGE_SIZE))
            self.assertNotIn("Bearer", call["headers"]["Authorization"])
            self.assertEqual(call["timeout"], (3, 5))
            page = call["params"]["pageNo"]
            if page == "1":
                rows = [{"lockId": f"L{i}", "lockAlias": "Broken Crest front door", "noKeyPwd": "hidden"} for i in range(20)]
                return FakeResponse({"code": 200, "data": {"list": rows, "pageNo": 1, "pages": 2, "total": 25}})
            rows = [{"lockId": f"L{i}", "lockAlias": "Broken Crest room 3"} for i in range(20, 25)]
            return FakeResponse({"code": 200, "data": {"list": rows, "pageNo": 2, "pages": 2, "total": 25}})

        clock = Clock()
        session = FakeSession(handler)
        client = SifelyClient("sk-example", session=session, sleep=clock.sleep, clock=clock, per_minute=1000)
        locks = client.list_locks(use_cache=False)
        self.assertEqual(len(locks), 25)
        self.assertNotIn("noKeyPwd", locks[0])
        self.assertEqual(len(session.calls), 2)

    def test_ambiguous_passcode_fails_closed(self) -> None:
        none = select_current_code([], NOW)
        two = select_current_code(
            [
                {"status": 1, "keyboardPwdType": 2, "keyboardPwd": FAKE_A},
                {"status": 1, "keyboardPwdType": 2, "keyboardPwd": FAKE_B},
            ],
            NOW,
        )
        one = select_current_code([{"status": 1, "keyboardPwdType": 2, "keyboardPwd": FAKE_A}], NOW)
        self.assertEqual(none.status, "ambiguous")
        self.assertEqual(none.code, "")
        self.assertEqual(two.status, "ambiguous")
        self.assertEqual(two.code, "")
        self.assertEqual(one.code, FAKE_A)

    def test_cache_ttl_and_rate_limit(self) -> None:
        clock = Clock()
        calls = {"n": 0}

        def handler(call):
            calls["n"] += 1
            return FakeResponse({"data": {"list": [{"lockId": "L1", "lockAlias": "Spanish Moss back door"}], "pages": 1, "total": 1}})

        client = SifelyClient(
            "sk-example",
            session=FakeSession(handler),
            sleep=clock.sleep,
            clock=clock,
            per_minute=25,
            lock_list_ttl=60,
        )
        client.list_locks()
        client.list_locks()
        self.assertEqual(calls["n"], 1)
        clock.t += 61
        client.list_locks()
        self.assertEqual(calls["n"], 2)
        paced = Clock()

        def paced_handler(call):
            return FakeResponse({"data": {"list": [{"lockId": "L1", "lockAlias": "Parker front"}], "pages": 1, "total": 1}})

        paced_client = SifelyClient(
            "sk-example",
            session=FakeSession(paced_handler),
            sleep=paced.sleep,
            clock=paced,
            per_minute=25,
        )
        paced_client.list_locks(use_cache=False)
        paced_client.list_locks(use_cache=False)
        self.assertAlmostEqual(paced.slept[0], 2.4, places=2)

    def test_timeout_retries_once_and_429_backs_off(self) -> None:
        clock = Clock()
        state = {"n": 0}

        def handler(call):
            state["n"] += 1
            if state["n"] == 1:
                raise requests.Timeout("slow")
            return FakeResponse({"data": {"list": [], "pages": 1, "total": 0}})

        client = SifelyClient("sk-example", session=FakeSession(handler), sleep=clock.sleep, clock=clock, per_minute=1000, rng=lambda: 0.0)
        self.assertEqual(client.list_locks(use_cache=False), [])
        self.assertEqual(state["n"], 2)

        state["n"] = 0

        def limited(call):
            state["n"] += 1
            if state["n"] == 1:
                return FakeResponse({}, status=429)
            return FakeResponse({"data": {"list": [{"lockId": "L9", "lockAlias": "Parker front door"}], "pages": 1}})

        client = SifelyClient("sk-example", session=FakeSession(limited), sleep=clock.sleep, clock=clock, per_minute=1000, rng=lambda: 0.0)
        locks = client.list_locks(use_cache=False)
        self.assertEqual(locks[0]["lockId"], "L9")
        self.assertIn(2.0, clock.slept)


# phrase, is_code_request, kind, ambiguous, lockout
FAST_PHRASES = [
    ("whats the code", True, "unknown", True, False),
    ("send me the code pls", True, "unknown", True, False),
    ("code?", True, "unknown", True, False),
    ("the code isn't working", True, "door", False, True),
    ("the code isnt working", True, "door", False, True),
    ("keypad not working", True, "door", False, True),
    ("lock is not working", True, "door", False, True),
    ("the lock is dead", True, "door", False, True),
    ("battery died on the lock", True, "door", False, True),
    ("help I can't open my door", True, "door", False, True),
    ("I cant open my door", True, "door", False, True),
    ("I lost my key", True, "unknown", True, False),
    ("cual es el codigo", True, "unknown", True, False),
    ("cuál es el código", True, "unknown", True, False),
    ("no puedo entrar", True, "door", False, True),
    ("se me olvido el codigo", True, "unknown", True, False),
    ("se me olvidó el código", True, "unknown", True, False),
    ("estoy afuera y no puedo entrar", True, "door", False, True),
    ("whats the cod", True, "unknown", True, False),
    ("send the coed pls", True, "unknown", True, False),
    ("lockd out", True, "door", False, True),
    ("loked out", True, "door", False, True),
    ("cant get inn", True, "door", False, True),
    ("what's my code", True, "unknown", True, False),
    ("new code please", True, "unknown", True, False),
    ("I forgot my code", True, "unknown", True, False),
    ("what is the wifi password", True, "wifi", False, False),
    ("my rent is late", False, "unknown", False, False),
    ("the toilet is leaking", False, "unknown", False, False),
    ("thanks!", False, "unknown", False, False),
    ("who changed the code?", False, "unknown", False, False),
]


class ClassifierTests(unittest.TestCase):
    def test_phrase_table(self) -> None:
        for phrase, is_request, kind, ambiguous, lockout in FAST_PHRASES:
            with self.subTest(phrase=phrase):
                got = code_request.classify_fast(phrase)
                self.assertEqual(got.is_code_request, is_request)
                self.assertEqual(got.kind, kind)
                self.assertEqual(got.ambiguous, ambiguous)
                self.assertEqual(got.lockout, lockout)

    def test_lockout_is_a_door_request_and_wifi_is_not_answered(self) -> None:
        door = code_request.classify_fast("I'm locked out and can't get in")
        self.assertTrue(door.is_code_request)
        self.assertEqual(door.kind, "door")
        self.assertTrue(door.lockout)
        wifi = code_request.classify_fast("what is the wifi password")
        self.assertEqual(wifi.kind, "wifi")
        self.assertFalse(wifi.ambiguous)

    def test_jev_is_off_by_default_and_errors_fall_back(self) -> None:
        calls = []

        def post(payload, timeout):
            calls.append(payload)
            raise TimeoutError("slow")

        jev = code_request.JevClassifier(post=post, environ={"JEV_CODE_CLASSIFY_ENABLE": "", "AI_GATEWAY_API_KEY": "secret"})
        result = code_request.classify("what's my code", jev)
        self.assertEqual(calls, [])
        self.assertTrue(result.ambiguous)
        self.assertEqual(result.mentioned_room, "")

        env = enable_env(JEV_CODE_CLASSIFY_ENABLE="1", AI_GATEWAY_API_KEY="secret")
        jev = code_request.JevClassifier(post=post, environ=env)
        fallback = jev.refine("what's my code", result)
        self.assertTrue(fallback.ambiguous)
        self.assertEqual(fallback.mentioned_room, "")
        self.assertTrue(calls)
        self.assertEqual(calls[0]["model"], "typesafe-ai/jev")
        self.assertIsInstance(calls[0]["state"], str)
        self.assertEqual(calls[0]["providerOptions"]["gateway"]["zeroDataRetention"], True)
        self.assertEqual(calls[0]["providerOptions"]["gateway"]["disallowPromptTraining"], True)
        self.assertNotIn("9", calls[0]["state"])

    def test_jev_cannot_choose_a_room(self) -> None:
        sent = {}

        def post(payload, timeout):
            sent["payload"] = payload
            sent["timeout"] = timeout
            return {
                "answers": {
                    "is_code_request": {"type": "boolean", "probability": 0.99},
                    "kind": {
                        "type": "choice",
                        "choice": "room",
                        "probabilities": {"room": 0.99, "door": 0.01, "lockbox": 0, "wifi": 0, "unknown": 0},
                    },
                    "room": "9",
                }
            }

        env = enable_env(JEV_CODE_CLASSIFY_ENABLE="1", AI_GATEWAY_API_KEY="secret")
        jev = code_request.JevClassifier(post=post, environ=env)
        fast = code_request.classify_fast("what's my code for room 2")
        refined = jev.refine("what's my code for room 2", fast)
        self.assertEqual(refined.mentioned_room, "2")
        self.assertEqual(refined.kind, "room")
        self.assertNotIn("9", sent["payload"]["state"])
        self.assertEqual(sent["timeout"], code_request.JEV_TIMEOUT_S)

    def test_unclear_messages_call_jev_and_failures_log_a_reason(self) -> None:
        calls = []

        def post(payload, timeout):
            calls.append(payload)
            self.assertEqual(timeout, code_request.JEV_TIMEOUT_S)
            return {
                "answers": {
                    "is_code_request": {"type": "boolean", "probability": 0.91},
                    "kind": {
                        "type": "choice",
                        "choice": "door",
                        "probabilities": {"door": 0.91, "room": 0, "lockbox": 0, "wifi": 0, "unknown": 0},
                    },
                }
            }

        env = enable_env(JEV_CODE_CLASSIFY_ENABLE="1", AI_GATEWAY_API_KEY="secret")
        jev = code_request.JevClassifier(post=post, environ=env)
        for phrase in ("hey is there a way in", "yo can u send it again"):
            with self.subTest(phrase=phrase):
                got = code_request.classify(phrase, jev)
                self.assertTrue(got.is_code_request)
                self.assertEqual(got.kind, "door")
                self.assertEqual(got.mentioned_room, "")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["model"], "typesafe-ai/jev")
        self.assertIsInstance(calls[0]["state"], str)

        def explode(payload, timeout):
            raise TimeoutError("way in " + payload["state"])

        noisy = code_request.JevClassifier(post=explode, environ=env)
        logs = io.StringIO()
        with redirect_stderr(logs):
            missed = code_request.classify("hey is there a way in", noisy)
        self.assertFalse(missed.is_code_request)
        self.assertIn("timeout", logs.getvalue())
        self.assertNotIn("way in", logs.getvalue())
        self.assertNotIn("hey", logs.getvalue())

        quiet = len(calls)
        code_request.classify("my rent is late", jev)
        code_request.classify("thanks!", jev)
        code_request.classify("the toilet is leaking", jev)
        self.assertEqual(len(calls), quiet)

    def test_evaluate_post_matches_the_gateway(self) -> None:
        captured = {}

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "answers": {
                        "is_code_request": {"type": "boolean", "probability": 0.8},
                        "kind": {"type": "choice", "choice": "door", "probabilities": {"door": 0.8}},
                    }
                }

        class FakeSession:
            def __init__(self) -> None:
                self.trust_env = True
                self.mounted = []

            def mount(self, prefix, adapter) -> None:
                self.mounted.append((prefix, adapter))

            def post(self, url, headers=None, json=None, timeout=None):
                captured["url"] = url
                captured["headers"] = headers
                captured["json"] = json
                captured["timeout"] = timeout
                captured["trust_env"] = self.trust_env
                captured["mounted"] = list(self.mounted)
                return Resp()

        with patch("requests.Session", FakeSession):
            body = code_request._default_post(
                code_request.jev_payload("is there a way in"),
                code_request.JEV_TIMEOUT_S,
                {"AI_GATEWAY_API_KEY": "test-key"},
            )
        self.assertEqual(captured["url"], "https://ai-gateway.vercel.sh/v1/evaluate")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertIsInstance(captured["json"]["questions"], dict)
        self.assertNotIsInstance(captured["json"]["questions"], list)
        self.assertEqual(captured["json"]["model"], "typesafe-ai/jev")
        self.assertEqual(captured["json"]["state"], "is there a way in")
        self.assertEqual(captured["json"]["questions"]["is_code_request"]["type"], "boolean")
        self.assertEqual(captured["json"]["questions"]["kind"]["type"], "choice")
        self.assertTrue(captured["json"]["providerOptions"]["gateway"]["zeroDataRetention"])
        self.assertFalse(captured["trust_env"])
        self.assertEqual(captured["timeout"].total, code_request.JEV_TIMEOUT_S)
        self.assertLessEqual(captured["timeout"].connect_timeout, 2)
        retries = captured["mounted"][0][1].max_retries
        self.assertEqual(int(retries.total), 0)
        self.assertEqual(body["answers"]["kind"]["choice"], "door")

        with self.assertRaises(code_request.JevError) as raised:
            code_request._default_post(
                {"model": "typesafe-ai/jev", "state": "hi", "questions": [{"type": "boolean"}]},
                code_request.JEV_TIMEOUT_S,
                {"AI_GATEWAY_API_KEY": "test-key"},
            )
        self.assertIn("record", str(raised.exception))

    def test_jev_smoke_prints_flags_without_the_key(self) -> None:
        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {
                    "answers": {
                        "class": {"type": "choice", "choice": "A", "probabilities": {"A": 0.9, "B": 0.05, "C": 0.05}},
                        "needs_photo": {"type": "boolean", "probability": 0.1},
                        "is_actual_water_leak": {"type": "boolean", "probability": 0.02},
                    }
                }

        class FakeSession:
            def __init__(self) -> None:
                self.trust_env = True

            def mount(self, prefix, adapter) -> None:
                return None

            def post(self, url, headers=None, json=None, timeout=None):
                self.last = {"url": url, "json": json, "timeout": timeout}
                FakeSession.last = self.last
                return Resp()

        out = io.StringIO()
        with patch("requests.Session", FakeSession), patch.dict(os.environ, {"AI_GATEWAY_API_KEY": ""}, clear=False), redirect_stdout(out):
            code = code_request.main(["--jev-smoke"])
        self.assertEqual(code, 2)
        self.assertIn("missing AI_GATEWAY_API_KEY", out.getvalue())

        out = io.StringIO()
        err = io.StringIO()
        env = {"AI_GATEWAY_API_KEY": "test-key", "JEV_CODE_CLASSIFY_ENABLE": "1"}
        with patch("requests.Session", FakeSession), patch.dict(os.environ, env, clear=False), redirect_stdout(out), redirect_stderr(err):
            code = code_request.main(["--jev-smoke", "--debug"])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("status=200", text)
        self.assertIn("elapsed_s=", text)
        self.assertIn("class=A", text)
        self.assertIn("needs_photo=False", text)
        self.assertIn("is_actual_water_leak=False", text)
        self.assertNotIn("test-key", text)
        self.assertNotIn(code_request._SMOKE_TEXT, text)
        sent = FakeSession.last["json"]["questions"]
        self.assertIsInstance(sent, dict)
        self.assertEqual(set(sent), {"class", "needs_photo", "is_actual_water_leak"})
        self.assertEqual(sent["class"]["type"], "choice")
        self.assertEqual(sent["needs_photo"]["type"], "boolean")
        self.assertEqual(sent["is_actual_water_leak"]["type"], "boolean")
        self.assertIn("true", sent["needs_photo"]["criteria"])
        self.assertIn("A", sent["class"]["criteria"])

    def test_jev_http_error_logs_body_snippet_without_the_message(self) -> None:
        state = "The sink is dripping slowly."

        class Resp:
            status_code = 400
            text = 'expected record, received array ' + state

            def json(self):
                return {}

        class FakeSession:
            def __init__(self) -> None:
                self.trust_env = True

            def mount(self, prefix, adapter) -> None:
                return None

            def post(self, url, headers=None, json=None, timeout=None):
                return Resp()

        out = io.StringIO()
        err = io.StringIO()
        env = {"AI_GATEWAY_API_KEY": "test-key"}
        with patch("requests.Session", FakeSession), redirect_stdout(out), redirect_stderr(err):
            code = code_request.jev_smoke(env, debug=True)
        self.assertEqual(code, 1)
        combined = out.getvalue() + err.getvalue()
        self.assertIn("status=400", out.getvalue())
        self.assertIn("expected record, received array", combined)
        self.assertNotIn(state, combined)
        self.assertNotIn("test-key", combined)

    def test_disabled_even_when_collection_only(self) -> None:
        def post(payload, timeout):
            raise AssertionError("jev should not run")

        env = {
            "CI": "",
            "GITHUB_ACTIONS": "",
            "JEV_CODE_CLASSIFY_ENABLE": "1",
            "AI_GATEWAY_API_KEY": "secret",
        }
        jev = code_request.JevClassifier(post=post, environ=env)
        self.assertFalse(jev.enabled())


class ReplyResolutionTests(unittest.TestCase):
    def test_ambiguous_ask_sends_own_room_and_front_door(self) -> None:
        for phrase in ("what's my code", "new code please", "I forgot my code", "whats the code"):
            with self.subTest(phrase=phrase):
                sent = []
                rows = process(
                    [thread(phrase)],
                    directory=Directory([live_row()]),
                    send_fn=lambda chat, body: sent.append(body),
                    send_enabled=True,
                    dry_run=False,
                )
                self.assertEqual(rows[0]["action"], "sent")
                self.assertIn(FAKE_A, sent[0])
                self.assertIn(FAKE_B, sent[0])
                self.assertEqual(sent[0].count(FAKE_A), 1)
                self.assertEqual(sent[0].count(FAKE_B), 1)

    def test_ambiguous_other_room_does_not_send_that_room(self) -> None:
        sent = []
        rows = process(
            [thread("whats the code for room 9")],
            directory=Directory([live_row()]),
            send_fn=lambda chat, body: sent.append(body),
            send_enabled=True,
            dry_run=False,
        )
        self.assertEqual(rows[0]["action"], "sent")
        self.assertIn(FAKE_A, sent[0])
        self.assertNotIn(FAKE_B, sent[0])

    def test_wifi_password_is_not_sent_on_this_path(self) -> None:
        sent = []
        rows = process(
            [thread("what is the wifi password")],
            directory=Directory([live_row()]),
            send_fn=lambda chat, body: sent.append(body),
            send_enabled=True,
            dry_run=False,
        )
        self.assertEqual(rows[0]["action"], "skip_wifi")
        self.assertEqual(sent, [])

    def test_who_changed_the_code_is_not_a_request(self) -> None:
        sent = []
        rows = process(
            [thread("who changed the code?")],
            directory=Directory([live_row()]),
            send_fn=lambda chat, body: sent.append(body),
            send_enabled=True,
            dry_run=False,
        )
        self.assertEqual(rows, [])
        self.assertEqual(sent, [])

    def test_text_claimed_room_does_not_send_that_room_code(self) -> None:
        sent = []
        flags = []
        rows = process(
            [thread("what is my room code for room 9")],
            directory=Directory([live_row()]),
            send_fn=lambda chat, body: sent.append(body),
            post_discord=flags.append,
            send_enabled=True,
            dry_run=False,
        )
        self.assertEqual(rows[0]["action"], "sent")
        self.assertIn(FAKE_A, sent[0])
        self.assertNotIn(FAKE_B, sent[0])
        self.assertNotIn("9", "".join(flags))
        self.assertFalse(any(ch.isdigit() for ch in "".join(flags)))
        self.assertIn("needs a tap", flags[0])

    def test_terminated_moved_out_multiple_and_api_down_do_not_send(self) -> None:
        sent = []

        def run(directory, **thread_kwargs):
            return process(
                [thread("I'm locked out", **thread_kwargs)],
                directory=directory,
                send_fn=lambda chat, body: sent.append(body),
                send_enabled=True,
                dry_run=False,
            )

        terminated = run(Directory([live_row(is_terminated=True)]))
        self.assertEqual(terminated[0]["action"], "no_send")
        moved = run(Directory([live_row(move_out_date="2026-10-01")]))
        self.assertEqual(moved[0]["reason"], "moved out")
        multiple = run(
            Directory(
                [
                    live_row(),
                    live_row(occupancy_id="occ-2"),
                ]
            )
        )
        self.assertEqual(multiple[0]["reason"], "multiple occupants")
        down = run(Directory([], error=partner_members.MembersRequestError("request failed")))
        self.assertEqual(down[0]["reason"], "partner members unavailable")
        self.assertEqual(sent, [])

    def test_sifely_timeout_falls_back_to_digest(self) -> None:
        sent = []

        def boom(lock_id: str) -> CurrentCode:
            raise SifelyUnavailable("timeout")

        rows = process(
            [thread("what is my room code")],
            directory=Directory([live_row()]),
            passcodes_for=boom,
            digest_for=lambda slug: {"r2": FAKE_C},
            send_fn=lambda chat, body: sent.append(body),
            send_enabled=True,
            dry_run=False,
        )
        self.assertEqual(rows[0]["action"], "sent")
        self.assertEqual(rows[0]["source"], "digest")
        self.assertIn(FAKE_C, sent[0])

    def test_mismatch_replies_with_sifely_and_syncs_only_when_enabled(self) -> None:
        sent = []
        flags = []
        db = FakeDB({"broken_crest_1025": {"r2": FAKE_C}})
        env = enable_env(CODES_DIGEST_SYNC_ENABLE="1")
        with patch.dict(os.environ, env, clear=False):
            rows = process(
                [thread("what is my room code")],
                directory=Directory([live_row()]),
                digest_for=lambda slug: {"r2": FAKE_C},
                send_fn=lambda chat, body: sent.append(body),
                post_discord=flags.append,
                sync_db=db,
                sync_enabled=runtime.send_enabled("codes_digest_sync", env),
                send_enabled=True,
                dry_run=False,
            )
        self.assertIn(FAKE_B, sent[0])
        self.assertNotIn(FAKE_C, sent[0])
        self.assertEqual(db.doc.data["r2"], FAKE_B)
        self.assertEqual(len(db.doc.versions), 1)
        self.assertEqual(db.doc.versions[0]["source"], "sifely_sync")
        self.assertTrue(flags)
        self.assertFalse(any(ch.isdigit() for flag in flags for ch in flag))
        self.assertNotIn(FAKE_B, "".join(flags))
        again = code_reply.sync_digest_field(
            db,
            slug="broken_crest_1025",
            field_name="r2",
            code=FAKE_B,
            source="sifely",
            role="room",
            room="2",
            now=NOW,
            enabled=True,
            written=set(),
        )
        self.assertEqual(again.action, "equal")
        self.assertEqual(len(db.doc.versions), 1)

    def test_ambiguous_sifely_does_not_send_or_write(self) -> None:
        sent = []
        db = FakeDB({"broken_crest_1025": {"r2": FAKE_C}})

        def ambiguous(lock_id: str) -> CurrentCode:
            return CurrentCode("ambiguous", "", "two")

        rows = process(
            [thread("what is my room code")],
            directory=Directory([live_row()]),
            passcodes_for=ambiguous,
            digest_for=lambda slug: {"r2": FAKE_C},
            send_fn=lambda chat, body: sent.append(body),
            sync_db=db,
            sync_enabled=True,
            send_enabled=True,
            dry_run=False,
        )
        self.assertEqual(rows[0]["action"], "missing_code")
        self.assertEqual(sent, [])
        self.assertEqual(db.doc.versions, [])

    def test_idempotent_and_stale_and_allowlist(self) -> None:
        sent = []
        state = {"replied": {}}
        first = process(
            [thread("I'm locked out")],
            directory=Directory([live_row()]),
            send_fn=lambda chat, body: sent.append(body),
            send_enabled=True,
            dry_run=False,
            state=state,
        )
        second = process(
            [thread("I'm locked out")],
            directory=Directory([live_row()]),
            send_fn=lambda chat, body: sent.append(body),
            send_enabled=True,
            dry_run=False,
            state=state,
        )
        self.assertEqual(first[0]["action"], "sent")
        self.assertEqual(second[0]["action"], "already_sent")
        self.assertEqual(len(sent), 1)
        stale = process(
            [thread("I'm locked out", created="2026-10-09T08:00:00Z")],
            directory=Directory([live_row()]),
            send_fn=lambda chat, body: sent.append(body),
            send_enabled=True,
            dry_run=False,
        )
        self.assertEqual(stale[0]["action"], "stale")
        env = enable_env(CODE_AUTOMATION_TEST_HOUSES="spanish_moss")
        skipped = process(
            [thread("I'm locked out")],
            directory=Directory([live_row()]),
            send_fn=lambda chat, body: sent.append("again"),
            send_enabled=True,
            dry_run=False,
            environ=env,
        )
        self.assertEqual(skipped[0]["action"], "skipped_allowlist")
        self.assertEqual(len(sent), 1)


class InterlockTests(unittest.TestCase):
    def test_occupied_refused_and_clear_cases_allowed(self) -> None:
        occupied = assert_room_action_allowed(
            "broken_crest_1025", "2", "rotate", directory=Directory([live_row()]), now=NOW, property_id="bc"
        )
        self.assertIsInstance(occupied, Refused)
        terminated = assert_room_action_allowed(
            "broken_crest_1025",
            "2",
            "reset",
            directory=Directory([live_row(is_terminated=True)]),
            now=NOW,
            property_id="bc",
        )
        self.assertIsInstance(terminated, Allowed)
        confirmed = assert_room_action_allowed(
            "broken_crest_1025",
            "2",
            "lockout",
            directory=Directory([live_row(events=["MOVE_OUT_CONFIRMED"])]),
            now=NOW,
            property_id="bc",
        )
        self.assertIsInstance(confirmed, Allowed)
        passed = assert_room_action_allowed(
            "broken_crest_1025",
            "2",
            "clear",
            directory=Directory([live_row(move_out_date="2026-10-01")]),
            now=NOW,
            property_id="bc",
        )
        self.assertIsInstance(passed, Allowed)
        still_there = assert_room_action_allowed(
            "broken_crest_1025",
            "2",
            "rotate",
            directory=Directory([live_row(move_out_date="2026-10-01", present_after_move_out=True)]),
            now=NOW,
            property_id="bc",
        )
        self.assertIsInstance(still_there, Refused)

    def test_failed_lookup_and_stale_snapshot_refuse(self) -> None:
        self.assertIsInstance(
            assert_room_action_allowed("broken_crest_1025", "2", "rotate", directory=None, now=NOW, property_id="bc"),
            Refused,
        )
        down = Directory([], error=partner_members.MembersRequestError("request failed"))
        self.assertIsInstance(
            assert_room_action_allowed("broken_crest_1025", "2", "rotate", directory=down, now=NOW, property_id="bc"),
            Refused,
        )
        stale = Directory([], as_of=NOW - timedelta(minutes=31))
        self.assertIsInstance(
            assert_room_action_allowed("broken_crest_1025", "2", "rotate", directory=stale, now=NOW, property_id="bc"),
            Refused,
        )
        fresh = Directory([], as_of=NOW - timedelta(minutes=10))
        self.assertIsInstance(
            assert_room_action_allowed("broken_crest_1025", "2", "rotate", directory=fresh, now=NOW, property_id="bc"),
            Allowed,
        )

    def test_move_in_requires_the_new_live_occupant(self) -> None:
        allowed = assert_room_action_allowed(
            "broken_crest_1025",
            "2",
            "move_in",
            directory=Directory([live_row()]),
            now=NOW,
            property_id="bc",
            new_occupancy_id="occ-1",
        )
        refused = assert_room_action_allowed(
            "broken_crest_1025",
            "2",
            "move_in",
            directory=Directory([live_row()]),
            now=NOW,
            property_id="bc",
            new_occupancy_id="occ-other",
        )
        vacant = assert_room_action_allowed(
            "broken_crest_1025",
            "2",
            "move_in",
            directory=Directory([]),
            now=NOW,
            property_id="bc",
            new_occupancy_id="occ-1",
        )
        self.assertIsInstance(allowed, Allowed)
        self.assertIsInstance(refused, Refused)
        self.assertIsInstance(vacant, Refused)

    def test_lock_codes_live_actions_default_off(self) -> None:
        off = enable_env(LOCK_CODES_ENABLE="", PADSPLIT_SEND_LOCK_CODES="")
        on = enable_env(LOCK_CODES_ENABLE="1")
        with patch.dict(os.environ, off, clear=False):
            self.assertFalse(lock_codes.live_actions_enabled())
        with patch.dict(os.environ, on, clear=False):
            self.assertTrue(lock_codes.live_actions_enabled())

    def test_vacancy_default_comes_from_env_only(self) -> None:
        calls = []
        with patch.dict(os.environ, enable_env(SIFELY_VACANT_ROOM_DEFAULT="", LOCK_CODES_ENABLE="1"), clear=False):
            self.assertFalse(
                lock_codes.reset_vacant_room_code(
                    lock_id="L",
                    keyboard_pwd_id="P",
                    house="spanish_moss",
                    room="1",
                    directory=Directory([]),
                    now=NOW,
                    property_id="bc",
                )
            )
        with patch.dict(os.environ, enable_env(SIFELY_VACANT_ROOM_DEFAULT=FAKE_A, LOCK_CODES_ENABLE="1", SIFELY_API_KEY="sk-example"), clear=False), patch.object(
            lock_codes, "change_passcode", side_effect=lambda *args, **kwargs: calls.append(kwargs.get("new_code"))
        ):
            self.assertTrue(
                lock_codes.reset_vacant_room_code(
                    lock_id="L",
                    keyboard_pwd_id="P",
                    house="spanish_moss",
                    room="1",
                    directory=Directory([], property_id="sm"),
                    now=NOW,
                    property_id="sm",
                )
            )
        self.assertEqual(calls, [FAKE_A])

    def test_occupied_room_blocks_spanish_moss_rotate(self) -> None:
        changed = []
        with patch.object(lockout_reply, "post_automations_discord", return_value=None), patch.object(
            lock_codes, "sifely_api_key", return_value="sk-example"
        ), patch.object(
            lock_codes, "list_locks", return_value=[{"lockId": "L", "lockAlias": "Spanish Moss back"}]
        ), patch.object(lock_codes, "resolve_lock", return_value={"lockId": "L"}), patch.object(
            lock_codes, "list_passcodes", return_value=[{"keyboardPwdId": "P", "keyboardPwdName": "tenant"}]
        ), patch.object(lock_codes, "resolve_keyboard_pwd_id", return_value="P"), patch.object(
            lock_codes, "change_passcode", side_effect=lambda *a, **k: changed.append("rotated")
        ):
            code, source = lockout_reply.obtain_spanish_moss_back(
                directory=Directory([live_row()]),
                room="2",
                now=NOW,
                property_id="bc",
            )
        self.assertEqual(code, "")
        self.assertEqual(source, "interlock_refused")
        self.assertEqual(changed, [])


class NoticeTests(unittest.TestCase):
    def test_room_shared_vacant_retry_and_idempotent(self) -> None:
        env = enable_env(CODE_CHANGE_NOTIFY_ENABLE="1", CODE_CHANGE_NOTIFY_DRY_RUN="0")
        with tempfile_state() as path:
            sent = []
            logs = io.StringIO()
            flags = []
            with redirect_stderr(logs), patch.dict(os.environ, env, clear=False):
                code_change_notify.observe_change(
                    slug="broken_crest_1025",
                    role="room",
                    room="2",
                    code=FAKE_A,
                    lock_id="LR",
                    threads=[thread("hello")],
                    directory=Directory([live_row()]),
                    now=NOW,
                    property_id="bc",
                    state_path=path,
                    environ=env,
                    source="test",
                )
                code_change_notify.drain(
                    now=NOW,
                    state_path=path,
                    send_fn=lambda chat, body: sent.append((chat, body)),
                    post_discord=flags.append,
                    environ=env,
                )
                code_change_notify.drain(
                    now=NOW,
                    state_path=path,
                    send_fn=lambda chat, body: sent.append((chat, "dup")),
                    post_discord=flags.append,
                    environ=env,
                )
            self.assertEqual(len(sent), 1)
            self.assertIn("room", sent[0][1])
            self.assertIn(FAKE_A, sent[0][1])
            self.assertNotIn("wifi", sent[0][1].lower())
            self.assertNotIn(FAKE_A, logs.getvalue())
            stored = path.read_text()
            self.assertNotIn(FAKE_A, stored)

        with tempfile_state() as path:
            sent = []
            with patch.dict(os.environ, env, clear=False):
                code_change_notify.observe_change(
                    slug="broken_crest_1025",
                    role="back",
                    room="",
                    code=FAKE_B,
                    threads=[
                        thread("hello", chat="chat-1", room="2", occ="occ-1"),
                        thread("hello", chat="chat-2", room="3", occ="occ-3"),
                    ],
                    directory=Directory([live_row(), live_row(occupancy_id="occ-3", room_number="3")]),
                    now=NOW,
                    property_id="bc",
                    state_path=path,
                    environ=env,
                )
                code_change_notify.drain(
                    now=NOW,
                    state_path=path,
                    send_fn=lambda chat, body: sent.append(chat),
                    environ=env,
                )
            self.assertEqual(sorted(sent), ["chat-1", "chat-2"])

        with tempfile_state() as path:
            sent = []
            with patch.dict(os.environ, env, clear=False):
                queued = code_change_notify.observe_change(
                    slug="broken_crest_1025",
                    role="room",
                    room="9",
                    code=FAKE_A,
                    threads=[],
                    directory=Directory([]),
                    now=NOW,
                    property_id="bc",
                    state_path=path,
                    environ=env,
                )
                code_change_notify.drain(
                    now=NOW,
                    state_path=path,
                    send_fn=lambda chat, body: sent.append(body),
                    environ=env,
                )
            self.assertEqual(queued["resolution"], "vacant")
            self.assertEqual(sent, [])

        with tempfile_state() as path:
            flags = []
            attempts = {"n": 0}

            def fail_send(chat, body):
                attempts["n"] += 1
                raise RuntimeError("down")

            with patch.dict(os.environ, env, clear=False):
                code_change_notify.observe_change(
                    slug="broken_crest_1025",
                    role="room",
                    room="2",
                    code=FAKE_A,
                    threads=[thread("hello")],
                    directory=Directory([live_row()]),
                    now=NOW,
                    property_id="bc",
                    state_path=path,
                    environ=env,
                )
                for moment in (NOW, NOW + timedelta(minutes=4), NOW + timedelta(minutes=10)):
                    code_change_notify.drain(
                        now=moment,
                        state_path=path,
                        send_fn=fail_send,
                        post_discord=flags.append,
                        environ=env,
                    )
            self.assertEqual(attempts["n"], 3)
            self.assertTrue(flags)
            self.assertIn("code notice failed", flags[-1])
            self.assertNotIn(FAKE_A, flags[-1])
            self.assertFalse(any(ch.isdigit() for ch in flags[-1]))

    def test_detected_change_notifies_after_baseline(self) -> None:
        env = enable_env(CODE_CHANGE_NOTIFY_ENABLE="1", CODE_CHANGE_NOTIFY_DRY_RUN="0")
        holder = {"code": FAKE_A}
        sent: list[str] = []

        def reader(lock_id: str) -> CurrentCode:
            if lock_id == "LR":
                return CurrentCode("ok", holder["code"], "single")
            return CurrentCode("ambiguous", "", "skip")

        locks = inventory_locks([{"lockId": "LR", "lockAlias": "Broken Crest room 2", "lockName": ""}])
        with tempfile_state() as path, patch.dict(os.environ, env, clear=False):
            code_reply.run_once(
                now=NOW,
                threads=[thread("status")],
                directory=Directory([live_row()]),
                environ=env,
                inventory=locks,
                passcodes_for=reader,
                notify_path=path,
                send_fn=lambda chat, body: sent.append(body),
            )
            self.assertEqual(sent, [])
            holder["code"] = FAKE_B
            code_reply.run_once(
                now=NOW,
                threads=[thread("status")],
                directory=Directory([live_row()]),
                environ=env,
                inventory=locks,
                passcodes_for=reader,
                notify_path=path,
                send_fn=lambda chat, body: sent.append(body),
            )
        self.assertEqual(len(sent), 1)
        self.assertIn(FAKE_B, sent[0])
        self.assertNotIn(FAKE_A, sent[0])


class HouseReportTests(unittest.TestCase):
    def test_offline_report_prints_counts_only(self) -> None:
        text, code = code_reply.run_house_report(now=NOW)
        self.assertEqual(code, 0)
        self.assertIn("house: broken_crest_1025", text)
        self.assertIn("sifely reachable: y", text)
        self.assertIn("would update 1 rooms", text)
        self.assertIn("unmapped/ambiguous lock count: 1", text)
        self.assertNotIn("FAKE-CODE", text)
        self.assertIn("interlock refused: 1", text)
        unknown, status = code_reply.run_house_report(house="not-a-house", now=NOW)
        self.assertEqual(status, 2)
        self.assertIn("unknown house", unknown)


class FakeDoc:
    def __init__(self, data: dict) -> None:
        self.data = dict(data)
        self.versions: list = []
        self.exists = True

    def get(self):
        return self

    def to_dict(self):
        return dict(self.data)

    def set(self, payload, merge=False):
        if merge:
            self.data.update(payload)
        else:
            self.data = dict(payload)

    def collection(self, name):
        return self

    def document(self, id=None):
        return self

    def set_version(self, payload):
        self.versions.append(payload)


class FakeDB:
    def __init__(self, docs: dict) -> None:
        self.doc = FakeDoc(docs.get("broken_crest_1025") or {})
        self._slug = "broken_crest_1025"

    def collection(self, name):
        return self

    def document(self, slug=None):
        if slug == codes_collection_guard(slug):
            return self.doc
        return self.doc


def codes_collection_guard(slug):
    return slug


# FakeDB.document().collection().document().set needs to record versions.
# The chain is collection().document(slug).set and collection().document(slug).collection().document().set
# FakeDoc.collection returns self, document returns self, set updates data.
# That would treat a version write as a field merge. Split them.


class VersionWriter:
    def __init__(self, doc: FakeDoc) -> None:
        self.doc = doc

    def document(self, id=None):
        return self

    def set(self, payload, merge=False):
        self.doc.versions.append(payload)


class DocRef:
    def __init__(self, doc: FakeDoc) -> None:
        self.doc = doc

    def get(self):
        return self.doc

    def set(self, payload, merge=False):
        self.doc.set(payload, merge=merge)

    def collection(self, name):
        return VersionWriter(self.doc)


class FakeDB(FakeDB):  # type: ignore[no-redef]
    def document(self, slug=None):
        return DocRef(self.doc)


def process(threads, **kwargs):
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("inventory", crest_inventory())
    kwargs.setdefault("passcodes_for", codes_for)
    kwargs.setdefault("state", {"replied": {}})
    return code_reply.process_code_requests(threads, **kwargs)


def tempfile_state():
    import tempfile
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp) / "notices.json"

    return _ctx()


if __name__ == "__main__":
    unittest.main()
