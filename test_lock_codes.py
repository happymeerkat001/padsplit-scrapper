#!/usr/bin/env python3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from padsplit_scraper import lock_codes


CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=CT)
PLACEHOLDER = "REDACTED"


def moss_room(*, vacant: bool, photos: int = 0, turned: bool = False, room: int = 1) -> dict:
    return {
        "property_id": "SM",
        "address": "Spanish Moss",
        "room_number": room,
        "vacant": vacant,
        "turned": turned,
        "move_out_photos": photos,
        "listed_move_out": "2026-09-01",
    }


def green_hill_room(*, vacant: bool = True, photos: int = 2) -> dict:
    return {
        "property_id": "GH",
        "address": "Green Hill Drive",
        "room_number": 3,
        "vacant": vacant,
        "turned": True,
        "move_out_photos": photos,
        "listed_move_out": "2026-09-01",
    }


def moss_member_thread(
    *,
    chat_id: str = "chat-spanish-moss",
    room: int = 2,
    move_in: str = "2026-09-02",
    move_out: str | None = None,
    phone: str = "",
    cancelled: bool = False,
    booking_status: str | None = None,
) -> dict:
    user = {"firstName": "Member", "lastName": "Example"}
    if phone:
        user["phone"] = phone
    last: dict = {
        "created": "2026-09-03T12:00:00+00:00",
        "bookingStatus": {"status": booking_status} if booking_status else None,
    }
    return {
        "id": chat_id,
        "isCancelled": cancelled,
        "occupancy": {
            "id": f"occ-{chat_id}",
            "moveInDate": move_in,
            "moveOutDate": move_out,
            "user": user,
            "room": {"roomNumber": room},
        },
        "property": {"address": {"street1": "Spanish Moss"}},
        "lastMessage": last,
    }


def _run_live(**kwargs):
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("dry_run", False)
    with patch.object(lock_codes, "running_in_ci", return_value=False), \
         patch.object(lock_codes, "live_actions_enabled", return_value=True):
        return lock_codes.run(**kwargs)


class DecideTests(unittest.TestCase):
    def test_ci_never_rotates_or_posts(self) -> None:
        plan = lock_codes.decide(
            in_ci=True,
            api_key_present=True,
            api_available=True,
            human_change=True,
            pending_vacancy=True,
            inbound_share=True,
        )
        self.assertEqual(plan.action, "skip_ci")
        self.assertFalse(plan.update_digest)
        self.assertFalse(plan.notify_padsplit)
        self.assertIsNone(plan.discord_kind)
        self.assertFalse(plan.rotate_via_api)

    def test_missing_key_is_need_you_noop(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=False,
            api_available=False,
            human_change=False,
            pending_vacancy=True,
            inbound_share=True,
        )
        self.assertEqual(plan.action, "need_you")
        self.assertEqual(plan.discord_kind, "need_you")
        self.assertFalse(plan.update_digest)
        self.assertFalse(plan.notify_padsplit)
        self.assertFalse(plan.rotate_via_api)
        self.assertIn("SIFELY_API_KEY", lock_codes.need_you_missing_key_text())
        self.assertFalse(lock_codes.has_digit_characters(lock_codes.need_you_missing_key_text()))

    def test_missing_firebase_is_need_you(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=True,
            api_available=True,
            human_change=False,
            pending_vacancy=False,
            inbound_share=False,
            firebase_ready=False,
        )
        self.assertEqual(plan.action, "need_you")
        self.assertEqual(plan.discord_kind, "need_you_firebase")
        self.assertFalse(lock_codes.has_digit_characters(lock_codes.need_you_missing_firebase_text()))

    def test_human_change_is_discord_only(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=True,
            api_available=True,
            human_change=True,
            pending_vacancy=True,
            inbound_share=False,
        )
        self.assertEqual(plan.action, "announce_human")
        self.assertEqual(plan.discord_kind, "human")
        self.assertFalse(plan.update_digest)
        self.assertFalse(plan.notify_padsplit)
        self.assertFalse(plan.rotate_via_api)
        self.assertEqual(lock_codes.discord_human_change_text(), "Spanish Moss code changed.")
        self.assertFalse(lock_codes.has_digit_characters(lock_codes.discord_human_change_text()))

    def test_vacant_plus_empty_photo_asks_ang(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=True,
            api_available=True,
            human_change=False,
            pending_vacancy=True,
            inbound_share=False,
        )
        self.assertEqual(plan.action, "ask_ang")
        self.assertFalse(plan.update_digest)
        self.assertFalse(plan.notify_padsplit)
        self.assertFalse(plan.rotate_via_api)
        self.assertEqual(plan.discord_kind, "ask_ang")
        self.assertFalse(lock_codes.has_digit_characters(lock_codes.discord_rotated_text()))

    def test_api_down_inbound_share_updates_digest_and_padsplit(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=True,
            api_available=False,
            human_change=False,
            pending_vacancy=True,
            inbound_share=True,
        )
        self.assertEqual(plan.action, "fallback_share")
        self.assertTrue(plan.update_digest)
        self.assertTrue(plan.notify_padsplit)
        self.assertTrue(plan.use_inbound_share)
        self.assertIsNone(plan.discord_kind)
        self.assertFalse(plan.rotate_via_api)

    def test_no_action_when_nothing_changed(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=True,
            api_available=True,
            human_change=False,
            pending_vacancy=False,
            inbound_share=False,
        )
        self.assertEqual(plan.action, "noop")
        self.assertFalse(plan.update_digest)
        self.assertFalse(plan.notify_padsplit)


class MemberThreadTests(unittest.TestCase):
    def test_current_members_are_spanish_moss_only(self) -> None:
        departed = moss_member_thread()
        departed["id"] = "chat-departed"
        departed["occupancy"]["moveOutDate"] = "2026-08-01"
        green = moss_member_thread()
        green["id"] = "chat-green"
        green["property"]["address"]["street1"] = "Green Hill Drive"
        current = lock_codes.current_member_threads(
            [moss_member_thread(), departed, green],
            NOW,
        )
        self.assertEqual([row["id"] for row in current], ["chat-spanish-moss"])

    def test_send_host_message_reuses_scraper_graphql(self) -> None:
        calls = []

        def request_fn(session, method, url, **kwargs):
            calls.append((method, url, kwargs["json"]["query"], kwargs["json"]["variables"]))
            class Resp:
                def raise_for_status(self) -> None:
                    return None

                def json(self):
                    return {"data": {"messenger": {"chat": {"sendMessage": {"ok": True}}}}}

            return Resp()

        sent = lock_codes.send_host_message(
            object(),
            {"email": "e", "password": "p"},
            "chat-spanish-moss",
            lock_codes.member_host_message(PLACEHOLDER),
            request_fn=request_fn,
        )
        self.assertTrue(sent["ok"])
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][1], lock_codes.GRAPHQL_URL)
        self.assertIn("sendMessage", calls[0][2])
        self.assertEqual(calls[0][3]["chatId"], "chat-spanish-moss")
        self.assertIn(PLACEHOLDER, calls[0][3]["text"])


class ScopeAndVacancyTests(unittest.TestCase):
    def test_vacant_alone_does_not_rotate(self) -> None:
        self.assertFalse(lock_codes.vacancy_allows_rotate(moss_room(vacant=True, photos=0)))

    def test_vacant_plus_photo_does_rotate(self) -> None:
        self.assertTrue(lock_codes.vacancy_allows_rotate(moss_room(vacant=True, photos=1)))
        self.assertTrue(lock_codes.vacancy_allows_rotate(moss_room(vacant=True, turned=True)))

    def test_green_hill_is_not_spanish_moss(self) -> None:
        self.assertFalse(lock_codes.vacancy_allows_rotate(green_hill_room()))
        self.assertFalse(lock_codes.is_spanish_moss_address("Green Hill Drive"))
        self.assertFalse(
            lock_codes.is_spanish_moss_back_lock(
                {"lockAlias": "Green Hill front door", "lockName": "GH"}
            )
        )
        pending = lock_codes.pending_auto_rotate_rooms(
            [green_hill_room(), moss_room(vacant=True, photos=0)],
            [],
        )
        self.assertEqual(pending, [])

    def test_already_rotated_vacancy_is_not_pending(self) -> None:
        room = moss_room(vacant=True, photos=2)
        pending = lock_codes.pending_auto_rotate_rooms([room], [lock_codes.vacancy_key(room)])
        self.assertEqual(pending, [])

    def test_spanishmoss_compact_alias_matches(self) -> None:
        self.assertTrue(lock_codes.is_spanish_moss_address("Spanishmoss"))
        self.assertTrue(lock_codes.is_spanish_moss_address("Spanish Moss"))
        self.assertEqual(lock_codes.match_house_slug("Spanishmoss back door"), "spanish_moss")
        self.assertEqual(lock_codes.match_house_slug("spanish moss room two"), "spanish_moss")


class HouseAndLockMatchTests(unittest.TestCase):
    def test_classifies_front_back_room_and_shared(self) -> None:
        front = lock_codes.classify_sifely_lock(
            {"lockId": "F", "lockAlias": "Leana front door", "lockName": "L"}
        )
        back = lock_codes.classify_sifely_lock(
            {"lockId": "B", "lockAlias": "Spanishmoss back", "lockName": "SM"}
        )
        room = lock_codes.classify_sifely_lock(
            {"lockId": "R", "lockAlias": "Spanish Moss R2", "lockName": "room"}
        )
        shared = lock_codes.classify_sifely_lock(
            {"lockId": "S", "lockAlias": "Pebbleshores front/back", "lockName": "PB"}
        )
        self.assertEqual((front.slug, front.role), ("leana_6623", "front"))
        self.assertEqual((back.slug, back.role), ("spanish_moss", "back"))
        self.assertEqual((room.slug, room.role, room.room), ("spanish_moss", "room", "2"))
        self.assertEqual((shared.slug, shared.role), ("pebbleshores_3414", "shared"))

    def test_greenhill_does_not_resolve_as_spanish_moss(self) -> None:
        green = lock_codes.classify_sifely_lock(
            {"lockId": "G", "lockAlias": "Green Hill front", "lockName": "GH"}
        )
        self.assertIsNotNone(green)
        self.assertEqual(green.slug, "greenhill_3406")
        self.assertIsNone(lock_codes.match_house_slug("Green Hill and Spanish Moss"))
        self.assertEqual(lock_codes.match_house_slug("Green Hill front"), "greenhill_3406")

    def test_find_lock_prefers_unique_room(self) -> None:
        inventory = lock_codes.inventory_locks(
            [
                {"lockId": "R1", "lockAlias": "Spanish Moss room 1", "lockName": ""},
                {"lockId": "R2", "lockAlias": "Spanish Moss room 2", "lockName": ""},
                {"lockId": "B", "lockAlias": "Spanish Moss back", "lockName": ""},
            ]
        )
        match = lock_codes.find_lock(inventory, "spanish_moss", "room", "2")
        self.assertIsNotNone(match)
        self.assertEqual(match.lock_id, "R2")
        self.assertEqual(lock_codes.codes_field_for("spanish_moss", "room", "2"), "r2")
        self.assertEqual(lock_codes.codes_field_for("pebbleshores_3414", "shared"), "front_back")


class ShareAndRedactionTests(unittest.TestCase):
    def test_parse_inbound_share_uses_placeholder_not_pin_digits(self) -> None:
        text = "Sifely share: Spanish Moss back door\nPasscode: REDACTED"
        self.assertEqual(lock_codes.parse_sifely_share_code(text), PLACEHOLDER)
        self.assertIsNone(lock_codes.parse_sifely_share_code("Sifely share Green Hill\nPasscode: REDACTED"))

    def test_redact_for_log_strips_keys_and_digit_runs(self) -> None:
        cleaned = lock_codes.redact_for_log("Authorization: sk-REDACTED body 12345678")
        self.assertNotIn("sk-REDACTED", cleaned)
        self.assertIn("SIFELY_API_KEY", cleaned)
        self.assertNotIn("12345678", cleaned)

    def test_auth_header_is_raw_key_without_bearer(self) -> None:
        headers = lock_codes.sifely_headers("sk-REDACTED")
        self.assertEqual(headers["Authorization"], "sk-REDACTED")
        self.assertNotIn("Bearer", headers["Authorization"])

    def test_discord_outbound_templates_have_no_digits(self) -> None:
        for text in (
            lock_codes.discord_human_change_text(),
            lock_codes.discord_rotated_text(),
            lock_codes.need_you_missing_key_text(),
            lock_codes.need_you_missing_firebase_text(),
            lock_codes.need_you_missing_phone_text(),
            lock_codes.need_you_missing_lock_text(),
            lock_codes.discord_move_in_text("Spanish Moss", "2", "Member Example"),
            lock_codes.discord_ask_ang_text("Spanish Moss", "2", "Member Example"),
            lock_codes.discord_ang_yes_text("Spanish Moss", "2", shared_rotated=True),
            lock_codes.discord_ang_no_text("Spanish Moss", "2"),
        ):
            self.assertFalse(lock_codes.has_digit_characters(text), msg=text)
            self.assertEqual(lock_codes.assert_discord_outbound_safe(text), text)
            self.assertNotIn(lock_codes.VACANT_ROOM_DEFAULT, text)
        ask = lock_codes.discord_ask_ang_text("Spanish Moss", "2", "Member Example")
        self.assertIn("front and back door", ask)
        self.assertNotIn("vacant", ask)
        self.assertNotIn("room lock", ask)

    def test_discord_outbound_rejects_digits(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "digits"):
            lock_codes.assert_discord_outbound_safe("Spanish Moss code REDACTED 1")

    def test_member_message_is_not_a_discord_payload(self) -> None:
        text = lock_codes.member_host_message(PLACEHOLDER)
        self.assertIn(PLACEHOLDER, text)
        self.assertIn("Spanish Moss", text)
        self.assertNotIn(lock_codes.DISCORD_NEW_TENANTS_CHANNEL_ID, text)
        move_in = lock_codes.member_move_in_message("Spanish Moss", "2", PLACEHOLDER)
        self.assertIn(PLACEHOLDER, move_in)
        self.assertIn("room 2", move_in)
        doors = lock_codes.member_shared_door_message(
            "Spanish Moss",
            {"front": PLACEHOLDER, "back": PLACEHOLDER},
        )
        self.assertIn(PLACEHOLDER, doors)
        self.assertIn("Front door", doors)
        self.assertNotIn(lock_codes.DISCORD_AUTOMATIONS_CHANNEL_ID, doors)


class PhoneAndAngReplyTests(unittest.TestCase):
    def test_phone_last4_strips_country_code_without_logging(self) -> None:
        # In-memory fixture only. The value is not a live tenant PIN.
        self.assertEqual(lock_codes.phone_last4("+1 (555) 010-1212"), "1212")
        self.assertEqual(lock_codes.phone_last4("5550101212"), "1212")
        self.assertIsNone(lock_codes.phone_last4("12"))
        self.assertIsNone(lock_codes.phone_last4(""))

    def test_ang_reply_yes_no_ignores_digits(self) -> None:
        self.assertEqual(lock_codes.classify_ang_reply("yes"), "yes")
        self.assertEqual(lock_codes.classify_ang_reply("No"), "no")
        self.assertIsNone(lock_codes.classify_ang_reply("yes 0417"))
        self.assertIsNone(lock_codes.classify_ang_reply("not sure"))
        messages = [
            {"id": "reply-1", "content": "yes", "message_reference": {"message_id": "ask-1"}},
        ]
        self.assertEqual(lock_codes.parse_ang_reply(messages, "ask-1"), "yes")
        self.assertIsNone(lock_codes.parse_ang_reply(messages, "ask-other"))


class HumanChangeAndHashTests(unittest.TestCase):
    def test_fingerprint_change_that_is_not_our_rotate_is_human(self) -> None:
        previous = {"PWDID": lock_codes.hash_passcode("OLDREDACTED")}
        current = {"PWDID": lock_codes.hash_passcode(PLACEHOLDER)}
        self.assertTrue(lock_codes.detect_human_change(current, previous, last_auto_rotate_hash=""))

    def test_our_rotate_hash_is_not_human(self) -> None:
        digest = lock_codes.hash_passcode(PLACEHOLDER)
        previous = {"PWDID": lock_codes.hash_passcode("OLDREDACTED")}
        current = {"PWDID": digest}
        self.assertFalse(lock_codes.detect_human_change(current, previous, last_auto_rotate_hash=digest))

    def test_passcode_list_hashes_without_keeping_plaintext_in_result_keys(self) -> None:
        hashes = lock_codes.passcode_hashes_from_list(
            [{"keyboardPwdId": "PWDID", "keyboardPwd": PLACEHOLDER}]
        )
        self.assertEqual(hashes["PWDID"], lock_codes.hash_passcode(PLACEHOLDER))
        self.assertNotIn(PLACEHOLDER, hashes)


class DefaultOffAndCiTests(unittest.TestCase):
    def test_live_actions_default_off(self) -> None:
        env = {"CI": "", "GITHUB_ACTIONS": "", "LOCK_CODES_ENABLE": ""}
        self.assertFalse(lock_codes.runtime.send_enabled("lock_codes", env))
        env_on = {
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
            "LOCK_CODES_ENABLE": "1",
        }
        self.assertTrue(lock_codes.runtime.send_enabled("lock_codes", env_on))

    def test_ci_run_is_skip_and_does_not_touch_sifely(self) -> None:
        posts: list[str] = []
        with patch.dict("os.environ", {"CI": "true", "SIFELY_API_KEY": "sk-REDACTED"}, clear=False):
            result = lock_codes.run(
                now=NOW,
                dry_run=True,
                occupancy_rooms=[moss_room(vacant=True, photos=2)],
                host_messages=[moss_member_thread()],
                post_discord=posts.append,
                update_digest=lambda code: posts.append(f"digest:{code}") or True,
                notify_members=lambda code: posts.append(f"padsplit:{code}") or 1,
            )
        self.assertEqual(result.action, "skip_ci")
        self.assertEqual(posts, [])

    def test_disabled_run_does_not_rotate(self) -> None:
        posts: list[str] = []
        with patch.object(lock_codes, "running_in_ci", return_value=False), \
             patch.object(lock_codes, "live_actions_enabled", return_value=False):
            result = lock_codes.run(
                now=NOW,
                dry_run=False,
                occupancy_rooms=[],
                host_messages=[moss_member_thread()],
                post_discord=posts.append,
            )
        self.assertEqual(result.action, "disabled")
        self.assertEqual(posts, [])


class RunFlowTests(unittest.TestCase):
    def test_missing_key_posts_need_you_and_does_not_invent_a_key(self) -> None:
        posts: list[str] = []
        env = {"SIFELY_API_KEY": ""}
        with patch.dict("os.environ", env, clear=False):
            with patch.object(lock_codes, "sifely_api_key", return_value=""):
                result = _run_live(
                    dry_run=True,
                    occupancy_rooms=[moss_room(vacant=True, photos=2)],
                    post_discord=posts.append,
                )
        self.assertEqual(result.action, "need_you")
        self.assertEqual(result.discord_posts, [lock_codes.need_you_missing_key_text()])
        self.assertFalse(lock_codes.has_digit_characters("".join(result.discord_posts)))

    def test_missing_firebase_fail_closed_skips_move_in_rotate(self) -> None:
        posts: list[str] = []
        changed: list[str] = []
        locks = [
            {"lockId": "ROOM2", "lockAlias": "Spanish Moss room 2", "lockName": "SM"},
        ]
        passcodes = {"ROOM2": [{"keyboardPwdId": "PWDID", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}]}
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"):
                result = _run_live(
                    occupancy_rooms=[],
                    host_messages=[moss_member_thread(phone="5550101212")],
                    locks=locks,
                    passcodes_by_lock=passcodes,
                    change_fn=lambda **k: changed.append(k["new_code"]),
                    post_discord=posts.append,
                    update_records=lambda slug, fields: False,
                    notify_member=lambda chat_id, text: 1,
                    firebase_ready=False,
                    state_path=state_path,
                )
        self.assertEqual(changed, [])
        self.assertEqual(posts, [lock_codes.need_you_missing_firebase_text()])
        self.assertFalse(any(lock_codes.has_digit_characters(item) for item in posts))
        self.assertEqual(result.padsplit_notified, 0)

    def test_move_in_sets_last4_messages_member_updates_records_digitless_discord(self) -> None:
        digest: list[tuple[str, dict]] = []
        padsplit: list[tuple[str, str]] = []
        posts: list[str] = []
        changed: list[str] = []
        locks = [
            {"lockId": "ROOM2", "lockAlias": "Spanish Moss room 2", "lockName": "SM"},
            {"lockId": "BACK", "lockAlias": "Spanish Moss back", "lockName": "SM"},
        ]
        passcodes = {
            "ROOM2": [{"keyboardPwdId": "PWDID", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"):
                result = _run_live(
                    occupancy_rooms=[],
                    host_messages=[moss_member_thread(phone="5550101212")],
                    locks=locks,
                    passcodes_by_lock=passcodes,
                    change_fn=lambda **k: changed.append("ok"),
                    post_discord=posts.append,
                    update_records=lambda slug, fields: digest.append((slug, fields)) or True,
                    notify_member=lambda chat_id, text: padsplit.append((chat_id, text)) or 1,
                    firebase_ready=True,
                    state_path=state_path,
                )
        self.assertEqual(result.action, "move_in")
        self.assertEqual(changed, ["ok"])
        self.assertEqual(digest[0][0], "spanish_moss")
        self.assertEqual(digest[0][1]["r2"], "1212")
        self.assertEqual(padsplit[0][0], "chat-spanish-moss")
        self.assertIn("1212", padsplit[0][1])
        self.assertEqual(posts, [lock_codes.discord_move_in_text("Spanish Moss", "2", "Member Example")])
        self.assertFalse(any(lock_codes.has_digit_characters(item) for item in posts))
        self.assertTrue(result.digest_updated)
        self.assertEqual(result.padsplit_notified, 1)

    def test_move_in_is_deduped_after_first_run(self) -> None:
        posts: list[str] = []
        locks = [{"lockId": "ROOM2", "lockAlias": "Spanish Moss room 2", "lockName": "SM"}]
        passcodes = {"ROOM2": [{"keyboardPwdId": "PWDID", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}]}
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"):
                first = _run_live(
                    occupancy_rooms=[],
                    host_messages=[moss_member_thread(phone="5550101212")],
                    locks=locks,
                    passcodes_by_lock=passcodes,
                    change_fn=lambda **k: None,
                    post_discord=posts.append,
                    update_records=lambda slug, fields: True,
                    notify_member=lambda chat_id, text: 1,
                    firebase_ready=True,
                    state_path=state_path,
                )
                second = _run_live(
                    occupancy_rooms=[],
                    host_messages=[moss_member_thread(phone="5550101212")],
                    locks=locks,
                    passcodes_by_lock=passcodes,
                    change_fn=lambda **k: None,
                    post_discord=posts.append,
                    update_records=lambda slug, fields: True,
                    notify_member=lambda chat_id, text: 1,
                    firebase_ready=True,
                    state_path=state_path,
                )
        self.assertEqual(first.action, "move_in")
        self.assertEqual(second.action, "noop")
        self.assertEqual(len(posts), 1)

    def test_old_move_in_is_not_backfilled(self) -> None:
        posts: list[str] = []
        with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"):
            result = _run_live(
                dry_run=True,
                occupancy_rooms=[],
                host_messages=[moss_member_thread(move_in="2026-08-01", phone="5550101212")],
                locks=[{"lockId": "ROOM2", "lockAlias": "Spanish Moss room 2", "lockName": "SM"}],
                post_discord=posts.append,
                firebase_ready=True,
            )
        self.assertEqual(result.action, "noop")
        self.assertEqual(posts, [])

    def test_move_out_resets_room_immediately_and_asks_ang_about_doors(self) -> None:
        posts: list[str] = []
        changed: list[tuple[str, str]] = []
        records: list[dict] = []
        locks = [
            {"lockId": "ROOM2", "lockAlias": "Spanish Moss room 2", "lockName": "SM"},
            {"lockId": "FRONT", "lockAlias": "Spanish Moss front", "lockName": "SM"},
            {"lockId": "BACK", "lockAlias": "Spanish Moss back", "lockName": "SM"},
        ]
        passcodes = {
            "ROOM2": [{"keyboardPwdId": "P1", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"):
                result = _run_live(
                    occupancy_rooms=[],
                    host_messages=[moss_member_thread(move_in="2026-08-01", move_out="2026-09-02")],
                    locks=locks,
                    passcodes_by_lock=passcodes,
                    change_fn=lambda **k: changed.append((k["lock_id"], k["new_code"])),
                    update_records=lambda slug, fields: records.append(fields) or True,
                    post_discord=lambda text: posts.append(text) or {"id": "ask-1"},
                    firebase_ready=True,
                    state_path=state_path,
                )
                saved = lock_codes.load_state(state_path)
        self.assertEqual(result.action, "ask_ang")
        self.assertEqual(changed, [("ROOM2", lock_codes.VACANT_ROOM_DEFAULT)])
        self.assertEqual(records[0]["r2"], lock_codes.VACANT_ROOM_DEFAULT)
        self.assertEqual(
            posts,
            [lock_codes.discord_ask_ang_text("Spanish Moss", "2", "Member Example")],
        )
        self.assertNotIn("vacant", posts[0])
        self.assertFalse(any(lock_codes.has_digit_characters(item) for item in posts))
        self.assertEqual(saved["pending_ang_asks"][0]["discord_message_id"], "ask-1")
        self.assertTrue(saved["pending_ang_asks"][0]["room_reset"])

    def test_terminated_resets_room_and_asks_ang_about_doors(self) -> None:
        posts: list[str] = []
        changed: list[tuple[str, str]] = []
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"):
                result = _run_live(
                    occupancy_rooms=[],
                    host_messages=[
                        moss_member_thread(
                            move_in="2026-08-01",
                            cancelled=True,
                            booking_status="TERMINATED",
                        )
                    ],
                    locks=[{"lockId": "ROOM2", "lockAlias": "Spanish Moss room 2", "lockName": "SM"}],
                    passcodes_by_lock={
                        "ROOM2": [{"keyboardPwdId": "P1", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}],
                    },
                    change_fn=lambda **k: changed.append((k["lock_id"], k["new_code"])),
                    update_records=lambda slug, fields: True,
                    post_discord=lambda text: posts.append(text) or {"id": "ask-term"},
                    firebase_ready=True,
                    state_path=state_path,
                )
        self.assertEqual(result.action, "ask_ang")
        self.assertEqual(changed, [("ROOM2", lock_codes.VACANT_ROOM_DEFAULT)])
        self.assertIn("terminated", posts[0])
        self.assertIn("front and back door", posts[0])
        self.assertNotIn("vacant", posts[0])
        self.assertFalse(lock_codes.has_digit_characters(posts[0]))

    def test_ang_yes_rotates_shared_doors_and_blasts_housemates(self) -> None:
        posts: list[str] = []
        changed: list[tuple[str, str]] = []
        records: list[tuple[str, dict]] = []
        padsplit: list[tuple[str, str]] = []
        locks = [
            {"lockId": "ROOM2", "lockAlias": "Spanish Moss room 2", "lockName": "SM"},
            {"lockId": "FRONT", "lockAlias": "Spanish Moss front", "lockName": "SM"},
            {"lockId": "BACK", "lockAlias": "Spanish Moss back", "lockName": "SM"},
        ]
        passcodes = {
            "FRONT": [{"keyboardPwdId": "P2", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}],
            "BACK": [{"keyboardPwdId": "P3", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}],
        }
        replies = [{"id": "r1", "content": "yes", "message_reference": {"message_id": "ask-1"}}]
        staying = moss_member_thread(chat_id="chat-staying", room=3, move_in="2026-08-01")
        departed = moss_member_thread(chat_id="chat-departed", room=2, move_in="2026-08-01", move_out="2026-09-02")
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            ask = {
                "event_key": "move_out|occ|spanish_moss|2|2026-09-02",
                "house_slug": "spanish_moss",
                "house_label": "Spanish Moss",
                "room": "2",
                "member": "Member Example",
                "kind": "move_out",
                "chat_id": "chat-departed",
                "room_reset": True,
                "discord_message_id": "ask-1",
            }
            lock_codes.save_state({**lock_codes._empty_state(), "pending_ang_asks": [ask]}, state_path)
            with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"):
                result = _run_live(
                    occupancy_rooms=[],
                    host_messages=[staying, departed],
                    locks=locks,
                    passcodes_by_lock=passcodes,
                    change_fn=lambda **k: changed.append((k["lock_id"], k["new_code"])),
                    post_discord=posts.append,
                    update_records=lambda slug, fields: records.append((slug, fields)) or True,
                    notify_member=lambda chat_id, text: padsplit.append((chat_id, text)) or 1,
                    fetch_discord=lambda: replies,
                    generate_code=lambda: PLACEHOLDER,
                    firebase_ready=True,
                    state_path=state_path,
                )
                saved = lock_codes.load_state(state_path)
        self.assertEqual(result.action, "ang_yes")
        self.assertEqual([item[0] for item in changed], ["FRONT", "BACK"])
        self.assertEqual(changed[0][1], PLACEHOLDER)
        self.assertNotIn("r2", records[0][1])
        self.assertEqual(records[0][1]["front_door"], PLACEHOLDER)
        self.assertEqual(records[0][1]["back_door"], PLACEHOLDER)
        self.assertEqual([row[0] for row in padsplit], ["chat-staying"])
        self.assertIn(PLACEHOLDER, padsplit[0][1])
        self.assertEqual(result.padsplit_notified, 1)
        self.assertEqual(posts, [lock_codes.discord_ang_yes_text("Spanish Moss", "2", shared_rotated=True)])
        self.assertFalse(any(lock_codes.has_digit_characters(item) for item in posts))
        self.assertEqual(saved["pending_ang_asks"], [])
        self.assertTrue(saved["processed_events"])

    def test_ang_no_leaves_shared_doors_alone(self) -> None:
        posts: list[str] = []
        changed: list[tuple[str, str]] = []
        records: list[dict] = []
        padsplit: list[tuple[str, str]] = []
        locks = [
            {"lockId": "ROOM2", "lockAlias": "Spanish Moss room 2", "lockName": "SM"},
            {"lockId": "FRONT", "lockAlias": "Spanish Moss front", "lockName": "SM"},
            {"lockId": "BACK", "lockAlias": "Spanish Moss back", "lockName": "SM"},
        ]
        replies = [{"id": "r1", "content": "no", "message_reference": {"message_id": "ask-1"}}]
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            ask = {
                "event_key": "move_out|occ|spanish_moss|2|2026-09-02",
                "house_slug": "spanish_moss",
                "house_label": "Spanish Moss",
                "room": "2",
                "member": "Member Example",
                "kind": "move_out",
                "chat_id": "chat-departed",
                "room_reset": True,
                "discord_message_id": "ask-1",
            }
            lock_codes.save_state({**lock_codes._empty_state(), "pending_ang_asks": [ask]}, state_path)
            with patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"):
                result = _run_live(
                    occupancy_rooms=[],
                    host_messages=[moss_member_thread(chat_id="chat-staying", room=3, move_in="2026-08-01")],
                    locks=locks,
                    change_fn=lambda **k: changed.append((k["lock_id"], k["new_code"])),
                    post_discord=posts.append,
                    update_records=lambda slug, fields: records.append(fields) or True,
                    notify_member=lambda chat_id, text: padsplit.append((chat_id, text)) or 1,
                    fetch_discord=lambda: replies,
                    generate_code=lambda: PLACEHOLDER,
                    firebase_ready=True,
                    state_path=state_path,
                )
        self.assertEqual(result.action, "ang_no")
        self.assertEqual(changed, [])
        self.assertEqual(records, [])
        self.assertEqual(padsplit, [])
        self.assertEqual(posts, [lock_codes.discord_ang_no_text("Spanish Moss", "2")])
        self.assertFalse(any(lock_codes.has_digit_characters(item) for item in posts))

    def test_fallback_share_does_not_post_digits_to_discord(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=True,
            api_available=False,
            human_change=False,
            pending_vacancy=True,
            inbound_share=True,
        )
        self.assertEqual(plan.action, "fallback_share")
        self.assertTrue(plan.use_inbound_share)
        self.assertIsNone(plan.discord_kind)

    def test_new_tenants_and_automations_are_the_discord_channel_constants(self) -> None:
        self.assertEqual(lock_codes.DISCORD_NEW_TENANTS_CHANNEL_ID, "1542260130614354055")
        self.assertEqual(lock_codes.DISCORD_AUTOMATIONS_CHANNEL_ID, "1543396445799845908")
        source = Path(lock_codes.__file__).read_text()
        self.assertNotIn("1540475874955231343", source)
        self.assertNotIn("ai-tasks-temp", source)
        self.assertNotIn("to-buy", source)
        self.assertNotIn("field_mms", source)


if __name__ == "__main__":
    unittest.main()
