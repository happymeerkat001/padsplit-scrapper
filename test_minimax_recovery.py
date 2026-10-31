import contextlib
import http.client
import io
import json
import os
import tempfile
import traceback
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import message_summarizer as summarizer


def response(payload):
    result = MagicMock()
    result.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return result


class MiniMaxRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"MINIMAX_API_KEY": "dummy"}, clear=True))
        self.enterContext(patch("socket.socket", side_effect=AssertionError("network forbidden")))
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))

    def test_application_transient_errors_recover_with_same_request_and_cap(self):
        for code in (1000, 1001, 1002, 1024, 1033, 2064, 2066):
            with self.subTest(code=code):
                failure = response({"choices": None, "base_resp": {
                    "status_code": code, "status_msg": "PRIVATE_SENTINEL"}})
                success = response({"choices": [{"message": {"content": "[]"}}]})
                with (patch.object(summarizer.urllib.request, "urlopen", side_effect=[failure, success]) as request,
                      patch.object(summarizer.time, "sleep") as sleep):
                    self.assertEqual(summarizer.call_minimax("dummy prompt"), "[]")
                    self.assertEqual(request.call_count, 2)
                    first = request.call_args_list[0]
                    self.assertEqual(first.args[0].data, request.call_args_list[1].args[0].data)
                    body = json.loads(first.args[0].data)
                    self.assertEqual(body["model"], "MiniMax-M2.5")
                    self.assertEqual(body["max_completion_tokens"], 10240)
                    self.assertEqual(first.kwargs["timeout"], 120)
                    sleep.assert_called_once_with(10)
                    self.assertNotIn("PRIVATE_SENTINEL", self.output.getvalue())

    def test_exhaustion_has_safe_code_and_fixed_attempt_budget(self):
        failure = response({"choices": None, "base_resp": {
            "status_code": 1002, "status_msg": "PRIVATE_SENTINEL"}})
        with (patch.object(summarizer.urllib.request, "urlopen", return_value=failure) as request,
              patch.object(summarizer.time, "sleep") as sleep):
            with self.assertRaisesRegex(RuntimeError, "status_code=1002") as error:
                summarizer.call_minimax("dummy")
            self.assertEqual(request.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertNotIn("PRIVATE_SENTINEL", "".join(traceback.format_exception(error.exception)))

    def test_http_and_application_errors_share_attempt_budget(self):
        http_error = urllib.error.HTTPError("https://dummy.test", 429, "PRIVATE_SENTINEL", {}, None)
        application_error = response({"choices": None, "base_resp": {"status_code": 1002}})
        with (patch.object(summarizer.urllib.request, "urlopen",
                           side_effect=[http_error, application_error, application_error]) as request,
              patch.object(summarizer.time, "sleep") as sleep):
            with self.assertRaisesRegex(RuntimeError, "status_code=1002"):
                summarizer.call_minimax("dummy")
            self.assertEqual(request.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [10, 20])
            self.assertNotIn("PRIVATE_SENTINEL", self.output.getvalue())

    def test_connection_close_and_socket_timeout_can_recover(self):
        for error in (http.client.RemoteDisconnected("PRIVATE_SENTINEL"), TimeoutError("PRIVATE_SENTINEL")):
            with self.subTest(error_type=type(error).__name__):
                success = response({"choices": [{"message": {"content": "[]"}}]})
                with (patch.object(summarizer.urllib.request, "urlopen", side_effect=[error, success]) as request,
                      patch.object(summarizer.time, "sleep") as sleep):
                    self.assertEqual(summarizer.call_minimax("dummy"), "[]")
                    self.assertEqual(request.call_count, 2)
                    sleep.assert_called_once_with(10)
                    self.assertNotIn("PRIVATE_SENTINEL", self.output.getvalue())

    def test_mixed_transport_failures_exhaust_same_budget_safely(self):
        errors = [http.client.RemoteDisconnected("PRIVATE_SENTINEL"),
                  urllib.error.URLError("PRIVATE_SENTINEL"), TimeoutError("PRIVATE_SENTINEL")]
        with (patch.object(summarizer.urllib.request, "urlopen", side_effect=errors) as request,
              patch.object(summarizer.time, "sleep") as sleep):
            with self.assertRaisesRegex(SystemExit, "transport request failed") as error:
                summarizer.call_minimax("dummy")
            self.assertEqual(request.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertNotIn("PRIVATE_SENTINEL", str(error.exception))

    def test_permanent_status_is_actionable_without_retry_or_raw_text(self):
        for code in (1004, 1008, 1039, 2013, 2022, 2049, 2056, 2057, 2061, 2067, 2068, 2069, 9999):
            with self.subTest(code=code):
                failure = response({"choices": None, "base_resp": {
                    "status_code": code, "status_msg": "PRIVATE_SENTINEL"}})
                with (patch.object(summarizer.urllib.request, "urlopen", return_value=failure) as request,
                      patch.object(summarizer.time, "sleep") as sleep):
                    with self.assertRaisesRegex(RuntimeError, f"status_code={code}") as error:
                        summarizer.call_minimax("dummy")
                    request.assert_called_once()
                    sleep.assert_not_called()
                    self.assertNotIn("PRIVATE_SENTINEL", str(error.exception))

    def test_main_recovers_and_sends_only_validated_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dummy.json"
            path.write_text('{"messages": []}')
            failure = response({"choices": None, "base_resp": {"status_code": 1002}})
            success = response({"choices": [{"message": {"content": "[]"}}]})
            with (patch.object(summarizer, "DATA_PATH", path),
                  patch.object(summarizer.urllib.request, "urlopen", side_effect=[failure, success]),
                  patch.object(summarizer.time, "sleep"),
                  patch.object(summarizer, "send_to_discord") as send):
                summarizer.main()
                send.assert_called_once_with("No urgent tenant messages.")

    def test_main_rejects_two_invalid_contents_without_raw_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dummy.json"
            path.write_text('{"messages": []}')
            with (patch.object(summarizer, "DATA_PATH", path),
                  patch.object(summarizer, "call_minimax", return_value="PRIVATE_SENTINEL") as call,
                  patch.object(summarizer, "send_to_discord") as send):
                with self.assertRaisesRegex(RuntimeError, "invalid summary JSON") as error:
                    summarizer.main()
                self.assertEqual(call.call_count, 2)
                send.assert_not_called()
                self.assertNotIn("PRIVATE_SENTINEL", "".join(traceback.format_exception(error.exception)))

    def test_main_format_retry_cannot_exceed_six_provider_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dummy.json"
            path.write_text('{"messages": []}')
            transient = response({"choices": None, "base_resp": {"status_code": 1002}})
            malformed = response({"choices": [{"message": {"content": "DUMMY_INVALID_JSON"}}]})
            with (patch.object(summarizer, "DATA_PATH", path),
                  patch.object(summarizer.urllib.request, "urlopen",
                               side_effect=[transient, transient, malformed] * 2) as request,
                  patch.object(summarizer.time, "sleep") as sleep,
                  patch.object(summarizer, "send_to_discord") as send):
                with self.assertRaisesRegex(RuntimeError, "invalid summary JSON"):
                    summarizer.main()
                self.assertEqual(request.call_count, 6)
                self.assertEqual(sleep.call_count, 4)
                send.assert_not_called()
                self.assertNotIn("DUMMY_INVALID_JSON", self.output.getvalue())

    def test_recovery_reaches_discord_http_boundary_without_logging_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dummy.json"
            path.write_text('{"messages": [{"id": "dummy-chat"}]}')
            content = '[{"chat_id":"dummy-chat","summary":"DUMMY_PRIVATE_SUMMARY","sent_at":"now"}]'
            failure = response({"choices": None, "base_resp": {"status_code": 1002}})
            success = response({"choices": [{"message": {"content": content}}]})
            discord = response({})
            discord.__enter__.return_value.getcode.return_value = 204
            with (patch.dict(os.environ, {"DISCORD_WEBHOOK_MESSAGES": "https://discord.test/dummy"}),
                  patch.object(summarizer, "DATA_PATH", path),
                  patch.object(summarizer.urllib.request, "urlopen", side_effect=[failure, success, discord]) as request,
                  patch.object(summarizer.time, "sleep")):
                summarizer.main()
                self.assertEqual(request.call_count, 3)
                sent = request.call_args.args[0]
                self.assertEqual(sent.full_url, "https://discord.test/dummy")
                self.assertIn("DUMMY_PRIVATE_SUMMARY", json.loads(sent.data)["content"])
                self.assertNotIn("DUMMY_PRIVATE_SUMMARY", self.output.getvalue())

    def test_malformed_summary_shapes_raise_safe_validation_errors(self):
        for raw in ("```", "```json```", '[{"chat_id":[],"summary":"dummy"}]',
                    '[{"chat_id":"dummy","summary":{}}]',
                    '[{"chat_id":"dummy","summary":"dummy","sent_at":[]}]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                summarizer.parse_urgent_items(raw)

    def test_truncated_completion_never_becomes_a_summary(self):
        raw = json.dumps({"choices": [{"finish_reason": "length", "message": {"content": "[]"}}]}).encode()
        with self.assertRaisesRegex(RuntimeError, "truncated completion"):
            summarizer._validated_minimax_content(raw)


if __name__ == "__main__":
    unittest.main()
