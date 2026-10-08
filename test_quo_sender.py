#!/usr/bin/env python3
"""Offline tests for the leak-alert Quo sender. No live HTTP."""

import contextlib
import io
import json
import re
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import leak_alert
from padsplit_scraper import leak_reply
from padsplit_scraper import quo_sender
from test_leak_reply import NOW
from test_leak_reply import member_thread


FROM = "+15555550100"
GROUP_A = "+15555550101"
GROUP_B = "+15555550102"
TENANT = "+15555550110"
INCIDENT = "sample:1"


def _live(**extra: str) -> dict:
    base = {
        "CI": "",
        "GITHUB_ACTIONS": "",
        "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
        "PADSPLIT_COLLECTION_ONLY": "0",
        "LEAK_ALERT_ENABLE": "1",
        "LEAK_ALERT_DRY_RUN": "0",
        "QUO_API_KEY": "quo-test",
        "QUO_FROM_NUMBER": FROM,
        "LEAK_ALERT_GROUP_E164S": f"{FROM}, {GROUP_A}, {GROUP_B}",
    }
    base.update(extra)
    return base


def _plan(tenant: str = TENANT, *, ask_photos: bool = True) -> leak_alert.AlertPlan:
    body = leak_alert.tenant_body("Sample House", "2", ask_photos=ask_photos)
    script = leak_alert.fixed_script("Sample House", "2", "water")
    return leak_alert.AlertPlan(
        incident=INCIDENT,
        entries=[
            {
                "key": f"group:{INCIDENT}",
                "kind": "quo_group",
                "status": "planned",
                "mode": "group",
                "content": script,
                "provider": "quo",
            },
            {
                "key": f"voice:{INCIDENT}:don",
                "kind": "voice",
                "status": "planned",
                "provider": "bland",
                "role": "don",
            },
            {
                "key": f"tenant:{INCIDENT}:{leak_alert.recipient_hash(tenant)}",
                "kind": "tenant_text",
                "status": "planned",
                "mode": "private_1to1",
                "content": body,
                "body": body,
                "provider": "quo",
            },
        ],
    )


class Recorder:
    def __init__(self, responses) -> None:
        self.calls: list[dict] = []
        self.responses = list(responses)

    def __call__(self, url, *, headers, payload, timeout):
        self.calls.append(
            {
                "url": url,
                "headers": {str(key): str(value) for key, value in headers.items()},
                "payload": {
                    "content": payload["content"],
                    "from": payload["from"],
                    "to": list(payload["to"]),
                },
                "timeout": timeout,
                "keys": set(payload.keys()),
            }
        )
        if not self.responses:
            raise AssertionError("unexpected extra POST")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class GateTests(unittest.TestCase):
    def test_defaults_and_ci_do_not_allow_http(self) -> None:
        quiet = {
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
            "PADSPLIT_COLLECTION_ONLY": "0",
        }
        self.assertTrue(quo_sender.dry_run_enabled(quiet))
        self.assertFalse(quo_sender.http_allowed(quiet))
        enabled = dict(quiet)
        enabled["LEAK_ALERT_ENABLE"] = "1"
        self.assertFalse(quo_sender.http_allowed(enabled))
        enabled["LEAK_ALERT_DRY_RUN"] = "1"
        self.assertFalse(quo_sender.http_allowed(enabled))
        live = _live()
        self.assertTrue(quo_sender.http_allowed(live))
        ci = _live(CI="true")
        self.assertFalse(quo_sender.http_allowed(ci))
        collected = _live(PADSPLIT_COLLECTION_ONLY="1")
        self.assertFalse(quo_sender.http_allowed(collected))

    def test_gate_off_and_dry_run_make_no_http(self) -> None:
        poster = Recorder([(200, {})])

        def boom(*_args, **_kwargs):
            raise AssertionError("default HTTP must not run")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak_alert_state.json"
            state: dict = {"alerts": {}}
            with patch("padsplit_scraper.quo_sender.default_post", boom):
                gated = quo_sender.maybe_deliver(
                    _plan(),
                    state=state,
                    state_path=path,
                    now=NOW,
                    environ=_live(LEAK_ALERT_ENABLE=""),
                    tenant_numbers=[TENANT],
                    http_post=poster,
                )
                dry = quo_sender.maybe_deliver(
                    _plan(),
                    state=state,
                    state_path=path,
                    now=NOW,
                    environ=_live(LEAK_ALERT_DRY_RUN=""),
                    tenant_numbers=[TENANT],
                    http_post=poster,
                )
        self.assertEqual(gated, [])
        self.assertEqual(dry, [])
        self.assertEqual(poster.calls, [])
        self.assertEqual(state["alerts"], {})
        self.assertFalse(path.exists())


class PreviewTests(unittest.TestCase):
    def test_preview_prints_exact_copy_and_masks_numbers(self) -> None:
        env = {
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PADSPLIT_ENABLE_ACTION_HOOKS": "",
            "PADSPLIT_COLLECTION_ONLY": "",
        }
        text = quo_sender.render_preview(env)
        group = leak_alert.fixed_script("Sample House", "2", "water")
        tenant = quo_sender.tenant_sms("Sample House", "2", ask_photos=True)
        self.assertEqual(
            group,
            "Water emergency at Sample House, room 2. Category water. "
            "Tenant has been told to shut off the water. This is an automated notice.",
        )
        self.assertEqual(
            tenant,
            "Sample House room 2 leak: turn the water OFF now. "
            "The shut-off box is between the water meter and the house. "
            "Use the water key to turn it off. "
            "(How-to: https://youtube.com/shorts/SCryjPiyZcs)\n"
            "\n"
            "Once it's off, turn it on only briefly for drinking water. "
            "Please reply with photos of the leak.\n"
            "\n"
            "This is a service alert about a leak at your house.\n"
            "\n"
            "Reply STOP to opt out.",
        )
        self.assertTrue(tenant.endswith("Reply STOP to opt out."))
        no_photo = quo_sender.tenant_sms("Sample House", "2", ask_photos=False)
        self.assertNotIn("photos", no_photo.lower())
        self.assertIn("Once it's off, turn it on only briefly for drinking water.", no_photo)
        self.assertTrue(no_photo.endswith("Reply STOP to opt out."))
        from_planner = quo_sender.outbound_tenant_text(
            leak_alert.tenant_body("Sample House", "2", ask_photos=True)
        )
        self.assertEqual(from_planner, tenant)
        self.assertEqual(
            quo_sender.outbound_tenant_text(
                leak_alert.tenant_body("Sample House", "2", ask_photos=False)
            ),
            no_photo,
        )
        self.assertIn(group, text)
        self.assertIn(tenant, text)
        self.assertNotIn("The water is being shut off.", group)
        self.assertNotIn("Reply STOP", group)
        self.assertNotIn("turn the water OFF now", group)
        self.assertIn("Use the water key to turn OFF the water immediately", leak_reply.BAKED_T5_TEXT)
        self.assertNotIn("Reply STOP", leak_reply.BAKED_T5_TEXT)
        self.assertIn(
            "water is shut off for a leak",
            leak_alert.tenant_body("Sample House", "2", ask_photos=False),
        )
        self.assertNotIn("lock code", text.lower())
        self.assertNotIn("wifi", text.lower())
        self.assertIn("group from: ***00", text)
        self.assertIn("group to: ***01, ***02, ***03", text)
        self.assertIn("tenant to: ***10", text)
        self.assertIn("tenant to: ***11", text)
        for number in (FROM, GROUP_A, GROUP_B, "+15555550103", TENANT, "+15555550111"):
            self.assertNotIn(number, text)
        self.assertIn("runtime.send_enabled(leak_alert): off", text)
        self.assertIn("LEAK_ALERT_ENABLE: off (unset)", text)
        self.assertIn("LEAK_ALERT_DRY_RUN: on (default)", text)
        self.assertIn("HTTP: off (preview does not send)", text)
        self.assertIn("call_script:", text)
        self.assertIn("padsplit_scraper.quo_sender.preview_call_script", text)
        self.assertIn(quo_sender.preview_call_script(), text)
        self.assertNotIn("max_duration", text)
        leak_alert.assert_plan_has_no_numbers(text)

    def test_call_script_hook_is_replaceable(self) -> None:
        with patch(
            "padsplit_scraper.quo_sender.preview_call_script",
            return_value="(hooked)",
        ):
            text = quo_sender.render_preview({})
        self.assertIn("(hooked)", text)

    def test_preview_cli_writes_the_file_and_does_not_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak_alert_preview.txt"

            def boom(*_args, **_kwargs):
                raise AssertionError("HTTP")

            with patch("padsplit_scraper.quo_sender.PREVIEW_PATH", path), patch(
                "padsplit_scraper.quo_sender.default_post",
                boom,
            ):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = quo_sender.main(["--preview"])
            written = path.read_text()
            self.assertEqual(stdout.getvalue(), written)
        self.assertEqual(code, 0)
        self.assertEqual(written, quo_sender.render_preview())
        self.assertNotIn(FROM, written)


class SendTests(unittest.TestCase):
    def _deliver(self, poster, *, state, path, plan=None, sleeper=None, environ=None):
        env = environ or _live()
        return quo_sender.maybe_deliver(
            plan or _plan(),
            state=state,
            state_path=path,
            now=NOW,
            environ=env,
            tenant_numbers=[TENANT, FROM],
            http_post=poster,
            sleeper=sleeper or (lambda _seconds: None),
        )

    def test_enabled_post_has_raw_auth_and_group_excludes_from(self) -> None:
        poster = Recorder([(200, {"id": "g"}), (201, {"id": "t"})])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak_alert_state.json"
            state = {"alerts": {}}
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                rows = self._deliver(poster, state=state, path=path)
            raw = path.read_text()
        self.assertEqual([row["status"] for row in rows], ["sent", "sent"])
        self.assertEqual(len(poster.calls), 2)
        group, tenant = poster.calls
        self.assertEqual(group["url"], "https://api.quo.com/v1/messages")
        self.assertEqual(group["timeout"], 30.0)
        self.assertEqual(group["keys"], {"content", "from", "to"})
        self.assertEqual(group["headers"]["Authorization"], "quo-test")
        self.assertNotIn("Bearer", group["headers"]["Authorization"])
        self.assertFalse(group["headers"]["Authorization"].lower().startswith("bearer"))
        self.assertEqual(group["headers"]["Quo-Api-Version"], "2026-03-30")
        self.assertEqual(group["headers"]["Content-Type"], "application/json")
        self.assertEqual(group["payload"]["from"], FROM)
        self.assertEqual(group["payload"]["to"], [GROUP_A, GROUP_B])
        self.assertNotIn(FROM, group["payload"]["to"])
        self.assertEqual(
            group["payload"]["content"],
            leak_alert.fixed_script("Sample House", "2", "water"),
        )
        self.assertEqual(tenant["payload"]["to"], [TENANT])
        self.assertNotIn(FROM, tenant["payload"]["to"])
        self.assertEqual(
            tenant["payload"]["content"],
            quo_sender.tenant_sms("Sample House", "2", ask_photos=True),
        )
        self.assertTrue(tenant["payload"]["content"].endswith("Reply STOP to opt out."))
        self.assertNotIn("Reply STOP", group["payload"]["content"])
        self.assertIn("Tenant has been told to shut off the water.", group["payload"]["content"])
        self.assertNotIn("The water is being shut off.", group["payload"]["content"])
        self.assertNotIn("voice", json.dumps([call["payload"]["content"] for call in poster.calls]).lower() or "bland")
        for secret in (FROM, GROUP_A, GROUP_B, TENANT, "quo-test"):
            self.assertNotIn(secret, raw)
            self.assertNotIn(secret, stderr.getvalue())
        self.assertIn(f"tenant:{INCIDENT}:{leak_alert.recipient_hash(TENANT)}", raw)
        self.assertEqual(state["alerts"][f"group:{INCIDENT}"]["status"], "sent")
        leak_alert.assert_plan_has_no_numbers(raw)

    def test_bearer_prefix_is_stripped(self) -> None:
        poster = Recorder([(200, {})])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self._deliver(
                poster,
                state={"alerts": {}},
                path=path,
                plan=leak_alert.AlertPlan(
                    incident=INCIDENT,
                    entries=[
                        {
                            "key": f"group:{INCIDENT}",
                            "kind": "quo_group",
                            "status": "planned",
                            "mode": "group",
                            "content": "Water emergency at Sample House. This is an automated notice.",
                        }
                    ],
                ),
                environ=_live(QUO_API_KEY="Bearer quo-test"),
            )
        self.assertEqual(poster.calls[0]["headers"]["Authorization"], "quo-test")
        self.assertNotIn("Bearer", poster.calls[0]["headers"]["Authorization"])

    def test_state_is_sending_on_disk_before_post_and_dedupes(self) -> None:
        seen: list[str] = []

        def poster(url, *, headers, payload, timeout):
            raw = path.read_text()
            seen.append(raw)
            self.assertNotIn(TENANT, raw)
            self.assertNotIn(FROM, raw)
            if len(payload["to"]) > 1:
                self.assertIn('"status": "sending"', raw)
                self.assertIn(f"group:{INCIDENT}", raw)
            else:
                self.assertIn(leak_alert.recipient_hash(TENANT), raw)
                self.assertIn('"status": "sending"', raw)
            return 200, {}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak_alert_state.json"
            state = {"alerts": {}}
            first = self._deliver(poster, state=state, path=path)
            second_poster = Recorder([(200, {})])
            second = self._deliver(second_poster, state=state, path=path)
            raw = path.read_text()
        self.assertEqual(len(seen), 2)
        self.assertEqual([row["status"] for row in first], ["sent", "sent"])
        self.assertEqual([row["status"] for row in second], ["already_claimed", "already_claimed"])
        self.assertEqual(second_poster.calls, [])
        self.assertNotIn(TENANT, raw)
        self.assertEqual(state["alerts"][f"group:{INCIDENT}"]["status"], "sent")

    def test_terminal_codes_are_not_retried_and_block_a_second_send(self) -> None:
        poster = Recorder(
            [
                (403, {"code": "0206400", "message": TENANT}),
                (429, {"error": {"code": "0204403"}}),
            ]
        )
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak_alert_state.json"
            state = {"alerts": {}}
            with contextlib.redirect_stderr(stderr):
                rows = self._deliver(poster, state=state, path=path)
            again = Recorder([(200, {})])
            with contextlib.redirect_stderr(io.StringIO()):
                second = self._deliver(again, state=state, path=path)
            raw = path.read_text()
        self.assertEqual(len(poster.calls), 2)
        self.assertEqual(rows[0]["reason"], "0206400")
        self.assertEqual(rows[1]["reason"], "0204403")
        self.assertEqual(state["alerts"][f"group:{INCIDENT}"]["status"], "failed")
        self.assertEqual(
            state["alerts"][f"tenant:{INCIDENT}:{leak_alert.recipient_hash(TENANT)}"]["status"],
            "failed",
        )
        self.assertEqual(second[0]["status"], "already_claimed")
        self.assertEqual(again.calls, [])
        log = stderr.getvalue()
        self.assertIn("unapproved or unregistered (0206400)", log)
        self.assertIn("daily cap (0204403)", log)
        self.assertIn("not retrying", log)
        self.assertNotIn(TENANT, log)
        self.assertNotIn(TENANT, raw)

    def test_429_and_5xx_retry_then_stop(self) -> None:
        sleeps: list[float] = []
        poster = Recorder([(429, {}), (500, {}), (502, {"error": "no"})])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = {"alerts": {}}
            rows = self._deliver(
                poster,
                state=state,
                path=path,
                plan=leak_alert.AlertPlan(
                    incident=INCIDENT,
                    entries=[
                        {
                            "key": f"group:{INCIDENT}",
                            "kind": "quo_group",
                            "status": "planned",
                            "content": "Water emergency at Sample House. This is an automated notice.",
                            "mode": "group",
                        }
                    ],
                ),
                sleeper=sleeps.append,
            )
        self.assertEqual(len(poster.calls), 3)
        self.assertEqual(sleeps, [0.5, 1.0])
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["reason"], "http_502")

    def test_5xx_can_succeed_on_a_later_attempt(self) -> None:
        poster = Recorder([(503, {}), (200, {})])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            rows = self._deliver(
                poster,
                state={"alerts": {}},
                path=path,
                plan=leak_alert.AlertPlan(
                    incident=INCIDENT,
                    entries=[
                        {
                            "key": f"group:{INCIDENT}",
                            "kind": "quo_group",
                            "status": "planned",
                            "content": "Water emergency at Sample House. This is an automated notice.",
                            "mode": "group",
                        }
                    ],
                ),
            )
        self.assertEqual(len(poster.calls), 2)
        self.assertEqual(rows[0]["status"], "sent")

    def test_other_4xx_and_timeouts_are_not_retried(self) -> None:
        bad = Recorder([(400, {"code": "other", "to": [TENANT]})])
        result = quo_sender.post_text(
            content="notice",
            from_number=FROM,
            to_numbers=[GROUP_A],
            api_key="quo-test",
            http_post=bad,
            sleeper=lambda _seconds: (_ for _ in ()).throw(AssertionError("slept")),
        )
        self.assertEqual(len(bad.calls), 1)
        self.assertEqual(result["reason"], "http_400")
        timed = Recorder([TimeoutError("slow")])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            timed_out = quo_sender.post_text(
                content="notice",
                from_number=FROM,
                to_numbers=[GROUP_A],
                api_key="quo-test",
                http_post=timed,
            )
        self.assertEqual(len(timed.calls), 1)
        self.assertEqual(timed_out["reason"], "timeout")
        self.assertIn("timed out", stderr.getvalue())
        self.assertIn("not retrying", stderr.getvalue())
        self.assertNotIn(GROUP_A, stderr.getvalue())
        url_error = Recorder([urllib.error.URLError("down")])
        failed = quo_sender.post_text(
            content="notice",
            from_number=FROM,
            to_numbers=[GROUP_A],
            api_key="quo-test",
            http_post=url_error,
        )
        self.assertEqual(len(url_error.calls), 1)
        self.assertEqual(failed["reason"], "timeout")

    def test_over_cap_does_not_claim_or_post(self) -> None:
        poster = Recorder([(200, {})])
        numbers = [f"+155555501{index:02d}" for index in range(11, 22)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = {"alerts": {}}
            rows = quo_sender.deliver(
                leak_alert.AlertPlan(
                    incident=INCIDENT,
                    entries=[
                        {
                            "key": f"group:{INCIDENT}",
                            "kind": "quo_group",
                            "status": "planned",
                            "content": "Water emergency at Sample House. This is an automated notice.",
                            "mode": "group",
                        }
                    ],
                ),
                state=state,
                state_path=path,
                now=NOW,
                from_number=FROM,
                group_numbers=numbers,
                tenant_numbers=[],
                api_key="quo-test",
                http_post=poster,
            )
        self.assertEqual(poster.calls, [])
        self.assertEqual(rows[0]["reason"], "over_cap")
        self.assertEqual(state["alerts"], {})
        self.assertFalse(path.exists())

    def test_sources_only_use_the_fictional_range(self) -> None:
        for relative in ("padsplit_scraper/quo_sender.py", "test_quo_sender.py"):
            source = Path(relative).read_text()
            for number in re.findall(r"\+\d{10,15}", source):
                self.assertTrue(number.startswith("+155555501"), number)
        sender = Path("padsplit_scraper/quo_sender.py").read_text()
        self.assertIn('"Authorization": key', sender)
        self.assertNotIn('f"Bearer', sender)
        self.assertNotIn('"Bearer "', sender)


class HookTests(unittest.TestCase):
    def test_leak_reply_does_not_post_until_dry_run_is_off(self) -> None:
        def boom(*_args, **_kwargs):
            raise AssertionError("HTTP")

        thread = member_thread(street="Sample Way", city="Town", state="Texas", zip_code="00000")
        env = _live()
        env.pop("LEAK_ALERT_DRY_RUN")
        with tempfile.TemporaryDirectory() as tmp:
            alert_path = Path(tmp) / "leak_alert_state.json"
            with patch("padsplit_scraper.quo_sender.default_post", boom):
                rows = leak_reply.process_leaks(
                    [thread],
                    now=NOW,
                    state_path=Path(tmp) / "leak_state.json",
                    dry_run=False,
                    send_enabled=False,
                    plan_alerts=True,
                    alert_enabled=True,
                    alert_state_path=alert_path,
                    alert_environ=env,
                    leak_body="Thanks for reporting the water leak.",
                )
            raw = alert_path.read_text()
        self.assertEqual(rows[0]["action"], "would_send")
        self.assertIn(f"group:{rows[0]['chat_id']}:m-leak", raw)
        self.assertNotIn("sending", raw)
        self.assertNotIn("sent", raw)
        leak_alert.assert_plan_has_no_numbers(raw)

    def test_enabled_hook_posts_once_and_dedupes(self) -> None:
        calls: list[dict] = []

        def poster(url, *, headers, payload, timeout):
            calls.append(
                {
                    "url": url,
                    "headers": dict(headers),
                    "payload": dict(payload),
                    "timeout": timeout,
                }
            )
            return 200, {}

        thread = member_thread(street="Sample Way", city="Town", state="Texas", zip_code="00000")
        env = _live()
        with tempfile.TemporaryDirectory() as tmp:
            alert_path = Path(tmp) / "leak_alert_state.json"
            state_path = Path(tmp) / "leak_state.json"
            with patch("padsplit_scraper.quo_sender.default_post", poster):
                leak_reply.process_leaks(
                    [thread],
                    now=NOW,
                    state_path=state_path,
                    dry_run=False,
                    send_enabled=False,
                    plan_alerts=True,
                    alert_enabled=True,
                    alert_state_path=alert_path,
                    alert_environ=env,
                    leak_body="Thanks for reporting the water leak.",
                )
                leak_reply.process_leaks(
                    [thread],
                    now=NOW,
                    state_path=state_path,
                    dry_run=False,
                    send_enabled=False,
                    plan_alerts=True,
                    alert_enabled=True,
                    alert_state_path=alert_path,
                    alert_environ=env,
                    leak_body="Thanks for reporting the water leak.",
                )
            raw = alert_path.read_text()
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["headers"]["Authorization"], "quo-test")
        self.assertNotIn("Bearer", call["headers"]["Authorization"])
        self.assertEqual(call["headers"]["Quo-Api-Version"], "2026-03-30")
        self.assertEqual(set(call["payload"].keys()), {"content", "from", "to"})
        self.assertEqual(call["payload"]["from"], FROM)
        self.assertEqual(call["payload"]["to"], [GROUP_A, GROUP_B])
        self.assertNotIn(FROM, call["payload"]["to"])
        self.assertEqual(
            call["payload"]["content"],
            "Water emergency at the house, room 2. Category wall_ceiling. "
            "Tenant has been told to shut off the water. This is an automated notice.",
        )
        self.assertNotIn("Reply STOP", call["payload"]["content"])
        stored = json.loads(raw)
        group = stored["alerts"]["group:chat-leana:m-leak"]
        self.assertEqual(group["status"], "sent")
        for secret in (FROM, GROUP_A, GROUP_B, TENANT, "quo-test"):
            self.assertNotIn(secret, raw)
        leak_alert.assert_plan_has_no_numbers(raw)


if __name__ == "__main__":
    unittest.main()
