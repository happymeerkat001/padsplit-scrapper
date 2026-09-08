#!/usr/bin/env python3
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from padsplit_scraper.field_mms import (
    ANG_VOICE_PHONE,
    DAD_PHONE,
    DON_PHONE,
    DON_WRONG_PHONE,
    FIELD_MMS_CHAT_NAME_DEFAULT,
    GROUP_RECIPIENTS,
    JOE_PHONE,
    QUO_API_VERSION,
    QUO_FROM_NUMBER_DEFAULT,
    QUO_MESSAGES_URL,
    QuoTransportError,
    assert_group_recipients,
    build_launchd_plist,
    build_mms_body,
    contains_lock_code_like,
    digest_discord_open_tasks,
    extract_open_task_lines,
    normalize_phone,
    plan_send,
    resolve_field_mms_transport,
    resolve_quo_from_number,
    run_window,
    sanitize_sms,
    send_group_mms,
    send_via_google_voice_chrome,
    send_via_quo,
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
        self.assertEqual(first.window_id, "2026-09-01-06")
        self.assertEqual(second.action, "skip_duplicate")
        self.assertEqual(len(sent), 1)
        self.assertIn("kitchen sink leak", sent[0][0])
        self.assertEqual(sent[0][1], GROUP_RECIPIENTS)

    def test_evening_run_same_day_is_same_morning_window(self) -> None:
        sent: list[str] = []

        def host(_since: datetime) -> list[str]:
            return ["10235 Ridge Oak Rm 5 — kitchen sink leak"]

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "sent.json"
            morning = run_window(
                now=datetime(2026, 9, 1, 6, 5, tzinfo=CT),
                host_fetcher=host,
                task_fetcher=lambda _s: [],
                sender=lambda body, _r: sent.append(body),
                state_path=state,
                ci=False,
            )
            evening = run_window(
                now=datetime(2026, 9, 1, 19, 5, tzinfo=CT),
                host_fetcher=host,
                task_fetcher=lambda _s: [],
                sender=lambda body, _r: sent.append(body),
                state_path=state,
                ci=False,
            )

        self.assertEqual(morning.action, "send")
        self.assertEqual(morning.window_id, "2026-09-01-06")
        self.assertEqual(evening.action, "skip_duplicate")
        self.assertEqual(evening.window_id, "2026-09-01-06")
        self.assertEqual(len(sent), 1)

    def test_window_for_is_morning_6am_only(self) -> None:
        before = window_for(datetime(2026, 9, 1, 5, 59, tzinfo=CT))
        self.assertEqual(before.date, "2026-08-31")
        self.assertEqual(before.hour, 6)
        self.assertEqual(before.id, "2026-08-31-06")

        morning = window_for(datetime(2026, 9, 1, 6, 0, tzinfo=CT))
        self.assertEqual(morning.date, "2026-09-01")
        self.assertEqual(morning.hour, 6)
        self.assertEqual(morning.id, "2026-09-01-06")

        afternoon = window_for(datetime(2026, 9, 1, 13, 30, tzinfo=CT))
        self.assertEqual(afternoon.id, "2026-09-01-06")

        evening = window_for(datetime(2026, 9, 1, 19, 0, tzinfo=CT))
        self.assertEqual(evening.date, "2026-09-01")
        self.assertEqual(evening.hour, 6)
        self.assertEqual(evening.id, "2026-09-01-06")

    def test_discord_only_content_would_send(self) -> None:
        window = window_for(datetime(2026, 9, 1, 6, 0, tzinfo=CT))
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

    def test_launchd_plist_is_6am_every_day(self) -> None:
        payload = build_launchd_plist(Path("/Users/leon/Documents/Code/padsplit-scraper"))
        slot = payload["StartCalendarInterval"]
        self.assertEqual(slot, {"Hour": 6, "Minute": 0})
        self.assertNotIn("Weekday", slot)
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
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "quo"}):
            self.assertEqual(resolve_field_mms_transport(), "quo")
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "google_voice"}):
            self.assertEqual(resolve_field_mms_transport(), "google_voice")
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "messages"}):
            self.assertEqual(resolve_field_mms_transport(), "messages")

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    @patch("padsplit_scraper.field_mms.send_via_quo")
    def test_auto_prefers_quo_and_skips_fallbacks(self, quo, gv, messages, _allowed) -> None:
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "auto"}):
            send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        quo.assert_called_once()
        self.assertEqual(quo.call_args[0][0], "PadSplit: kitchen sink leak")
        self.assertEqual(tuple(quo.call_args[0][1]), GROUP_RECIPIENTS)
        gv.assert_not_called()
        messages.assert_not_called()

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    @patch("padsplit_scraper.field_mms.send_via_quo")
    def test_auto_tries_quo_then_google_voice_then_messages_on_challenge(
        self, quo, gv, messages, _allowed
    ) -> None:
        quo.side_effect = QuoTransportError("QUO_API_KEY missing")
        gv.side_effect = GoogleVoiceChallenge("Google Voice login or challenge wall")
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "auto"}):
            send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        quo.assert_called_once()
        gv.assert_called_once()
        self.assertEqual(gv.call_args[0][0], "PadSplit: kitchen sink leak")
        self.assertEqual(tuple(gv.call_args[0][1]), GROUP_RECIPIENTS)
        messages.assert_called_once_with("PadSplit: kitchen sink leak", FIELD_MMS_CHAT_NAME_DEFAULT)

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    @patch("padsplit_scraper.field_mms.send_via_quo")
    def test_auto_falls_back_on_transport_failure(self, quo, gv, messages, _allowed) -> None:
        quo.side_effect = QuoTransportError("Quo SMS send failed: HTTP 503")
        gv.side_effect = GoogleVoiceTransportError("Google Voice Chrome failed to launch")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FIELD_MMS_TRANSPORT", None)
            send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        quo.assert_called_once()
        gv.assert_called_once()
        messages.assert_called_once()

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    @patch("padsplit_scraper.field_mms.send_via_quo")
    def test_quo_strict_does_not_fallback(self, quo, gv, messages, _allowed) -> None:
        quo.side_effect = QuoTransportError("Quo SMS send failed: HTTP 401")
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "quo"}):
            with self.assertRaises(QuoTransportError):
                send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        quo.assert_called_once()
        gv.assert_not_called()
        messages.assert_not_called()

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    @patch("padsplit_scraper.field_mms.send_via_quo")
    def test_google_voice_strict_does_not_fallback(self, quo, gv, messages, _allowed) -> None:
        gv.side_effect = GoogleVoiceChallenge("Google Voice login or challenge wall")
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "google_voice"}):
            with self.assertRaises(GoogleVoiceChallenge):
                send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        gv.assert_called_once()
        quo.assert_not_called()
        messages.assert_not_called()

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    @patch("padsplit_scraper.field_mms.send_via_quo")
    def test_messages_transport_skips_quo_and_google_voice(self, quo, gv, messages, _allowed) -> None:
        with patch.dict(os.environ, {"FIELD_MMS_TRANSPORT": "messages"}):
            send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        quo.assert_not_called()
        gv.assert_not_called()
        messages.assert_called_once_with("PadSplit: kitchen sink leak", FIELD_MMS_CHAT_NAME_DEFAULT)

    @patch("padsplit_scraper.field_mms.sending_allowed", return_value=True)
    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    @patch("padsplit_scraper.field_mms.send_via_quo")
    def test_send_group_mms_refuses_wrong_don_before_any_send(self, quo, gv, messages, _allowed) -> None:
        with self.assertRaisesRegex(RuntimeError, "wrong Don number"):
            send_group_mms(
                "PadSplit: kitchen sink leak",
                (DAD_PHONE, JOE_PHONE, DON_WRONG_PHONE),
            )
        quo.assert_not_called()
        gv.assert_not_called()
        messages.assert_not_called()

    @patch("padsplit_scraper.field_mms.send_via_messages_chat")
    @patch("padsplit_scraper.field_mms.send_via_google_voice_chrome")
    @patch("padsplit_scraper.field_mms.send_via_quo")
    def test_send_group_mms_never_sends_when_ci_disallows(self, quo, gv, messages) -> None:
        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "must not send"):
                send_group_mms("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        quo.assert_not_called()
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


FAKE_QUO_KEY = "test-quo-key-not-real"
FAKE_QUO_FROM = "+15555550100"


class FieldMmsQuoTests(unittest.TestCase):
    def test_default_from_number_is_quo_469(self) -> None:
        with patch.dict(os.environ, {"QUO_FROM_NUMBER": "", "FIELD_MMS_QUO_FROM": ""}):
            self.assertEqual(resolve_quo_from_number(), QUO_FROM_NUMBER_DEFAULT)
            self.assertEqual(QUO_FROM_NUMBER_DEFAULT, "+14693732048")

    def test_from_number_env_aliases(self) -> None:
        with patch.dict(os.environ, {"QUO_FROM_NUMBER": FAKE_QUO_FROM, "FIELD_MMS_QUO_FROM": ""}):
            self.assertEqual(resolve_quo_from_number(), FAKE_QUO_FROM)
        with patch.dict(
            os.environ,
            {"QUO_FROM_NUMBER": "", "FIELD_MMS_QUO_FROM": "+1 (555) 555-0101"},
        ):
            self.assertEqual(resolve_quo_from_number(), "+15555550101")

    def test_send_via_quo_posts_group_sms_with_version_header(self) -> None:
        posted: list[dict] = []

        def http_post(url, *, headers, json, timeout):
            posted.append(
                {"url": url, "headers": headers, "json": json, "timeout": timeout}
            )
            return Mock(status_code=202)

        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=True):
            with patch.dict(
                os.environ,
                {"QUO_API_KEY": FAKE_QUO_KEY, "QUO_FROM_NUMBER": FAKE_QUO_FROM},
            ):
                send_via_quo(
                    "PadSplit: kitchen sink leak",
                    GROUP_RECIPIENTS,
                    http_post=http_post,
                )

        self.assertEqual(len(posted), 1)
        call = posted[0]
        self.assertEqual(call["url"], QUO_MESSAGES_URL)
        self.assertEqual(call["url"], "https://api.quo.com/v1/messages")
        self.assertEqual(call["headers"]["Authorization"], FAKE_QUO_KEY)
        self.assertFalse(str(call["headers"]["Authorization"]).lower().startswith("bearer"))
        self.assertEqual(call["headers"]["Quo-Api-Version"], QUO_API_VERSION)
        self.assertEqual(call["headers"]["Content-Type"], "application/json")
        self.assertEqual(call["json"]["content"], "PadSplit: kitchen sink leak")
        self.assertEqual(call["json"]["from"], FAKE_QUO_FROM)
        self.assertEqual(call["json"]["to"], list(GROUP_RECIPIENTS))
        self.assertLessEqual(len(call["json"]["to"]), 10)

    def test_send_via_quo_missing_key_does_not_post(self) -> None:
        posted = []

        def http_post(*_args, **_kwargs):
            posted.append(True)
            return Mock(status_code=202)

        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=True):
            with patch.dict(os.environ, {"QUO_API_KEY": ""}):
                with self.assertRaisesRegex(QuoTransportError, "QUO_API_KEY missing"):
                    send_via_quo(
                        "PadSplit: kitchen sink leak",
                        GROUP_RECIPIENTS,
                        http_post=http_post,
                    )
        self.assertEqual(posted, [])

    def test_send_via_quo_http_error_does_not_include_key_or_body(self) -> None:
        def http_post(*_args, **_kwargs):
            return Mock(status_code=401)

        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=True):
            with patch.dict(os.environ, {"QUO_API_KEY": FAKE_QUO_KEY}):
                with self.assertRaises(QuoTransportError) as raised:
                    send_via_quo(
                        "PadSplit: door code 4125",
                        GROUP_RECIPIENTS,
                        http_post=http_post,
                    )
        message = str(raised.exception)
        self.assertIn("HTTP 401", message)
        self.assertNotIn(FAKE_QUO_KEY, message)
        self.assertNotIn("4125", message)
        self.assertNotIn("PadSplit: door code", message)

    def test_send_via_quo_never_posts_when_ci_disallows(self) -> None:
        posted = []

        def http_post(*_args, **_kwargs):
            posted.append(True)
            return Mock(status_code=202)

        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=False):
            with patch.dict(os.environ, {"QUO_API_KEY": FAKE_QUO_KEY}):
                with self.assertRaisesRegex(RuntimeError, "must not send"):
                    send_via_quo(
                        "PadSplit: kitchen sink leak",
                        GROUP_RECIPIENTS,
                        http_post=http_post,
                    )
        self.assertEqual(posted, [])

    def test_send_via_quo_refuses_wrong_don_before_http(self) -> None:
        posted = []

        def http_post(*_args, **_kwargs):
            posted.append(True)
            return Mock(status_code=202)

        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=True):
            with patch.dict(os.environ, {"QUO_API_KEY": FAKE_QUO_KEY}):
                with self.assertRaisesRegex(RuntimeError, "wrong Don number"):
                    send_via_quo(
                        "PadSplit: kitchen sink leak",
                        (DAD_PHONE, JOE_PHONE, DON_WRONG_PHONE),
                        http_post=http_post,
                    )
        self.assertEqual(posted, [])

    def test_send_via_quo_uses_requests_post(self) -> None:
        response = Mock(status_code=202)
        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=True):
            with patch("padsplit_scraper.field_mms.requests.post", return_value=response) as post:
                with patch.dict(
                    os.environ,
                    {"QUO_API_KEY": FAKE_QUO_KEY, "QUO_FROM_NUMBER": FAKE_QUO_FROM},
                ):
                    send_via_quo("PadSplit: kitchen sink leak", GROUP_RECIPIENTS)
        post.assert_called_once()
        self.assertEqual(post.call_args[0][0], QUO_MESSAGES_URL)
        headers = post.call_args.kwargs["headers"]
        payload = post.call_args.kwargs["json"]
        self.assertEqual(headers["Authorization"], FAKE_QUO_KEY)
        self.assertEqual(headers["Quo-Api-Version"], QUO_API_VERSION)
        self.assertEqual(payload["from"], FAKE_QUO_FROM)
        self.assertEqual(payload["to"], list(GROUP_RECIPIENTS))

    def test_send_via_quo_request_exception_is_transport_error(self) -> None:
        import requests as requests_lib

        def http_post(*_args, **_kwargs):
            raise requests_lib.Timeout("slow")

        with patch("padsplit_scraper.field_mms.sending_allowed", return_value=True):
            with patch.dict(os.environ, {"QUO_API_KEY": FAKE_QUO_KEY}):
                with self.assertRaisesRegex(QuoTransportError, "Timeout"):
                    send_via_quo(
                        "PadSplit: kitchen sink leak",
                        GROUP_RECIPIENTS,
                        http_post=http_post,
                    )


if __name__ == "__main__":
    unittest.main()
