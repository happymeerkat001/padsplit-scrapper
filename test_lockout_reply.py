#!/usr/bin/env python3
"""Unit tests for lockout auto-reply. Fake placeholder codes only. No live sends."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import lock_codes
from padsplit_scraper import lockout_reply
from padsplit_scraper import new_booking


NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
FAKE_FRONT = "XXXX"  # fake placeholder, not a real code
FAKE_BACK = "0000"  # fake placeholder, not a real code
FAKE_ROOM = "XXXX"  # fake placeholder, not a real code
FAKE_EXTRA = "0000"  # fake placeholder, not a real code
FAKE_LOCATION = "under the fake bench"


def fake_doc(**overrides) -> dict:
    doc = {
        "front_door": FAKE_FRONT,
        "back_door": FAKE_BACK,
        "r2": FAKE_ROOM,
        "lockbox_2": FAKE_ROOM,
        "lockbox_2_location": FAKE_LOCATION,
    }
    doc.update(overrides)
    return doc


def member_thread(
    *,
    chat_id: str = "chat-leana",
    street: str = "Leana Drive",
    room: int = 2,
    text: str = "I'm locked out and can't get in",
    created: str = "2026-09-07T14:30:00Z",
    host_texts: list[str] | None = None,
    move_out: str | None = None,
) -> dict:
    messages = [
        {
            "id": "m-lockout",
            "created": created,
            "text": text,
            "sender": {"roleId": "A_0", "firstName": "Member", "lastName": "Example"},
        }
    ]
    for index, host_text in enumerate(host_texts or []):
        messages.append(
            {
                "id": f"m-host-{index}",
                "created": created,
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
            codes_fn=lambda _slug: fake_doc(),
            sifely_fn=lambda: (FAKE_BACK, "sifely_current"),
            post_discord=fake.posts.append,
            send_enabled=True,
            dry_run=False,
        )
        defaults.update(kwargs)
        rows = lockout_reply.process_lockouts(threads, **defaults)
        state = lockout_reply.load_state(state_path)
        return rows, state


class DetectTests(unittest.TestCase):
    def test_lockout_phrases(self) -> None:
        for text in (
            "I'm locked out",
            "lockout help",
            "I can't get in",
            "cannot get into the house",
            "door code fails again",
            "the keypad won't work",
            "emergency entry please",
            "stuck outside right now",
        ):
            self.assertTrue(lockout_reply.detect_lockout(text), msg=text)

    def test_non_lockout_phrases(self) -> None:
        for text in (
            "AC is broken",
            "when is rent due",
            "thanks for the welcome",
        ):
            self.assertFalse(lockout_reply.detect_lockout(text), msg=text)


class HouseRoomCertaintyTests(unittest.TestCase):
    def test_unique_house_and_occupancy_room_is_100(self) -> None:
        thread = member_thread()
        house = lockout_reply.match_house(lockout_reply.thread_street(thread))
        room = lockout_reply.resolve_room(thread, "I'm locked out")
        certainty, _why = lockout_reply.score_certainty(house, room, "I'm locked out", codes_ready=True)
        self.assertEqual(house.slug, "leana_6623")
        self.assertEqual(room.room, "2")
        self.assertEqual(certainty, 100)

    def test_wrong_room_is_80_to_99(self) -> None:
        thread = member_thread(text="locked out of room 5")
        house = lockout_reply.match_house(lockout_reply.thread_street(thread))
        room = lockout_reply.resolve_room(thread, "locked out of room 5")
        certainty, _why = lockout_reply.score_certainty(
            house, room, "locked out of room 5", codes_ready=True
        )
        self.assertTrue(80 <= certainty <= 99)
        self.assertTrue(room.conflict)

    def test_unknown_house_is_below_80(self) -> None:
        thread = member_thread(street="Unknown Lane")
        house = lockout_reply.match_house(lockout_reply.thread_street(thread))
        room = lockout_reply.resolve_room(thread, "locked out")
        certainty, _why = lockout_reply.score_certainty(house, room, "locked out", codes_ready=True)
        self.assertLess(certainty, 80)

    def test_spanish_moss_is_not_greenhill(self) -> None:
        moss = lockout_reply.match_house("Spanish Moss Lane")
        hill = lockout_reply.match_house("Green Hill Drive")
        self.assertEqual(moss.slug, "spanish_moss")
        self.assertEqual(hill.slug, "greenhill_3406")
        self.assertNotEqual(moss.slug, hill.slug)


class CodesMappingTests(unittest.TestCase):
    def test_prefers_matching_room_lockbox_over_extra(self) -> None:
        doc = fake_doc(
            extra_lockbox_1_code=FAKE_EXTRA,
            extra_lockbox_1_location="gate fake",
        )
        entry = lockout_reply.codes_from_property_doc("leana_6623", doc, "2")
        self.assertEqual(entry.lockbox, FAKE_ROOM)
        self.assertEqual(entry.location, FAKE_LOCATION)
        self.assertFalse(entry.missing)

    def test_extra_lockbox_used_when_room_box_missing(self) -> None:
        doc = {
            "front_door": FAKE_FRONT,
            "extra_lockbox_1_code": FAKE_EXTRA,
            "extra_lockbox_1_location": "house box fake",
        }
        entry = lockout_reply.codes_from_property_doc("leana_6623", doc, "2")
        self.assertEqual(entry.lockbox, FAKE_EXTRA)
        self.assertEqual(entry.front, FAKE_FRONT)
        self.assertFalse(entry.missing)

    def test_pebbleshores_front_back_field_fills_both_doors(self) -> None:
        doc = {"front_back": FAKE_FRONT, "r3": FAKE_ROOM}
        entry = lockout_reply.codes_from_property_doc("pebbleshores_3414", doc, "3")
        self.assertEqual(entry.front, FAKE_FRONT)
        self.assertEqual(entry.back, FAKE_FRONT)
        self.assertEqual(entry.lockbox, FAKE_ROOM)
        self.assertFalse(entry.missing)

    def test_spanish_moss_never_uses_firestore_back_door(self) -> None:
        doc = {"back_door": FAKE_BACK, "front_door": FAKE_FRONT}
        entry = lockout_reply.codes_from_property_doc("spanish_moss", doc, "2")
        self.assertIn("sifely_back", entry.missing)
        self.assertEqual(entry.back, "")

    def test_spanish_moss_uses_sifely_back_only(self) -> None:
        doc = {"back_door": "SHOULD-NOT-USE", "front_door": FAKE_FRONT}
        entry = lockout_reply.codes_from_property_doc(
            "spanish_moss",
            doc,
            "2",
            sifely_back=FAKE_BACK,
        )
        self.assertEqual(entry.back, FAKE_BACK)
        self.assertNotIn("SHOULD-NOT-USE", entry.back)
        self.assertFalse(entry.missing)

    def test_missing_needed_code_does_not_format_body(self) -> None:
        entry = lockout_reply.codes_from_property_doc("leana_6623", {}, "2")
        self.assertTrue(entry.missing)
        with self.assertRaises(RuntimeError):
            lockout_reply.format_lockout_body("Leana", "2", entry)

    def test_template_uses_fake_placeholders_only(self) -> None:
        entry = lockout_reply.codes_from_property_doc("parker_4351", fake_doc(), "2")
        body = lockout_reply.format_lockout_body("Parker", "2", entry)
        self.assertIn(FAKE_FRONT, body)
        self.assertIn(FAKE_BACK, body)
        self.assertIn(FAKE_ROOM, body)
        self.assertIn(FAKE_LOCATION, body)
        self.assertIn("Sorry you’re locked out", body)
        self.assertIn("padsplit.com help", body)


class DiscordSafetyTests(unittest.TestCase):
    def test_discord_templates_have_no_digits_or_fake_codes(self) -> None:
        texts = [
            lockout_reply.discord_lockout_detected_text("Leana", needs_tap=True),
            lockout_reply.discord_lockout_detected_text("Spanish Moss", needs_tap=False),
            lockout_reply.discord_missing_codes_text("Parker"),
            lockout_reply.discord_spanish_moss_rotated_text(),
            lockout_reply.discord_sifely_unavailable_text(),
        ]
        for text in texts:
            self.assertFalse(lock_codes.has_digit_characters(text), msg=text)
            self.assertNotIn(FAKE_FRONT, text)
            self.assertNotIn(FAKE_BACK, text)
            self.assertEqual(lock_codes.assert_discord_outbound_safe(text), text)

    def test_discord_rejects_digits(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "digits"):
            lock_codes.assert_discord_outbound_safe("Lockout at Leana room 2")


class FlowTests(unittest.TestCase):
    def test_100_percent_sends_once_on_padsplit(self) -> None:
        fake = FakeSend()
        rows, state = run_process(fake, [member_thread()])
        self.assertEqual(rows[0]["action"], "sent")
        self.assertEqual(len(fake.sends), 1)
        self.assertEqual(fake.sends[0][0], "chat-leana")
        self.assertIn(FAKE_FRONT, fake.sends[0][1])
        self.assertIn("chat-leana", state["threads"])
        self.assertEqual(fake.order, ["close", "send"])

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
                codes_fn=lambda _slug: fake_doc(),
                post_discord=fake.posts.append,
                send_enabled=True,
            )
            first = lockout_reply.process_lockouts([member_thread()], **kwargs)
            second = lockout_reply.process_lockouts([member_thread()], **kwargs)
        self.assertEqual(first[0]["action"], "sent")
        self.assertEqual(second[0]["action"], "already_sent")
        self.assertEqual(len(fake.sends), 1)

    def test_wrong_room_does_not_send_and_discord_has_no_digits(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(fake, [member_thread(text="locked out of room 5")])
        self.assertEqual(rows[0]["action"], "needs_tap")
        self.assertEqual(fake.sends, [])
        self.assertTrue(rows[0]["discord"])
        self.assertFalse(lock_codes.has_digit_characters(rows[0]["discord"]))

    def test_unknown_house_asks_joe_without_codes(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(fake, [member_thread(street="Mystery House")])
        self.assertEqual(rows[0]["action"], "ask_joe")
        self.assertEqual(fake.sends, [])
        self.assertIn("Joe", rows[0]["discord"])
        self.assertFalse(lock_codes.has_digit_characters(rows[0]["discord"]))
        self.assertNotIn(FAKE_FRONT, rows[0]["discord"])

    def test_missing_codes_escalate_no_guess(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(
            fake,
            [member_thread()],
            codes_fn=lambda _slug: {},
        )
        self.assertEqual(rows[0]["action"], "missing_codes")
        self.assertEqual(fake.sends, [])
        self.assertFalse(lock_codes.has_digit_characters(rows[0]["discord"]))

    def test_leftover_drafts_hard_skip_no_send(self) -> None:
        leftover = [{"chat_id": "chat-leana", "kind": "draft"}]
        fake = FakeSend(leftover_tabs=leftover, close_fails=True)
        rows, state = run_process(fake, [member_thread()])
        self.assertEqual(rows[0]["action"], "skipped_leftover_drafts")
        self.assertEqual(fake.sends, [])
        self.assertNotIn("chat-leana", state.get("threads", {}))

    def test_unwired_leftover_store_hard_skips_send(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(fake, [member_thread()], leftover_compose_tabs=None)
        self.assertEqual(rows[0]["action"], "skipped_leftover_drafts")
        self.assertEqual(fake.sends, [])

    def test_spanish_moss_sends_sifely_back_and_digitless_discord_on_rotate(self) -> None:
        fake = FakeSend()
        thread = member_thread(chat_id="chat-moss", street="Spanish Moss", room=1)
        rows, _ = run_process(
            fake,
            [thread],
            codes_fn=lambda _slug: {"back_door": "SHOULD-NOT-USE"},
            sifely_fn=lambda: (FAKE_BACK, "sifely_rotated"),
        )
        self.assertEqual(rows[0]["action"], "sent")
        self.assertEqual(len(fake.sends), 1)
        self.assertIn(FAKE_BACK, fake.sends[0][1])
        self.assertNotIn("SHOULD-NOT-USE", fake.sends[0][1])
        self.assertEqual(rows[0]["discord"], lock_codes.discord_rotated_text())
        self.assertFalse(lock_codes.has_digit_characters(rows[0]["discord"]))

    def test_spanish_moss_api_down_without_share_does_not_use_static_back(self) -> None:
        fake = FakeSend()
        thread = member_thread(chat_id="chat-moss", street="Spanish Moss", room=1)
        rows, _ = run_process(
            fake,
            [thread],
            codes_fn=lambda _slug: {"back_door": FAKE_BACK},
            sifely_fn=lambda: ("", "missing"),
        )
        self.assertEqual(rows[0]["action"], "missing_codes")
        self.assertEqual(fake.sends, [])
        self.assertFalse(lock_codes.has_digit_characters(rows[0]["discord"]))

    def test_spanish_moss_without_lockout_does_not_call_sifely(self) -> None:
        fake = FakeSend()
        called = []
        quiet = member_thread(
            chat_id="chat-moss",
            street="Spanish Moss",
            room=1,
            text="thanks for the update",
        )
        rows, _ = run_process(
            fake,
            [quiet],
            sifely_fn=lambda: called.append("sifely") or (FAKE_BACK, "sifely_rotated"),
        )
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(called, [])
        self.assertEqual(fake.sends, [])

    def test_old_lockout_is_ignored(self) -> None:
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

    def test_enable_flag_defaults_off_and_ci_never_sends(self) -> None:
        with patch.dict("os.environ", {"CI": "true", "LOCKOUT_REPLY_ENABLE": "true"}, clear=False):
            self.assertFalse(lockout_reply.live_send_enabled())
        with patch.dict("os.environ", {"CI": "", "GITHUB_ACTIONS": "", "LOCKOUT_REPLY_ENABLE": ""}, clear=False):
            os_environ_enable = lockout_reply.live_send_enabled()
            self.assertFalse(os_environ_enable)

    def test_run_ci_is_skip(self) -> None:
        with patch.dict("os.environ", {"CI": "true"}, clear=False):
            result = lockout_reply.run(
                now=NOW,
                dry_run=False,
                host_messages=[member_thread()],
                leftover_compose_tabs=[],
            )
        self.assertEqual(result.action, "skip_ci")
        self.assertEqual(result.sent, 0)


class ObtainSifelyTests(unittest.TestCase):
    def test_current_sifely_passcode_is_used_without_firestore(self) -> None:
        lock = {"lockId": "LOCKID", "lockAlias": "Spanish Moss back", "lockName": "SM"}
        passcodes = [{"keyboardPwdId": "PWDID", "keyboardPwd": FAKE_BACK, "keyboardPwdName": "tenant"}]
        with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"), \
             patch.object(lock_codes, "list_locks", return_value=[lock]), \
             patch.object(lock_codes, "resolve_lock", return_value=lock), \
             patch.object(lock_codes, "list_passcodes", return_value=passcodes), \
             patch.object(lock_codes, "resolve_keyboard_pwd_id", return_value="PWDID"):
            code, source = lockout_reply.obtain_spanish_moss_back()
        self.assertEqual(code, FAKE_BACK)
        self.assertEqual(source, "sifely_current")

    def test_api_down_uses_inbound_share_placeholder(self) -> None:
        inbound = [
            {
                "id": "msg-share",
                "content": "Sifely share Spanish Moss back door Passcode: REDACTED",
            }
        ]
        with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"), \
             patch.object(lock_codes, "list_locks", side_effect=lock_codes.SifelyUnavailable("down")):
            code, source = lockout_reply.obtain_spanish_moss_back(inbound_messages=inbound)
        self.assertEqual(code, "REDACTED")
        self.assertEqual(source, "inbound_share")


if __name__ == "__main__":
    unittest.main()
