#!/usr/bin/env python3
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import message_summarizer
from message_summarizer import (
    DISCORD_MESSAGE_LIMIT,
    PROMPT,
    format_address,
    format_room,
    parse_urgent_items,
    render_summary,
    send_to_discord,
    truncate_for_discord,
)


class MessageSummarizerTests(unittest.TestCase):
    def run_main_with(self, responses: list[str]) -> tuple[list[str], list[str]]:
        payload = {
            "messages": [
                {
                    "id": "chat-1",
                    "occupancy": {"room": {"roomNumber": 2}},
                    "property": {
                        "address": {
                            "street1": "10235 Ridge Oak",
                            "city": {"name": "Dallas", "state": {"name": "TX"}},
                        }
                    },
                }
            ]
        }
        prompts = []
        sent_messages = []

        with tempfile.TemporaryDirectory() as tmpdir:
            data_path = Path(tmpdir) / "latest.json"
            data_path.write_text(json.dumps(payload))

            def fake_call_minimax(prompt: str) -> str:
                prompts.append(prompt)
                return responses.pop(0)

            with patch.object(message_summarizer, "DATA_PATH", data_path), \
                 patch.object(message_summarizer, "call_minimax", side_effect=fake_call_minimax), \
                 patch.object(message_summarizer, "send_to_discord", side_effect=sent_messages.append):
                message_summarizer.main()

        return prompts, sent_messages

    def test_format_room_and_address_from_complete_chat(self) -> None:
        chat = {
            "occupancy": {"room": {"roomNumber": 12}},
            "property": {
                "address": {
                    "street1": "10235 Ridge Oak",
                    "city": {"name": "Dallas", "state": {"name": "TX"}},
                }
            },
        }

        self.assertEqual(format_room(chat), "12")
        self.assertEqual(format_address(chat), "10235 Ridge Oak, Dallas, TX")

    def test_format_room_returns_unknown_when_occupancy_missing(self) -> None:
        self.assertEqual(format_room({}), "Unknown")

    def test_format_room_returns_unknown_when_room_number_is_none(self) -> None:
        self.assertEqual(format_room({"occupancy": {"room": {"roomNumber": None}}}), "Unknown")

    def test_format_address_falls_back_for_missing_state_only(self) -> None:
        chat = {
            "property": {
                "address": {
                    "street1": "10235 Ridge Oak",
                    "city": {"name": "Dallas"},
                }
            }
        }

        self.assertEqual(format_address(chat), "10235 Ridge Oak, Dallas, Unknown")

    def test_format_address_returns_unknown_when_property_missing(self) -> None:
        self.assertEqual(format_address({}), "Unknown")

    def test_prompt_requests_json_urgent_item_contract(self) -> None:
        self.assertIn("JSON array", PROMPT)
        self.assertIn('"chat_id"', PROMPT)
        self.assertIn('"summary"', PROMPT)
        self.assertIn('"sent_at"', PROMPT)

    def test_parse_urgent_items_parses_clean_json(self) -> None:
        raw = '[{"chat_id": "chat-1", "summary": "Water leak", "sent_at": "2026-07-26T10:00:00Z"}]'

        self.assertEqual(
            parse_urgent_items(raw),
            [{"chat_id": "chat-1", "summary": "Water leak", "sent_at": "2026-07-26T10:00:00Z"}],
        )

    def test_parse_urgent_items_parses_json_code_fence(self) -> None:
        raw = '```json\n[{"chat_id": "chat-1", "summary": "Water leak"}]\n```'

        self.assertEqual(parse_urgent_items(raw), [{"chat_id": "chat-1", "summary": "Water leak"}])

    def test_parse_urgent_items_accepts_empty_array(self) -> None:
        self.assertEqual(parse_urgent_items("[]"), [])

    def test_parse_urgent_items_drops_entries_without_chat_id(self) -> None:
        raw = '[{"summary": "No id"}, {"chat_id": "chat-1", "summary": "Water leak"}]'

        self.assertEqual(parse_urgent_items(raw), [{"chat_id": "chat-1", "summary": "Water leak"}])

    def test_parse_urgent_items_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            parse_urgent_items("urgent message: water leak")

    def test_render_summary_includes_correct_address_and_room_per_chat(self) -> None:
        messages_by_id = {
            "chat-1": {
                "occupancy": {"room": {"roomNumber": 2}},
                "property": {"address": {"street1": "10235 Ridge Oak", "city": {"name": "Dallas", "state": {"name": "TX"}}}},
            },
            "chat-2": {
                "occupancy": {"room": {"roomNumber": 7}},
                "property": {"address": {"street1": "4100 N Main St", "city": {"name": "Fort Worth", "state": {"name": "TX"}}}},
            },
        }
        urgent_items = [
            {"chat_id": "chat-1", "summary": "Water leak", "sent_at": "2026-07-26T10:00:00Z"},
            {"chat_id": "chat-2", "summary": "Gas smell", "sent_at": "2026-07-26T10:05:00Z"},
        ]

        rendered = render_summary(urgent_items, messages_by_id)

        self.assertIn("10235 Ridge Oak, Dallas, TX — Room 2 — 2026-07-26T10:00:00Z — Water leak", rendered)
        self.assertIn("4100 N Main St, Fort Worth, TX — Room 7 — 2026-07-26T10:05:00Z — Gas smell", rendered)

    def test_render_summary_skips_unknown_chat_and_keeps_valid_item(self) -> None:
        messages_by_id = {"chat-1": {"occupancy": {"room": {"roomNumber": 2}}, "property": {"address": {"street1": "10235 Ridge Oak", "city": {"name": "Dallas", "state": {"name": "TX"}}}}}}
        urgent_items = [
            {"chat_id": "missing", "summary": "Ignore", "sent_at": "now"},
            {"chat_id": "chat-1", "summary": "Water leak", "sent_at": "now"},
        ]

        rendered = render_summary(urgent_items, messages_by_id)

        self.assertNotIn("Ignore", rendered)
        self.assertIn("Water leak", rendered)

    def test_render_summary_returns_empty_state(self) -> None:
        self.assertEqual(render_summary([], {}), "No urgent tenant messages.")

    def test_render_summary_uses_unknown_for_missing_room_and_address(self) -> None:
        rendered = render_summary(
            [{"chat_id": "chat-1", "summary": "Water leak", "sent_at": "now"}],
            {"chat-1": {}},
        )

        self.assertIn("Unknown — Room Unknown — now — Water leak", rendered)

    def test_main_sends_rendered_summary_for_valid_json(self) -> None:
        prompts, sent_messages = self.run_main_with(
            ['[{"chat_id": "chat-1", "summary": "Water leak", "sent_at": "now"}]']
        )

        self.assertEqual(len(prompts), 1)
        self.assertEqual(sent_messages, ["10235 Ridge Oak, Dallas, TX — Room 2 — now — Water leak"])

    def test_main_retries_with_json_reinforcement_after_invalid_response(self) -> None:
        prompts, sent_messages = self.run_main_with(
            ["not json", '[{"chat_id": "chat-1", "summary": "Water leak", "sent_at": "now"}]']
        )

        self.assertEqual(len(prompts), 2)
        self.assertIn("Your last response was not valid JSON", prompts[1])
        self.assertEqual(sent_messages, ["10235 Ridge Oak, Dallas, TX — Room 2 — now — Water leak"])

    def test_main_sends_visible_fallback_when_both_responses_are_invalid(self) -> None:
        _, sent_messages = self.run_main_with(["not json", "still not json"])

        self.assertEqual(
            sent_messages,
            ["⚠️ Formatting fallback (AI response was not valid JSON):\n\nstill not json"],
        )

    def test_main_sends_no_urgent_messages_empty_state(self) -> None:
        _, sent_messages = self.run_main_with(["[]"])

        self.assertEqual(sent_messages, ["No urgent tenant messages."])

    def test_send_to_discord_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.getcode.return_value = 204
        mock_resp.__enter__.return_value = mock_resp

        with patch.object(message_summarizer.os, "getenv", return_value="https://discord.test/webhook"), \
             patch.object(message_summarizer.urllib.request, "urlopen", return_value=mock_resp) as mock_urlopen:
            send_to_discord("hello")

        self.assertTrue(mock_urlopen.called)
        sent_body = json.loads(mock_urlopen.call_args[0][0].data)
        self.assertEqual(sent_body, {"content": "hello"})

    def test_send_to_discord_missing_env(self) -> None:
        with patch.object(message_summarizer.os, "getenv", return_value=None), \
             patch.object(message_summarizer.urllib.request, "urlopen") as mock_urlopen:
            send_to_discord("hello")

        self.assertFalse(mock_urlopen.called)

    def test_send_to_discord_http_error(self) -> None:
        error = urllib.error.HTTPError("url", 400, "Bad Request", {}, None)

        with patch.object(message_summarizer.os, "getenv", return_value="https://discord.test/webhook"), \
             patch.object(message_summarizer.urllib.request, "urlopen", side_effect=error):
            send_to_discord("hello")  # should not raise

    def test_send_to_discord_truncates_long_content(self) -> None:
        long_message = "x" * (DISCORD_MESSAGE_LIMIT + 500)

        truncated = truncate_for_discord(long_message)

        self.assertEqual(len(truncated), DISCORD_MESSAGE_LIMIT)
        self.assertTrue(truncated.endswith("[truncated]"))


if __name__ == "__main__":
    unittest.main()
