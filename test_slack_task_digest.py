#!/usr/bin/env python3
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

import slack_task_digest as digest


class SendToDiscordTests(unittest.TestCase):
    @patch.dict("os.environ", {"DISCORD_WEBHOOK_TASKS": "https://discord.test/webhook"})
    @patch("slack_task_digest.urllib.request.urlopen")
    def test_send_to_discord_success(self, mock_urlopen):
        resp = MagicMock()
        resp.getcode.return_value = 204
        mock_urlopen.return_value.__enter__.return_value = resp

        digest.send_to_discord("hello")

        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://discord.test/webhook")

    @patch.dict("os.environ", {}, clear=True)
    @patch("slack_task_digest.urllib.request.urlopen")
    def test_send_to_discord_missing_env(self, mock_urlopen):
        digest.send_to_discord("hello")

        mock_urlopen.assert_not_called()

    @patch.dict("os.environ", {"DISCORD_WEBHOOK_TASKS": "https://discord.test/webhook"})
    @patch("slack_task_digest.urllib.request.urlopen")
    def test_send_to_discord_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://discord.test/webhook", 500, "Server Error", {}, None
        )

        try:
            digest.send_to_discord("hello")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"send_to_discord raised unexpectedly: {exc}")

    @patch.dict("os.environ", {"DISCORD_WEBHOOK_TASKS": "https://discord.test/webhook"})
    @patch("slack_task_digest.urllib.request.urlopen")
    def test_send_to_discord_truncates_long_digest(self, mock_urlopen):
        resp = MagicMock()
        resp.getcode.return_value = 204
        mock_urlopen.return_value.__enter__.return_value = resp

        digest.send_to_discord("Tasks Digest:\n" + "\n".join(f"line {i}" for i in range(500)))

        request = mock_urlopen.call_args[0][0]
        sent_body = request.data.decode()
        self.assertIn(digest.TRUNCATION_SUFFIX, sent_body)


if __name__ == "__main__":
    unittest.main()
