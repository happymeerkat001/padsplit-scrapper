import unittest
from unittest.mock import MagicMock, patch

import requests

import discord_notifier


class PostDiscordMessageTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"DISCORD_BOT_TOKEN": "test-token", "DISCORD_CHANNEL_ID": "12345"},
    )
    @patch("discord_notifier.requests.post")
    def test_posts_with_bot_authorization_and_returns_message_json(self, mock_post):
        response = MagicMock()
        response.json.return_value = {"id": "999", "channel_id": "12345"}
        mock_post.return_value = response

        result = discord_notifier.post_discord_message("hello")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://discord.com/api/v10/channels/12345/messages")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot test-token")
        self.assertEqual(kwargs["json"], {"content": "hello"})
        response.raise_for_status.assert_called_once()
        self.assertEqual(result, {"id": "999", "channel_id": "12345"})

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_token_or_channel_raises(self):
        with self.assertRaises(RuntimeError):
            discord_notifier.post_discord_message("hello")

    @patch.dict(
        "os.environ",
        {"DISCORD_BOT_TOKEN": "test-token", "DISCORD_CHANNEL_ID": "12345"},
    )
    @patch("discord_notifier.requests.post")
    def test_http_error_propagates(self, mock_post):
        response = MagicMock()
        response.raise_for_status.side_effect = requests.HTTPError("boom")
        mock_post.return_value = response

        with self.assertRaises(requests.HTTPError):
            discord_notifier.post_discord_message("hello")


if __name__ == "__main__":
    unittest.main()
