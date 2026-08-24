#!/usr/bin/env python3
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

import message_summarizer as ms


class SendToDiscordTests(unittest.TestCase):
    @patch.dict("os.environ", {"DISCORD_WEBHOOK_MESSAGES": "https://discord.test/webhook"})
    @patch("message_summarizer.urllib.request.urlopen")
    def test_send_to_discord_success(self, mock_urlopen):
        resp = MagicMock()
        resp.getcode.return_value = 204
        mock_urlopen.return_value.__enter__.return_value = resp

        ms.send_to_discord("hello")

        self.assertTrue(mock_urlopen.called)
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://discord.test/webhook")

    @patch.dict("os.environ", {}, clear=True)
    @patch("message_summarizer.urllib.request.urlopen")
    def test_send_to_discord_missing_env(self, mock_urlopen):
        ms.send_to_discord("hello")

        mock_urlopen.assert_not_called()

    @patch.dict("os.environ", {"DISCORD_WEBHOOK_MESSAGES": "https://discord.test/webhook"})
    @patch("message_summarizer.urllib.request.urlopen")
    def test_send_to_discord_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://discord.test/webhook", 500, "Server Error", {}, None
        )

        try:
            ms.send_to_discord("hello")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"send_to_discord raised unexpectedly: {exc}")

    @patch.dict("os.environ", {"DISCORD_WEBHOOK_MESSAGES": "https://discord.test/webhook"})
    @patch("message_summarizer.urllib.request.urlopen")
    def test_send_to_discord_truncates_long_content(self, mock_urlopen):
        resp = MagicMock()
        resp.getcode.return_value = 204
        mock_urlopen.return_value.__enter__.return_value = resp

        ms.send_to_discord("x" * 3000)

        request = mock_urlopen.call_args[0][0]
        sent_body = request.data.decode()
        self.assertLessEqual(len(sent_body), ms.DISCORD_MESSAGE_LIMIT + len('{"content": ""}'))
        self.assertIn(ms.TRUNCATION_SUFFIX, sent_body)


if __name__ == "__main__":
    unittest.main()
