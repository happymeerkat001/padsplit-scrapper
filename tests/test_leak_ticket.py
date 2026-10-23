#!/usr/bin/env python3
"""Triage-only maintenance-ticket leak alerts. No live Discord, no tenant sends."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import padsplit_scraper.persist as persist
import padsplit_scraper.scraper as scraper
from padsplit_scraper import leak_alert
from padsplit_scraper import leak_reply
from padsplit_scraper import leak_ticket


FIXTURE_TEXT = "Location: Bathroom, Private / Details: I think it is leaked"
PLACEHOLDER_NAME = "Placeholder Name"
PLACEHOLDER_PHONE = "555-010-0199"


def _env(**overrides: str) -> dict:
    base = {
        "CI": "",
        "GITHUB_ACTIONS": "",
        "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
        "PADSPLIT_COLLECTION_ONLY": "0",
        "LEAK_TICKET_ALERT_ENABLE": "1",
        "LEAK_ALERT_DRY_RUN": "0",
    }
    base.update(overrides)
    return base


def leaked_ticket(**overrides: object) -> dict:
    ticket = {
        "id": 4242,
        "status": "submitted",
        "location": "Bathroom, Private",
        "details": "I think it is leaked",
        "room_number": 2,
        "property_address": {"street1": "100 Placeholder Ave"},
        "reported_by": PLACEHOLDER_NAME,
        "phone": PLACEHOLDER_PHONE,
        "author": {"name": PLACEHOLDER_NAME, "phone": PLACEHOLDER_PHONE},
    }
    ticket.update(overrides)
    return ticket


class DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _recent_chat() -> list:
    from datetime import datetime, timezone

    return [
        {
            "id": "chat-1",
            "lastMessage": {
                "created": datetime.now(timezone.utc).isoformat(),
                "text": "Need help",
            },
        }
    ]


class DetectTests(unittest.TestCase):
    def test_placeholder_fixture_matches_water(self) -> None:
        self.assertEqual(leak_ticket.ticket_leak_text(leaked_ticket()), FIXTURE_TEXT)
        self.assertTrue(leak_ticket.detect_ticket_leak(FIXTURE_TEXT))
        self.assertEqual(leak_alert.leak_category(FIXTURE_TEXT), "water")
        self.assertFalse(leak_reply.detect_leak("I think it is leaked"))
        self.assertFalse(leak_reply.detect_leak(FIXTURE_TEXT))

    def test_resolved_wording_does_not_match(self) -> None:
        self.assertFalse(leak_ticket.detect_ticket_leak("no leak anymore, fixed"))
        self.assertFalse(
            leak_ticket.detect_ticket_leak("The pipe burst last week and is fixed now")
        )


class AlertTests(unittest.TestCase):
    def _run(self, tasks, env, posts, tmp: Path):
        return leak_ticket.run_for_scraper(
            tasks,
            environ=env,
            state_path=tmp / "leak_ticket_state.json",
            preview_path=tmp / "leak_ticket_preview.jsonl",
            post_discord=posts.append,
        )

    def test_same_ticket_id_posts_once_and_body_has_no_identity(self) -> None:
        posts: list = []
        tasks = {"Requests": [leaked_ticket(), leaked_ticket()]}
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            first = self._run(tasks, _env(), posts, tmp)
            second = self._run(tasks, _env(), posts, tmp)
            state = json.loads((tmp / "leak_ticket_state.json").read_text())
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(posts), 1)
        body = posts[0]
        self.assertIn("LEAK_TICKET", body)
        self.assertIn('house="Placeholder Ave"', body)
        self.assertIn("room=2", body)
        self.assertIn("category=water", body)
        self.assertIn("ticket=4242", body)
        self.assertNotIn(PLACEHOLDER_NAME, body)
        self.assertNotIn(PLACEHOLDER_PHONE, body)
        self.assertNotIn("5550100199", body)
        self.assertNotIn("I think it is leaked", body)
        self.assertNotIn("Bathroom", body)
        self.assertNotIn(PLACEHOLDER_NAME, json.dumps(state))
        self.assertNotIn(PLACEHOLDER_PHONE, json.dumps(state))
        leak_ticket.assert_leak_ticket_safe(body, ticket_id="4242", room="2")

    def test_flag_off_or_ci_gives_no_post(self) -> None:
        off = _env()
        off.pop("LEAK_TICKET_ALERT_ENABLE")
        cases = {
            "unset": off,
            "explicit_off": _env(LEAK_TICKET_ALERT_ENABLE="0"),
            "ci": _env(CI="true"),
            "github_actions": _env(GITHUB_ACTIONS="true"),
        }
        for label, env in cases.items():
            posts: list = []
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                rows = self._run({"Requests": [leaked_ticket()]}, env, posts, tmp)
                self.assertEqual(posts, [], msg=label)
                self.assertEqual(rows, [], msg=label)
                self.assertFalse((tmp / "leak_ticket_state.json").exists(), msg=label)
                self.assertFalse((tmp / "leak_ticket_preview.jsonl").exists(), msg=label)

    def test_resolved_ticket_and_closed_status_do_not_post(self) -> None:
        posts: list = []
        tasks = {
            "Requests": [
                leaked_ticket(id=4301, details="no leak anymore, fixed"),
            ],
            "In Progress": [leaked_ticket(id=4302, status="in_progress")],
            "Complete": [leaked_ticket(id=4303, status="completed")],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            rows = self._run(tasks, _env(), posts, Path(tmpdir))
        self.assertEqual(rows, [])
        self.assertEqual(posts, [])

    def test_dry_run_default_writes_preview_once_and_does_not_post(self) -> None:
        posts: list = []
        env = _env()
        env.pop("LEAK_ALERT_DRY_RUN")
        tasks = {"Open": [leaked_ticket(status="accepted")]}
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            first = self._run(tasks, env, posts, tmp)
            second = self._run(tasks, env, posts, tmp)
            preview = (tmp / "leak_ticket_preview.jsonl").read_text()
        self.assertEqual(posts, [])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["action"], "preview")
        self.assertEqual(second, [])
        self.assertEqual(preview.count("\n"), 1)
        self.assertIn("LEAK_TICKET", preview)
        self.assertIn("category=water", preview)
        self.assertNotIn(PLACEHOLDER_NAME, preview)
        self.assertNotIn(PLACEHOLDER_PHONE, preview)
        self.assertNotIn("5550100199", preview)

    def test_missing_discord_post_does_not_consume_the_ticket(self) -> None:
        tasks = {"Requests": [leaked_ticket()]}
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch.object(leak_reply, "post_automations_discord", return_value=None):
                first = leak_ticket.run_for_scraper(
                    tasks,
                    environ=_env(),
                    state_path=tmp / "leak_ticket_state.json",
                    preview_path=tmp / "leak_ticket_preview.jsonl",
                )
            posts: list = []
            second = self._run(tasks, _env(), posts, tmp)
        self.assertEqual(first, [])
        self.assertEqual(len(second), 1)
        self.assertEqual(posts, [second[0]["body"]])

    def test_default_poster_is_the_automations_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch.object(leak_reply, "post_automations_discord") as post:
                rows = leak_ticket.run_for_scraper(
                    {"Requests": [leaked_ticket()]},
                    environ=_env(),
                    state_path=tmp / "state.json",
                    preview_path=tmp / "preview.jsonl",
                )
        self.assertEqual(len(rows), 1)
        post.assert_called_once()
        body = post.call_args.args[0]
        guard = post.call_args.kwargs["guard"]
        self.assertEqual(guard(body), body)
        self.assertIn("LEAK_TICKET", body)
        with self.assertRaises(RuntimeError):
            guard(body + " " + PLACEHOLDER_PHONE)

    def test_extra_digits_are_refused(self) -> None:
        body = leak_ticket.format_leak_ticket_text(
            house="Placeholder Ave",
            room="2",
            category="water",
            ticket_id="4242",
            ticket=leaked_ticket(),
        )
        with self.assertRaises(RuntimeError):
            leak_ticket.assert_leak_ticket_safe(
                body + " 555-010-0199",
                ticket_id="4242",
                room="2",
            )

    def test_does_not_send_the_tenant_pack(self) -> None:
        source = (ROOT / "padsplit_scraper" / "leak_ticket.py").read_text().lower()
        for banned in (
            "gmail",
            "leak_alert_bland",
            "process_leaks",
            "send_host_message",
            "format_leak_body",
            "quo_sender",
        ):
            self.assertNotIn(banned, source)
        posts: list = []
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(leak_reply, "process_leaks") as process:
                self._run({"Requests": [leaked_ticket()]}, _env(), posts, Path(tmpdir))
        process.assert_not_called()
        self.assertEqual(len(posts), 1)


class ScraperHookTests(unittest.TestCase):
    def test_hook_list_passes_tasks_after_fetch(self) -> None:
        self.assertEqual(
            scraper._ACTION_HOOKS,
            (
                ("new_booking", "run_for_scraper"),
                ("lockout_reply", "run_for_scraper"),
                ("leak_reply", "run_for_scraper"),
                ("leak_ticket", "run_for_scraper"),
            ),
        )
        tasks = {"Requests": [{"id": 1, "status": "submitted"}]}
        message_modules: list = []
        task_calls: list = []

        def invoke(module_name, func_name, session, creds, messages):
            message_modules.append((module_name, func_name))

        def task_hook(module_name, func_name, fetched):
            task_calls.append((module_name, func_name, fetched))

        env = {
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
            "PADSPLIT_COLLECTION_ONLY": "0",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            docs_data_dir = Path(tmpdir) / "docs" / "data"
            with (
                patch.dict("os.environ", env, clear=False),
                patch.object(persist, "OUTPUT_DIR", output_dir),
                patch.object(persist, "DOCS_DATA_DIR", docs_data_dir),
                patch.object(scraper, "load_credentials", return_value={"email": "user", "password": "pw"}),
                patch.object(scraper, "create_session", return_value=DummySession()),
                patch.object(scraper, "login"),
                patch.object(scraper, "fetch_messages", return_value=_recent_chat()),
                patch.object(scraper, "fetch_thread_messages", return_value=[{"id": "message-1"}]),
                patch.object(scraper, "fetch_tasks", return_value=tasks),
                patch.object(scraper, "fetch_rooms", return_value=[{"id": 1}]),
                patch.object(scraper, "fetch_properties_stats", return_value=[{"id": 9}]),
                patch.object(scraper, "fetch_earnings", return_value={"results": []}),
                patch.object(
                    scraper,
                    "compute_kpis",
                    return_value={
                        "score": 92,
                        "avg_flip_days": 2.0,
                        "occupancy_pct": 95.0,
                        "avg_tenure_days": 180.0,
                        "bonuses": [],
                        "penalties": [],
                    },
                ),
                patch.object(
                    scraper,
                    "fetch_performance_history",
                    return_value={"2026-05": {"avg_flip_days": 2.0, "occupancy_pct": 95.0, "avg_tenure_days": 180.0}},
                ),
                patch.object(scraper, "_invoke_action_hook", side_effect=invoke),
                patch.object(scraper, "_invoke_task_hook", side_effect=task_hook),
            ):
                exit_code = scraper.main([])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            message_modules,
            [
                ("new_booking", "run_for_scraper"),
                ("lockout_reply", "run_for_scraper"),
                ("leak_reply", "run_for_scraper"),
            ],
        )
        self.assertEqual(task_calls, [("leak_ticket", "run_for_scraper", tasks)])

    def test_invoke_task_hook_calls_run_for_scraper_with_tasks_only(self) -> None:
        tasks = {"Requests": [leaked_ticket()]}
        with patch("padsplit_scraper.leak_ticket.run_for_scraper") as run:
            scraper._invoke_task_hook("leak_ticket", "run_for_scraper", tasks)
        run.assert_called_once_with(tasks)


if __name__ == "__main__":
    unittest.main()
