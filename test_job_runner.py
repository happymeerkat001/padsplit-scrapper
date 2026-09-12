#!/usr/bin/env python3
"""Offline fixture tests for the collection-only job runner."""

from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import job_runner
from padsplit_scraper import persist
from padsplit_scraper import runtime

_DEFAULT_OUTPUT = Path(persist.OUTPUT_DIR)
_DEFAULT_DOCS = Path(persist.DOCS_DATA_DIR)


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


class JobRunnerTests(unittest.TestCase):
    def tearDown(self) -> None:
        persist.configure_output_dirs(output_dir=_DEFAULT_OUTPUT, docs_data_dir=_DEFAULT_DOCS)

    def test_zero_outgoing_hooks_and_output_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "iso"
            live_docs = Path(tmpdir) / "docs" / "data"
            live_docs.mkdir(parents=True)
            env = {
                "CI": "",
                "GITHUB_ACTIONS": "",
                "PADSPLIT_OUTPUT_DIR": str(output),
                "PADSPLIT_COLLECTION_ONLY": "1",
                "LOCKOUT_REPLY_ENABLE": "1",
                "LEAK_REPLY_ENABLE": "1",
                "PADSPLIT_GIT_PUBLISH": "1",
            }
            with (
                patch.dict("os.environ", env, clear=False),
                patch.object(job_runner.scraper, "run", side_effect=_fake_ok_run) as run_mock,
                patch.object(job_runner.scraper, "_invoke_action_hook", side_effect=AssertionError("hook")),
                patch("subprocess.run", side_effect=AssertionError("git must not run")),
            ):
                result = job_runner.run_collection(
                    environ=env,
                    lock_directory=Path(tmpdir) / "collection.lock",
                )
            self.assertEqual(result["action"], "ok")
            self.assertFalse(result["git_published"])
            self.assertFalse(result["hooks"])
            self.assertEqual(result["output_dir"], str(output))
            self.assertEqual(persist.OUTPUT_DIR, output)
            self.assertTrue(str(persist.DOCS_DATA_DIR).startswith(str(output)))
            self.assertFalse(any(live_docs.iterdir()))
            run_mock.assert_called_once()

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
            result = job_runner.run_collection(
                messages_only=True,
                run_fn=capture,
                environ=env,
                lock_directory=Path(tmpdir) / "lock",
            )
            self.assertEqual(result["action"], "ok")
            self.assertTrue(seen["messages_only"])
            self.assertEqual(persist.OUTPUT_DIR, Path(tmpdir))

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
