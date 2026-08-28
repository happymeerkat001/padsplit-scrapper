#!/usr/bin/env python3
import unittest
from unittest.mock import MagicMock, patch

from padsplit_scraper.discord_slate_wake import (
    ASK_AI_AGENT_CHANNEL_ID,
    GENERAL_CHANNEL_ID,
    LIAISON_OPS_GUILD_ID,
    PADSPLIT_OPS_APPLICATION_ID,
    _SeenMessageIds,
    author_display_name,
    build_slate_payload,
    handle_message_create,
    load_runtime_config,
    post_slate_ask,
    should_notify_slate,
    slate_webhook_headers,
)

BOT_ID = PADSPLIT_OPS_APPLICATION_ID
OPS_CHANNEL_ID = "999000111222333444"


def _message(
    *,
    content: str,
    channel_id: str = ASK_AI_AGENT_CHANNEL_ID,
    guild_id: str = LIAISON_OPS_GUILD_ID,
    author_id: str = "111222333444555666",
    author_bot: bool = False,
    webhook_id=None,
    message_id: str = "888777666555444333",
    username: str = "ang",
    global_name: str = "Ang",
    nick: str = "",
    mentions=None,
    timestamp: str = "2026-08-28T15:40:00.000000+00:00",
) -> dict:
    payload = {
        "id": message_id,
        "channel_id": channel_id,
        "guild_id": guild_id,
        "content": content,
        "timestamp": timestamp,
        "author": {
            "id": author_id,
            "username": username,
            "global_name": global_name,
            "bot": author_bot,
        },
        "member": {"nick": nick} if nick else {},
        "mentions": mentions if mentions is not None else [],
    }
    if webhook_id is not None:
        payload["webhook_id"] = webhook_id
    return payload


class MentionFilterTests(unittest.TestCase):
    def test_wakes_on_explicit_mention_in_ask_ai_agent(self) -> None:
        msg = _message(content=f"hey <@{BOT_ID}> what is occupancy?")
        self.assertTrue(should_notify_slate(msg, BOT_ID))

    def test_wakes_on_nickname_mention_in_general(self) -> None:
        msg = _message(
            content=f"<@!{BOT_ID}> status",
            channel_id=GENERAL_CHANNEL_ID,
        )
        self.assertTrue(should_notify_slate(msg, BOT_ID))

    def test_ignores_mentions_in_other_channels(self) -> None:
        msg = _message(content=f"<@{BOT_ID}> wake", channel_id=OPS_CHANNEL_ID)
        self.assertFalse(should_notify_slate(msg, BOT_ID))

    def test_ignores_bot_own_messages(self) -> None:
        msg = _message(
            content=f"<@{BOT_ID}> loop",
            author_id=BOT_ID,
            author_bot=True,
        )
        self.assertFalse(should_notify_slate(msg, BOT_ID))

    def test_ignores_other_bots(self) -> None:
        msg = _message(
            content=f"<@{BOT_ID}> from another bot",
            author_id="555666777888999000",
            author_bot=True,
        )
        self.assertFalse(should_notify_slate(msg, BOT_ID))

    def test_ignores_reply_thread_without_explicit_mention(self) -> None:
        msg = _message(
            content="thanks, that worked",
            mentions=[{"id": BOT_ID, "username": "PadSplit Ops", "bot": True}],
        )
        self.assertFalse(should_notify_slate(msg, BOT_ID))

    def test_ignores_webhook_messages(self) -> None:
        msg = _message(content=f"<@{BOT_ID}> scrape done", webhook_id="123")
        self.assertFalse(should_notify_slate(msg, BOT_ID))

    def test_ignores_other_guilds(self) -> None:
        msg = _message(content=f"<@{BOT_ID}> hello", guild_id="1")
        self.assertFalse(should_notify_slate(msg, BOT_ID))


class PayloadAndAuthTests(unittest.TestCase):
    def test_payload_includes_requested_fields(self) -> None:
        msg = _message(
            content=f"<@{BOT_ID}> occupancy at Burton?",
            nick="Ang Li",
            channel_id=ASK_AI_AGENT_CHANNEL_ID,
            message_id="101010101010101010",
        )
        payload = build_slate_payload(msg, channel_name="ask-ai-agent")
        self.assertEqual(payload["author_display_name"], "Ang Li")
        self.assertEqual(payload["author_id"], "111222333444555666")
        self.assertEqual(payload["channel_id"], ASK_AI_AGENT_CHANNEL_ID)
        self.assertEqual(payload["channel_name"], "ask-ai-agent")
        self.assertEqual(payload["message_id"], "101010101010101010")
        self.assertEqual(payload["message_text"], f"<@{BOT_ID}> occupancy at Burton?")
        self.assertEqual(
            payload["jump_url"],
            (
                f"https://discord.com/channels/{LIAISON_OPS_GUILD_ID}/"
                f"{ASK_AI_AGENT_CHANNEL_ID}/101010101010101010"
            ),
        )
        self.assertEqual(payload["timestamp"], "2026-08-28T15:40:00.000000+00:00")

    def test_display_name_falls_back_to_global_then_username(self) -> None:
        msg = _message(content="x", nick="", global_name="Ang", username="angli")
        self.assertEqual(author_display_name(msg), "Ang")
        msg["author"]["global_name"] = ""
        self.assertEqual(author_display_name(msg), "angli")

    def test_auth_header_is_bearer_sender_key(self) -> None:
        headers = slate_webhook_headers("sender-key-value")
        self.assertEqual(headers["Authorization"], "Bearer sender-key-value")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_post_sends_bearer_header_and_json_body(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        payload = {"message_id": "1"}
        with patch(
            "padsplit_scraper.discord_slate_wake.requests.post",
            return_value=mock_response,
        ) as mock_post:
            post_slate_ask(payload, url="https://example.test/slate", key="k")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://example.test/slate")
        self.assertEqual(kwargs["json"], payload)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer k")


class HandleMessageCreateTests(unittest.TestCase):
    def test_posts_once_for_allowlisted_mention(self) -> None:
        poster = MagicMock()
        msg = _message(content=f"<@{BOT_ID}> hello")
        posted = handle_message_create(
            msg,
            bot_user_id=BOT_ID,
            webhook_url="https://example.test/slate",
            webhook_key="k",
            poster=poster,
        )
        self.assertTrue(posted)
        poster.assert_called_once()
        body = poster.call_args.kwargs["json"] if "json" in poster.call_args.kwargs else poster.call_args.args[0]
        self.assertEqual(poster.call_args.kwargs["url"], "https://example.test/slate")
        self.assertEqual(poster.call_args.kwargs["key"], "k")
        self.assertEqual(body["channel_id"], ASK_AI_AGENT_CHANNEL_ID)
        self.assertEqual(body["jump_url"].count(ASK_AI_AGENT_CHANNEL_ID), 1)

    def test_does_not_post_for_ops_channel(self) -> None:
        poster = MagicMock()
        msg = _message(content=f"<@{BOT_ID}> hello", channel_id=OPS_CHANNEL_ID)
        posted = handle_message_create(
            msg,
            bot_user_id=BOT_ID,
            webhook_url="https://example.test/slate",
            webhook_key="k",
            poster=poster,
        )
        self.assertFalse(posted)
        poster.assert_not_called()

    def test_dedups_same_message_id(self) -> None:
        poster = MagicMock()
        seen = _SeenMessageIds()
        msg = _message(content=f"<@{BOT_ID}> hello", message_id="42")
        first = handle_message_create(
            msg,
            bot_user_id=BOT_ID,
            webhook_url="https://example.test/slate",
            webhook_key="k",
            seen=seen,
            poster=poster,
        )
        second = handle_message_create(
            msg,
            bot_user_id=BOT_ID,
            webhook_url="https://example.test/slate",
            webhook_key="k",
            seen=seen,
            poster=poster,
        )
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(poster.call_count, 1)

    def test_does_not_auto_reply_in_discord(self) -> None:
        poster = MagicMock()
        with patch("padsplit_scraper.discord_slate_wake.requests.post") as discord_post:
            handle_message_create(
                _message(content=f"<@{BOT_ID}> hello"),
                bot_user_id=BOT_ID,
                webhook_url="https://example.test/slate",
                webhook_key="k",
                poster=poster,
            )
        discord_post.assert_not_called()
        poster.assert_called_once()


class RuntimeConfigTests(unittest.TestCase):
    def test_missing_env_names_are_reported(self) -> None:
        with patch("padsplit_scraper.discord_slate_wake.load_root_env"), patch(
            "os.getenv", return_value=""
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Missing DISCORD_BOT_TOKEN, SLATE_ASK_WEBHOOK_URL, SLATE_ASK_WEBHOOK_KEY",
            ):
                load_runtime_config()

    def test_reads_existing_bot_token_and_slate_webhook_env(self) -> None:
        values = {
            "DISCORD_BOT_TOKEN": "bot-token",
            "SLATE_ASK_WEBHOOK_URL": "https://example.test/slate",
            "SLATE_ASK_WEBHOOK_KEY": "sender-key",
        }
        with patch("padsplit_scraper.discord_slate_wake.load_root_env"), patch(
            "os.getenv", side_effect=lambda name, default=None: values.get(name, default)
        ):
            config = load_runtime_config()
        self.assertEqual(config["token"], "bot-token")
        self.assertEqual(config["url"], "https://example.test/slate")
        self.assertEqual(config["key"], "sender-key")


if __name__ == "__main__":
    unittest.main()
