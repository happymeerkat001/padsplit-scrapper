import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import discord_reply_monitor


class FetchRepliesTests(unittest.TestCase):
    def test_fetch_replies_reverses_newest_first_response_to_chronological(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"id": "3", "content": "third"},
            {"id": "2", "content": "second"},
            {"id": "1", "content": "first"},
        ]
        mock_response.raise_for_status.return_value = None

        with patch.object(discord_reply_monitor.requests, "get", return_value=mock_response) as mock_get:
            replies = discord_reply_monitor._fetch_replies("token", "channel-1", "0")

        self.assertEqual([r["id"] for r in replies], ["1", "2", "3"])
        headers = mock_get.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bot token")
        self.assertEqual(mock_get.call_args.kwargs["params"]["after"], "0")


class MainFlowTests(unittest.TestCase):
    def _run_main(self, *, env, meta, processed=None, replies=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "docs" / "data").mkdir(parents=True)
            (base_dir / "docs" / "data" / "latest.json").write_text(
                json.dumps({"tasks": {"Requests": [], "Open": []}})
            )
            if meta is not None:
                (base_dir / "docs" / "data" / "discord_digest_meta.json").write_text(json.dumps(meta))
            if processed is not None:
                (base_dir / "docs" / "data" / "processed_replies.json").write_text(json.dumps(processed))

            update_calls = []

            with patch.object(discord_reply_monitor.Path, "resolve", return_value=base_dir / "discord_reply_monitor.py"), \
                 patch("os.getenv", side_effect=lambda k, d=None: env.get(k, d)), \
                 patch.object(discord_reply_monitor, "load_credentials", return_value={"email": "e", "password": "p"}), \
                 patch.object(discord_reply_monitor, "create_session"), \
                 patch.object(discord_reply_monitor, "login"), \
                 patch.object(discord_reply_monitor, "update_task_status", side_effect=lambda *a, **k: update_calls.append(a)), \
                 patch.object(discord_reply_monitor, "_fetch_replies", return_value=replies or []):
                discord_reply_monitor.main()

            return update_calls

    def test_missing_bot_token_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Missing DISCORD_BOT_TOKEN"):
            self._run_main(env={}, meta={"channel": "c", "message_id": "1"})

    def test_missing_meta_file_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Missing or invalid docs/data/discord_digest_meta.json"):
            self._run_main(env={"DISCORD_BOT_TOKEN": "tok"}, meta=None)

    def test_reply_matching_address_and_complete_marks_task(self) -> None:
        meta = {"channel": "c", "message_id": "0"}
        replies = [{"id": "5", "content": "1025 Broken Crest Rd Complete"}]

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            (base_dir / "docs" / "data").mkdir(parents=True)
            (base_dir / "docs" / "data" / "latest.json").write_text(json.dumps({
                "tasks": {
                    "Requests": [{"id": 101, "property_address": {"street1": "1025 Broken Crest Rd"}}],
                    "Open": [],
                }
            }))
            (base_dir / "docs" / "data" / "discord_digest_meta.json").write_text(json.dumps(meta))

            update_calls = []
            with patch.object(discord_reply_monitor.Path, "resolve", return_value=base_dir / "discord_reply_monitor.py"), \
                 patch("os.getenv", side_effect=lambda k, d=None: {"DISCORD_BOT_TOKEN": "tok"}.get(k, d)), \
                 patch.object(discord_reply_monitor, "load_credentials", return_value={"email": "e", "password": "p"}), \
                 patch.object(discord_reply_monitor, "create_session"), \
                 patch.object(discord_reply_monitor, "login"), \
                 patch.object(discord_reply_monitor, "update_task_status", side_effect=lambda *a, **k: update_calls.append(a)), \
                 patch.object(discord_reply_monitor, "_fetch_replies", return_value=replies):
                discord_reply_monitor.main()

            self.assertEqual(len(update_calls), 1)
            self.assertEqual(update_calls[0][2], 101)

            processed = json.loads((base_dir / "docs" / "data" / "processed_replies.json").read_text())
            self.assertIn("5", processed["processed_reply_ts"])

    def test_already_processed_reply_id_is_skipped(self) -> None:
        update_calls = self._run_main(
            env={"DISCORD_BOT_TOKEN": "tok"},
            meta={"channel": "c", "message_id": "0"},
            processed={"processed_reply_ts": ["5"]},
            replies=[{"id": "5", "content": "1025 Broken Crest Rd Complete"}],
        )

        self.assertEqual(update_calls, [])


if __name__ == "__main__":
    unittest.main()
