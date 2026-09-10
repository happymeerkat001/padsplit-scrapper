#!/usr/bin/env python3
"""Unit tests for water-leak auto-reply. No live sends."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import leak_reply
from padsplit_scraper import lock_codes
from padsplit_scraper import new_booking


NOW = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)


def member_thread(
    *,
    chat_id: str = "chat-leana",
    street: str = "Leana Drive",
    room: int = 2,
    text: str = "there's a water leak in the kitchen",
    created: str = "2026-09-10T14:30:00Z",
    host_texts: list[str] | None = None,
    host_created: str = "2026-09-10T14:35:00Z",
    move_out: str | None = None,
    role_id: str = "A_0",
) -> dict:
    messages = [
        {
            "id": "m-leak",
            "created": created,
            "text": text,
            "sender": {"roleId": role_id, "firstName": "Member", "lastName": "Example"},
        }
    ]
    for index, host_text in enumerate(host_texts or []):
        messages.append(
            {
                "id": f"m-host-{index}",
                "created": host_created,
                "text": host_text,
                "sender": {"roleId": "A_1", "firstName": "Ang"},
            }
        )
    return {
        "id": chat_id,
        "title": "Member Example",
        "occupancy": {
            "moveInDate": "2026-08-01",
            "moveOutDate": move_out,
            "user": {"firstName": "Member", "lastName": "Example"},
            "room": {"roomNumber": room},
        },
        "property": {"address": {"street1": street}},
        "recent_messages": messages,
        "lastMessage": messages[0],
    }


class FakeSend:
    def __init__(self, leftover_tabs=None, close_fails: bool = False) -> None:
        self.tabs = list(leftover_tabs or [])
        self.sends: list[tuple[str, str]] = []
        self.order: list[str] = []
        self.close_fails = close_fails
        self.posts: list[str] = []

    def close_tabs(self, open_tabs, chat_id: str) -> list[dict]:
        self.order.append("close")
        if self.close_fails:
            raise RuntimeError("compose tab stuck")
        return new_booking.close_leftover_compose_tabs(open_tabs, chat_id)

    def send(self, chat_id: str, text: str) -> dict:
        self.order.append("send")
        if new_booking.leftover_tabs_open(self.tabs, chat_id):
            raise AssertionError("send called while leftover compose/draft tabs still open")
        self.sends.append((chat_id, text))
        return {"ok": True}


def run_process(fake: FakeSend, threads: list[dict], **kwargs):
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = Path(tmpdir) / "state.json"
        defaults = dict(
            now=NOW,
            state_path=state_path,
            leftover_compose_tabs=fake.tabs,
            close_tabs_fn=fake.close_tabs,
            send_fn=fake.send,
            post_discord=fake.posts.append,
            send_enabled=True,
            dry_run=False,
        )
        defaults.update(kwargs)
        rows = leak_reply.process_leaks(threads, **defaults)
        state = leak_reply.load_state(state_path)
        return rows, state


class DetectTests(unittest.TestCase):
    def test_current_leak_phrases(self) -> None:
        for text in (
            "leak in the kitchen sink",
            "the kitchen sink is leaking",
            "water leak under the sink",
            "water leaking from the ceiling",
            "pipe leak in the bathroom",
            "pipe is leaking",
            "there's a water leak",
            "there is a leak in the bathroom",
            "the bathroom is flooding",
            "toilet has a slow leak",
            "I found a leak behind the toilet",
        ):
            self.assertTrue(leak_reply.detect_leak(text), msg=text)

    def test_host_reminder_and_historical_do_not_fire(self) -> None:
        blast = (
            "WATER USAGE AND LEAK REMINDER\n"
            "Hi everyone! Please be reminded to use water responsibly.\n"
            "In Case of a Water Leak\n"
            "If you notice a water leak, please act quickly to help prevent flooding.\n"
            "The water shut-off box is between the water meter and the house.\n"
            "How to use a water key:\n"
            "https://youtube.com/shorts/SCryjPiyZcs?si=NfvowNCrVSnHK99w"
        )
        for text in (
            blast,
            "the previous leak is fixed",
            "the sink faucet that was leaking is fixed",
            "thanks, the last leak is already fixed",
            "no water leak here",
            "please check for any water leak",
            "gas leak smell in the garage",
            "when is rent due",
            "thanks for the welcome",
        ):
            self.assertFalse(leak_reply.detect_leak(text), msg=text)

    def test_current_leak_after_historical_clause_still_fires(self) -> None:
        text = (
            "The sink faucet that was leaking is fixed the only leaking "
            "I've found now is the toilet has a slow leak."
        )
        self.assertTrue(leak_reply.detect_leak(text))


class BodyTests(unittest.TestCase):
    def test_body_contains_quo_and_youtube(self) -> None:
        body = leak_reply.format_leak_body()
        self.assertIn(leak_reply.QUO_FIELD_PHONE, body)
        self.assertIn("+1 (469) 373-2048", body)
        self.assertIn(leak_reply.WATER_KEY_YOUTUBE_URL, body)
        self.assertIn("https://youtube.com/shorts/SCryjPiyZcs", body)
        self.assertNotIn("si=", body)
        self.assertNotIn("utm_", body)
        self.assertIn("between the water meter and the house", body)
        self.assertIn("water key", body.lower())
        self.assertNotIn("lock code", body.lower())
        self.assertNotIn("ssn", body.lower())
        self.assertNotIn("amazon", body.lower())


class DiscordTests(unittest.TestCase):
    def test_water_key_order_is_digit_free_and_round_trips(self) -> None:
        text = leak_reply.discord_water_key_order_text(
            house_label="Leana",
            street="6623 Leana Drive",
            room="2",
            chat_id="TWVzc2VuZ2VyQ2hhdFR5cGU6MTc4MzUz",
        )
        self.assertIn(leak_reply.WATER_KEY_ORDER_MARKER, text)
        self.assertIn("house=Leana", text)
        self.assertIn("addr=Leana Drive", text)
        self.assertFalse(lock_codes.has_digit_characters(text))
        lock_codes.assert_discord_outbound_safe(text)
        self.assertEqual(leak_reply.decode_digits_from_discord("[two]"), "2")
        self.assertIn("room=[two]", text)
        self.assertIn(
            leak_reply.encode_digits_for_discord("TWVzc2VuZ2VyQ2hhdFR5cGU6MTc4MzUz"),
            text,
        )
        self.assertEqual(
            leak_reply.decode_digits_from_discord(
                leak_reply.encode_digits_for_discord("TWVzc2VuZ2VyQ2hhdFR5cGU6MTc4MzUz")
            ),
            "TWVzc2VuZ2VyQ2hhdFR5cGU6MTc4MzUz",
        )


class EnableGateTests(unittest.TestCase):
    def test_enable_flag_defaults_off_and_ci_never_sends(self) -> None:
        with patch.dict("os.environ", {"CI": "true", "LEAK_REPLY_ENABLE": "true"}, clear=False):
            self.assertFalse(leak_reply.live_send_enabled())
        with patch.dict(
            "os.environ",
            {"CI": "", "GITHUB_ACTIONS": "", "LEAK_REPLY_ENABLE": ""},
            clear=False,
        ):
            self.assertFalse(leak_reply.live_send_enabled())
        clean = {
            key: value
            for key, value in os.environ.items()
            if key not in {"CI", "GITHUB_ACTIONS"}
        }
        clean["LEAK_REPLY_ENABLE"] = "1"
        with patch.dict("os.environ", clean, clear=True):
            self.assertTrue(leak_reply.live_send_enabled())

    def test_run_ci_is_skip(self) -> None:
        with patch.dict("os.environ", {"CI": "true"}, clear=False):
            result = leak_reply.run(
                now=NOW,
                dry_run=False,
                host_messages=[member_thread()],
                leftover_compose_tabs=[],
            )
        self.assertEqual(result.action, "skip_ci")
        self.assertEqual(result.sent, 0)

    def test_run_for_scraper_noops_when_disabled(self) -> None:
        with patch.dict("os.environ", {"CI": "", "GITHUB_ACTIONS": "", "LEAK_REPLY_ENABLE": ""}, clear=False):
            rows = leak_reply.run_for_scraper(object(), {"email": "x"}, [member_thread()])
        self.assertEqual(rows, [])


class FlowTests(unittest.TestCase):
    def test_100_percent_sends_and_posts_digit_free_event(self) -> None:
        fake = FakeSend()
        rows, state = run_process(fake, [member_thread()])
        self.assertEqual(rows[0]["action"], "sent")
        self.assertEqual(len(fake.sends), 1)
        body = fake.sends[0][1]
        self.assertIn(leak_reply.QUO_FIELD_PHONE, body)
        self.assertIn(leak_reply.WATER_KEY_YOUTUBE_URL, body)
        self.assertEqual(fake.order, ["close", "send"])
        self.assertEqual(len(fake.posts), 1)
        self.assertIn(leak_reply.WATER_KEY_ORDER_MARKER, fake.posts[0])
        self.assertFalse(lock_codes.has_digit_characters(fake.posts[0]))
        self.assertIn("sent_at", state["threads"]["chat-leana"])

    def test_host_message_does_not_fire(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(
            fake,
            [
                member_thread(
                    text="WATER USAGE AND LEAK REMINDER please check for leaks",
                    role_id="A_1",
                )
            ],
        )
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])

    def test_idempotent_same_thread_within_window(self) -> None:
        fake = FakeSend()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            kwargs = dict(
                now=NOW,
                state_path=state_path,
                leftover_compose_tabs=fake.tabs,
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
                post_discord=fake.posts.append,
                send_enabled=True,
            )
            first = leak_reply.process_leaks([member_thread()], **kwargs)
            second = leak_reply.process_leaks([member_thread()], **kwargs)
        self.assertEqual(first[0]["action"], "sent")
        self.assertEqual(second[0]["action"], "already_sent")
        self.assertEqual(len(fake.sends), 1)
        self.assertEqual(len(fake.posts), 1)

    def test_host_pack_already_on_thread_skips(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(
            fake,
            [member_thread(host_texts=[leak_reply.format_leak_body()])],
        )
        self.assertEqual(rows[0]["action"], "already_sent")
        self.assertEqual(fake.sends, [])

    def test_leftover_drafts_hard_skip_no_send(self) -> None:
        leftover = [{"chat_id": "chat-leana", "kind": "draft"}]
        fake = FakeSend(leftover_tabs=leftover, close_fails=True)
        rows, state = run_process(fake, [member_thread()])
        self.assertEqual(rows[0]["action"], "skipped_leftover_drafts")
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])
        self.assertNotIn("chat-leana", state.get("threads", {}))

    def test_unwired_leftover_store_hard_skips_send(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(fake, [member_thread()], leftover_compose_tabs=None)
        self.assertEqual(rows[0]["action"], "skipped_leftover_drafts")
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])

    def test_old_leak_is_ignored(self) -> None:
        fake = FakeSend()
        old = (NOW - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows, _ = run_process(fake, [member_thread(created=old)])
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(fake.sends, [])

    def test_departed_occupant_is_skipped(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(fake, [member_thread(move_out="2026-08-01")])
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(fake.sends, [])


if __name__ == "__main__":
    unittest.main()
