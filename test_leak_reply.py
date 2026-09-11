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
    street: str = "6623 Leana Avenue",
    room: int = 2,
    text: str = "water leaking from the ceiling",
    created: str = "2026-09-10T14:30:00Z",
    host_texts: list[str] | None = None,
    host_created: str = "2026-09-10T14:35:00Z",
    move_out: str | None = None,
    role_id: str = "A_0",
    city: str = "Dallas",
    state: str = "Texas",
    zip_code: str = "75241",
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
        "property": {
            "address": {
                "street1": street,
                "zip": zip_code,
                "city": {"name": city, "state": {"name": state}} if city else {},
            }
        },
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


def firestore_t5_doc(text: str | None = None) -> dict:
    return {
        "fields": {
            "n5": {"stringValue": "Water leak announcement"},
            "t5": {"stringValue": text or leak_reply.BAKED_T5_TEXT},
            "n6": {"stringValue": "Water leak new tenants"},
            "t6": {"stringValue": "Welcome variant — not used for auto member leak reply"},
        }
    }


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
            fetch_doc=lambda: firestore_t5_doc(),
        )
        defaults.update(kwargs)
        rows = leak_reply.process_leaks(threads, **defaults)
        state = leak_reply.load_state(state_path)
        return rows, state


class DetectTests(unittest.TestCase):
    def test_active_water_phrases_fire(self) -> None:
        for text in (
            "pipe burst in the backyard",
            "the pipe burst",
            "burst pipe by the meter",
            "water main is leaking",
            "the water main broke",
            "main water line burst",
            "the bathroom is flooding",
            "it's flooding in the hallway",
            "water leaking from the ceiling",
            "water leaking from the wall",
            "ceiling is leaking water",
            "pipe leak in the bathroom",
            "the pipe is leaking",
        ):
            self.assertTrue(leak_reply.detect_leak(text), msg=text)

    def test_low_urgency_and_broad_leak_do_not_fire(self) -> None:
        for text in (
            "leak",
            "leaking",
            "slow leak",
            "there's a slow leak under the sink",
            "drip",
            "it's dripping",
            "dripping under the sink",
            "seepage behind the washer",
            "seeping at the base",
            "toilet has a slow leak",
            "the toilet is leaking",
            "toilet clogged",
            "the toilet is clogged",
            "wax ring is leaking",
            "seep at the base of the toilet",
            "toilet base seep",
            "I found a leak behind the toilet",
            "the kitchen sink is leaking",
            "leak in the kitchen sink",
            "there's a water leak in the kitchen",
            "active water leak under the house",
            "pipe has a slow leak",
            "the pipe is dripping",
        ):
            self.assertFalse(leak_reply.detect_leak(text), msg=text)

    def test_toilet_plus_flooding_still_fires(self) -> None:
        self.assertTrue(
            leak_reply.detect_leak("the toilet overflowed and the bathroom is flooding")
        )
        self.assertTrue(
            leak_reply.detect_leak("toilet clogged and water is flooding the floor")
        )

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
            leak_reply.BAKED_T5_TEXT,
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

    def test_historical_plus_toilet_slow_leak_does_not_fire(self) -> None:
        text = (
            "The sink faucet that was leaking is fixed the only leaking "
            "I've found now is the toilet has a slow leak."
        )
        self.assertFalse(leak_reply.detect_leak(text))

    def test_negation_history_and_question_false_positives_do_not_fire(self) -> None:
        for text in (
            "There is no flooding",
            "The pipe burst last week and is fixed now",
            "Where is the water main?",
        ):
            self.assertFalse(leak_reply.detect_leak(text), msg=text)

    def test_true_emergencies_still_fire_after_fp_filters(self) -> None:
        for text in (
            "pipe burst",
            "water leaking from ceiling",
            "toilet flooding",
        ):
            self.assertTrue(leak_reply.detect_leak(text), msg=text)


class BodyTests(unittest.TestCase):
    def test_body_contains_quo_and_youtube(self) -> None:
        body = leak_reply.format_leak_body(leak_reply.BAKED_T5_TEXT)
        self.assertIn(leak_reply.QUO_FIELD_PHONE, body)
        self.assertIn("Call/text Quo:", body)
        self.assertIn("+1 (469) 373-2048", body)
        self.assertIn(leak_reply.WATER_KEY_YOUTUBE_URL, body)
        self.assertIn("https://youtube.com/shorts/SCryjPiyZcs", body)
        self.assertNotIn("si=", body)
        self.assertNotIn("utm_", body)
        self.assertIn("between the water meter and the house", body)
        self.assertIn("water key", body.lower())
        self.assertIn("drinking water", body.lower())
        self.assertNotIn("Dear All", body)
        self.assertNotIn("common closet", body.lower())
        self.assertNotIn("lock code", body.lower())
        self.assertNotIn("ssn", body.lower())
        self.assertNotIn("amazon", body.lower())
        self.assertIn(leak_reply.LEAK_PACK_MARKER, body)

    def test_live_t5_is_adapted_and_quo_injected(self) -> None:
        body = leak_reply.format_leak_body(fetch_doc=lambda: firestore_t5_doc())
        self.assertIn(leak_reply.QUO_FIELD_PHONE, body)
        self.assertIn(leak_reply.WATER_KEY_YOUTUBE_URL, body)
        self.assertNotIn("si=", body)
        self.assertNotIn("Welcome variant", body)

    def test_t6_welcome_variant_is_not_the_auto_source(self) -> None:
        source = leak_reply.pick_t5_source(
            leak_reply.load_shared_template_fields(lambda: firestore_t5_doc())
        )
        self.assertIn("Dear All", source)
        self.assertNotIn("Welcome variant", source)
        self.assertEqual(leak_reply.T6_LABEL.lower(), "water leak new tenants")

    def test_t5_fetch_failure_falls_back_to_baked(self) -> None:
        def boom() -> dict:
            raise RuntimeError("firestore down")

        body = leak_reply.format_leak_body(fetch_doc=boom)
        self.assertIn(leak_reply.QUO_FIELD_PHONE, body)
        self.assertIn(leak_reply.WATER_KEY_YOUTUBE_URL, body)
        self.assertIn(leak_reply.LEAK_PACK_MARKER, body)


class DiscordTests(unittest.TestCase):
    def test_water_key_order_requires_house_and_full_address(self) -> None:
        ship_to = "6623 Leana Avenue, Dallas, TX 75241"
        text = leak_reply.discord_water_key_order_text(
            house_label="Leana",
            ship_to=ship_to,
            room="2",
            chat_id="TWVzc2VuZ2VyQ2hhdFR5cGU6MTc4MzUz",
        )
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn(leak_reply.WATER_KEY_ORDER_MARKER, text)
        self.assertIn('house="Leana"', text)
        self.assertIn(f'ship_to="{ship_to}"', text)
        self.assertIn(f'keyword="{leak_reply.WATER_KEY_KEYWORD}"', text)
        self.assertIn("water curb key", text)
        self.assertIn(leak_reply.encode_digits_for_discord(leak_reply.WATER_KEY_ASIN), text)
        self.assertNotIn(leak_reply.WATER_KEY_ASIN, text)
        remainder = text.replace(ship_to, "", 1)
        self.assertFalse(lock_codes.has_digit_characters(remainder))
        leak_reply.assert_water_key_order_safe(text, ship_to=ship_to)
        self.assertEqual(
            leak_reply.decode_digits_from_discord(
                leak_reply.encode_digits_for_discord(leak_reply.WATER_KEY_ASIN)
            ),
            leak_reply.WATER_KEY_ASIN,
        )
        self.assertIsNone(
            leak_reply.discord_water_key_order_text(house_label="Leana", ship_to="Leana Avenue")
        )
        self.assertIsNone(
            leak_reply.discord_water_key_order_text(
                house_label="",
                ship_to=ship_to,
            )
        )

    def test_spanish_moss_is_not_an_address_override(self) -> None:
        moss = "123 Spanish Moss Lane, Dallas, TX 75241"
        self.assertIsNone(
            leak_reply.discord_water_key_order_text(
                house_label="Leana",
                ship_to=moss,
            )
        )
        allowed = leak_reply.discord_water_key_order_text(
            house_label="Spanish Moss",
            ship_to=moss,
        )
        self.assertIsNotNone(allowed)
        assert allowed is not None
        self.assertIn(moss, allowed)


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
        clean["PADSPLIT_ENABLE_ACTION_HOOKS"] = "1"
        clean.pop("PADSPLIT_COLLECTION_ONLY", None)
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
        ship_to = rows[0]["ship_to"]
        remainder = fake.posts[0].replace(ship_to, "", 1)
        self.assertFalse(lock_codes.has_digit_characters(remainder))
        self.assertIn("6623 Leana Avenue", fake.posts[0])
        self.assertIn("water curb key", fake.posts[0])
        self.assertIn("sent_at", state["threads"]["chat-leana"])

    def test_toilet_slow_leak_does_not_send(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(fake, [member_thread(text="toilet has a slow leak")])
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])

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
            kwargs["fetch_doc"] = lambda: firestore_t5_doc()
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
            [member_thread(host_texts=[leak_reply.format_leak_body(leak_reply.BAKED_T5_TEXT)])],
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
