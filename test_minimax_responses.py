"""Offline regression tests; no secrets, network or Discord sends."""
import contextlib
import io
import json
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch
import message_summarizer as m


def response(payload):
    r = MagicMock()
    r.__enter__.return_value = r
    r.getcode.return_value = 200
    r.read.return_value = json.dumps(payload).encode()
    return r


GOOD = {"base_resp": {"status_code": 0}, "choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]}


class MiniMaxResponseTests(unittest.TestCase):
    def call(self, effects):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "secret-canary"}), \
             patch.object(m.urllib.request, "urlopen", side_effect=effects) as request, \
             patch.object(m.time, "sleep") as sleep:
            output = m.call_minimax("private-tenant-canary")
        return output, request, sleep

    def test_success_and_timeout(self):
        result, request, sleep = self.call([response(GOOD)])
        self.assertEqual(result, "[]")
        self.assertEqual(request.call_args.kwargs["timeout"], 120)
        sleep.assert_not_called()

    def test_invalid_shapes_are_controlled_errors(self):
        values = [None, [], {}, {"choices": None}, {"choices": []}, {"choices": {}},
                  {"choices": [None]}, {"choices": [{"message": None}]},
                  {"base_resp": None}, {"base_resp": {"status_code": "0"}}]
        for content in [None, "", "  ", [], {}]:
            values.append({"choices": [{"message": {"content": content}}]})
        for value in values:
            with self.subTest(value=value), self.assertRaises(m.MiniMaxError):
                m.minimax_content(value)

    def test_permanent_api_errors_do_not_retry_or_leak(self):
        for code in [1004, 1008, 2013, 2022, 2069]:
            payload = {"choices": None, "base_resp": {"status_code": code, "status_msg": "secret-canary private-tenant-canary"}}
            with patch.dict(os.environ, {"MINIMAX_API_KEY": "secret-canary"}), \
                 patch.object(m.urllib.request, "urlopen", return_value=response(payload)) as req, \
                 patch.object(m.time, "sleep") as sleep, \
                 self.assertRaises(SystemExit) as caught:
                m.call_minimax("private-tenant-canary")
            self.assertIn(str(code), str(caught.exception))
            self.assertNotIn("canary", str(caught.exception))
            self.assertEqual(req.call_count, 1)
            sleep.assert_not_called()

    def test_transient_api_error_recovers(self):
        _, req, sleep = self.call([response({"base_resp": {"status_code": 1002}}), response(GOOD)])
        self.assertEqual(req.call_count, 2)
        sleep.assert_called_once_with(10)

    def test_null_choices_exhaust_retries(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "dummy"}), \
             patch.object(m.urllib.request, "urlopen", return_value=response({"choices": None})) as req, \
             patch.object(m.time, "sleep") as sleep, self.assertRaises(SystemExit) as caught:
            m.call_minimax("test")
        self.assertEqual(req.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIn("summary unavailable", str(caught.exception))

    def test_http_and_network_retries(self):
        for error in [TimeoutError("secret-canary"), urllib.error.URLError("secret-canary"),
                      urllib.error.HTTPError("url", 503, "secret-canary", {}, io.BytesIO(b"bad gateway"))]:
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                _, req, _ = self.call([error, response(GOOD)])
            self.assertEqual(req.call_count, 2)
            self.assertNotIn("secret-canary", output.getvalue())

    def test_http_error_retains_api_code(self):
        error = urllib.error.HTTPError("url", 401, "secret-canary", {}, io.BytesIO(json.dumps({"base_resp": {"status_code": 1004}}).encode()))
        with self.assertRaises(SystemExit) as caught:
            self.call([error])
        self.assertIn("1004", str(caught.exception))
        self.assertIn("HTTP=401", str(caught.exception))

    def test_invalid_json_recovers(self):
        bad = response(None)
        bad.read.return_value = b"<html>secret-canary</html>"
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result, _, _ = self.call([bad, response(GOOD)])
        self.assertEqual(result, "[]")
        self.assertNotIn("canary", output.getvalue())

    def test_truncated_or_error_response_rejected(self):
        for value in [{"error": {"message": "secret-canary"}},
                      {"choices": [{"finish_reason": "length", "message": {"content": "[]"}}]}]:
            with self.assertRaises(m.MiniMaxError):
                m.minimax_content(value)

    def test_main_does_not_send_when_api_fails(self):
        with patch.object(m, "DATA_PATH") as path, patch.object(m, "call_minimax", side_effect=SystemExit("unavailable")), patch.object(m, "send_to_discord") as send:
            path.exists.return_value = True
            path.read_text.return_value = '{"messages": []}'
            with self.assertRaises(SystemExit):
                m.main()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
