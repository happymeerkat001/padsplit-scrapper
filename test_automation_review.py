#!/usr/bin/env python3
"""Offline automation-review suite. Network is forbidden."""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from padsplit_scraper import leak_reply
from padsplit_scraper import lockout_reply
from padsplit_scraper import new_booking

from test_lockout_reply import (
    DOOR_HOST,
    FAKE_BACK,
    FAKE_FRONT,
    FAKE_ROOM,
    NOW,
    FakeSend,
    fake_doc,
    member_thread,
    run_process,
)
from test_leak_reply import firestore_t5_doc
from test_leak_reply import member_thread as leak_member_thread
from test_leak_reply import run_process as run_leak_process


def _block_network(*_args, **_kwargs):
    raise AssertionError("network forbidden in test_automation_review")


class NetworkGuard(unittest.TestCase):
    def setUp(self) -> None:
        self._create_connection = socket.create_connection
        socket.create_connection = _block_network  # type: ignore[assignment]
        self._socket_connect = socket.socket.connect

        def _blocked_connect(sock, address):  # type: ignore[no-untyped-def]
            raise AssertionError(f"network forbidden: connect {address}")

        socket.socket.connect = _blocked_connect  # type: ignore[method-assign]

    def tearDown(self) -> None:
        socket.create_connection = self._create_connection
        socket.socket.connect = self._socket_connect  # type: ignore[method-assign]


def moss_thread(**kwargs: Any) -> dict:
    defaults = dict(chat_id="chat-moss", street="Spanish Moss", room=1)
    defaults.update(kwargs)
    return member_thread(**defaults)


class PreviewGateTests(NetworkGuard):
    def test_preview_never_calls_default_rotating_sifely(self) -> None:
        fake = FakeSend()
        calls = []

        def boom() -> tuple[str, str]:
            calls.append("obtain")
            raise AssertionError("default obtain_spanish_moss_back must not run in preview")

        with patch.object(lockout_reply, "obtain_spanish_moss_back", side_effect=boom):
            with tempfile.TemporaryDirectory() as tmpdir:
                rows = lockout_reply.process_lockouts(
                    [moss_thread()],
                    now=NOW,
                    state_path=Path(tmpdir) / "state.json",
                    leftover_compose_tabs=[],
                    close_tabs_fn=fake.close_tabs,
                    send_fn=fake.send,
                    codes_fn=lambda _slug: {},
                    sifely_fn=None,
                    post_discord=fake.posts.append,
                    send_enabled=False,
                    dry_run=True,
                )
        self.assertEqual(calls, [])
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])
        self.assertIn(rows[0]["action"], {"missing_codes", "would_send", "ask_joe"})

    def test_non_occupant_never_fetches_codes_or_sifely(self) -> None:
        fake = FakeSend()
        codes = []
        sifely = []
        departed = moss_thread(move_out="2026-08-01")
        rows, _ = run_process(
            fake,
            [departed],
            codes_fn=lambda slug: codes.append(slug) or fake_doc(),
            sifely_fn=lambda: sifely.append("sifely") or (FAKE_BACK, "sifely_current"),
        )
        self.assertEqual(rows[0]["action"], "skip")
        self.assertEqual(codes, [])
        self.assertEqual(sifely, [])
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])

    def test_disabled_send_does_not_post_discord(self) -> None:
        fake = FakeSend()
        rows, _ = run_process(
            fake,
            [member_thread(room=99)],
            send_enabled=False,
            dry_run=False,
        )
        self.assertEqual(rows[0]["action"], "missing_codes")
        self.assertTrue(rows[0].get("discord"))
        self.assertEqual(fake.posts, [])
        self.assertEqual(fake.sends, [])

    def test_dry_run_injected_sifely_does_not_write_state(self) -> None:
        fake = FakeSend()
        sifely = []
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            rows = lockout_reply.process_lockouts(
                [moss_thread()],
                now=NOW,
                state_path=state_path,
                leftover_compose_tabs=[],
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
                codes_fn=lambda _slug: {},
                sifely_fn=lambda: sifely.append("ok") or (FAKE_BACK, "sifely_current"),
                post_discord=fake.posts.append,
                send_enabled=True,
                dry_run=True,
            )
            self.assertFalse(state_path.exists())
        self.assertEqual(sifely, ["ok"])
        self.assertEqual(rows[0]["action"], "would_send")
        self.assertEqual(fake.sends, [])
        self.assertEqual(fake.posts, [])

    def test_missing_credentials_never_sends(self) -> None:
        fake = FakeSend()
        rows, state = run_process(
            fake,
            [member_thread()],
            codes_fn=lambda _slug: {},
        )
        self.assertEqual(rows[0]["action"], "missing_codes")
        self.assertEqual(fake.sends, [])
        self.assertNotIn("door_sent_at", (state.get("threads") or {}).get("chat-leana") or {})

    def test_door_then_lockbox_ladder(self) -> None:
        fake = FakeSend()
        first, state = run_process(fake, [member_thread()])
        self.assertEqual(first[0]["action"], "sent")
        self.assertEqual(first[0]["stage"], "door")
        self.assertIn(FAKE_FRONT, fake.sends[0][1])
        self.assertNotIn(FAKE_ROOM, fake.sends[0][1])

        follow = member_thread(
            host_texts=[DOOR_HOST],
            follow_up="the door still fails / code didn’t work, still locked out",
        )
        second, state = run_process(fake, [follow])
        self.assertEqual(second[0]["action"], "sent")
        self.assertEqual(second[0]["stage"], "lockbox")
        self.assertEqual(len(fake.sends), 2)
        self.assertIn(FAKE_ROOM, fake.sends[1][1])
        self.assertNotIn("Front door code", fake.sends[1][1])
        self.assertIn("lockbox_sent_at", state["threads"]["chat-leana"])

    def test_batch_sends_once(self) -> None:
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
                sifely_fn=lambda: (FAKE_BACK, "sifely_current"),
                post_discord=fake.posts.append,
                send_enabled=True,
                dry_run=False,
            )
            first = lockout_reply.process_lockouts([member_thread()], **kwargs)
            second = lockout_reply.process_lockouts([member_thread()], **kwargs)
        self.assertEqual(first[0]["action"], "sent")
        self.assertEqual(second[0]["action"], "already_sent")
        self.assertEqual(len(fake.sends), 1)


class LeakBoundaryTests(NetworkGuard):
    def test_true_emergencies(self) -> None:
        for text in (
            "pipe burst",
            "water leaking from ceiling",
            "toilet flooding",
        ):
            self.assertTrue(leak_reply.detect_leak(text), msg=text)

    def test_negation_history_question_are_false(self) -> None:
        for text in (
            "There is no flooding",
            "The pipe burst last week and is fixed now",
            "Where is the water main?",
        ):
            self.assertFalse(leak_reply.detect_leak(text), msg=text)

    def test_leak_send_uses_t5_and_is_idempotent(self) -> None:
        fake = FakeSend()
        first, _ = run_leak_process(fake, [leak_member_thread()])
        second, _ = run_leak_process(fake, [leak_member_thread()])
        self.assertEqual(first[0]["action"], "sent")
        self.assertEqual(second[0]["action"], "already_sent")
        self.assertEqual(len(fake.sends), 1)
        self.assertIn(leak_reply.LEAK_PACK_MARKER, fake.sends[0][1])
        self.assertIn(leak_reply.QUO_FIELD_PHONE, fake.sends[0][1])


class CharacterizationTests(NetworkGuard):
    """Known-risk documentation. Not acceptance criteria."""

    def test_corrupt_state_fail_open_lockout(self) -> None:
        fake = FakeSend()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text("{not-json")
            rows = lockout_reply.process_lockouts(
                [member_thread()],
                now=NOW,
                state_path=state_path,
                leftover_compose_tabs=[],
                close_tabs_fn=fake.close_tabs,
                send_fn=fake.send,
                codes_fn=lambda _slug: fake_doc(),
                post_discord=fake.posts.append,
                send_enabled=True,
                dry_run=False,
            )
        # Characterization: corrupt JSON is treated as empty and sending proceeds.
        self.assertEqual(rows[0]["action"], "sent")

    def test_discord_callback_failure_loses_checkpoint(self) -> None:
        def boom(_text: str) -> None:
            raise RuntimeError("discord down")

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            thread = leak_member_thread()
            with self.assertRaises(RuntimeError):
                leak_reply.process_leaks(
                    [thread],
                    now=datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc),
                    state_path=state_path,
                    leftover_compose_tabs=[],
                    close_tabs_fn=lambda tabs, chat_id: new_booking.close_leftover_compose_tabs(
                        tabs, chat_id
                    ),
                    send_fn=lambda chat_id, text: {"ok": True},
                    post_discord=boom,
                    send_enabled=True,
                    dry_run=False,
                    fetch_doc=lambda: firestore_t5_doc(),
                )
            saved = {}
            if state_path.exists():
                try:
                    saved = json.loads(state_path.read_text())
                except ValueError:
                    saved = {}
        # Characterization: member send already happened; checkpoint was not saved.
        threads = saved.get("threads") or {}
        self.assertNotIn("chat-leana", threads)


class StateStoreOutboxTests(NetworkGuard):
    def test_state_store_and_outbox_are_offline(self) -> None:
        from padsplit_scraper import outbox
        from padsplit_scraper import state_store

        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PADSPLIT_STATE_DIR": tmpdir}
            state_store.save_json("demo", {"ok": True}, environ=env)
            self.assertEqual(state_store.load_json("demo", environ=env)["ok"], True)
            item = outbox.enqueue("lockout", {"chat_id": "x"}, environ=env)
            self.assertEqual(len(outbox.pending(environ=env)), 1)
            self.assertTrue(outbox.mark_done(item["id"], environ=env))
            self.assertEqual(outbox.pending(environ=env), [])


if __name__ == "__main__":
    unittest.main()
