#!/usr/bin/env python3
"""Unit tests for new-booking Hirevire + leftover-draft gate. No live sends."""

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
INBOX = [{"id": "booking-1", "approved": False, "room": {"roomNumber": 2}}]


def pending_inbox(*ids: str) -> list[dict]:
    return [{"id": booking_id, "approved": False} for booking_id in ids] or list(INBOX)


def occupancy_thread(
    *,
    chat_id: str = "chat-1",
    occupancy_id: str = "booking-1",
    host_texts: list[str] | None = None,
    last_text: str = "Hey checking on everything ??",
) -> dict:
    """Thread whose lastMessage is TEXT — the messages-only digest miss."""
    messages = [
        {
            "id": "m-member",
            "text": last_text,
            "messageType": "TEXT",
            "deleted": None,
            "sender": {"roleId": "A_0", "firstName": "Alexandria"},
            "bookingStatus": None,
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
        "title": "Alexandria Hudson",
        "occupancy": {
            "id": occupancy_id,
            "moveInDate": "2026-09-02",
            "room": {"roomNumber": 2},
            "user": {"firstName": "Alexandria", "lastName": "Hudson"},
        },
        "property": {
            "address": {
                "street1": "3541 Parker Road East",
                "city": {"name": "Haltom City"},
            }
        },
        "recent_messages": messages,
        "lastMessage": messages[0],
    }


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
    def __init__(self, leftover_tabs: list[dict] | None = None, close_fails: bool = False) -> None:
        self.tabs = leftover_tabs if leftover_tabs is not None else []
        self.closed: list[dict] = []
        self.sends: list[tuple[str, str]] = []
        self.order: list[str] = []
        self.close_fails = close_fails
        self.packs: list[dict] = []

    def close_tabs(self, open_tabs, chat_id: str) -> list[dict]:
        self.order.append("close")
        if self.close_fails:
            raise RuntimeError("compose tab stuck")
        closed = new_booking.close_leftover_compose_tabs(self.tabs, chat_id)
        self.closed.extend(closed)
        return closed

    def send(self, chat_id: str, text: str) -> dict:
        self.order.append("send")
        if new_booking.leftover_tabs_open(self.tabs, chat_id):
            raise AssertionError("send called while leftover compose/draft tabs still open")
        self.sends.append((chat_id, text))
        return {"ok": True, "message": {"id": f"sent-{len(self.sends)}", "text": text}}

    def notify(self, result: dict) -> dict:
        self.packs.append(result)
        return {"posted": True}


def run_process(fake: FakeSend, threads: list[dict], inbox=None, **kwargs):
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = Path(tmpdir) / "state.json"
        defaults = dict(
            pending_inbox=inbox if inbox is not None else pending_inbox("booking-1"),
            now=NOW,
            state_path=state_path,
            load_template=lambda: TEMPLATE,
            rental_history_fn=lambda _thread: {"totalEvictions": 0, "isInEviction": False},
            leftover_compose_tabs=fake.tabs,
            close_tabs_fn=fake.close_tabs,
            send_fn=fake.send,
            notify_fn=fake.notify,
        )
        defaults.update(kwargs)
        results = new_booking.process_new_bookings(threads, **defaults)
        state = json.loads(state_path.read_text()) if state_path.exists() else {"bookings": {}}
        return results, state, state_path


class NewBookingFirstMessageTests(unittest.TestCase):
    def test_inbox_hit_joins_occupancy_when_last_message_is_text(self) -> None:
        fake = FakeSend()
        results, state, _ = run_process(fake, [occupancy_thread()])
        self.assertEqual(results[0]["action"], "sent")
        self.assertEqual(results[0]["source"], "hostPendingBookingRequests")
        self.assertEqual(len(fake.sends), 1)
        self.assertEqual(fake.sends[0][1], HIREVIRE_BODY)
        self.assertTrue(results[0]["pack_posted"])
        self.assertEqual(len(fake.packs), 1)
        self.assertTrue(state["bookings"]["booking-1"]["pack_posted"])

    def test_messages_only_digest_without_inbox_is_not_a_hit(self) -> None:
        fake = FakeSend()
        thread = pending_thread()
        results, _, _ = run_process(fake, [thread], inbox=[])
        self.assertEqual(results, [])
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.packs, [])

    def test_approved_inbox_node_is_ignored(self) -> None:
        fake = FakeSend()
        results, _, _ = run_process(
            fake,
            [occupancy_thread()],
            inbox=[{"id": "booking-1", "approved": True}],
        )
        self.assertEqual(results, [])
        self.assertEqual(fake.sends, [])

    def test_eviction_in_last_7_years_does_not_send_or_pack(self) -> None:
        fake = FakeSend(leftover_tabs=[{"chat_id": "chat-1", "kind": "draft"}])
        results, state, _ = run_process(
            fake,
            [occupancy_thread()],
            rental_history_fn=lambda _thread: {"latestEvictionMonth": "2022-03", "totalEvictions": 1},
        )
        self.assertEqual(results[0]["action"], "auto_deny_eviction")
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.packs, [])
        self.assertEqual(state["bookings"]["booking-1"]["action"], "auto_deny_eviction")
        self.assertFalse(state["bookings"]["booking-1"]["pack_posted"])

    def test_no_eviction_sends_live_template_once_and_packs_once(self) -> None:
        fake = FakeSend(leftover_tabs=[{"chat_id": "chat-1", "kind": "compose"}])
        results, _, _ = run_process(fake, [occupancy_thread()])
        self.assertEqual(results[0]["action"], "sent")
        self.assertEqual(len(fake.sends), 1)
        self.assertEqual(fake.sends[0][0], "chat-1")
        self.assertIn("hirevire.com/applications/977d344b-8592-4fc5-bd41-38717d6fa90a", fake.sends[0][1])
        self.assertEqual(len(fake.packs), 1)
        snap = results[0]["screening_snapshot"]
        self.assertEqual(snap["first_name"], "Alexandria")
        self.assertEqual(snap["last_initial"], "H")
        self.assertEqual(snap["property"], "3541 Parker Road East, Haltom City")
        self.assertEqual(snap["room"], 2)
        self.assertTrue(snap["wait_for_hirevire_video"])
        self.assertIn("Approve/Reject", snap["decision"])
        self.assertNotIn("email", snap)
        self.assertNotIn("phone", snap)
        self.assertNotIn("ssn", snap)
        self.assertNotIn("picture", snap)

    def test_second_run_does_not_duplicate_hirevire_or_pack(self) -> None:
        fake = FakeSend()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            kwargs = dict(
                pending_inbox=pending_inbox("booking-1"),
                now=NOW,
                state_path=state_path,
                load_template=lambda: TEMPLATE,
                rental_history_fn=lambda _thread: {"totalEvictions": 0},
                leftover_compose_tabs=fake.tabs,
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
                notify_fn=fake.notify,
            )
            first = new_booking.process_new_bookings([occupancy_thread()], **kwargs)
            second = new_booking.process_new_bookings([occupancy_thread()], **kwargs)
        self.assertEqual(first[0]["action"], "sent")
        self.assertEqual(second[0]["action"], "already_handled")
        self.assertEqual(len(fake.sends), 1)
        self.assertEqual(len(fake.packs), 1)

    def test_leftover_draft_tabs_closed_before_send(self) -> None:
        leftover = [{"chat_id": "chat-1", "kind": "draft"}, {"chat_id": "chat-1", "kind": "compose"}]
        fake = FakeSend(leftover_tabs=leftover)
        run_process(fake, [occupancy_thread()])
        self.assertEqual(fake.order, ["close", "send"])
        self.assertEqual(fake.tabs, [])
        self.assertEqual(len(fake.closed), 2)
        self.assertEqual(len(fake.sends), 1)

    def test_leftover_drafts_not_cleared_hard_skips_send_no_duplicate_bubbles(self) -> None:
        leftover = [{"chat_id": "chat-1", "kind": "draft"}]
        fake = FakeSend(leftover_tabs=leftover, close_fails=True)
        first, state, _ = run_process(fake, [occupancy_thread()])
        self.assertEqual(first[0]["action"], "skipped_leftover_drafts")
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.packs, [])
        self.assertNotIn("booking-1", state.get("bookings", {}))

        fake.close_fails = True
        second, _, _ = run_process(fake, [occupancy_thread()])
        self.assertEqual(second[0]["action"], "skipped_leftover_drafts")
        self.assertEqual(fake.sends, [])

        fake.close_fails = False
        third, _, _ = run_process(fake, [occupancy_thread()])
        self.assertEqual(third[0]["action"], "sent")
        self.assertEqual(len(fake.sends), 1)

    def test_send_host_message_hard_skips_when_leftover_store_unwired(self) -> None:
        with self.assertRaises(new_booking.LeftoverDraftGateError):
            new_booking.send_host_message(
                object(),
                {"email": "user", "password": "pw"},
                "chat-1",
                HIREVIRE_BODY,
                leftover_compose_tabs=None,
            )

    def test_already_sent_hirevire_on_thread_skips_send_but_packs_once(self) -> None:
        fake = FakeSend()
        thread = occupancy_thread(host_texts=[HIREVIRE_BODY])
        results, _, _ = run_process(fake, [thread])
        self.assertIn(results[0]["action"], {"skipped_already_sent", "pack_posted"})
        self.assertEqual(fake.sends, [])
        self.assertEqual(len(fake.packs), 1)

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
            leftover_compose_tabs=[],
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

    def test_pack_mentions_joe_only_and_waits_for_video(self) -> None:
        snap = new_booking.build_screening_snapshot(
            booking_id="booking-1",
            thread=occupancy_thread(),
            hirevire_sent=True,
        )
        text = new_booking.format_new_tenants_pack(snap, joe_user_id="1234567890")
        self.assertIn("<@1234567890>", text)
        self.assertIn("Wait for the Hirevire video", text)
        self.assertIn("Ang taps Approve/Reject", text)
        self.assertIn("do not auto-approve", text)
        self.assertIn("hostPendingBookingRequests", text)
        self.assertNotIn("Cindy", text)
        self.assertNotRegex(text, r"(?i)ssn")
        self.assertNotRegex(text, r"(?i)lock ?code")

    def test_pack_refuses_forbidden_content(self) -> None:
        with self.assertRaises(RuntimeError):
            new_booking.format_new_tenants_pack(
                {
                    "first_name": "Pat",
                    "last_initial": "C",
                    "property": "lock code 1234",
                    "hirevire_sent": True,
                },
                joe_user_id="1",
            )

    def test_parse_pending_inbox_skips_approved(self) -> None:
        nodes = new_booking.parse_pending_inbox_payload(
            {
                "data": {
                    "bookingRequests": {
                        "all": {
                            "edges": [
                                {"node": {"id": "a", "approved": False}},
                                {"node": {"id": "b", "approved": True}},
                                {"node": {"id": "c"}},
                            ]
                        }
                    }
                }
            }
        )
        self.assertEqual([n["id"] for n in nodes], ["a", "c"])

    def test_occupancy_pk_from_gid(self) -> None:
        self.assertEqual(new_booking.occupancy_pk_from_gid("42"), 42)
        import base64

        gid = base64.b64encode(b"OccupancyType:99").decode()
        self.assertEqual(new_booking.occupancy_pk_from_gid(gid), 99)

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
