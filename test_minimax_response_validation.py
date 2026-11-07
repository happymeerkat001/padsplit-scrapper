import json
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import message_summarizer as summarizer


class ResponseValidationTests(unittest.TestCase):
    def test_invalid_responses_fail_without_send_or_retry(self):
        valid = {"choices": [{"message": {"content": "[]"}}]}
        envelopes = [{}, {"choices": None}, {"choices": []},
                     {"choices": {}}, {"choices": "bad"}, {"choices": [None]},
                     {"choices": [{}]}, {"choices": [{"message": None}]},
                     {"choices": [{"message": []}]}, [],
                     {**valid, "error": {"message": "PRIVATE_SENTINEL"}},
                     {**valid, "base_resp": {"status_code": 1004,
                                              "status_msg": "PRIVATE_SENTINEL"}}]
        for content in (None, [], {}, 7, "", "   "):
            envelopes.append({"choices": [{"message": {"content": content}}]})
        envelopes.append({"choices": [{"message": {}}]})
        for status in (None, [], "bad", False, 0, {},
                       {"status_code": False}, {"status_code": 0.0},
                       {"status_code": "0"}, {"status_code": None}):
            envelopes.append({**valid, "base_resp": status})
        bodies = [json.dumps(item).encode() for item in envelopes]
        bodies.extend([b"PRIVATE_SENTINEL invalid json", b"\xff"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dummy.json"
            path.write_text('{"messages": []}')
            for body in bodies:
                with self.subTest(body=body):
                    response = MagicMock()
                    response.__enter__.return_value.read.return_value = body
                    with (patch.dict("os.environ", {"MINIMAX_API_KEY": "dummy"}, clear=True),
                          patch.object(summarizer, "DATA_PATH", path),
                          patch("socket.socket", side_effect=AssertionError("network forbidden")),
                          patch.object(summarizer.urllib.request, "urlopen", return_value=response) as request,
                          patch.object(summarizer, "send_to_discord") as send,
                          patch.object(summarizer.time, "sleep") as sleep):
                        with self.assertRaisesRegex(RuntimeError, "MiniMax response rejected") as error:
                            summarizer.main()
                        self.assertNotIn("PRIVATE_SENTINEL", str(error.exception))
                        request.assert_called_once()
                        send.assert_not_called()
                        sleep.assert_not_called()

    def test_parsing_traceback_is_sanitized(self):
        for raw in (b"PRIVATE_SENTINEL", b"\xffPRIVATE_SENTINEL"):
            try:
                summarizer._validated_minimax_content(raw)
            except RuntimeError as error:
                formatted = "".join(traceback.format_exception(error))
                self.assertNotIn("PRIVATE_SENTINEL", formatted)
                self.assertNotIn("JSONDecodeError", formatted)
                self.assertNotIn("UnicodeDecodeError", formatted)
                self.assertTrue(error.__suppress_context__)
            else:
                self.fail("invalid response accepted")

    def test_valid_content_preserved(self):
        content = '  []\n'
        for extra in ({}, {"base_resp": {"status_code": 0}}):
            raw = json.dumps({**extra, "choices": [{"message": {"content": content}}]}).encode()
            self.assertEqual(summarizer._validated_minimax_content(raw), content)


if __name__ == "__main__":
    unittest.main()
