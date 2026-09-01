#!/usr/bin/env python3
"""Unit tests for new-booking Hirevire first-host send. No live PadSplit sends."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import new_booking


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)
HIREVIRE_BODY = (
    "Hi, Thank you for considering our home. Please use the link below as a sample "
    "to make an introductory video of yourself explaining why you’re a best fit "
    "for our property. Copy link and paste on your browser and should be submitted "
    "on time for a smooth move-in.\n\n"
    "https://app.hirevire.com/applications/977d344b-8592-4fc5-bd41-38717d6fa90a?lang=EN"
)
TEMPLATE = {"label": "NEW BOOKING REQUEST", "text": HIREVIRE_BODY, "index": "0"}


def pending_thread(
    *,
    chat_id: str = "chat-1",
    booking_id: str = "booking-1",
    occupancy_id: str = "occ-1",
    host_texts: list[str] | None = None,
) -> dict:
    messages = [
        {
            "id": "m-booking",
            "text": None,
            "messageType": "BOOKING_STATUS",
            "sender": {"roleId": "A_1"},
            "bookingStatus": {"id": booking_id, "status": "PENDING", "created": "2026-09-01T12:00:00Z"},
        }
    ]
    for i, text in enumerate(host_texts or []):
        messages.append(
            {
                "id": f"m-host-{i}",
                "text": text,
                "messageType": "TEXT",
                "deleted": None,
                "sender": {"roleId": "A_1", "firstName": "Ang"},
                "bookingStatus": None,
            }
        )
    return {
        "id": chat_id,
        "title": "Applicant",
        "occupancy": {"id": occupancy_id, "room": {"roomNumber": 1}},
        "recent_messages": messages,
        "lastMessage": messages[0],
    }


class FakeSend:
    def __init__(self, leftover_tabs: list[dict] | None = None) -> None:
        self.tabs = leftover_tabs if leftover_tabs is not None else []
        self.closed: list[dict] = []
        self.sends: list[tuple[str, str]] = []
        self.order: list[str] = []

    def close_tabs(self, open_tabs, chat_id: str) -> list[dict]:
        self.order.append("close")
        closed = new_booking.close_leftover_compose_tabs(self.tabs, chat_id)
        self.closed.extend(closed)
        return closed

    def send(self, chat_id: str, text: str) -> dict:
        self.order.append("send")
        if new_booking.leftover_tabs_open(self.tabs, chat_id):
            raise AssertionError("send called while leftover compose/draft tabs still open")
        self.sends.append((chat_id, text))
        return {"ok": True, "message": {"id": "sent-1", "text": text}}


class NewBookingFirstMessageTests(unittest.TestCase):
    def test_eviction_in_last_7_years_does_not_send(self) -> None:
        fake = FakeSend(leftover_tabs=[{"chat_id": "chat-1", "kind": "draft"}])
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            results = new_booking.process_new_bookings(
                [pending_thread()],
                now=NOW,
                state_path=state_path,
                load_template=lambda: TEMPLATE,
                rental_history_fn=lambda _thread: {"latestEvictionMonth": "2022-03", "totalEvictions": 1},
                leftover_compose_tabs=fake.tabs,
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
            )
            state = json.loads(state_path.read_text())
        self.assertEqual(results[0]["action"], "auto_deny_eviction")
        self.assertEqual(fake.sends, [])
        self.assertEqual(state["bookings"]["booking-1"]["action"], "auto_deny_eviction")

    def test_no_eviction_sends_live_template_once(self) -> None:
        fake = FakeSend(leftover_tabs=[{"chat_id": "chat-1", "kind": "compose"}])
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            results = new_booking.process_new_bookings(
                [pending_thread()],
                now=NOW,
                state_path=state_path,
                load_template=lambda: TEMPLATE,
                rental_history_fn=lambda _thread: {"totalEvictions": 0, "isInEviction": False},
                leftover_compose_tabs=fake.tabs,
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
            )
        self.assertEqual(results[0]["action"], "sent")
        self.assertEqual(len(fake.sends), 1)
        self.assertEqual(fake.sends[0][0], "chat-1")
        self.assertEqual(fake.sends[0][1], HIREVIRE_BODY)
        self.assertIn("hirevire.com/applications/977d344b-8592-4fc5-bd41-38717d6fa90a", fake.sends[0][1])

    def test_second_run_does_not_duplicate(self) -> None:
        fake = FakeSend()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            kwargs = dict(
                now=NOW,
                state_path=state_path,
                load_template=lambda: TEMPLATE,
                rental_history_fn=lambda _thread: {"totalEvictions": 0},
                leftover_compose_tabs=fake.tabs,
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
            )
            first = new_booking.process_new_bookings([pending_thread()], **kwargs)
            second = new_booking.process_new_bookings([pending_thread()], **kwargs)
        self.assertEqual(first[0]["action"], "sent")
        self.assertEqual(second[0]["action"], "already_handled")
        self.assertEqual(len(fake.sends), 1)

    def test_leftover_draft_tabs_closed_before_send(self) -> None:
        leftover = [{"chat_id": "chat-1", "kind": "draft"}, {"chat_id": "chat-1", "kind": "compose"}]
        fake = FakeSend(leftover_tabs=leftover)
        with tempfile.TemporaryDirectory() as tmpdir:
            new_booking.process_new_bookings(
                [pending_thread()],
                now=NOW,
                state_path=Path(tmpdir) / "state.json",
                load_template=lambda: TEMPLATE,
                rental_history_fn=lambda _thread: {"totalEvictions": 0},
                leftover_compose_tabs=fake.tabs,
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
            )
        self.assertEqual(fake.order, ["close", "send"])
        self.assertEqual(fake.tabs, [])
        self.assertEqual(len(fake.closed), 2)
        self.assertEqual(len(fake.sends), 1)

    def test_already_sent_hirevire_on_thread_skips(self) -> None:
        fake = FakeSend()
        thread = pending_thread(host_texts=[HIREVIRE_BODY])
        with tempfile.TemporaryDirectory() as tmpdir:
            results = new_booking.process_new_bookings(
                [thread],
                now=NOW,
                state_path=Path(tmpdir) / "state.json",
                load_template=lambda: TEMPLATE,
                rental_history_fn=lambda _thread: {"totalEvictions": 0},
                leftover_compose_tabs=fake.tabs,
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
            )
        self.assertEqual(results[0]["action"], "skipped_already_sent")
        self.assertEqual(fake.sends, [])

    def test_old_eviction_outside_7_years_would_send(self) -> None:
        self.assertFalse(
            new_booking.has_recent_eviction(
                {"totalEvictions": 1, "latestEvictionMonth": "2018-01"},
                now=NOW,
            )
        )
        self.assertTrue(
            new_booking.has_recent_eviction(
                {"totalEvictions": 1, "latestEvictionMonth": "2022-03"},
                now=NOW,
            )
        )

    def test_template_loader_uses_new_booking_request_card(self) -> None:
        doc = {
            "fields": {
                "n0": {"stringValue": "NEW BOOKING REQUEST"},
                "t0": {"stringValue": f"NEW BOOKING REQUEST\n\n{HIREVIRE_BODY}"},
                "n1": {"stringValue": "PROOF OF PAYMENT"},
                "t1": {"stringValue": "not this card"},
            }
        }
        loaded = new_booking.load_live_new_booking_template(fetch_doc=lambda: doc)
        self.assertEqual(loaded["text"], HIREVIRE_BODY)
        self.assertEqual(new_booking.verify_hirevire_template(loaded["text"]).count("hirevire.com"), 1)

    def test_wrong_hirevire_url_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            new_booking.verify_hirevire_template(
                "https://app.hirevire.com/applications/not-the-liaison-account?lang=EN"
            )

    def test_send_host_message_posts_sendmessage_not_approve_or_reject(self) -> None:
        calls = []

        class Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "data": {
                        "messenger": {
                            "chat": {"sendMessage": {"ok": True, "message": {"id": "m1"}}}
                        }
                    }
                }

        def request_fn(session, method, url, **kwargs):
            calls.append((method, url, kwargs["json"]))
            return Resp()

        sent = new_booking.send_host_message(
            object(),
            {"email": "user", "password": "pw"},
            "chat-1",
            HIREVIRE_BODY,
            request_fn=request_fn,
        )
        self.assertTrue(sent["ok"])
        self.assertEqual(len(calls), 1)
        query = calls[0][2]["query"]
        self.assertIn("mutation sendMessage", query)
        self.assertNotIn("Approve", query)
        self.assertNotIn("Reject", query)
        self.assertNotIn("approve", query)
        self.assertNotIn("reject", query)

    def test_ci_must_not_send(self) -> None:
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}, clear=False):
            self.assertFalse(new_booking.live_send_enabled())

    def test_scraper_hook_is_called_after_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "output"
            docs_data_dir = root / "docs" / "data"
            with (
                patch("padsplit_scraper.persist.OUTPUT_DIR", output_dir),
                patch("padsplit_scraper.persist.DOCS_DATA_DIR", docs_data_dir),
                patch("padsplit_scraper.scraper.load_credentials", return_value={"email": "user", "password": "pw"}),
                patch("padsplit_scraper.scraper.create_session") as session_cm,
                patch("padsplit_scraper.scraper.login"),
                patch("padsplit_scraper.scraper.fetch_messages", return_value=[{"id": "chat-1", "lastMessage": {"created": "2026-09-01T12:00:00+00:00"}}]),
                patch("padsplit_scraper.scraper.fetch_thread_messages", return_value=[]),
                patch("padsplit_scraper.scraper.fetch_tasks", return_value={}),
                patch("padsplit_scraper.scraper.fetch_rooms", return_value=[]),
                patch("padsplit_scraper.scraper.fetch_properties_stats", return_value=[]),
                patch("padsplit_scraper.scraper.fetch_earnings", return_value={"results": []}),
                patch("padsplit_scraper.scraper.fetch_performance_history", return_value={}),
                patch("padsplit_scraper.scraper.compute_kpis", return_value={"score": 1, "rooms_over_30d": 0}),
                patch("padsplit_scraper.new_booking.run_for_scraper") as run_mock,
            ):
                session_cm.return_value.__enter__.return_value = object()
                session_cm.return_value.__exit__.return_value = False
                import padsplit_scraper.scraper as scraper

                self.assertEqual(scraper.main(["--messages-only"]), 0)
                run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
