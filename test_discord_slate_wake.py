#!/usr/bin/env python3
import unittest
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

from padsplit_scraper.discord_slate_wake import (
    AI_TASKS_TEMP_CHANNEL_ID,
    ASK_AI_AGENT_CHANNEL_ID,
    CALLBACK_DEFERRED_UPDATE_MESSAGE,
    CALLBACK_UPDATE_MESSAGE,
    GENERAL_CHANNEL_ID,
    LIAISON_OPS_GUILD_ID,
    PADSPLIT_OPS_APPLICATION_ID,
    TASK_DONE_CUSTOM_ID,
    TASK_UNDO_CUSTOM_ID,
    TODO_JOE_CHANNEL_ID,
    UNDO_WINDOW_SEC,
    _SeenMessageIds,
    author_display_name,
    build_ops_task_payload,
    build_slate_payload,
    build_task_interaction_callback,
    classify_ops_task_message,
    handle_interaction_create,
    handle_message_create,
    list_ops_tasks,
    load_runtime_config,
    post_ops_task,
    post_slate_ask,
    should_notify_slate,
    slate_webhook_headers,
    summarize_ops_task_messages,
    task_done_components,
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


NOW = datetime(2026, 8, 31, 19, 45, tzinfo=timezone.utc)
JOE_USER_ID = "1540500758116565113"
OTHER_USER_ID = "111222333444555666"


def _done_row(disabled: bool = False, custom_id: str = TASK_DONE_CUSTOM_ID, label: str = "Done"):
    return {
        "type": 1,
        "components": [
            {
                "type": 2,
                "style": 3,
                "custom_id": custom_id,
                "label": label,
                "disabled": disabled,
            }
        ],
    }


def _task_message(
    *,
    content: str = "Fix lock at Burton",
    channel_id: str = TODO_JOE_CHANNEL_ID,
    components=None,
    message_id: str = "999888777666555444",
    timestamp: str = "2026-08-31T12:00:00.000000+00:00",
    author_id: str = BOT_ID,
) -> dict:
    return {
        "id": message_id,
        "channel_id": channel_id,
        "guild_id": LIAISON_OPS_GUILD_ID,
        "content": content,
        "timestamp": timestamp,
        "author": {"id": author_id, "username": "PadSplit Ops", "bot": True},
        "components": task_done_components() if components is None else components,
    }


def _interaction(
    *,
    custom_id: str = TASK_DONE_CUSTOM_ID,
    user_id: str = JOE_USER_ID,
    channel_id: str = TODO_JOE_CHANNEL_ID,
    message: Optional[dict] = None,
    guild_id: str = LIAISON_OPS_GUILD_ID,
    username: str = "joe",
    global_name: str = "Joe",
    nick: str = "",
    bot: bool = False,
) -> dict:
    user = {
        "id": user_id,
        "username": username,
        "global_name": global_name,
        "bot": bot,
    }
    member = {"user": user}
    if nick:
        member["nick"] = nick
    return {
        "id": "interaction-id",
        "token": "interaction-token",
        "type": 3,
        "channel_id": channel_id,
        "guild_id": guild_id,
        "data": {"custom_id": custom_id, "component_type": 2},
        "member": member,
        "message": message if message is not None else _task_message(channel_id=channel_id),
    }


class TaskButtonPayloadTests(unittest.TestCase):
    def test_todo_joe_gets_done_button(self) -> None:
        payload = build_ops_task_payload("Bring filters", TODO_JOE_CHANNEL_ID)
        self.assertEqual(payload["content"], "Bring filters")
        custom_ids = [
            item["custom_id"]
            for row in payload["components"]
            for item in row["components"]
        ]
        self.assertEqual(custom_ids, [TASK_DONE_CUSTOM_ID])
        self.assertFalse(payload["components"][0]["components"][0]["disabled"])

    def test_ai_tasks_temp_gets_done_button(self) -> None:
        payload = build_ops_task_payload("PadSplit ticket", AI_TASKS_TEMP_CHANNEL_ID)
        self.assertIn("components", payload)
        self.assertEqual(
            payload["components"][0]["components"][0]["custom_id"],
            TASK_DONE_CUSTOM_ID,
        )

    def test_other_channels_never_get_components(self) -> None:
        for channel_id in (
            ASK_AI_AGENT_CHANNEL_ID,
            GENERAL_CHANNEL_ID,
            "1540000000000000000",
        ):
            payload = build_ops_task_payload("Do not button this", channel_id)
            self.assertEqual(payload, {"content": "Do not button this"})
            self.assertNotIn("components", payload)

    def test_post_ops_task_sends_button_body_without_live_discord(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "1"}
        mock_response.raise_for_status.return_value = None
        poster = MagicMock(return_value=mock_response)
        post_ops_task("Bring filters", TODO_JOE_CHANNEL_ID, token="bot-token", poster=poster)
        poster.assert_called_once()
        args, kwargs = poster.call_args
        self.assertTrue(args[0].endswith(f"/channels/{TODO_JOE_CHANNEL_ID}/messages"))
        self.assertEqual(kwargs["json"]["content"], "Bring filters")
        self.assertEqual(
            kwargs["json"]["components"][0]["components"][0]["custom_id"],
            TASK_DONE_CUSTOM_ID,
        )
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot bot-token")


class TaskButtonInteractionTests(unittest.TestCase):
    def test_member_tap_ticks_message(self) -> None:
        callback = build_task_interaction_callback(_interaction(), now=NOW)
        self.assertIsNotNone(callback)
        self.assertEqual(callback["type"], CALLBACK_UPDATE_MESSAGE)
        content = callback["data"]["content"]
        self.assertIn("~~Fix lock at Burton~~", content)
        self.assertIn("✅ Done · Joe · 2026-08-31T19:45:00Z", content)
        custom_ids = [
            item["custom_id"]
            for row in callback["data"]["components"]
            for item in row["components"]
        ]
        self.assertEqual(custom_ids, [TASK_UNDO_CUSTOM_ID])

    def test_non_joe_member_tap_ticks(self) -> None:
        original = _task_message()
        callback = build_task_interaction_callback(
            _interaction(
                user_id=OTHER_USER_ID,
                username="angli",
                global_name="Ang",
                message=original,
            ),
            now=NOW,
        )
        self.assertEqual(callback["type"], CALLBACK_UPDATE_MESSAGE)
        content = callback["data"]["content"]
        self.assertIn("~~Fix lock at Burton~~", content)
        self.assertIn("✅ Done · Ang · 2026-08-31T19:45:00Z", content)
        self.assertEqual(
            callback["data"]["components"][0]["components"][0]["custom_id"],
            TASK_UNDO_CUSTOM_ID,
        )

    def test_bot_tap_does_not_tick(self) -> None:
        callback = build_task_interaction_callback(
            _interaction(user_id="555666777888999000", bot=True, username="other-bot"),
            now=NOW,
        )
        self.assertEqual(callback["type"], CALLBACK_DEFERRED_UPDATE_MESSAGE)

    def test_joe_undo_within_window_restores_open_button(self) -> None:
        ticked = _task_message(
            content="~~Fix lock at Burton~~\n\n✅ Done · Joe · 2026-08-31T19:44:00Z",
            components=[
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 2,
                            "custom_id": TASK_UNDO_CUSTOM_ID,
                            "label": "Undo",
                            "disabled": False,
                        }
                    ],
                }
            ],
        )
        callback = build_task_interaction_callback(
            _interaction(custom_id=TASK_UNDO_CUSTOM_ID, message=ticked),
            now=NOW,
        )
        self.assertEqual(callback["type"], CALLBACK_UPDATE_MESSAGE)
        self.assertEqual(callback["data"]["content"], "Fix lock at Burton")
        self.assertEqual(
            callback["data"]["components"][0]["components"][0]["custom_id"],
            TASK_DONE_CUSTOM_ID,
        )
        self.assertFalse(callback["data"]["components"][0]["components"][0]["disabled"])

    def test_joe_undo_after_window_leaves_ticked(self) -> None:
        done_at = NOW - timedelta(seconds=UNDO_WINDOW_SEC + 1)
        ticked = _task_message(
            content=(
                "~~Fix lock at Burton~~\n\n"
                f"✅ Done · Joe · {done_at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            ),
            components=[
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 2,
                            "custom_id": TASK_UNDO_CUSTOM_ID,
                            "label": "Undo",
                            "disabled": False,
                        }
                    ],
                }
            ],
        )
        callback = build_task_interaction_callback(
            _interaction(custom_id=TASK_UNDO_CUSTOM_ID, message=ticked),
            now=NOW,
        )
        self.assertEqual(callback["type"], CALLBACK_UPDATE_MESSAGE)
        self.assertIn("✅ Done · Joe ·", callback["data"]["content"])
        button = callback["data"]["components"][0]["components"][0]
        self.assertTrue(button["disabled"])
        self.assertEqual(button["custom_id"], TASK_DONE_CUSTOM_ID)

    def test_button_in_other_channel_is_noop(self) -> None:
        callback = build_task_interaction_callback(
            _interaction(channel_id=GENERAL_CHANNEL_ID),
            now=NOW,
        )
        self.assertEqual(callback["type"], CALLBACK_DEFERRED_UPDATE_MESSAGE)

    def test_handle_interaction_acks_without_live_discord(self) -> None:
        poster = MagicMock()
        poster.return_value = MagicMock()
        handled = handle_interaction_create(
            _interaction(),
            token="bot-token",
            now=NOW,
            poster=poster,
        )
        self.assertTrue(handled)
        poster.assert_called_once()
        args, kwargs = poster.call_args
        self.assertIn("/interactions/interaction-id/interaction-token/callback", args[0])
        self.assertEqual(kwargs["json"]["type"], CALLBACK_UPDATE_MESSAGE)


class TaskBoardClassifyTests(unittest.TestCase):
    def test_classifies_open_vs_ticked_from_message_json(self) -> None:
        open_msg = _task_message(
            content="Bring filters",
            timestamp="2026-08-31T18:00:00.000000+00:00",
        )
        stale_msg = _task_message(
            content="Old leak",
            message_id="2",
            timestamp="2026-08-28T12:00:00.000000+00:00",
        )
        ticked_msg = _task_message(
            content="~~Bought soap~~\n\n✅ Done · Joe · 2026-08-31T19:00:00Z",
            message_id="3",
            components=[_done_row(disabled=True, label="✅ Done")],
            timestamp="2026-08-31T10:00:00.000000+00:00",
        )
        other_author = _task_message(
            content="Not ops",
            message_id="4",
            author_id=OTHER_USER_ID,
        )
        plain = {
            "id": "5",
            "channel_id": TODO_JOE_CHANNEL_ID,
            "content": "no button",
            "author": {"id": BOT_ID, "bot": True},
            "components": [],
            "timestamp": "2026-08-31T18:00:00.000000+00:00",
        }

        open_cls = classify_ops_task_message(open_msg, now=NOW)
        stale_cls = classify_ops_task_message(stale_msg, now=NOW)
        ticked_cls = classify_ops_task_message(ticked_msg, now=NOW)
        self.assertEqual(open_cls["status"], "open")
        self.assertFalse(open_cls["stale"])
        self.assertEqual(stale_cls["status"], "open")
        self.assertTrue(stale_cls["stale"])
        self.assertEqual(ticked_cls["status"], "ticked")
        self.assertEqual(ticked_cls["who"], "Joe")
        self.assertEqual(ticked_cls["when"], "2026-08-31T19:00:00Z")
        self.assertTrue(ticked_cls["recently_ticked"])
        self.assertIsNone(classify_ops_task_message(plain, now=NOW))

        summary = summarize_ops_task_messages(
            [open_msg, stale_msg, ticked_msg, other_author, plain],
            now=NOW,
        )
        self.assertEqual(summary["open_count"], 2)
        self.assertEqual(summary["stale_unticked_count"], 1)
        self.assertEqual(summary["recently_ticked_count"], 1)
        self.assertEqual(summary["ticked_count"], 1)

    def test_temp_channel_ticked_by_done_marker_and_undo_button(self) -> None:
        msg = _task_message(
            content="~~Ticket 12~~\n\n✅ Done · Joe · 2026-08-31T19:40:00Z",
            channel_id=AI_TASKS_TEMP_CHANNEL_ID,
            components=[
                {
                    "type": 1,
                    "components": [
                        {
                            "type": 2,
                            "style": 2,
                            "custom_id": TASK_UNDO_CUSTOM_ID,
                            "label": "Undo",
                            "disabled": False,
                        }
                    ],
                }
            ],
        )
        classified = classify_ops_task_message(msg, now=NOW)
        self.assertEqual(classified["status"], "ticked")
        self.assertEqual(classified["channel_name"], "ai-tasks-temp")
        self.assertEqual(classified["who"], "Joe")

    def test_list_ops_tasks_gets_both_boards_without_live_discord(self) -> None:
        open_msg = _task_message(content="Bring filters")
        ticked_msg = _task_message(
            content="~~Ticket 12~~\n\n✅ Done · Joe · 2026-08-31T19:40:00Z",
            channel_id=AI_TASKS_TEMP_CHANNEL_ID,
            components=[_done_row(disabled=True, label="✅ Done")],
        )
        seen = []

        def getter(url, headers=None, params=None, timeout=None):
            seen.append(url)
            response = MagicMock()
            response.raise_for_status.return_value = None
            if TODO_JOE_CHANNEL_ID in url:
                response.json.return_value = [open_msg]
            else:
                response.json.return_value = [ticked_msg]
            return response

        board = list_ops_tasks(token="bot-token", now=NOW, getter=getter)
        self.assertEqual(len(seen), 2)
        self.assertTrue(any(TODO_JOE_CHANNEL_ID in url for url in seen))
        self.assertTrue(any(AI_TASKS_TEMP_CHANNEL_ID in url for url in seen))
        self.assertEqual(board["open_count"], 1)
        self.assertEqual(board["recently_ticked_count"], 1)
        self.assertEqual(board["channels"][TODO_JOE_CHANNEL_ID]["open_count"], 1)
        self.assertEqual(board["channels"][AI_TASKS_TEMP_CHANNEL_ID]["ticked_count"], 1)


if __name__ == "__main__":
    unittest.main()
