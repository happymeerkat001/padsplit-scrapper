#!/usr/bin/env python3
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
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


def moss_member_thread() -> dict:
    return {
        "id": "chat-spanish-moss",
        "occupancy": {
            "moveInDate": "2026-08-01",
            "moveOutDate": None,
            "user": {"firstName": "Member", "lastName": "Example"},
            "room": {"roomNumber": 2},
        },
        "property": {"address": {"street1": "Spanish Moss"}},
    }


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

    def test_vacant_plus_empty_photo_auto_rotates(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=True,
            api_available=True,
            human_change=False,
            pending_vacancy=True,
            inbound_share=False,
        )
        self.assertEqual(plan.action, "auto_rotate")
        self.assertTrue(plan.update_digest)
        self.assertTrue(plan.notify_padsplit)
        self.assertTrue(plan.rotate_via_api)
        self.assertEqual(plan.discord_kind, "rotated")
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

    def test_already_rotated_incomplete_delivery_redistributes(self) -> None:
        plan = lock_codes.decide(
            in_ci=False,
            api_key_present=True,
            api_available=True,
            human_change=False,
            pending_vacancy=True,
            inbound_share=False,
            already_rotated=True,
        )
        self.assertEqual(plan.action, "redistribute")
        self.assertTrue(plan.update_digest)
        self.assertTrue(plan.notify_padsplit)
        self.assertTrue(plan.redistribute)
        self.assertFalse(plan.rotate_via_api)
        self.assertIsNone(plan.discord_kind)

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

    def test_green_hill_is_out_of_v1(self) -> None:
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


class ShareAndRedactionTests(unittest.TestCase):
    def test_parse_inbound_share_uses_placeholder_not_pin_digits(self) -> None:
        text = "Sifely share: Spanish Moss back door\nPasscode: REDACTED"
        self.assertEqual(lock_codes.parse_sifely_share_code(text), PLACEHOLDER)
        self.assertIsNone(lock_codes.parse_sifely_share_code("Sifely share Green Hill\nPasscode: REDACTED"))
        self.assertIsNone(lock_codes.parse_sifely_share_code("Spanish Moss code changed."))
        self.assertFalse(lock_codes.is_supported_passcode("changed"))
        self.assertTrue(lock_codes.is_supported_passcode(PLACEHOLDER))
        self.assertTrue(lock_codes.is_supported_passcode("0" * 6))
        self.assertFalse(lock_codes.is_supported_passcode("1"))

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
        ):
            self.assertFalse(lock_codes.has_digit_characters(text))
            self.assertEqual(lock_codes.assert_discord_outbound_safe(text), text)

    def test_discord_outbound_rejects_digits(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "digits"):
            lock_codes.assert_discord_outbound_safe("Spanish Moss code REDACTED 1")

    def test_member_message_is_not_a_discord_payload(self) -> None:
        text = lock_codes.member_host_message(PLACEHOLDER)
        self.assertIn(PLACEHOLDER, text)
        self.assertIn("Spanish Moss", text)
        self.assertNotIn(lock_codes.DISCORD_NEW_TENANTS_CHANNEL_ID, text)


class HumanChangeAndHashTests(unittest.TestCase):
    def test_fingerprint_change_that_is_not_our_rotate_is_human(self) -> None:
        previous = {"PWDID": lock_codes.hash_passcode("OLDREDACTED", key="REDACTED_HMAC")}
        current = {"PWDID": lock_codes.hash_passcode(PLACEHOLDER, key="REDACTED_HMAC")}
        self.assertTrue(lock_codes.detect_human_change(current, previous, last_auto_rotate_hash=""))

    def test_our_rotate_hash_is_not_human(self) -> None:
        digest = lock_codes.hash_passcode(PLACEHOLDER, key="REDACTED_HMAC")
        previous = {"PWDID": lock_codes.hash_passcode("OLDREDACTED", key="REDACTED_HMAC")}
        current = {"PWDID": digest}
        self.assertFalse(lock_codes.detect_human_change(current, previous, last_auto_rotate_hash=digest))

    def test_passcode_list_hashes_without_keeping_plaintext_in_result_keys(self) -> None:
        hashes = lock_codes.passcode_hashes_from_list(
            [{"keyboardPwdId": "PWDID", "keyboardPwd": PLACEHOLDER}],
            key="REDACTED_HMAC",
        )
        self.assertEqual(hashes["PWDID"], lock_codes.hash_passcode(PLACEHOLDER, key="REDACTED_HMAC"))
        self.assertNotIn(PLACEHOLDER, hashes)

    def test_fingerprint_is_keyed_hmac_not_raw_sha256(self) -> None:
        a = lock_codes.hash_passcode(PLACEHOLDER, key="REDACTED_HMAC")
        b = lock_codes.hash_passcode(PLACEHOLDER, key="OTHER_HMAC")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, hashlib.sha256(PLACEHOLDER.encode("utf-8")).hexdigest())


class RunFlowTests(unittest.TestCase):
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

    def test_missing_key_posts_need_you_and_does_not_invent_a_key(self) -> None:
        posts: list[str] = []
        env = {"SIFELY_API_KEY": ""}
        with patch.dict("os.environ", env, clear=False):
            with patch.object(lock_codes, "sifely_api_key", return_value=""):
                with patch.object(lock_codes, "running_in_ci", return_value=False):
                    result = lock_codes.run(
                        now=NOW,
                        dry_run=True,
                        occupancy_rooms=[moss_room(vacant=True, photos=2)],
                        post_discord=posts.append,
                    )
        self.assertEqual(result.action, "need_you")
        self.assertEqual(result.discord_posts, [lock_codes.need_you_missing_key_text()])
        self.assertFalse(lock_codes.has_digit_characters("".join(result.discord_posts)))

    def test_human_change_does_not_write_digest_or_padsplit(self) -> None:
        digest: list[str] = []
        padsplit: list[str] = []
        posts: list[str] = []
        lock = {"lockId": "LOCKID", "lockAlias": "Spanish Moss back", "lockName": "SM"}
        passcodes = [{"keyboardPwdId": "PWDID", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}]
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch.object(lock_codes, "running_in_ci", return_value=False), \
                 patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"), \
                 patch.object(lock_codes, "list_locks", return_value=[lock]), \
                 patch.object(lock_codes, "list_passcodes", return_value=passcodes), \
                 patch.object(lock_codes, "load_state", return_value={
                     "passcode_hashes": {"PWDID": lock_codes.hash_passcode("OLDREDACTED")},
                     "rotated_vacancy_keys": [],
                     "processed_discord_ids": [],
                     "last_auto_rotate_hash": "",
                     "need_you_sent_on": "",
                 }):
                result = lock_codes.run(
                    now=NOW,
                    dry_run=False,
                    occupancy_rooms=[moss_room(vacant=True, photos=2)],
                    host_messages=[moss_member_thread()],
                    post_discord=posts.append,
                    update_digest=lambda code: digest.append(code) or True,
                    notify_members=lambda code: padsplit.append(code) or 1,
                    state_path=state_path,
                )
        self.assertEqual(result.action, "announce_human")
        self.assertEqual(posts, ["Spanish Moss code changed."])
        self.assertEqual(digest, [])
        self.assertEqual(padsplit, [])

    def test_auto_rotate_updates_digest_padsplit_and_digitless_discord(self) -> None:
        digest: list[str] = []
        padsplit: list[str] = []
        posts: list[str] = []
        lock = {"lockId": "LOCKID", "lockAlias": "Spanish Moss back", "lockName": "SM"}
        passcodes = [{"keyboardPwdId": "PWDID", "keyboardPwd": "OLDREDACTED", "keyboardPwdName": "tenant"}]
        changed = []
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch.object(lock_codes, "running_in_ci", return_value=False), \
                 patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"), \
                 patch.object(lock_codes, "list_locks", return_value=[lock]), \
                 patch.object(lock_codes, "list_passcodes", return_value=passcodes), \
                 patch.object(lock_codes, "change_passcode", side_effect=lambda *a, **k: changed.append("ok")), \
                 patch.object(lock_codes, "load_state", return_value=lock_codes._empty_state()):
                result = lock_codes.run(
                    now=NOW,
                    dry_run=False,
                    occupancy_rooms=[moss_room(vacant=True, photos=2)],
                    host_messages=[moss_member_thread()],
                    post_discord=posts.append,
                    update_digest=lambda code: digest.append(code) or True,
                    notify_members=lambda code: padsplit.append(code) or 1,
                    generate_code=lambda: PLACEHOLDER,
                    state_path=state_path,
                )
        self.assertEqual(result.action, "auto_rotate")
        self.assertEqual(digest, [PLACEHOLDER])
        self.assertEqual(padsplit, [PLACEHOLDER])
        self.assertEqual(posts, ["Spanish Moss lock was rotated."])
        self.assertFalse(any(lock_codes.has_digit_characters(item) for item in posts))
        self.assertEqual(changed, ["ok"])

    def test_fallback_share_does_not_post_digits_to_discord(self) -> None:
        digest: list[str] = []
        padsplit: list[str] = []
        posts: list[str] = []
        inbound = [
            {
                "id": "msg-share",
                "content": "Sifely share Spanish Moss back door Passcode: REDACTED",
            }
        ]
        with patch.object(lock_codes, "running_in_ci", return_value=False), \
             patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"), \
             patch.object(lock_codes, "list_locks", side_effect=lock_codes.SifelyUnavailable("down")), \
             patch.object(lock_codes, "load_state", return_value=lock_codes._empty_state()):
            result = lock_codes.run(
                now=NOW,
                dry_run=True,
                occupancy_rooms=[moss_room(vacant=True, photos=2)],
                inbound_messages=inbound,
                post_discord=posts.append,
                update_digest=lambda code: digest.append(code) or True,
                notify_members=lambda code: padsplit.append(code) or 1,
            )
        self.assertEqual(result.action, "fallback_share")
        self.assertEqual(digest, [PLACEHOLDER])
        self.assertEqual(padsplit, [PLACEHOLDER])
        self.assertEqual(posts, [])

    def test_failed_digest_retries_without_second_rotate(self) -> None:
        room = moss_room(vacant=True, photos=2)
        lock = {"lockId": "LOCKID", "lockAlias": "Spanish Moss back", "lockName": "SM"}
        old_codes = [{"keyboardPwdId": "PWDID", "keyboardPwd": "OLDREDACTED", "keyboardPwdName": "tenant"}]
        new_codes = [{"keyboardPwdId": "PWDID", "keyboardPwd": PLACEHOLDER, "keyboardPwdName": "tenant"}]
        changed: list[str] = []
        digest_calls: list[str] = []

        def digest_fn(code: str) -> bool:
            digest_calls.append(code)
            return len(digest_calls) > 1

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            with patch.object(lock_codes, "running_in_ci", return_value=False), \
                 patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"), \
                 patch.object(lock_codes, "list_locks", return_value=[lock]), \
                 patch.object(lock_codes, "list_passcodes", return_value=old_codes), \
                 patch.object(lock_codes, "change_passcode", side_effect=lambda *a, **k: changed.append("ok")):
                first = lock_codes.run(
                    now=NOW,
                    dry_run=False,
                    occupancy_rooms=[room],
                    host_messages=[moss_member_thread()],
                    post_discord=lambda text: None,
                    update_digest=digest_fn,
                    notify_members=lambda code: 1,
                    generate_code=lambda: PLACEHOLDER,
                    state_path=state_path,
                )
            state = json.loads(state_path.read_text())
            self.assertEqual(first.action, "auto_rotate")
            self.assertFalse(first.digest_updated)
            self.assertNotIn(lock_codes.vacancy_key(room), state.get("rotated_vacancy_keys") or [])
            self.assertIsNotNone(state.get("pending_delivery"))

            with patch.object(lock_codes, "running_in_ci", return_value=False), \
                 patch.object(lock_codes, "sifely_api_key", return_value="sk-REDACTED"), \
                 patch.object(lock_codes, "list_locks", return_value=[lock]), \
                 patch.object(lock_codes, "list_passcodes", return_value=new_codes), \
                 patch.object(lock_codes, "change_passcode", side_effect=lambda *a, **k: changed.append("again")):
                second = lock_codes.run(
                    now=NOW,
                    dry_run=False,
                    occupancy_rooms=[room],
                    host_messages=[moss_member_thread()],
                    post_discord=lambda text: None,
                    update_digest=digest_fn,
                    notify_members=lambda code: 1,
                    generate_code=lambda: PLACEHOLDER,
                    state_path=state_path,
                )
            state = json.loads(state_path.read_text())
        self.assertEqual(second.action, "redistribute")
        self.assertTrue(second.digest_updated)
        self.assertEqual(changed, ["ok"])
        self.assertIn(lock_codes.vacancy_key(room), state.get("rotated_vacancy_keys") or [])
        self.assertIsNone(state.get("pending_delivery"))

    def test_sifely_application_error_is_unavailable(self) -> None:
        with self.assertRaises(lock_codes.SifelyUnavailable):
            lock_codes._unwrap_sifely({"errcode": 1, "errmsg": "gateway offline"})
        with self.assertRaises(lock_codes.SifelyUnavailable):
            lock_codes._unwrap_sifely({"code": 402, "data": {}})
        self.assertEqual(lock_codes._unwrap_sifely({"errcode": 0, "description": "ok"}), {"errcode": 0, "description": "ok"})

        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"errcode": 1, "errmsg": "gateway offline"}
        session.request.return_value = response
        with self.assertRaises(lock_codes.SifelyUnavailable):
            lock_codes.change_passcode(
                "sk-REDACTED",
                lock_id="LOCKID",
                keyboard_pwd_id="PWDID",
                new_code=PLACEHOLDER,
                session=session,
            )

    def test_new_tenants_is_the_only_discord_channel_constant_for_digits(self) -> None:
        self.assertEqual(lock_codes.DISCORD_NEW_TENANTS_CHANNEL_ID, "1542260130614354055")
        source = Path_read()
        self.assertNotIn("1540475874955231343", source)
        self.assertNotIn("ai-tasks-temp", source)
        self.assertNotIn("to-buy", source)
        self.assertNotIn("field_mms", source)


def Path_read() -> str:
    from pathlib import Path

    return Path(lock_codes.__file__).read_text()


if __name__ == "__main__":
    unittest.main()
