#!/usr/bin/env python3
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from padsplit_scraper.field_mms import (
    ANG_VOICE_PHONE,
    DAD_PHONE,
    DON_PHONE,
    DON_WRONG_PHONE,
    FIELD_MMS_CHAT_NAME_DEFAULT,
    GROUP_RECIPIENTS,
    JOE_PHONE,
    assert_group_recipients,
    build_launchd_plist,
    build_mms_body,
    contains_lock_code_like,
    digest_discord_open_tasks,
    extract_open_task_lines,
    normalize_phone,
    plan_send,
    resolve_field_mms_transport,
    run_window,
    sanitize_sms,
    send_group_mms,
    send_via_google_voice_chrome,
    summarize_host_messages,
    window_for,
)
from padsplit_scraper.google_voice_chrome import (
    VOICE_MESSAGES_URL,
    GoogleVoiceChallenge,
    GoogleVoiceTransportError,
    detect_google_voice_challenge,
    format_voice_recipient,
    send_on_google_voice_page,
)


CT = ZoneInfo("America/Chicago")


def host_thread(street: str, room: int, text: str, created: str, *, role: str = "A_0") -> dict:
    return {
        "id": f"chat-{street}-{room}",
        "title": "Tenant Example",
        "occupancy": {
            "room": {"roomNumber": room},
            "user": {"firstName": "Tenant", "lastName": "Example"},
        },
        "property": {"address": {"street1": street}},
        "recent_messages": [
            {
                "created": created,
                "text": text,
                "deleted": None,
                "messageType": "TEXT",
                "sender": {
                    "roleId": role,
                    "firstName": "Tenant",
                    "lastName": "Example",
                },
            }
        ],
    }


class FieldMmsTests(unittest.TestCase):
    def test_empty_plus_empty_skips(self) -> None:
        window = window_for(datetime(2026, 9, 1, 6, 0, tzinfo=CT))
        plan = plan_send([], [], window, set())
        self.assertEqual(plan.action, "skip_empty")

    def test_one_source_sends_once_second_run_same_window_does_not(self) -> None:
        now = datetime(2026, 9, 1, 6, 5, tzinfo=CT)
        sent: list[tuple[str, tuple[str, ...]]] = []

        def host(_since: datetime) -> list[str]:
            return ["10235 Ridge Oak Rm 5 — kitchen sink leak"]

        def tasks(_since: datetime) -> list[str]:
            return []

        def sender(body: str, recipients) -> None:
            sent.append((body, tuple(recipients)))

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "sent.json"
            first = run_window(
                now=now,
                host_fetcher=host,
                task_fetcher=tasks,
                sender=sender,
                state_path=state,
                ci=False,
            )
            second = run_window(
                now=now.replace(minute=40),
                host_fetcher=host,
                task_fetcher=tasks,
                sender=sender,
                state_path=state,
                ci=False,
            )

        self.assertEqual(first.action, "send")
        self.assertEqual(second.action, "skip_duplicate")
        self.assertEqual(len(sent), 1)
        self.assertIn("kitchen sink leak", sent[0][0])
        self.assertEqual(sent[0][1], GROUP_RECIPIENTS)

    def test_discord_only_content_would_send(self) -> None:
        window = window_for(datetime(2026, 9, 1, 19, 0, tzinfo=CT))
        plan = plan_send([], ["4100 N Main St Rm 1 — [Open] smoke detector"], window, set())
        self.assertEqual(plan.action, "send")
        self.assertIn("Open tasks:", plan.body)

    def test_don_number_is_live_not_old(self) -> None:
        self.assertEqual(normalize_phone("(214) 779-8338"), DON_PHONE)
        self.assertEqual(DON_PHONE, "+12147798338")
        self.assertNotEqual(DON_PHONE, DON_WRONG_PHONE)
        self.assertNotEqual(DON_PHONE, normalize_phone("214-454-1768"))
        self.assertNotIn(DON_WRONG_PHONE, GROUP_RECIPIENTS)
        self.assertEqual(GROUP_RECIPIENTS, (DAD_PHONE, JOE_PHONE, DON_PHONE))
        self.assertEqual(assert_group_recipients(GROUP_RECIPIENTS), list(GROUP_RECIPIENTS))
        with self.assertRaisesRegex(RuntimeError, "wrong Don number"):
            assert_group_recipients((DAD_PHONE, JOE_PHONE, DON_WRONG_PHONE))
        with self.assertRaisesRegex(RuntimeError, "never solo Don|group MMS only"):
            assert_group_recipients((DON_PHONE,))

    def test_sms_body_never_contains_lock_code_like_strings(self) -> None:
        threads = [
            host_thread(
                "10235 Ridge Oak",
                5,
                "AC is out and the hallway is hot. Front door code 4125. WiFi spectrumsetup22 password cosmicloyal912.",
                "2026-09-01T11:00:00Z",
            ),
            host_thread(
                "3406 Green Hill",
                2,
                "The new lock code for the front door is 34061234. SSN 123-45-6789",
                "2026-09-01T11:10:00Z",
            ),
        ]
        since = datetime(2026, 8, 31, 12, 0, tzinfo=CT)
        host_lines = summarize_host_messages(threads, since=since)
        task_lines = extract_open_task_lines(
            "Tasks Digest (2026-09-01):\n"
            "10235 Ridge Oak:\n"
            "[Requests] (Room 5) AC out — door code 9999 and wifi pass secret\n"
        )
        body = build_mms_body(host_lines, task_lines)
        self.assertTrue(host_lines)
        self.assertIn("AC is out", sanitize_sms("AC is out and the hallway is hot. Front door code 4125"))
        self.assertFalse(contains_lock_code_like(body))
        self.assertFalse(contains_lock_code_like(sanitize_sms("Front door code 4125. Room code 121212")))
        for forbidden in ("4125", "121212", "34061234", "123-45-6789", "cosmicloyal912", "9999", "spectrumsetup22"):
            self.assertNotIn(forbidden, body)
        self.assertNotIn("214-454-1768", body)

    def test_thread_owner_is_ang_voice_and_group_is_never_one_to_one(self) -> None:
        window = window_for(datetime(2026, 9, 1, 6, 0, tzinfo=CT))
        plan = plan_send(["10235 Ridge Oak Rm 1 — AC out"], [], window, set())
        self.assertEqual(plan.thread_owner, ANG_VOICE_PHONE)
        self.assertEqual(normalize_phone("(469) 626-7260"), ANG_VOICE_PHONE)
        self.assertGreaterEqual(len(plan.recipients), 3)
        self.assertIn(DON_PHONE, plan.recipients)
        self.assertIn(DAD_PHONE, plan.recipients)
        self.assertIn(JOE_PHONE, plan.recipients)

    def test_ci_never_sends(self) -> None:
        sent: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            plan = run_window(
                now=datetime(2026, 9, 1, 6, 0, tzinfo=CT),
                host_fetcher=lambda _s: ["10235 Ridge Oak Rm 1 — AC out"],
                task_fetcher=lambda _s: [],
                sender=lambda body, _r: sent.append(body),
                state_path=Path(tmp) / "sent.json",
                ci=True,
            )
        self.assertEqual(plan.action, "skip_ci")
        self.assertEqual(sent, [])

    def test_staff_broadcast_is_not_a_host_inbox_item(self) -> None:
        thread = host_thread(
            "3541 Parker Road East",
            4,
            "HOUSE REMINDER do not flush wipes",
            "2026-09-01T11:00:00Z",
            role="A_1",
        )
        lines = summarize_host_messages([thread], since=datetime(2026, 8, 31, 12, 0, tzinfo=CT))
        self.assertEqual(lines, [])

    def test_discord_no_open_tasks_is_empty(self) -> None:
        messages = [
            {"content": "Tasks Digest (2026-09-01): ✅ No open or pending tasks.", "timestamp": "2026-09-01T11:00:00Z"}
        ]
        self.assertEqual(digest_discord_open_tasks(messages), [])

    def test_launchd_plist_is_6am_and_7pm_every_day(self) -> None:
        payload = build_launchd_plist(Path("/Users/leon/Documents/Code/padsplit-scraper"))
        slots = payload["StartCalendarInterval"]
        self.assertEqual(sorted((item["Hour"], item["Minute"]) for item in slots), [(6, 0), (19, 0)])
        for item in slots:
            self.assertNotIn("Weekday", item)
        self.assertEqual(payload["Label"], "com.padsplit.field-mms")
        self.assertTrue(str(payload["ProgramArguments"][1]).endswith("run_field_mms.sh"))


class RecordingVoicePage:
    """In-memory Google Voice page. Tests never launch Chrome or send."""

    def __init__(
        self,
        *,
        url: str = VOICE_MESSAGES_URL,
        title: str = "Messages",
        text: str = "Send a message Type a message",
        chips_after_add: int = 3,
    ) -> None:
        self.url = url
        self.title = title
        self.text = text
        self.chips_after_add = chips_after_add
        self.recipients: list[str] = []
        self.body: str | None = None
        self.sends = 0
        self.composed = False
        self.closed = False

    def current_url(self) -> str:
        return self.url

    def page_title(self) -> str:
        return self.title

    def page_text(self) -> str:
        return self.text

    def goto_messages(self) -> None:
        if "accounts.google.com" not in self.url:
            self.url = VOICE_MESSAGES_URL

    def open_compose(self) -> None:
        self.composed = True

    def add_recipient(self, phone: str) -> None:
        self.recipients.append(phone)

    def recipient_chip_count(self) -> int:
        return self.chips_after_add if self.recipients else 0

    def set_body(self, body: str) -> None:
        self.body = body

    def click_send(self) -> None:
        self.sends += 1

    def close(self) -> None:
        self.closed = True


class FieldMmsTransportTests(unittest.TestCase):
    def test_default_transport_is_auto(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FIELD_MMS_TRANSPORT", None)
            self.assertEqual(resolve_field_mms_transport(), "auto")

    def test_transport_aliases(self) -> None:
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "google_voice"}):
            self.assertEqual(resolve_field_mms_transport(), "google_voice")
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "messages"}):
            self.assertEqual(resolve_field_mms_transport(), "messages")

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    def test_auto_tries_google_voice_then_messages_on_challenge(
        self, gv, messages, _allowed
    ) -> None:
        gv.side_effect = GoogleVoiceChallenge("Google Voice login or challenge wall")
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "auto"}):
            send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        gv.assert_called_once()
        self.assertEqual(gv.call_args[0][0], "PadSplit: kitchen sink leak")
        self.assertEqual(tuple(gv.call_args[0][1]), GROUP_RECIPIENTS)
        messages.assert_called_once_with("PadSplit: kitchen sink leak", FIELD_MMS_CHAT_NAME_DEFAULT)

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    def test_auto_falls_back_on_transport_failure(self, gv, messages, _allowed) -> None:
        gv.side_effect = GoogleVoiceTransportError("Google Voice Chrome failed to launch")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FIELD_MMS_TRANSPORT", None)
            send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        gv.assert_called_once()
        messages.assert_called_once()

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    def test_google_voice_strict_does_not_fallback(self, gv, messages, _allowed) -> None:
        gv.side_effect = GoogleVoiceChallenge("Google Voice login or challenge wall")
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "google_voice"}):
            with self.assertRaises(GoogleVoiceChallenge):
                send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        gv.assert_called_once()
        messages.assert_not_called()

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    def test_messages_transport_skips_google_voice(self, gv, messages, _allowed) -> None:
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "messages"}):
            send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        gv.assert_not_called()
        messages.assert_called_once_with("PadSplit: kitchen sink leak", FIELD_MMS_CHAT_NAME_DEFAULT)

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    def test_send_group_mms_refuses_wrong_don_before_any_send(self, gv, messages, _allowed) -> None:
        with self.assertRaisesRegex(RuntimeError, "wrong Don number"):
            send_group_mms(
                "PadSplit: kitchen sink leak",
                (DAD_PHONE, JOE_PHONE, DON_WRONG_PHONE),
            )
        gv.assert_not_called()
        messages.assert_not_called()

    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    def test_send_group_mms_never_sends_when_ci_disallows(self, gv, messages) -> None:
        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "must not send"):
                send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        gv.assert_not_called()
        messages.assert_not_called()

    def test_detect_google_voice_challenge_signin_and_captcha(self) -> None:
        self.assertTrue(
            detect_google_voice_challenge(
                url="https://accounts.google.com/v3/signin/challenge",
                title="Sign in",
                visible_text="Verify it's you",
            )
        )
        self.assertTrue(
            detect_google_voice_challenge(
                url="https://voice.google.com/u/0/messages",
                title="Messages",
                visible_text="I'm not a robot recaptcha",
            )
        )
        self.assertFalse(
            detect_google_voice_challenge(
                url=VOICE_MESSAGES_URL,
                title="Messages",
                visible_text="Send a message Type a message",
            )
        )

    def test_compose_is_one_group_send_not_three_solos(self) -> None:
        page = RecordingVoicePage()
        send_on_google_voice_page(page, "PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        self.assertTrue(page.composed)
        self.assertEqual(tuple(page.recipients), GROUP_RECIPIENTS)
        self.assertEqual(page.body, "PadSplit: kitchen sink leak")
        self.assertEqual(page.sends, 1)

    def test_compose_refuses_incomplete_group(self) -> None:
        page = RecordingVoicePage(chips_after_add=1)
        with self.assertRaisesRegex(GoogleVoiceTransportError, "all three recipients"):
            send_on_google_voice_page(page, "PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        self.assertEqual(page.sends, 0)

    def test_google_voice_chrome_raises_on_signin_wall_without_sending(self) -> None:
        page = RecordingVoicePage(
            url="https://accounts.google.com/signin",
            title="Sign in",
            text="Use your Google Account",
        )
        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=True):
            with self.assertRaises(GoogleVoiceChallenge):
                send_via_google_voice_chrome(
                    "PadSplit: kitchen sink leak",
                    GROUP_RECIPIENTS,
                    voice_page=page,
                )
        self.assertEqual(page.sends, 0)

    def test_google_voice_chrome_sends_group_once_when_page_is_ready(self) -> None:
        page = RecordingVoicePage()
        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=True):
            send_via_google_voice_chrome(
                "PadSplit: kitchen sink leak",
                GROUP_RECIPIENTS,
                voice_page=page,
            )
        self.assertEqual(tuple(page.recipients), GROUP_RECIPIENTS)
        self.assertEqual(page.sends, 1)
        self.assertFalse(page.closed)

    def test_google_voice_chrome_refuses_wrong_don(self) -> None:
        page = RecordingVoicePage()
        with self.assertRaisesRegex(RuntimeError, "wrong Don number"):
            send_via_google_voice_chrome(
                "PadSplit: kitchen sink leak",
                (DAD_PHONE, JOE_PHONE, DON_WRONG_PHONE),
                voice_page=page,
            )
        self.assertEqual(page.sends, 0)

    def test_google_voice_chrome_never_sends_in_ci(self) -> None:
        page = RecordingVoicePage()
        with patch.dict(os.environ, {"CI": "true", "GITHUB_ACTIONS": "true"}):
            with self.assertRaisesRegex(RuntimeError, "must not send"):
                send_via_google_voice_chrome(
                    "PadSplit: kitchen sink leak",
                    GROUP_RECIPIENTS,
                    voice_page=page,
                )
        self.assertEqual(page.sends, 0)

    def test_format_voice_recipient_is_national(self) -> None:
        self.assertEqual(format_voice_recipient(DON_PHONE), "(214) 779-8338")
        self.assertEqual(format_voice_recipient(DAD_PHONE), "(945) 241-3070")
        self.assertEqual(format_voice_recipient(JOE_PHONE), "(469) 373-2048")


if __name__ == "__main__":
    unittest.main()
