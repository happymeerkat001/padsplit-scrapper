#!/usr/bin/env python3
"""Offline fixture tests for the collection-only job runner."""

from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from padsplit_scraper import job_runner
from padsplit_scraper import persist
from padsplit_scraper import runtime
from padsplit_scraper import scraper

_DEFAULT_OUTPUT = Path(persist.OUTPUT_DIR)
_DEFAULT_DOCS = Path(persist.DOCS_DATA_DIR)


class DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def recent_chat() -> list[dict]:
    return [
        {
            "id": "chat-1",
            "lastMessage": {
                "created": datetime.now(timezone.utc).isoformat(),
                "text": "Need help",
            },
        }
    ]


def sample_kpis(score: int = 92) -> dict:
    return {
        "score": score,
        "avg_flip_days": 2.0,
        "occupancy_pct": 95.0,
        "avg_tenure_days": 180.0,
        "bonuses": [{"label": "occupancy >= 90%", "points": 20}],
        "penalties": [],
    }


def _dotenv_enables_hooks(path=None, *args, **kwargs):
    """Simulate a .env that turns action hooks on after the runner started."""
    os.environ["PADSPLIT_ENABLE_ACTION_HOOKS"] = "1"
    os.environ["PADSPLIT_EMAIL"] = "user"
    os.environ["PADSPLIT_PASSWORD"] = "pw"
    return True


def _fake_ok_run(messages_only=False, *, isolate_output=False):
    return 0


def _fake_fail_run(messages_only=False, *, isolate_output=False):
    raise RuntimeError("worker boom")


def _child_runner(lock_path: str, output_dir: str, ready_path: str, release_path: str) -> None:
    def hold_run(messages_only=False, *, isolate_output=False):
        Path(ready_path).write_text("ready")
        deadline = time.time() + 5
        while not Path(release_path).exists() and time.time() < deadline:
            time.sleep(0.05)
        return 0

    env = {
        "CI": "",
        "GITHUB_ACTIONS": "",
        "PADSPLIT_OUTPUT_DIR": output_dir,
        "PADSPLIT_COLLECTION_ONLY": "1",
    }
    with patch.dict("os.environ", env, clear=False):
        job_runner.run_collection(
            run_fn=hold_run,
            lock_directory=Path(lock_path),
            environ=env,
        )


def _scraper_io_patches(*, earnings_error=None):
    earnings_patch = (
        patch.object(scraper, "fetch_earnings", side_effect=earnings_error)
        if earnings_error is not None
        else patch.object(scraper, "fetch_earnings", return_value={"results": []})
    )
    return (
        patch.object(scraper, "load_dotenv", side_effect=_dotenv_enables_hooks),
        patch.object(scraper, "create_session", return_value=DummySession()),
        patch.object(scraper, "login"),
        patch.object(scraper, "fetch_messages", return_value=recent_chat()),
        patch.object(scraper, "fetch_thread_messages", return_value=[{"id": "message-1"}]),
        patch.object(scraper, "fetch_tasks", return_value={"Requests": [{"id": 1, "status": "submitted"}]}),
        patch.object(scraper, "fetch_rooms", return_value=[{"id": 1}]),
        patch.object(scraper, "fetch_properties_stats", return_value=[{"id": 9}]),
        earnings_patch,
        patch.object(scraper, "compute_kpis", return_value=sample_kpis(92)),
        patch.object(
            scraper,
            "fetch_performance_history",
            return_value={"2026-05": {"avg_flip_days": 2.0, "occupancy_pct": 95.0, "avg_tenure_days": 180.0}},
        ),
        patch.object(
            scraper,
            "_invoke_action_hook",
            side_effect=AssertionError("action hook must not run"),
        ),
        patch("subprocess.run", side_effect=AssertionError("git must not run")),
    )


class JobRunnerTests(unittest.TestCase):
    def tearDown(self) -> None:
        persist.configure_output_dirs(output_dir=_DEFAULT_OUTPUT, docs_data_dir=_DEFAULT_DOCS)

    def test_collection_runner_real_scraper_ignores_ambient_dotenv_and_injected_hooks(self) -> None:
        """Runner + real scraper.run. Ambient and dotenv enable hooks; injected env disagrees.

        Policy must still force collection-only. Mocking scraper.run itself cannot
        catch this; I/O is mocked, the worker is not.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "iso"
            live_docs = Path(tmpdir) / "docs" / "data"
            live_docs.mkdir(parents=True)
            ambient = {
                "CI": "",
                "GITHUB_ACTIONS": "",
                "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
                "LOCKOUT_REPLY_ENABLE": "1",
                "LEAK_REPLY_ENABLE": "1",
            }
            injected = {
                "CI": "",
                "GITHUB_ACTIONS": "",
                "PADSPLIT_ENABLE_ACTION_HOOKS": "0",
                "PADSPLIT_OUTPUT_DIR": str(output),
                "PADSPLIT_GIT_PUBLISH": "1",
                "LOCKOUT_REPLY_ENABLE": "1",
                "LEAK_REPLY_ENABLE": "1",
            }
            seen: dict = {}
            real_hooks = scraper._run_action_hooks

            def wrap_hooks(session, creds, messages, policy=None):
                seen["ambient_would_enable"] = runtime.action_hooks_enabled()
                seen["injected_would_enable"] = runtime.action_hooks_enabled(injected)
                seen["policy"] = policy
                seen["policy_allow_hooks"] = getattr(policy, "allow_hooks", None)
                return real_hooks(session, creds, messages, policy=policy)

            with ExitStack() as stack:
                stack.enter_context(patch.dict("os.environ", ambient, clear=False))
                stack.enter_context(patch.object(scraper, "_run_action_hooks", side_effect=wrap_hooks))
                for cm in _scraper_io_patches():
                    stack.enter_context(cm)
                before_output = Path(persist.OUTPUT_DIR)
                before_docs = Path(persist.DOCS_DATA_DIR)
                result = job_runner.run_collection(
                    environ=injected,
                    lock_directory=Path(tmpdir) / "collection.lock",
                )

            self.assertEqual(result["action"], "ok")
            self.assertEqual(result["exit_code"], 0)
            self.assertFalse(result["git_published"])
            self.assertFalse(result["hooks"])
            self.assertEqual(result["output_dir"], str(output))
            self.assertTrue(seen.get("ambient_would_enable"), "dotenv/ambient must enable hooks without policy")
            self.assertFalse(seen.get("injected_would_enable"))
            self.assertIs(seen.get("policy"), runtime.COLLECTION_ONLY_POLICY)
            self.assertFalse(seen.get("policy_allow_hooks"))
            self.assertEqual(persist.OUTPUT_DIR, before_output)
            self.assertEqual(persist.DOCS_DATA_DIR, before_docs)
            self.assertFalse(any(live_docs.iterdir()))
            latest = json.loads((output / "latest.json").read_text())
            self.assertEqual(latest["run_status"]["state"], "ok")
            self.assertEqual(latest["run_status"]["sources"]["messages"]["state"], "ok")

    def test_earnings_failure_with_fallback_is_degraded_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "iso"
            output.mkdir(parents=True)
            prior_stats = {
                "scraped_at": "2026-05-01T12:00:00Z",
                "rooms": [{"id": 88}],
                "properties": [{"id": 99}],
                "earnings": [{"month": "2026-05", "net_amount": 1234}],
                "kpis": {"score": 77},
                "run_status": {
                    "state": "ok",
                    "mode": "full",
                    "run_scraped_at": "2026-05-01T12:00:00Z",
                    "last_complete_success": "2026-05-01T12:00:00Z",
                    "host": "stale-host",
                },
            }
            (output / "stats.json").write_text(json.dumps(prior_stats, indent=2))
            ambient = {
                "CI": "",
                "GITHUB_ACTIONS": "",
                "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
            }
            injected = {
                "CI": "",
                "GITHUB_ACTIONS": "",
                "PADSPLIT_ENABLE_ACTION_HOOKS": "0",
                "PADSPLIT_OUTPUT_DIR": str(output),
            }
            before_output = Path(persist.OUTPUT_DIR)
            with ExitStack() as stack:
                stack.enter_context(patch.dict("os.environ", ambient, clear=False))
                for cm in _scraper_io_patches(
                    earnings_error=requests.exceptions.ConnectionError("earnings down"),
                ):
                    stack.enter_context(cm)
                result = job_runner.run_collection(
                    environ=injected,
                    lock_directory=Path(tmpdir) / "lock",
                )

            self.assertEqual(result["action"], "degraded")
            self.assertEqual(result["exit_code"], persist.DEGRADED_EXIT_CODE)
            self.assertNotEqual(result["action"], "ok")
            self.assertEqual(persist.OUTPUT_DIR, before_output)
            status = result["run_status"]
            self.assertEqual(status["state"], "degraded")
            self.assertEqual(status["failed_phase"], "earnings_stats")
            self.assertTrue(status["fallback_used"])
            self.assertNotEqual(status["run_scraped_at"], "2026-05-01T12:00:00Z")
            self.assertGreaterEqual(status["run_scraped_at"], status["started_at"])
            self.assertGreaterEqual(status["finished_at"], status["started_at"])
            self.assertTrue(status["host"])
            self.assertIn("release", status["omissions"])
            self.assertEqual(status["last_complete_success"], "2026-05-01T12:00:00Z")
            self.assertEqual(status["sources"]["messages"]["state"], "ok")
            self.assertEqual(status["sources"]["messages"]["scraped_at"], status["run_scraped_at"])
            self.assertEqual(status["sources"]["stats"]["state"], "degraded")
            self.assertEqual(status["sources"]["stats"]["scraped_at"], "2026-05-01T12:00:00Z")
            self.assertGreaterEqual(status["counts"]["messages"], 1)
            stats_payload = json.loads((output / "stats.json").read_text())
            self.assertEqual(stats_payload["scraped_at"], "2026-05-01T12:00:00Z")
            self.assertEqual(stats_payload["run_status"]["state"], "degraded")
            self.assertEqual(stats_payload["run_status"]["run_scraped_at"], status["run_scraped_at"])
            self.assertNotEqual(stats_payload["run_status"].get("host"), "stale-host")

    def test_earnings_failure_without_fallback_is_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "iso"
            ambient = {"CI": "", "GITHUB_ACTIONS": "", "PADSPLIT_ENABLE_ACTION_HOOKS": "1"}
            injected = {
                "CI": "",
                "GITHUB_ACTIONS": "",
                "PADSPLIT_ENABLE_ACTION_HOOKS": "0",
                "PADSPLIT_OUTPUT_DIR": str(output),
            }
            before_output = Path(persist.OUTPUT_DIR)
            with ExitStack() as stack:
                stack.enter_context(patch.dict("os.environ", ambient, clear=False))
                for cm in _scraper_io_patches(
                    earnings_error=requests.exceptions.ConnectionError("earnings down"),
                ):
                    stack.enter_context(cm)
                result = job_runner.run_collection(
                    environ=injected,
                    lock_directory=Path(tmpdir) / "lock",
                )

            self.assertEqual(result["action"], "degraded")
            self.assertEqual(result["exit_code"], persist.DEGRADED_EXIT_CODE)
            self.assertEqual(persist.OUTPUT_DIR, before_output)
            status = result["run_status"]
            self.assertEqual(status["state"], "degraded")
            self.assertEqual(status["failed_phase"], "earnings_stats")
            self.assertFalse(status["fallback_used"])
            self.assertNotIn("last_complete_success", status)
            self.assertEqual(status["sources"]["messages"]["state"], "ok")
            self.assertEqual(status["sources"]["messages"]["scraped_at"], status["run_scraped_at"])
            self.assertIsNone(status["sources"]["stats"].get("scraped_at"))
            self.assertFalse((output / "stats.json").exists())
            latest = json.loads((output / "latest.json").read_text())
            self.assertEqual(latest["run_status"]["state"], "degraded")
            self.assertEqual(latest["run_status"]["run_scraped_at"], status["run_scraped_at"])

    def test_stale_prior_run_health_is_rejected(self) -> None:
        previous = {
            "scraped_at": "2026-09-12T20:00:00Z",
            "run_status": {
                "state": "ok",
                "run_scraped_at": "2026-09-12T21:00:00Z",
                "last_complete_success": "2026-09-12T21:00:00Z",
            },
        }
        self.assertIsNone(
            persist.prior_last_complete_success(previous, this_run_scraped_at="2026-09-12T20:00:00Z")
        )
        older = {
            "scraped_at": "2026-05-01T12:00:00Z",
            "run_status": {
                "state": "ok",
                "run_scraped_at": "2026-05-01T12:00:00Z",
                "last_complete_success": "2026-04-01T00:00:00Z",
            },
        }
        self.assertEqual(
            persist.prior_last_complete_success(older, this_run_scraped_at="2026-09-12T20:00:00Z"),
            "2026-04-01T00:00:00Z",
        )

    def test_ci_and_legacy_flags_do_not_enable_hooks(self) -> None:
        env = {
            "CI": "true",
            "LOCKOUT_REPLY_ENABLE": "1",
            "LEAK_REPLY_ENABLE": "true",
            "PADSPLIT_ENABLE_ACTION_HOOKS": "1",
        }
        self.assertTrue(runtime.collection_only(env))
        self.assertFalse(runtime.action_hooks_enabled(env))
        self.assertFalse(runtime.send_enabled("lockout", env))

    def test_messages_only_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            seen = {}

            def capture(messages_only=False, *, isolate_output=False):
                seen["messages_only"] = messages_only
                return 0

            env = {"CI": "", "GITHUB_ACTIONS": "", "PADSPLIT_OUTPUT_DIR": tmpdir}
            before = Path(persist.OUTPUT_DIR)
            result = job_runner.run_collection(
                messages_only=True,
                run_fn=capture,
                environ=env,
                lock_directory=Path(tmpdir) / "lock",
            )
            self.assertEqual(result["action"], "ok")
            self.assertTrue(seen["messages_only"])
            self.assertEqual(persist.OUTPUT_DIR, before)

    def test_failure_releases_lock_for_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock = Path(tmpdir) / "lock"
            env = {"CI": "", "GITHUB_ACTIONS": "", "PADSPLIT_OUTPUT_DIR": tmpdir}
            failed = job_runner.run_collection(
                run_fn=_fake_fail_run,
                environ=env,
                lock_directory=lock,
            )
            self.assertEqual(failed["action"], "failed")
            self.assertFalse(lock.exists())
            restarted = job_runner.run_collection(
                run_fn=_fake_ok_run,
                environ=env,
                lock_directory=lock,
            )
            self.assertEqual(restarted["action"], "ok")

    def test_overlap_second_worker_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            lock = str(Path(tmpdir) / "lock")
            ready = str(Path(tmpdir) / "ready")
            release = str(Path(tmpdir) / "release")
            output = str(Path(tmpdir) / "out")
            child = multiprocessing.Process(
                target=_child_runner,
                args=(lock, output, ready, release),
            )
            child.start()
            deadline = time.time() + 5
            while not Path(ready).exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(Path(ready).exists())
            env = {"CI": "", "GITHUB_ACTIONS": "", "PADSPLIT_OUTPUT_DIR": output}
            skipped = job_runner.run_collection(
                run_fn=_fake_ok_run,
                environ=env,
                lock_directory=Path(lock),
            )
            self.assertEqual(skipped["action"], "skipped_lock")
            Path(release).write_text("go")
            child.join(5)
            self.assertEqual(child.exitcode, 0)
            again = job_runner.run_collection(
                run_fn=_fake_ok_run,
                environ=env,
                lock_directory=Path(lock),
            )
            self.assertEqual(again["action"], "ok")


if __name__ == "__main__":
    unittest.main()
