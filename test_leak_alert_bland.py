#!/usr/bin/env python3
"""Offline Bland leak-alert tests. No live HTTP to Bland or Quo."""

import hashlib
import hmac
import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from padsplit_scraper import leak_alert
from padsplit_scraper import leak_alert_bland as bland
from test_leak_alert import DON
from test_leak_alert import GROUP_LIST
from test_leak_alert import TOM
from test_leak_alert import _env
from test_leak_reply import NOW
from test_leak_reply import member_thread


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "padsplit_scraper" / "fixtures" / "bland_webhook_answered.json"
SECRET = "test-webhook-secret"
CALL_ID = "12345678-1234-1234-1234-123456789012"
INCIDENT = "chat-fixture:m-leak"
FAKE_TO = "+15555550100"
FAKE_FROM = "+15555550199"
FRESH = datetime(2026, 10, 8, 14, 1, tzinfo=timezone.utc)
RULES = (ROOT / "firestore.rules").read_text()


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _payload(outcome: str, *, role: str = "tom", call_id: str = "call-tom-001") -> dict:
    base = {
        "call_id": call_id,
        "c_id": call_id,
        "to": FAKE_TO,
        "from": FAKE_FROM,
        "completed": True,
        "created_at": "2026-10-08T13:58:00Z",
        "end_at": "2026-10-08T14:00:00Z",
        "metadata": {"incident_id": INCIDENT, "role": role},
        "variables": {"phone_number": FAKE_TO, "to": FAKE_TO, "from": FAKE_FROM},
        "summary": "Short recap with no digits.",
    }
    if outcome == "answered":
        base.update({
            "status": "completed",
            "answered_by": "human",
            "disposition_tag": "confirmed_going",
            "analysis": {"confirmed_going": True, "eta_minutes": 20},
            "summary": "They confirmed they are going and gave an ETA of 20 minutes.",
        })
    elif outcome == "voicemail":
        base.update({
            "status": "completed",
            "answered_by": "voicemail",
            "disposition_tag": "voicemail",
            "summary": "Reached voicemail and left the leak notice.",
        })
    elif outcome == "no_answer":
        base.update({
            "status": "no-answer",
            "answered_by": "no-answer",
            "completed": True,
            "summary": "No one answered.",
        })
    elif outcome == "failed":
        base.update({
            "status": "failed",
            "completed": False,
            "error_message": "carrier rejected the call",
            "summary": "The call failed before it connected.",
        })
    else:
        raise AssertionError(outcome)
    return base


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _store_for(call_id: str, role: str = "tom") -> bland.MemoryCallStore:
    store = bland.MemoryCallStore()
    store.put_pending(call_id, {
        "call_id": call_id,
        "incident_id": INCIDENT,
        "role": role,
        "placed_at": "2026-10-08T13:58:00Z",
    })
    return store


def _handle(payload: dict, store: bland.MemoryCallStore, **kwargs):
    raw = _raw(payload)
    log: list = kwargs.pop("result_log", [])
    result = bland.handle_webhook(
        raw,
        kwargs.pop("signature", _sign(raw)),
        secret=kwargs.pop("secret", SECRET),
        store=store,
        now=kwargs.pop("now", FRESH),
        headers=kwargs.pop("headers", None),
        dry_run=kwargs.pop("dry_run", True),
        result_log=log,
    )
    result_log = log
    return result, result_log


class SignatureTests(unittest.TestCase):
    def test_valid_signature_accepts(self) -> None:
        payload = _payload("voicemail")
        store = _store_for(payload["call_id"])
        result, _log = _handle(payload, store)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.error, "")
        self.assertEqual(result.record["outcome"], "voicemail")

    def test_invalid_signature_rejects(self) -> None:
        payload = _payload("voicemail")
        store = _store_for(payload["call_id"])
        raw = _raw(payload)
        result = bland.handle_webhook(
            raw,
            _sign(raw, "other-secret"),
            secret=SECRET,
            store=store,
            now=FRESH,
        )
        self.assertEqual(result.status, 401)
        self.assertEqual(result.error, "invalid_signature")
        self.assertIsNone(store.get_record(INCIDENT, "tom"))

    def test_raw_signature_logs_variant_only(self) -> None:
        payload = _payload("voicemail")
        raw = _raw(payload)
        signature = _sign(raw)
        with self.assertLogs("leak_alert_bland", level="INFO") as logs:
            self.assertTrue(bland.verify_signature(SECRET, raw, signature))
        text = "\n".join(logs.output)
        self.assertIn("variant=raw", text)
        self.assertNotIn("variant=canonical", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(signature, text)

    def test_canonical_signature_accepts_pretty_body(self) -> None:
        payload = _payload("voicemail", call_id="call-tom-canon")
        raw = json.dumps(payload, indent=2).encode("utf-8")
        canonical = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertNotEqual(raw, canonical)
        signature = _sign(canonical)
        store = _store_for(payload["call_id"])
        with self.assertLogs("leak_alert_bland", level="INFO") as logs:
            result = bland.handle_webhook(raw, signature, secret=SECRET, store=store, now=FRESH)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.record["outcome"], "voicemail")
        text = "\n".join(logs.output)
        self.assertIn("variant=canonical", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(signature, text)

    def test_canonical_signature_keeps_non_ascii(self) -> None:
        payload = _payload("voicemail", call_id="call-tom-cafe")
        payload["summary"] = "Reached voicemail. café"
        raw = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        escaped = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        compact = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertNotEqual(escaped, compact)
        store = _store_for(payload["call_id"])
        rejected = bland.handle_webhook(raw, _sign(escaped), secret=SECRET, store=store, now=FRESH)
        self.assertEqual(rejected.status, 401)
        accepted = bland.handle_webhook(raw, _sign(compact), secret=SECRET, store=store, now=FRESH)
        self.assertEqual(accepted.status, 200)
        self.assertIn("café", accepted.record["summary"])

    def test_missing_signature_rejects(self) -> None:
        payload = _payload("voicemail")
        store = _store_for(payload["call_id"])
        result = bland.handle_webhook(
            _raw(payload),
            None,
            secret=SECRET,
            store=store,
            now=FRESH,
        )
        self.assertEqual(result.status, 401)
        self.assertEqual(result.error, "missing_signature")
        self.assertEqual(store.records, {})

    def test_blank_signature_rejects(self) -> None:
        payload = _payload("voicemail")
        result = bland.handle_webhook(
            _raw(payload),
            "   ",
            secret=SECRET,
            store=_store_for(payload["call_id"]),
            now=FRESH,
        )
        self.assertEqual(result.error, "missing_signature")

    def test_stale_timestamp_rejects(self) -> None:
        payload = _payload("voicemail")
        store = _store_for(payload["call_id"])
        later = FRESH + timedelta(hours=25)
        result, _log = _handle(payload, store, now=later)
        self.assertEqual(result.status, 401)
        self.assertEqual(result.error, "stale_timestamp")
        self.assertEqual(store.records, {})

    def test_unknown_call_id_rejects(self) -> None:
        payload = _payload("answered", role="don", call_id=CALL_ID)
        store = bland.MemoryCallStore()
        result, _log = _handle(payload, store)
        self.assertEqual(result.status, 404)
        self.assertEqual(result.error, "unknown_call")
        self.assertEqual(store.queue, {})

    def test_incident_mismatch_rejects(self) -> None:
        payload = _payload("voicemail", call_id="call-tom-009")
        store = _store_for("call-tom-009")
        payload["metadata"]["incident_id"] = "other-incident:m9"
        result, _log = _handle(payload, store)
        self.assertEqual(result.status, 404)
        self.assertEqual(result.error, "unknown_incident")


class OutcomeTests(unittest.TestCase):
    def test_answered_confirmed_maps_record_and_line(self) -> None:
        payload = _payload("answered", role="don", call_id="call-don-020")
        store = _store_for("call-don-020", "don")
        result, log = _handle(payload, store)
        self.assertEqual(result.line, "Don: going, ETA 20m")
        self.assertTrue(result.enqueued)
        record = store.get_record(INCIDENT, "don")
        assert record is not None
        self.assertEqual(record["outcome"], "answered")
        self.assertIs(record["confirmed"], True)
        self.assertEqual(record["eta_minutes"], 20)
        self.assertEqual(record["call_id"], "call-don-020")
        self.assertNotIn("transcript", record)
        self.assertNotIn(FAKE_TO, json.dumps(record))
        self.assertEqual(log[0]["line"], "Don: going, ETA 20m")
        self.assertEqual(store.get_queue("call-don-020")["line"], "Don: going, ETA 20m")

    def test_not_going_line(self) -> None:
        payload = _payload("answered", role="don", call_id="call-don-021")
        payload["analysis"] = {"confirmed_going": False, "eta_minutes": 20}
        payload["disposition_tag"] = "not_going"
        payload["variables"]["confirmed_going"] = False
        store = _store_for("call-don-021", "don")
        result, _log = _handle(payload, store)
        self.assertEqual(result.line, "Don: not going")
        self.assertIs(result.record["confirmed"], False)
        self.assertIsNone(result.record["eta_minutes"])

    def test_voicemail_maps_record_and_line(self) -> None:
        payload = _payload("voicemail", call_id="call-tom-vm1")
        store = _store_for("call-tom-vm1")
        result, _log = _handle(payload, store)
        self.assertEqual(result.line, "Tom: voicemail")
        record = result.record
        assert record is not None
        self.assertEqual(record["outcome"], "voicemail")
        self.assertIsNone(record["confirmed"])
        self.assertIsNone(record["eta_minutes"])
        self.assertNotIn(FAKE_TO, json.dumps(record))

    def test_no_answer_maps_record_and_line(self) -> None:
        payload = _payload("no_answer", call_id="call-tom-na1")
        store = _store_for("call-tom-na1")
        result, _log = _handle(payload, store)
        self.assertEqual(result.line, "Tom: no answer")
        self.assertEqual(result.record["outcome"], "no_answer")
        self.assertNotIn(FAKE_FROM, json.dumps(result.record))

    def test_failed_maps_record_and_line(self) -> None:
        payload = _payload("failed", call_id="call-tom-fail")
        store = _store_for("call-tom-fail")
        result, _log = _handle(payload, store)
        self.assertEqual(result.line, "Tom: failed")
        self.assertEqual(result.record["outcome"], "failed")
        self.assertNotIn("error_message", result.record)
        self.assertNotIn(FAKE_TO, json.dumps(store.get_queue("call-tom-fail")))

    def test_second_delivery_does_not_enqueue_again(self) -> None:
        payload = _payload("voicemail", call_id="call-tom-dup")
        store = _store_for("call-tom-dup")
        first, _log = _handle(payload, store)
        second, _log = _handle(payload, store)
        self.assertTrue(first.enqueued)
        self.assertFalse(second.enqueued)
        self.assertEqual(len(store.queue), 1)


class DryRunE2ETests(unittest.TestCase):
    def test_fixture_maps_to_record_and_line(self) -> None:
        raw = FIXTURE.read_bytes()
        payload = json.loads(raw)
        self.assertIn(FAKE_TO, raw.decode("utf-8"))
        store = bland.MemoryCallStore()
        store.put_pending(CALL_ID, {
            "call_id": CALL_ID,
            "incident_id": payload["metadata"]["incident_id"],
            "role": "don",
            "placed_at": "2026-10-08T13:58:00Z",
        })
        result_log: list = []
        result = bland.handle_webhook(
            raw,
            _sign(raw),
            secret=SECRET,
            store=store,
            now=FRESH,
            dry_run=True,
            result_log=result_log,
        )
        record = result.record
        assert record is not None
        self.assertEqual(result.status, 200)
        self.assertEqual(result.line, "Don: going, ETA 20m")
        self.assertEqual(record["outcome"], "answered")
        self.assertIs(record["confirmed"], True)
        self.assertEqual(record["eta_minutes"], 20)
        self.assertEqual(record["call_id"], CALL_ID)
        self.assertEqual(record["role"], "don")
        self.assertEqual(record["incident_id"], "chat-fixture:m-leak")
        self.assertNotIn(FAKE_TO, json.dumps(record))
        self.assertNotIn(FAKE_FROM, json.dumps(record))
        self.assertNotIn("concatenated_transcript", record)
        encoded_log = json.dumps(result_log)
        self.assertEqual(result_log[0]["line"], "Don: going, ETA 20m")
        self.assertNotIn(FAKE_TO, encoded_log)
        self.assertNotIn(SECRET, encoded_log)
        print("E2E record:", json.dumps(record, sort_keys=True))
        print("E2E line:", result.line)
        print("E2E would-send:", encoded_log)


class ConfirmTests(unittest.TestCase):
    def test_confirm_requires_bearer_and_pending_call(self) -> None:
        store = _store_for("call-don-tool", "don")
        body = json.dumps({
            "call_id": "call-don-tool",
            "incident_id": INCIDENT,
            "role": "don",
            "confirmed_going": "true",
            "eta_minutes": "15",
        }).encode("utf-8")
        rejected = bland.handle_confirm(body, "Bearer wrong", secret=SECRET, store=store, now=FRESH)
        self.assertEqual(rejected.status, 401)
        accepted = bland.handle_confirm(
            body,
            f"Bearer {SECRET}",
            secret=SECRET,
            store=store,
            now=FRESH,
        )
        self.assertEqual(accepted.status, 200)
        pending = store.get_pending("call-don-tool")
        assert pending is not None
        self.assertIs(pending["confirmed_going"], True)
        self.assertEqual(pending["eta_minutes"], 15)
        self.assertNotIn(FAKE_TO, json.dumps(pending))


class PreviewTests(unittest.TestCase):
    def test_preview_masks_numbers_and_writes_file(self) -> None:
        text = bland.render_preview()
        self.assertIn("POST https://api.bland.ai/v1/calls", text)
        self.assertIn('"first_sentence"', text)
        self.assertIn("Water emergency at 100 Example Lane, room 2.", text)
        self.assertIn("Tenant has been told to shut off the water.", text)
        self.assertNotIn("The water is being shut off.", text)
        self.assertEqual(text.count("Check the Quo group text for details."), 2)
        self.assertIn('"max_duration": 2', text)
        self.assertIn('"confirmed_going": "boolean"', text)
        self.assertIn('"eta_minutes": "number"', text)
        self.assertIn("leave_message", text)
        self.assertIn("Bearer <BLAND_API_KEY>", text)
        self.assertIn("<BLAND_WEBHOOK_SECRET>", text)
        self.assertIn(bland.WEBHOOK_PLACEHOLDER, text)
        self.assertIn("Don: going, ETA 20m", text)
        self.assertIn("Don: not going", text)
        self.assertIn("Tom: voicemail", text)
        self.assertIn("Tom: no answer", text)
        self.assertNotIn(bland.PREVIEW_DON, text)
        self.assertNotIn(bland.PREVIEW_TOM, text)
        self.assertNotIn("15555550101", text)
        self.assertNotIn("15555550102", text)
        self.assertIsNone(leak_alert._E164_LEAK_RE.search(text))
        self.assertIn("+xxxxxxxxx01", text)
        self.assertIn("+xxxxxxxxx02", text)

    def test_cli_preview_writes_stdout_and_file(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak_alert_bland_preview.txt"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "padsplit_scraper.leak_alert_bland",
                    "--preview",
                    "--out",
                    str(path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(path.read_text(), proc.stdout)
            self.assertIn("Don: going, ETA 20m", proc.stdout)
            self.assertNotIn(bland.PREVIEW_DON, proc.stdout)


class SenderTests(unittest.TestCase):
    def _plan(self, environ: dict):
        plan = leak_alert.build_plan(
            member_thread(),
            now=NOW,
            house_threads=[member_thread()],
            environ=environ,
        )
        assert plan is not None
        state = {"alerts": {}}
        leak_alert.persist_plan(plan, state, now=NOW)
        return plan, state

    def test_dry_run_default_does_not_post(self) -> None:
        environ = _env()
        self.assertNotIn("LEAK_ALERT_DRY_RUN", environ)
        self.assertTrue(bland.dry_run_enabled(environ))
        self.assertFalse(bland.calls_live(environ))
        plan, state = self._plan(environ)

        def boom(*_args, **_kwargs):
            raise AssertionError("dry-run must not call Bland")

        with tempfile_log(environ) as (call_log, result_log):
            notes = bland.advance_plans(
                [plan],
                state,
                now=NOW,
                environ=environ,
                poster=boom,
                getter=boom,
                quo_post=boom,
                call_log=call_log,
                result_log=result_log,
            )
            logged = call_log.read_text()
        roles = {row["role"]: row["status"] for row in notes if row.get("role")}
        self.assertEqual(roles, {"don": "dry_run", "tom": "dry_run"})
        leak_alert.assert_plan_has_no_numbers(json.dumps(state))
        self.assertNotIn(DON, logged)
        self.assertIn("would_place", logged)

    def test_live_post_is_mocked_and_deduped(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0", LEAK_ALERT_BLAND_WEBHOOK_URL="https://example.invalid/leak")
        environ["BLAND_WEBHOOK_SECRET"] = SECRET
        self.assertTrue(bland.calls_live(environ))
        plan, state = self._plan(environ)
        calls = []

        def poster(body, api_key):
            calls.append((body, api_key))
            role = body["metadata"]["role"]
            return 200, {"call_id": f"call-live-{role}1", "status": "success"}

        store = bland.MemoryCallStore()
        notes = bland.place_plan_calls(
            plan,
            state,
            now=NOW,
            environ=environ,
            poster=poster,
            store=store,
        )
        self.assertEqual([row["status"] for row in notes], ["placed", "placed"])
        self.assertEqual(len(calls), 2)
        don_body = calls[0][1] and calls[0][0]
        self.assertEqual(don_body["phone_number"], DON)
        self.assertEqual(don_body["max_duration"], 2)
        self.assertEqual(
            don_body["voicemail"]["message"],
            don_body["first_sentence"] + " Check the Quo group text for details.",
        )
        self.assertNotIn("Check the Quo group text for details.", don_body["first_sentence"])
        self.assertNotIn("Check the Quo group text for details.", don_body["task"])
        self.assertEqual(don_body["analysis_schema"], {"confirmed_going": "boolean", "eta_minutes": "number"})
        self.assertEqual(don_body["metadata"], {"incident_id": plan.incident, "role": "don"})
        self.assertNotIn("phone", json.dumps(don_body["metadata"]))
        self.assertEqual(don_body["webhook"], "https://example.invalid/leak")
        self.assertEqual(don_body["tools"][0]["name"], "confirm_dispatch")
        self.assertEqual(don_body["tools"][0]["headers"]["Authorization"], f"Bearer {SECRET}")
        self.assertEqual(calls[0][1], "bland-test")
        encoded_state = json.dumps(state)
        leak_alert.assert_plan_has_no_numbers(encoded_state)
        self.assertNotIn(DON, encoded_state)
        self.assertNotIn(SECRET, encoded_state)
        self.assertEqual(store.get_pending("call-live-don1")["role"], "don")
        again = bland.place_plan_calls(
            plan,
            state,
            now=NOW,
            environ=environ,
            poster=poster,
            store=store,
        )
        self.assertEqual([row["status"] for row in again], ["already_placed", "already_placed"])
        self.assertEqual(len(calls), 2)

    def test_tool_bearer_overrides_signing_secret(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0", LEAK_ALERT_BLAND_WEBHOOK_URL="https://example.invalid/leak")
        environ["BLAND_WEBHOOK_SECRET"] = SECRET
        environ["LEAK_ALERT_TOOL_BEARER"] = "tool-bearer-test"
        plan, state = self._plan(environ)
        seen = []

        def poster(body, api_key):
            seen.append(body)
            return 200, {"call_id": f"call-bearer-{body['metadata']['role']}1"}

        notes = bland.place_plan_calls(plan, state, now=NOW, environ=environ, poster=poster)
        self.assertEqual([row["status"] for row in notes], ["placed", "placed"])
        header = seen[0]["tools"][0]["headers"]["Authorization"]
        self.assertEqual(header, "Bearer tool-bearer-test")
        self.assertNotIn(SECRET, header)
        encoded = json.dumps(state)
        self.assertNotIn("tool-bearer-test", encoded)
        self.assertNotIn(SECRET, encoded)

    def test_400_retries_once_without_named_field(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0", LEAK_ALERT_BLAND_WEBHOOK_URL="https://example.invalid/leak")
        environ["BLAND_WEBHOOK_SECRET"] = SECRET
        plan, state = self._plan(environ)
        seen = []

        def poster(body, api_key):
            seen.append(body)
            role = body["metadata"]["role"]
            if body.get("analysis_schema"):
                return 400, {"message": "analysis_schema is not a valid parameter"}
            return 200, {"call_id": f"call-drop-{role}1"}

        with self.assertLogs("leak_alert_bland", level="INFO") as logs:
            notes = bland.place_plan_calls(plan, state, now=NOW, environ=environ, poster=poster)
        self.assertEqual([row["status"] for row in notes], ["placed", "placed"])
        self.assertEqual(len(seen), 4)
        self.assertIn("analysis_schema", seen[0])
        self.assertNotIn("analysis_schema", seen[1])
        self.assertIn("tools", seen[1])
        text = "\n".join(logs.output)
        self.assertIn("retrying without analysis_schema", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(DON, text)
        self.assertNotIn(TOM, text)

    def test_400_with_call_id_does_not_retry(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0")
        plan, state = self._plan(environ)
        seen = []

        def poster(body, api_key):
            seen.append(body)
            return 400, {"call_id": "call-already-01", "message": "analysis_schema rejected"}

        notes = bland.place_plan_calls(plan, state, now=NOW, environ=environ, poster=poster)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(row["status"] == "failed" for row in notes))
        self.assertNotIn("call-already-01", json.dumps(state))

    def test_400_without_named_field_does_not_retry(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0")
        plan, state = self._plan(environ)
        seen = []

        def poster(body, api_key):
            seen.append(1)
            return 400, {"message": "invalid phone_number"}

        with self.assertNoLogs("leak_alert_bland", level="INFO"):
            notes = bland.place_plan_calls(plan, state, now=NOW, environ=environ, poster=poster)
        self.assertEqual(seen, [1, 1])
        self.assertTrue(all(row["status"] == "failed" for row in notes))

    def test_400_second_response_is_not_retried_again(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0", LEAK_ALERT_BLAND_WEBHOOK_URL="https://example.invalid/leak")
        environ["BLAND_WEBHOOK_SECRET"] = SECRET
        plan, state = self._plan(environ)
        seen = []

        def poster(body, api_key):
            seen.append(body)
            if "analysis_schema" in body:
                return 400, {"message": "analysis_schema rejected"}
            return 400, {"message": "tools rejected"}

        notes = bland.place_plan_calls(plan, state, now=NOW, environ=environ, poster=poster)
        self.assertEqual(len(seen), 4)
        self.assertNotIn("analysis_schema", seen[1])
        self.assertIn("tools", seen[1])
        self.assertTrue(all(row["status"] == "failed" for row in notes))

    def test_failure_is_not_retried_within_24h(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0")
        plan, state = self._plan(environ)
        calls = {"n": 0}

        def poster(body, api_key):
            calls["n"] += 1
            return 500, {}

        first = bland.place_plan_calls(plan, state, now=NOW, environ=environ, poster=poster)
        self.assertEqual(first[0]["status"], "failed")
        self.assertEqual(calls["n"], 2)
        second = bland.place_plan_calls(
            plan,
            state,
            now=NOW + timedelta(hours=23),
            environ=environ,
            poster=poster,
        )
        self.assertTrue(all(row["reason"] == "recent_failure" for row in second))
        self.assertEqual(calls["n"], 2)
        third = bland.place_plan_calls(
            plan,
            state,
            now=NOW + timedelta(hours=25),
            environ=environ,
            poster=poster,
        )
        self.assertEqual(third[0]["status"], "failed")
        self.assertEqual(calls["n"], 4)
        leak_alert.assert_plan_has_no_numbers(json.dumps(state))

    def test_default_bland_post_uses_urlopen_mock(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0")
        body = bland.build_call_body(
            phone_number=DON,
            script="Water emergency at 100 Example Lane, room 2. Category pipe. Tenant has been told to shut off the water. This is an automated notice.",
            incident_id="chat-leana:m-leak",
            role="don",
            webhook_url="https://example.invalid/leak",
            webhook_secret=SECRET,
        )
        captured = {}

        class Resp:
            status = 200

            def read(self):
                return b'{"status":"success","call_id":"call-mocked-01"}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def opener(request, timeout=30):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return Resp()

        status, payload = bland.default_bland_post(body, "bland-test", environ=environ, opener=opener)
        self.assertEqual(status, 200)
        self.assertEqual(payload["call_id"], "call-mocked-01")
        self.assertEqual(captured["url"], "https://api.bland.ai/v1/calls")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["auth"], "Bearer bland-test")
        self.assertEqual(captured["body"]["metadata"]["role"], "don")
        self.assertEqual(captured["body"]["phone_number"], DON)

    def test_default_post_refuses_when_dry_run(self) -> None:
        def opener(request, timeout=30):
            raise AssertionError("urlopen")

        with self.assertRaises(RuntimeError):
            bland.default_bland_post({"phone_number": DON}, "bland-test", environ=_env(), opener=opener)

    def test_poll_skips_when_webhook_record_exists(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0")
        state = {"alerts": {
            "voice:chat-leana:m-leak:tom": {
                "status": "placed",
                "call_id": "call-tom-poll",
                "role": "tom",
            }
        }}
        store = _store_for("call-tom-poll", "tom")
        store.put_record(INCIDENT, "tom", {})
        store.put_record("chat-leana:m-leak", "tom", {
            "call_id": "call-tom-poll",
            "outcome": "voicemail",
            "confirmed": None,
            "eta_minutes": None,
            "summary": "Left a message.",
            "role": "tom",
            "incident_id": "chat-leana:m-leak",
            "source": "webhook",
            "recorded_at": "2026-10-08T14:00:00Z",
        })

        def getter(call_id, api_key):
            raise AssertionError("poll must not GET when a webhook record exists")

        notes = bland.poll_open_calls(state, now=NOW, environ=environ, getter=getter, store=store)
        self.assertEqual(notes[0]["status"], "webhook_record")
        self.assertTrue(state["alerts"]["voice:chat-leana:m-leak:tom"]["result_recorded"])

    def test_poll_maps_get_payload(self) -> None:
        environ = _env(LEAK_ALERT_DRY_RUN="0")
        state = {"alerts": {
            "voice:chat-fixture:m-leak:tom": {
                "status": "placed",
                "call_id": "call-tom-get1",
                "role": "tom",
            }
        }}
        store = _store_for("call-tom-get1", "tom")

        def getter(call_id, api_key):
            self.assertEqual(call_id, "call-tom-get1")
            self.assertEqual(api_key, "bland-test")
            return 200, _payload("no_answer", call_id=call_id)

        notes = bland.poll_open_calls(state, now=FRESH, environ=environ, getter=getter, store=store)
        self.assertEqual(notes[0]["line"], "Tom: no answer")
        record = store.get_record(INCIDENT, "tom")
        assert record is not None
        self.assertEqual(record["source"], "poll")
        self.assertEqual(record["outcome"], "no_answer")
        self.assertNotIn(FAKE_TO, json.dumps(record))
        self.assertEqual(store.get_queue("call-tom-get1")["line"], "Tom: no answer")


class ResultPostTests(unittest.TestCase):
    def test_dry_run_logs_line_without_numbers(self) -> None:
        import tempfile

        environ = _env(LEAK_ALERT_RESULT_POST_ENABLE="1")
        self.assertTrue(bland.dry_run_enabled(environ))
        state = {"results": {}, "result_queue": {
            "call-don-020": {"call_id": "call-don-020", "line": "Don: going, ETA 20m", "role": "don"},
        }}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leak_alert_result.jsonl"
            notes = bland.drain_result_queue(
                state,
                now=NOW,
                environ=environ,
                result_log=path,
                quo_post=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no quo")),
            )
            text = path.read_text()
        self.assertEqual(notes[0]["status"], "would_send")
        self.assertIn("Don: going, ETA 20m", text)
        self.assertNotIn(DON, text)
        self.assertNotIn(GROUP_LIST, text)
        leak_alert.assert_plan_has_no_numbers(text)

    def test_live_result_posts_once_per_call(self) -> None:
        environ = _env(
            LEAK_ALERT_DRY_RUN="0",
            LEAK_ALERT_RESULT_POST_ENABLE="1",
            QUO_FROM_NUMBER="+15555550199",
        )
        store = bland.MemoryCallStore()
        store.enqueue("call-tom-vm1", {
            "call_id": "call-tom-vm1",
            "line": "Tom: voicemail",
            "role": "tom",
            "posted": False,
        })
        sent = []

        def quo_post(payload, api_key):
            sent.append((payload, api_key))
            return 200

        state = {"results": {}}
        first = bland.drain_result_queue(state, now=NOW, environ=environ, store=store, quo_post=quo_post)
        second = bland.drain_result_queue(state, now=NOW, environ=environ, store=store, quo_post=quo_post)
        self.assertEqual(first[0]["status"], "posted")
        self.assertEqual(second, [])
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0]["content"], "Tom: voicemail")
        self.assertEqual(sent[0][0]["from"], "+15555550199")
        self.assertNotIn("+15555550199", json.dumps(state))
        self.assertTrue(store.get_queue("call-tom-vm1")["posted"])

    def test_default_quo_post_uses_urlopen_mock(self) -> None:
        environ = _env(
            LEAK_ALERT_DRY_RUN="0",
            LEAK_ALERT_RESULT_POST_ENABLE="1",
            QUO_FROM_NUMBER="+15555550199",
        )
        captured = {}

        class Resp:
            status = 201

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def opener(request, timeout=30):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["version"] = request.get_header("Quo-api-version")
            return Resp()

        status = bland.default_quo_post(
            {"content": "Tom: no answer", "from": "+15555550199", "to": ["+15555550121"]},
            "quo-test",
            environ=environ,
            opener=opener,
        )
        self.assertEqual(status, 201)
        self.assertEqual(captured["url"], "https://api.quo.com/v1/messages")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"]["content"], "Tom: no answer")
        self.assertEqual(captured["version"], "2026-03-30")


class RulesTests(unittest.TestCase):
    def test_leak_alert_collections_are_admin_only(self) -> None:
        for snippet in (
            "match /leak_alert_calls/{incident}/roles/{role}",
            "match /leak_alert_pending/{callId}",
            "match /leak_alert_result_queue/{callId}",
        ):
            block = RULES.split(snippet)[1].split("match /")[0]
            self.assertIn("allow read, write: if false", block)


class tempfile_log:
    def __init__(self, _environ: dict) -> None:
        self._tmpdir = None

    def __enter__(self):
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        return root / "calls.jsonl", root / "results.jsonl"

    def __exit__(self, *_args):
        self._tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
