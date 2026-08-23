import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import discord_reply_monitor as monitor


class FetchRepliesTests(unittest.TestCase):
    @patch("discord_reply_monitor.requests.get")
    def test_fetch_replies_processes_oldest_first(self, mock_get):
        response = MagicMock()
        response.json.return_value = [
            {"id": "3", "content": "third"},
            {"id": "2", "content": "second"},
            {"id": "1", "content": "first"},
        ]
        mock_get.return_value = response

        replies = monitor._fetch_replies("token", "channel-1", "msg-0")

        self.assertEqual([r["id"] for r in replies], ["1", "2", "3"])
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], "https://discord.com/api/v10/channels/channel-1/messages")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot token")
        self.assertEqual(kwargs["params"]["after"], "msg-0")


class ProcessedRepliesDedupTests(unittest.TestCase):
    def test_load_and_save_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "processed_replies.json"
            monitor._save_processed_replies(path, {"1", "2"})
            loaded = monitor._load_processed_replies(path)
            self.assertEqual(loaded, {"1", "2"})


class MainGuardTests(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_bot_token_raises(self):
        with self.assertRaisesRegex(RuntimeError, "Missing DISCORD_BOT_TOKEN"):
            monitor.main()

    @patch.dict("os.environ", {"DISCORD_BOT_TOKEN": "token"})
    def test_missing_meta_file_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            with patch.object(monitor, "__file__", str(base_dir / "discord_reply_monitor.py")):
                with self.assertRaisesRegex(RuntimeError, "discord_digest_meta.json"):
                    monitor.main()


class AddressMatchIntegrationTests(unittest.TestCase):
    def test_complete_reply_matches_task_and_marks_processed(self):
        tasks = {
            "Requests": [
                {"id": 101, "property_address": {"street1": "1025 Broken Crest Rd", "city": "Fort Worth", "state": "TX"}},
            ],
            "Open": [],
        }
        matcher = monitor._build_address_matcher(tasks)

        task_ids = monitor._extract_completed_task_ids("1025 Broken Crest Rd Complete", matcher)

        self.assertEqual(task_ids, [101])


if __name__ == "__main__":
    unittest.main()
