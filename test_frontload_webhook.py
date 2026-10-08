#!/usr/bin/env python3
"""Offline tests for the frontload booking/listing webhook. No live HTTP."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

import padsplit_scraper.persist as persist
import padsplit_scraper.scraper as scraper
from padsplit_scraper import frontload_webhook as hook
from padsplit_scraper import runtime


NOW = datetime(2026, 10, 7, 14, 32, tzinfo=timezone.utc)
FORBIDDEN = (
    "Ada Lovelace",
    "ada@example.com",
    "214-555-0199",
    "LOCKCODE-998877",
    "balance due 400",
    "John Majica",
    "crsr_test_key",
)


class Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def env(**overrides: str) -> dict:
    base = {
        "CI": "",
        "GITHUB_ACTIONS": "",
        "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
        "PADSPLIT_COLLECTION_ONLY": "",
        "FRONTLOAD_WEBHOOK_ENABLE": "1",
        "FRONTLOAD_WEBHOOK_DRY_RUN": "1",
        "FRONTLOAD_WEBHOOK_URL": "https://webhook.example/frontload",
        "FRONTLOAD_WEBHOOK_KEY": "crsr_test_key",
    }
    base.update(overrides)
    return base


def thread(
    booking_id: str = "b1",
    status: str = "PENDING",
    street: str = "3414 Pebbleshores Drive",
    room: str = "2",
    move_in: str = "2026-11-01",
    cancelled: bool = False,
    message_type: str = "BOOKING_STATUS",
) -> dict:
    return {
        "id": "chat-1",
        "title": "Ada Lovelace",
        "isCancelled": cancelled,
        "property": {"address": {"street1": street, "city": {"name": "Dallas"}}},
        "occupancy": {
            "id": "occ-1",
            "moveInDate": move_in,
            "room": {"roomNumber": room},
            "user": {
                "firstName": "Ada",
                "lastName": "Lovelace",
                "email": "ada@example.com",
                "phone": "214-555-0199",
            },
        },
        "lastMessage": {
            "text": "balance due 400 LOCKCODE-998877 call 214-555-0199",
            "messageType": message_type,
            "created": "2026-10-01T12:00:00Z",
            "bookingStatus": {"id": booking_id, "status": status, "created": "2026-10-01T12:00:00Z"},
            "sender": {"firstName": "Ada", "displayName": "Ada Lovelace"},
        },
    }


def listing(
    room_id: int = 1,
    price: int = 174,
    status: str = "listed",
    promo=None,
    house: str = "3414 Pebbleshores Drive",
    number: str = "2",
    move_in=None,
    new_price=None,
) -> dict:
    return {
        "id": room_id,
        "room_number": number,
        "detailed_status": status,
        "base_price": price,
        "new_price": new_price,
        "latest_occupancy_move_in_date": move_in,
        "address": {"full_street": house},
        "active_promo": None
        if promo is None
        else {"price_drop_percentage": promo, "editor": {"first_name": "John", "last_name": "Majica"}},
        "fee_status": "paid",
        "member_name": "Ada Lovelace",
        "balance": "balance due 400",
    }


def occupancy(next_move_in=None, house: str = "3414 Pebbleshores Drive", room: str = "2") -> dict:
    return {
        "rooms": [
            {
                "address": house,
                "room_number": room,
                "next_move_in": next_move_in,
                "occupant_name": "Ada Lovelace",
                "phone": "214-555-0199",
            }
        ]
    }


def assert_safe(test: unittest.TestCase, payload) -> None:
    blob = json.dumps(payload)
    for secret in FORBIDDEN:
        test.assertNotIn(secret, blob)
    if isinstance(payload, dict):
        test.assertEqual(set(payload.keys()), set(hook.PAYLOAD_KEYS))
        test.assertIsNone(payload["fee_status"])


class FrontloadWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = root / "state.json"
        self.log = root / "dry.jsonl"
        self.posts = []
        self.sleeps = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def emit(self, messages=None, rooms=None, occupancy_payload=None, **kwargs):
        kwargs.setdefault("now", NOW)
        kwargs.setdefault("state_path", self.state)
        kwargs.setdefault("dry_run_log_path", self.log)
        kwargs.setdefault("environ", env())
        kwargs.setdefault("session", None)
        return hook.emit_for_scraper(
            messages if messages is not None else [thread()],
            rooms=rooms,
            occupancy=occupancy_payload,
            **kwargs,
        )

    def test_header_is_authorization_bearer(self) -> None:
        self.assertEqual(hook.AUTH_HEADER, "Authorization")
        self.assertEqual(hook.authorization_value("crsr_test_key"), "Bearer crsr_test_key")
        self.assertEqual(hook.authorization_value("Bearer already"), "Bearer already")

    def test_defaults_are_off_and_dry_run(self) -> None:
        self.assertFalse(hook.webhook_enabled({"CI": ""}))
        self.assertTrue(hook.dry_run_enabled({"CI": ""}))
        blocked = self.emit(environ={"CI": "", "GITHUB_ACTIONS": ""})
        self.assertEqual(blocked["action"], "collection_skip")
        self.assertFalse(self.state.exists())
        result = self.emit(
            environ={"CI": "", "GITHUB_ACTIONS": "", "PADSPLIT_ENABLE_ACTION_HOOKS": "1"}
        )
        self.assertEqual(result["action"], "disabled")
        self.assertFalse(self.state.exists())

    def test_ci_never_posts_even_when_live_flags_are_on(self) -> None:
        result = self.emit(
            environ=env(CI="true", FRONTLOAD_WEBHOOK_DRY_RUN="0"),
            post_fn=self._fail_if_called,
        )
        self.assertEqual(result["action"], "ci_skip")
        self.assertFalse(self.state.exists())
        self.assertEqual(self.posts, [])

    def test_first_enabled_run_baselines_without_emitting(self) -> None:
        result = self.emit(rooms=[listing()], occupancy_payload=occupancy())
        self.assertEqual(result["action"], "baseline")
        self.assertEqual(result["events"], [])
        self.assertFalse(self.log.exists())
        saved = json.loads(self.state.read_text())
        self.assertTrue(saved["seeded"])
        self.assertIn("b1", saved["projection"]["bookings"])

    def test_new_booking_then_dedupes(self) -> None:
        self.emit(messages=[thread(booking_id="seed")])
        result = self.emit(messages=[thread(booking_id="b9")])
        self.assertEqual(result["action"], "dry_run")
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        assert_safe(self, event)
        self.assertEqual(event["event_type"], "new_booking")
        self.assertEqual(event["booking_id"], "b9")
        self.assertEqual(event["house"], "3414 Pebbleshores Drive")
        self.assertEqual(event["room"], "2")
        self.assertEqual(event["move_in_date"], "2026-11-01")
        self.assertTrue(event["observed_at"].endswith("+00:00"))
        self.assertNotIn("Z", event["observed_at"])
        again = self.emit(messages=[thread(booking_id="b9")])
        self.assertEqual(again["events"], [])
        lines = self.log.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("crsr_test_key", self.log.read_text())

    def test_pending_inbox_is_a_booking_source(self) -> None:
        self.emit(messages=[])

        def fetch_pending(session, creds):
            return [
                {
                    "id": "inbox-7",
                    "created": "2026-10-07T00:00:00Z",
                    "approved": None,
                    "room": {"roomNumber": 4, "name": "Ada Lovelace"},
                }
            ]

        result = self.emit(messages=[], session=object(), creds={"email": "x"}, fetch_pending_fn=fetch_pending)
        self.assertEqual([event["event_type"] for event in result["events"]], ["new_booking"])
        self.assertEqual(result["events"][0]["booking_id"], "inbox-7")
        self.assertEqual(result["events"][0]["room"], "4")
        assert_safe(self, result["events"][0])

    def test_pending_inbox_failure_does_not_raise(self) -> None:
        self.emit(messages=[])

        def fetch_pending(session, creds):
            raise RuntimeError("ada@example.com token LOCKCODE-998877")

        result = self.emit(messages=[], session=object(), creds={"email": "x"}, fetch_pending_fn=fetch_pending)
        self.assertEqual(result["events"], [])

    def test_cancel_on_status_and_is_cancelled_flag(self) -> None:
        self.emit(messages=[thread(status="PENDING")])
        rejected = self.emit(messages=[thread(status="REJECTED")])
        self.assertEqual([event["event_type"] for event in rejected["events"]], ["cancel"])
        self.assertEqual(rejected["events"][0]["booking_id"], "b1")
        assert_safe(self, rejected["events"][0])

        self.emit(messages=[thread(booking_id="b2", status="PENDING", cancelled=False)])
        flagged = self.emit(messages=[thread(booking_id="b2", status="PENDING", cancelled=True)])
        self.assertEqual([event["event_type"] for event in flagged["events"]], ["cancel"])

    def test_disappearing_pending_booking_is_not_a_cancel(self) -> None:
        self.emit(messages=[thread(booking_id="stay")])
        result = self.emit(messages=[])
        self.assertEqual(result["events"], [])
        saved = json.loads(self.state.read_text())
        self.assertIn("stay", saved["projection"]["bookings"])

    def test_move_in_from_occupancy_and_listing_dedupes(self) -> None:
        self.emit(
            rooms=[listing(status="occupied", move_in=None)],
            occupancy_payload=occupancy(next_move_in=None),
        )
        result = self.emit(
            rooms=[listing(status="move-in", move_in="2026-12-01")],
            occupancy_payload=occupancy(next_move_in="2026-12-01"),
        )
        kinds = [event["event_type"] for event in result["events"]]
        self.assertEqual(kinds.count("move_in"), 1)
        move = next(event for event in result["events"] if event["event_type"] == "move_in")
        self.assertEqual(move["move_in_date"], "2026-12-01")
        self.assertEqual(move["house"], "3414 Pebbleshores Drive")
        self.assertEqual(move["booking_id"], "b1")
        assert_safe(self, move)

    def test_listing_edit_price_and_promo_sets_rents_not_fee(self) -> None:
        self.assertIsNone(hook.intro_rent_for(174, None))
        self.assertEqual(hook.intro_rent_for(200, 10), 180)
        self.assertEqual(hook.intro_rent_for(174, 10), 156.6)
        self.emit(rooms=[listing(price=200, promo=None, status="occupied")])
        result = self.emit(rooms=[listing(price=200, promo=10, status="occupied")])
        edits = [event for event in result["events"] if event["event_type"] == "listing_edit"]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["target_rent"], 200)
        self.assertEqual(edits[0]["intro_rent"], 180)
        self.assertIsNone(edits[0]["fee_status"])
        assert_safe(self, edits[0])

        listed = self.emit(rooms=[listing(price=180, promo=10, status="listed")])
        listed_edits = [event for event in listed["events"] if event["event_type"] == "listing_edit"]
        self.assertEqual(len(listed_edits), 1)
        self.assertEqual(listed_edits[0]["target_rent"], 180)

    def test_messages_only_does_not_wipe_listings(self) -> None:
        self.emit(rooms=[listing(price=174, status="listed")])
        quiet = self.emit(rooms=None, occupancy_payload=None)
        self.assertEqual(quiet["events"], [])
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["projection"]["listings"]["1"]["base_price"], 174)
        changed = self.emit(rooms=[listing(price=180, status="listed")])
        self.assertEqual([event["event_type"] for event in changed["events"]], ["listing_edit"])
        self.assertEqual(changed["events"][0]["target_rent"], 180)

    def test_event_id_is_stable(self) -> None:
        first = hook.make_event_id("new_booking", "b1")
        second = hook.make_event_id("new_booking", "b1")
        other = hook.make_event_id("cancel", "cancel|b1")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_live_post_uses_bearer_header_timeout_and_backoff(self) -> None:
        live = env(FRONTLOAD_WEBHOOK_DRY_RUN="0")
        self.emit(messages=[thread(booking_id="seed")], environ=live, post_fn=self._record_post)
        attempts = {"n": 0}

        def post(url, *, json, headers, timeout):
            attempts["n"] += 1
            self._record_post(url, json=json, headers=headers, timeout=timeout)
            if attempts["n"] < 3:
                raise requests.Timeout("slow")
            return Response(200)

        result = self.emit(
            messages=[thread(booking_id="live-1")],
            environ=live,
            post_fn=post,
            sleep_fn=self.sleeps.append,
        )
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(self.sleeps, [0.5, 1.5])
        self.assertEqual(self.posts[-1]["headers"]["Authorization"], "Bearer crsr_test_key")
        self.assertEqual(self.posts[-1]["timeout"], (5, 20))
        self.assertNotIn("crsr_test_key", json.dumps(self.posts[-1]["body"]))
        replay = self.emit(
            messages=[thread(booking_id="live-1")],
            environ=live,
            post_fn=post,
            sleep_fn=self.sleeps.append,
        )
        self.assertEqual(replay["events"], [])
        self.assertEqual(attempts["n"], 3)

    def test_server_error_stays_pending_then_retries(self) -> None:
        live = env(FRONTLOAD_WEBHOOK_DRY_RUN="0")
        self.emit(messages=[thread(booking_id="seed")], environ=live)

        def fail(url, *, json, headers, timeout):
            return Response(503)

        failed = self.emit(
            messages=[thread(booking_id="retry-1")],
            environ=live,
            post_fn=fail,
            sleep_fn=self.sleeps.append,
        )
        self.assertEqual(failed["events"], [])
        self.assertEqual(failed["failed"], 1)
        self.assertEqual(len(self.sleeps), 2)
        saved = json.loads(self.state.read_text())
        self.assertEqual(len(saved["pending"]), 1)
        self.assertEqual(saved["pending"][0]["event_id"], saved["pending"][0]["payload"]["event_id"])
        self.assertEqual(saved["sent_event_ids"], [])

        def ok(url, *, json, headers, timeout):
            return Response(204)

        retried = self.emit(
            messages=[thread(booking_id="retry-1")],
            environ=live,
            post_fn=ok,
            sleep_fn=self.sleeps.append,
        )
        self.assertEqual(len(retried["events"]), 1)
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["pending"], [])
        self.assertIn(retried["events"][0]["event_id"], saved["sent_event_ids"])

    def test_client_error_is_not_retried_or_kept(self) -> None:
        live = env(FRONTLOAD_WEBHOOK_DRY_RUN="0")
        self.emit(messages=[thread(booking_id="seed")], environ=live)
        calls = {"n": 0}

        def reject(url, *, json, headers, timeout):
            calls["n"] += 1
            return Response(400)

        result = self.emit(
            messages=[thread(booking_id="bad-1")],
            environ=live,
            post_fn=reject,
            sleep_fn=self.sleeps.append,
        )
        self.assertEqual(result["events"], [])
        self.assertEqual(calls["n"], 1)
        self.assertEqual(self.sleeps, [])
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["pending"], [])
        again = self.emit(
            messages=[thread(booking_id="bad-1")],
            environ=live,
            post_fn=reject,
        )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(again["events"], [])

    def test_collection_only_does_not_send_or_write_the_ledger(self) -> None:
        live = env(FRONTLOAD_WEBHOOK_DRY_RUN="0", PADSPLIT_COLLECTION_ONLY="1")
        self.assertFalse(runtime.send_enabled("frontload", live))
        result = self.emit(
            rooms=[listing()],
            environ=live,
            post_fn=self._fail_if_called,
        )
        self.assertEqual(result["action"], "collection_skip")
        self.assertFalse(self.state.exists())
        self.assertEqual(self.posts, [])

        allowed = env(FRONTLOAD_WEBHOOK_DRY_RUN="0")
        self.assertTrue(runtime.send_enabled("frontload", allowed))
        forced = self.emit(
            rooms=[listing()],
            environ=allowed,
            policy=runtime.COLLECTION_ONLY_POLICY,
            post_fn=self._fail_if_called,
        )
        self.assertEqual(forced["action"], "collection_skip")
        self.assertFalse(self.state.exists())

    def test_rejected_4xx_is_failed_and_not_retried(self) -> None:
        live = env(FRONTLOAD_WEBHOOK_DRY_RUN="0")
        self.emit(messages=[thread(booking_id="seed")], environ=live)
        calls = {"n": 0}

        def reject(url, *, json, headers, timeout):
            calls["n"] += 1
            return Response(422)

        first = self.emit(
            messages=[thread(booking_id="nope")],
            environ=live,
            post_fn=reject,
            sleep_fn=self.sleeps.append,
        )
        self.assertEqual(first["events"], [])
        self.assertEqual(calls["n"], 1)
        self.assertEqual(self.sleeps, [])
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["pending"], [])
        self.assertEqual(len(saved["failed"]), 1)
        record = saved["failed"][0]
        self.assertEqual(set(record.keys()), {"at", "event_id", "status_code"})
        self.assertEqual(record["status_code"], 422)
        self.assertTrue(str(record["at"]).endswith("+00:00"))
        blob = json.dumps(saved["failed"])
        for secret in FORBIDDEN:
            self.assertNotIn(secret, blob)
        self.assertNotIn("3414 Pebbleshores", blob)

        again = self.emit(
            messages=[thread(booking_id="nope")],
            environ=live,
            post_fn=reject,
        )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(again["events"], [])

    def test_pending_4xx_leaves_the_queue(self) -> None:
        live = env(FRONTLOAD_WEBHOOK_DRY_RUN="0")
        self.emit(messages=[thread(booking_id="seed")], environ=live)

        def fail(url, *, json, headers, timeout):
            return Response(503)

        self.emit(
            messages=[thread(booking_id="later-reject")],
            environ=live,
            post_fn=fail,
            sleep_fn=self.sleeps.append,
        )
        self.assertEqual(len(json.loads(self.state.read_text())["pending"]), 1)

        def reject(url, *, json, headers, timeout):
            self.posts.append("reject")
            return Response(400)

        rejected = self.emit(
            messages=[thread(booking_id="later-reject")],
            environ=live,
            post_fn=reject,
        )
        self.assertEqual(rejected["events"], [])
        self.assertEqual(self.posts, ["reject"])
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["pending"], [])
        self.assertEqual(saved["failed"][0]["status_code"], 400)
        self.assertNotIn("payload", saved["failed"][0])

        def boom(url, *, json, headers, timeout):
            raise AssertionError("failed event was retried")

        self.emit(
            messages=[thread(booking_id="later-reject")],
            environ=live,
            post_fn=boom,
        )

    def test_retry_cap_across_runs_marks_failed(self) -> None:
        live = env(FRONTLOAD_WEBHOOK_DRY_RUN="0")
        self.emit(messages=[thread(booking_id="seed")], environ=live)
        calls = {"n": 0}

        def fail(url, *, json, headers, timeout):
            calls["n"] += 1
            return Response(503)

        for _ in range(hook.MAX_PENDING_ATTEMPTS):
            self.emit(
                messages=[thread(booking_id="capped")],
                environ=live,
                post_fn=fail,
                sleep_fn=self.sleeps.append,
            )
        saved = json.loads(self.state.read_text())
        self.assertEqual(saved["pending"], [])
        self.assertEqual(saved["failed"][0]["status_code"], 503)
        self.assertEqual(set(saved["failed"][0].keys()), {"at", "event_id", "status_code"})
        posted = calls["n"]

        self.emit(
            messages=[thread(booking_id="capped")],
            environ=live,
            post_fn=fail,
            sleep_fn=self.sleeps.append,
        )
        self.assertEqual(calls["n"], posted)

    def test_price_round_trip_emits_twice_and_same_diff_dedupes(self) -> None:
        self.emit(rooms=[listing(price=100, status="occupied")])
        first = self.emit(rooms=[listing(price=200, status="occupied")])
        second = self.emit(rooms=[listing(price=100, status="occupied")])
        first_ids = [event["event_id"] for event in first["events"] if event["event_type"] == "listing_edit"]
        second_ids = [event["event_id"] for event in second["events"] if event["event_type"] == "listing_edit"]
        self.assertEqual(len(first_ids), 1)
        self.assertEqual(len(second_ids), 1)
        self.assertNotEqual(first_ids[0], second_ids[0])
        quiet = self.emit(rooms=[listing(price=100, status="occupied")])
        self.assertEqual([event for event in quiet["events"] if event["event_type"] == "listing_edit"], [])

        previous = {
            "bookings": {},
            "occupancy": {},
            "message_move_ins": {},
            "listings": {"1": {"base_price": 100, "promo_pct": None, "promo_weeks": None, "new_price": None, "detailed_status": "occupied", "house": "3414 Pebbleshores Drive", "room": "2", "move_in_date": None}},
        }
        current = {
            "bookings": {},
            "occupancy": {},
            "message_move_ins": {},
            "listings": {"1": {"base_price": 200, "promo_pct": None, "promo_weeks": None, "new_price": None, "detailed_status": "occupied", "house": "3414 Pebbleshores Drive", "room": "2", "move_in_date": None}},
        }
        morning = hook.diff_projection(
            previous, current, observed_at="2026-10-08T09:00:00+00:00", listings_refreshed=True, occupancy_refreshed=False
        )
        afternoon = hook.diff_projection(
            previous, current, observed_at="2026-10-08T16:00:00+00:00", listings_refreshed=True, occupancy_refreshed=False
        )
        self.assertEqual(morning[0]["event_id"], afternoon[0]["event_id"])
        next_day = hook.diff_projection(
            previous, current, observed_at="2026-10-09T09:00:00+00:00", listings_refreshed=True, occupancy_refreshed=False
        )
        self.assertNotEqual(morning[0]["event_id"], next_day[0]["event_id"])

    def test_missing_url_does_not_raise(self) -> None:
        quiet = env(FRONTLOAD_WEBHOOK_DRY_RUN="0", FRONTLOAD_WEBHOOK_URL="")
        self.emit(messages=[thread(booking_id="seed")], environ=quiet)
        result = self.emit(
            messages=[thread(booking_id="nourl")],
            environ=quiet,
            post_fn=self._fail_if_called,
        )
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.posts, [])

    def _record_post(self, url, *, json, headers, timeout):
        self.posts.append({"url": url, "body": json, "headers": dict(headers), "timeout": timeout})
        return Response(200)

    def _fail_if_called(self, *args, **kwargs):
        raise AssertionError("POST must not run")


class ScraperHookTests(unittest.TestCase):
    def test_messages_only_calls_emitter_and_swallows_errors(self) -> None:
        calls = []

        def boom(*args, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("ada@example.com LOCKCODE-998877")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            docs_data_dir = Path(tmpdir) / "docs" / "data"
            with (
                patch.object(persist, "OUTPUT_DIR", output_dir),
                patch.object(persist, "DOCS_DATA_DIR", docs_data_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(scraper, "fetch_messages", return_value=[{"id": "chat-1", "lastMessage": {"created": "2026-10-07T12:00:00+00:00"}}]),
                patch.object(scraper, "fetch_thread_messages", return_value=[]),
                patch("padsplit_scraper.frontload_webhook.emit_for_scraper", boom),
            ):
                exit_code = scraper.main(["--messages-only"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0].get("rooms"))
        self.assertIsNone(calls[0].get("occupancy"))

    def test_full_scrape_passes_rooms_and_occupancy(self) -> None:
        seen = {}

        def capture(messages, **kwargs):
            seen["rooms"] = kwargs.get("rooms")
            seen["occupancy"] = kwargs.get("occupancy")
            return {"action": "disabled", "events": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            docs_data_dir = Path(tmpdir) / "docs" / "data"
            with (
                patch.object(persist, "OUTPUT_DIR", output_dir),
                patch.object(persist, "DOCS_DATA_DIR", docs_data_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(
                    scraper,
                    "fetch_messages",
                    return_value=[{"id": "chat-1", "lastMessage": {"created": "2026-10-07T12:00:00+00:00"}}],
                ),
                patch.object(scraper, "fetch_thread_messages", return_value=[]),
                patch.object(scraper, "fetch_tasks", return_value={}),
                patch.object(scraper, "fetch_rooms", return_value=[{"id": 1, "room_number": 2, "base_price": 10}]),
                patch.object(scraper, "fetch_properties_stats", return_value=[]),
                patch.object(scraper, "fetch_earnings", return_value={"results": []}),
                patch.object(scraper, "compute_kpis", return_value={"score": 1}),
                patch.object(scraper, "fetch_performance_history", return_value={}),
                patch.object(scraper, "upload_stats_to_firestore"),
                patch("padsplit_scraper.frontload_webhook.emit_for_scraper", capture),
            ):
                exit_code = scraper.main([])
        self.assertEqual(exit_code, 0)
        self.assertEqual(seen["rooms"], [{"id": 1, "room_number": 2, "base_price": 10}])
        self.assertIn("rooms", seen["occupancy"])

    def test_room_fetch_failure_does_not_pass_empty_listings(self) -> None:
        seen = {}

        def capture(messages, **kwargs):
            seen["rooms"] = kwargs.get("rooms")
            seen["occupancy"] = kwargs.get("occupancy")
            return {"action": "disabled", "events": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            docs_data_dir = Path(tmpdir) / "docs" / "data"
            output_dir.mkdir(parents=True)
            (output_dir / "stats.json").write_text(json.dumps({"scraped_at": "2026-05-01T12:00:00Z", "kpis": {"score": 1}}))
            with (
                patch.object(persist, "OUTPUT_DIR", output_dir),
                patch.object(persist, "DOCS_DATA_DIR", docs_data_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(
                    scraper,
                    "fetch_messages",
                    return_value=[{"id": "chat-1", "lastMessage": {"created": "2026-10-07T12:00:00+00:00"}}],
                ),
                patch.object(scraper, "fetch_thread_messages", return_value=[]),
                patch.object(scraper, "fetch_tasks", return_value={}),
                patch.object(scraper, "fetch_rooms", side_effect=requests.exceptions.ConnectionError("socket hangup")),
                patch.object(scraper, "upload_stats_to_firestore"),
                patch("padsplit_scraper.frontload_webhook.emit_for_scraper", capture),
            ):
                exit_code = scraper.main([])
        self.assertEqual(exit_code, 0)
        self.assertIsNone(seen["rooms"])
        self.assertIsNotNone(seen["occupancy"])

    def test_collection_policy_from_scraper_does_not_post(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            docs_data_dir = Path(tmpdir) / "docs" / "data"
            state_path = Path(tmpdir) / "frontload_state.json"
            log_path = Path(tmpdir) / "frontload.jsonl"

            def boom(*args, **kwargs):
                raise AssertionError("collection-only job posted")

            live = env(FRONTLOAD_WEBHOOK_DRY_RUN="0")
            with (
                patch.dict("os.environ", live, clear=False),
                patch.object(persist, "OUTPUT_DIR", output_dir),
                patch.object(persist, "DOCS_DATA_DIR", docs_data_dir),
                patch.object(hook, "STATE_PATH", state_path),
                patch.object(hook, "DRY_RUN_LOG_PATH", log_path),
                patch.object(hook, "post_payload", boom),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(
                    scraper,
                    "fetch_messages",
                    return_value=[{"id": "chat-1", "lastMessage": {"created": "2026-10-07T12:00:00+00:00"}}],
                ),
                patch.object(scraper, "fetch_thread_messages", return_value=[]),
                patch.object(scraper, "fetch_tasks", return_value={}),
                patch.object(scraper, "fetch_rooms", return_value=[{"id": 1, "room_number": 2, "base_price": 10}]),
                patch.object(scraper, "fetch_properties_stats", return_value=[]),
                patch.object(scraper, "fetch_earnings", return_value={"results": []}),
                patch.object(scraper, "compute_kpis", return_value={"score": 1}),
                patch.object(scraper, "fetch_performance_history", return_value={}),
                patch.object(scraper, "upload_stats_to_firestore"),
            ):
                outcome = scraper.run(policy=runtime.COLLECTION_ONLY_POLICY)
            self.assertEqual(outcome.process_exit_code, 0)
            self.assertFalse(state_path.exists())
            self.assertFalse(log_path.exists())


if __name__ == "__main__":
    unittest.main()
