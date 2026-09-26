"""Offline transaction and scoped-approval regressions; all effects are injected."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import lock_codes as lc
from test_lock_codes import NOW, PLACEHOLDER, _run_live, moss_member_thread


class RetrySafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.changed, self.writes, self.sent = [], [], []
        self.locks = [{"lockId": role, "lockAlias": "Spanish Moss " + label}
                      for role, label in (("ROOM", "room 2"), ("FRONT", "front"), ("BACK", "back"))]
        self.codes = {row["lockId"]: [{"keyboardPwdId": "pwd-" + row["lockId"],
                                      "keyboardPwdName": "tenant", "keyboardPwd": PLACEHOLDER}]
                      for row in self.locks}
        self.addCleanup(patch.stopall)
        patch.object(lc, "load_environment").start()
        patch.object(lc, "sifely_api_key", return_value="sk-REDACTED").start()
        patch.dict(lc.os.environ, {"LOCK_CODES_JOE_USER_ID": "joe-fixture", "SIFELY_KEYBOARD_PWD_ID": ""}).start()

    def change(self, **kw):
        self.changed.append(kw["lock_id"])
        self.codes[kw["lock_id"]][0]["keyboardPwd"] = kw["new_code"]

    def write(self, slug, fields):
        self.writes.append((slug, set(fields)))
        return True

    def notify(self, chat_id, body):
        self.sent.append(chat_id)
        return 1

    def run_flow(self, **kwargs):
        defaults = dict(now=NOW, occupancy_rooms=[], host_messages=[], locks=self.locks,
                        passcodes_by_lock=self.codes, change_fn=self.change,
                        update_records=self.write, notify_member=self.notify,
                        post_discord=lambda text: {"id": "ask-fixture"},
                        firebase_ready=True, state_path=self.path)
        defaults.update(kwargs)
        return _run_live(**defaults)

    def pending(self, room_reset=False):
        ask = dict(event_key="move-out-fixture", house_slug="spanish_moss", house_label="Spanish Moss",
                   room="2", member="Former tenant", kind="move_out", chat_id="departed",
                   room_reset=room_reset, discord_message_id="ask-fixture")
        lc.save_state({**lc._empty_state(), "pending_ang_asks": [ask]}, self.path)

    def reply(self, author, content, ask="ask-fixture"):
        return dict(author={"id": author}, content=content, message_reference={"message_id": ask})

    def state(self):
        return lc.load_state(self.path)

    def test_failed_firebase_retries_without_repeating_successful_lock_change(self):
        member = moss_member_thread(phone="5550101212")
        self.run_flow(host_messages=[member], update_records=lambda *args: False)
        self.assertEqual(self.changed, ["ROOM"])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state()["processed_move_ins"], [])
        self.run_flow(host_messages=[member])
        self.assertEqual(self.changed, ["ROOM"])
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.sent, [member["id"]])
        self.assertEqual(len(self.state()["processed_move_ins"]), 1)

    def test_failed_notification_retries_without_repeating_lock_or_firebase(self):
        member = moss_member_thread(phone="5550101212")
        self.run_flow(host_messages=[member], notify_member=lambda *args: 0)
        self.assertEqual(self.state()["processed_move_ins"], [])
        self.run_flow(host_messages=[member])
        self.assertEqual(self.changed, ["ROOM"])
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.sent, [member["id"]])
        self.assertNotIn("1212", self.path.read_text())

    def test_uncertain_lock_failure_is_pending_and_never_blindly_repeated(self):
        member = moss_member_thread(phone="5550101212")
        def uncertain(**kwargs):
            self.changed.append(kwargs["lock_id"])
            raise lc.SifelyUnavailable("timeout")
        self.run_flow(host_messages=[member], change_fn=uncertain)
        self.run_flow(host_messages=[member])
        self.assertEqual(self.changed, ["ROOM"])
        self.assertEqual(self.writes, [])
        self.assertEqual(self.state()["processed_move_ins"], [])
        # A subsequent successful readback resolves ambiguity without another rotation.
        self.codes["ROOM"][0]["keyboardPwd"] = "1212"
        self.run_flow(host_messages=[member])
        self.assertEqual(self.changed, ["ROOM"])
        self.assertEqual(len(self.state()["processed_move_ins"]), 1)

    def test_interrupted_notification_requires_reconciliation_without_resending(self):
        member = moss_member_thread(phone="5550101212")
        self.run_flow(host_messages=[member], notify_member=lambda *args: (_ for _ in ()).throw(RuntimeError("interrupted")))
        self.run_flow(host_messages=[member])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.changed, ["ROOM"])
        self.assertEqual(self.state()["processed_move_ins"], [])

    def test_joe_scoped_room_confirmation_does_not_approve_shared_doors(self):
        self.pending()
        self.run_flow(fetch_discord=lambda: [self.reply("joe-fixture", "confirm vacant room reset"), self.reply("joe-fixture", "yes")])
        self.assertEqual(self.changed, ["ROOM"])
        self.assertTrue(self.state()["pending_ang_asks"][0]["room_reset"])
        self.assertNotIn("door_decision", self.state()["pending_ang_asks"][0])

    def test_don_generic_yes_wrong_event_and_unknown_author_never_reset_room(self):
        self.pending()
        self.run_flow(fetch_discord=lambda: [
            self.reply("don-fixture", "confirm vacant room reset"),
            self.reply("joe-fixture", "yes"),
            self.reply("owner-fixture", "confirm vacant room reset", "unrelated-ask"),
            self.reply("unknown", "confirm vacant room reset"),
            self.reply("owner-fixture", "yes"),
        ])
        self.assertEqual(self.changed, [])
        self.assertFalse(self.state()["pending_ang_asks"][0]["room_reset"])

    def test_room_reset_write_failure_keeps_pending_and_does_not_repeat_rotation(self):
        self.pending()
        replies = [self.reply("owner-fixture", "confirm vacant room reset"), self.reply("owner-fixture", "no")]
        self.run_flow(fetch_discord=lambda: replies, update_records=lambda *args: False)
        self.assertEqual(len(self.state()["pending_ang_asks"]), 1)
        self.assertEqual(self.state()["processed_events"], [])
        self.run_flow(fetch_discord=lambda: [])
        self.assertEqual(self.changed, ["ROOM"])
        self.assertEqual(self.state()["pending_ang_asks"], [])

    def test_shared_door_write_failure_and_partial_notify_preserve_completed_stages(self):
        self.pending(room_reset=True)
        members = [moss_member_thread(chat_id="staying-a", room=3, move_in="2026-08-01"),
                   moss_member_thread(chat_id="staying-b", room=4, move_in="2026-08-01")]
        replies = [self.reply("owner-fixture", "yes")]
        self.run_flow(host_messages=members, fetch_discord=lambda: replies,
                      generate_code=lambda: PLACEHOLDER, update_records=lambda *args: False)
        self.assertEqual(self.changed, ["FRONT"])
        self.assertEqual(self.state()["processed_events"], [])
        self.assertEqual(self.sent, [])
        def partial(chat_id, body):
            if chat_id == "staying-b":
                return 0
            return self.notify(chat_id, body)
        self.run_flow(host_messages=members, fetch_discord=lambda: [], notify_member=partial)
        self.assertEqual(self.sent, ["staying-a"])
        self.assertEqual(len(self.state()["pending_ang_asks"]), 1)
        self.run_flow(host_messages=members, fetch_discord=lambda: [])
        self.assertEqual(self.sent, ["staying-a", "staying-b", "staying-a", "staying-b"])
        self.assertEqual(self.changed, ["FRONT", "BACK"])
        self.assertEqual(len(self.writes), 2)
        self.assertEqual(self.state()["pending_ang_asks"], [])

    def test_missing_shared_lock_keeps_approval_pending_without_partial_rotation(self):
        self.pending(room_reset=True)
        self.run_flow(locks=[self.locks[1]], fetch_discord=lambda: [self.reply("owner-fixture", "yes")])
        self.assertEqual(self.changed, [])
        self.assertEqual(len(self.state()["pending_ang_asks"]), 1)

    def test_reoccupied_room_blocks_old_vacancy_confirmation(self):
        self.pending()
        self.run_flow(host_messages=[moss_member_thread(chat_id="new-tenant", room=2, move_in="2026-08-01")],
                      fetch_discord=lambda: [self.reply("joe-fixture", "confirm vacant room reset")])
        self.assertEqual(self.changed, [])
        self.assertFalse(self.state()["pending_ang_asks"][0]["room_reset"])

    def test_future_tenant_does_not_receive_current_shared_codes(self):
        future = moss_member_thread(move_in="2026-09-10")
        self.assertEqual(lc.house_member_threads([future], "spanish_moss", NOW), [])

    def test_admin_passcodes_and_global_id_for_different_lock_are_rejected(self):
        admin = [{"keyboardPwdId": "admin-id", "keyboardPwdName": "owner"}]
        self.assertIsNone(lc.resolve_keyboard_pwd_id(admin))
        with patch.dict(lc.os.environ, {"SIFELY_KEYBOARD_PWD_ID": "different-lock-pwd"}):
            self.assertIsNone(lc.resolve_keyboard_pwd_id(self.codes["ROOM"]))

    def test_corrupt_state_cannot_be_treated_as_fresh_install(self):
        self.path.write_text("{broken")
        with self.assertRaisesRegex(RuntimeError, "reconciliation"):
            self.run_flow()
        self.assertEqual(self.changed, [])

    def test_overlapping_run_cannot_execute_same_operation(self):
        with self.path.with_suffix(".json.lock").open("a") as handle:
            lc.fcntl.flock(handle, lc.fcntl.LOCK_EX | lc.fcntl.LOCK_NB)
            result = self.run_flow(host_messages=[moss_member_thread(phone="5550101212")])
            self.assertEqual(result.action, "busy")
        self.assertEqual(self.changed, [])

    def test_conditional_yes_cannot_authorize_doors(self):
        self.assertIsNone(lc.classify_ang_reply("yes but wait until tomorrow"))
        self.assertIsNone(lc.classify_ang_reply("yes no do not do it"))

    def test_missing_cached_phone_uses_read_only_lookup_before_rotation(self):
        member = moss_member_thread()
        with patch.object(lc, "_fetch_phone_for_thread", return_value="5550101212") as lookup:
            self.run_flow(host_messages=[member], fetch_phone=None)
        lookup.assert_called_once_with(member)
        self.assertEqual(self.changed, ["ROOM"])

    def test_successful_first_door_published_when_second_rotation_is_uncertain(self):
        self.pending(room_reset=True)
        member = moss_member_thread(chat_id="staying", room=3, move_in="2026-08-01")
        attempts = []
        def partial_change(**kwargs):
            attempts.append(kwargs["lock_id"])
            if kwargs["lock_id"] == "BACK":
                raise lc.SifelyUnavailable("timeout")
            self.change(**kwargs)
        kwargs = dict(host_messages=[member], fetch_discord=lambda: [self.reply("owner-fixture", "yes")],
                      change_fn=partial_change, generate_code=lambda: "TEST-ONLY-NEW")
        first = self.run_flow(**kwargs)
        self.assertEqual(attempts, ["FRONT", "BACK"])
        self.assertEqual(self.writes, [("spanish_moss", {"front_door"})])
        self.assertEqual(self.sent, ["staying"])
        self.assertTrue(any("another remains pending" in text for text in first.discord_posts))
        self.assertEqual(len(self.state()["pending_ang_asks"]), 1)
        self.run_flow(**kwargs)
        self.assertEqual(attempts, ["FRONT", "BACK"])
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.sent, ["staying"])
        self.codes["BACK"][0]["keyboardPwd"] = "TEST-ONLY-NEW"
        self.run_flow(**kwargs)
        self.assertEqual(attempts, ["FRONT", "BACK"])
        self.assertEqual(self.writes[-1], ("spanish_moss", {"back_door"}))
        self.assertEqual(self.sent, ["staying", "staying"])
        self.assertEqual(self.state()["pending_ang_asks"], [])

    def test_malformed_parseable_journals_fail_closed_without_mutation(self):
        cases = [
            {"operation_stages": []}, {"processed_move_ins": {}},
            {"processed_events": [None]}, {"pending_ang_asks": ["broken"]},
            {"passcode_hashes": []}, {"operation_stages": {"event": []}},
            {"operation_stages": {"event": {"locks": []}}},
            {"operation_stages": {"event": {"locks": {"lock": {"done": "true"}}}}},
            {"operation_stages": {"event": {"locks": {"lock": {"done": True}}}}},
            {"operation_stages": {"event": {"notifications": []}}},
            {"operation_stages": {"event": {"door_deliveries": {"lock": []}}}},
            {"operation_stages": {"event": {"writes": "not-a-list"}}},
        ]
        for malformed in cases:
            with self.subTest(malformed=malformed):
                lc.save_state({**lc._empty_state(), **malformed}, self.path)
                with self.assertRaisesRegex(RuntimeError, "reconciliation"):
                    self.run_flow(host_messages=[moss_member_thread(phone="5550101212")])
        self.assertEqual(self.changed, [])
        self.assertEqual(self.writes, [])
        self.assertEqual(self.sent, [])

    def test_later_tenancy_move_out_in_same_room_is_not_suppressed(self):
        previous = moss_member_thread(chat_id="previous", room=2, move_in="2026-08-01", move_out="2026-09-02")
        later = moss_member_thread(chat_id="later", room=2, move_in="2026-08-01", move_out="2026-09-02")
        previous_key = lc.move_out_event_key(previous, kind="move_out")
        later_key = lc.move_out_event_key(later, kind="move_out")
        lc.save_state({**lc._empty_state(), "processed_events": [previous_key]}, self.path)
        self.run_flow(host_messages=[previous, later])
        self.assertEqual([ask["event_key"] for ask in self.state()["pending_ang_asks"]], [later_key])
        self.assertEqual(self.changed, [])
        self.run_flow(host_messages=[previous, later])
        self.assertEqual(len(self.state()["pending_ang_asks"]), 1)


if __name__ == "__main__":
    unittest.main()
