#!/usr/bin/env python3
"""Offline tests for stale-PID directory lock recovery."""

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from padsplit_scraper import process_lock


DEAD_PID = 999_999_999


class ProcessLockTests(unittest.TestCase):
    def test_acquire_release_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "job.lock"
            held = process_lock.acquire_lock("job", directory=path)
            self.assertEqual(held.path, path)
            self.assertEqual(held.pid, os.getpid())
            self.assertFalse(held.recovered_stale)
            self.assertEqual(process_lock.read_lock_pid(path), os.getpid())
            self.assertFalse(process_lock.lock_is_stale(path))
            process_lock.release_lock(held)
            self.assertFalse(path.exists())

    def test_busy_lock_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "busy.lock"
            held = process_lock.acquire_lock("busy", directory=path)
            with self.assertRaises(process_lock.LockError):
                process_lock.acquire_lock("busy", directory=path, timeout=0)
            process_lock.release_lock(held)

    def test_stale_pid_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stale.lock"
            path.mkdir()
            (path / process_lock.PID_FILENAME).write_text(f"{DEAD_PID}\n")
            self.assertEqual(process_lock.read_lock_pid(path), DEAD_PID)
            self.assertTrue(process_lock.lock_is_stale(path))
            held = process_lock.acquire_lock("stale", directory=path)
            self.assertTrue(held.recovered_stale)
            self.assertEqual(held.pid, os.getpid())
            self.assertEqual(process_lock.read_lock_pid(path), os.getpid())
            self.assertFalse(process_lock.lock_is_stale(path))
            process_lock.release_lock(held)

    def test_fresh_empty_lock_is_not_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.lock"
            path.mkdir()
            self.assertIsNone(process_lock.read_lock_pid(path))
            self.assertFalse(process_lock.lock_is_stale(path))
            with self.assertRaises(process_lock.LockError):
                process_lock.acquire_lock("empty", directory=path, timeout=0)

    def test_aged_empty_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.lock"
            path.mkdir()
            aged = time.time() - process_lock.STALE_EMPTY_SECONDS - 1
            os.utime(path, (aged, aged))
            self.assertTrue(process_lock.lock_is_stale(path))
            held = process_lock.acquire_lock("empty", directory=path)
            self.assertTrue(held.recovered_stale)
            process_lock.release_lock(held)

    def test_mkdir_pid_race_cannot_double_acquire(self) -> None:
        """Regression: empty dir between mkdir and pid write must not be stolen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "race.lock"
            started = threading.Event()
            release = threading.Event()
            wins: list[process_lock.LockAcquisition] = []
            errors: list[BaseException] = []

            def pause_after_mkdir(_lock_path: Path) -> None:
                started.set()
                release.wait(2)

            def first() -> None:
                try:
                    wins.append(
                        process_lock.acquire_lock(
                            "race",
                            directory=path,
                            after_mkdir=pause_after_mkdir,
                        )
                    )
                except BaseException as exc:  # noqa: BLE001 — collect for assertion
                    errors.append(exc)

            def second() -> None:
                self.assertTrue(started.wait(2))
                try:
                    wins.append(process_lock.acquire_lock("race", directory=path, timeout=0))
                except process_lock.LockError as exc:
                    errors.append(exc)
                finally:
                    release.set()

            t1 = threading.Thread(target=first)
            t2 = threading.Thread(target=second)
            t1.start()
            t2.start()
            t1.join(3)
            t2.join(3)
            self.assertEqual(len(wins), 1, wins)
            self.assertTrue(any(isinstance(exc, process_lock.LockError) for exc in errors))
            self.assertEqual(process_lock.read_lock_pid(path), os.getpid())
            process_lock.release_lock(wins[0])

    def test_context_manager_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ctx.lock"
            with process_lock.acquire_lock("ctx", directory=path) as held:
                self.assertTrue(path.exists())
                self.assertEqual(held.pid, os.getpid())
            self.assertFalse(path.exists())

    def test_cli_status_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PADSPLIT_LOCK_DIR": tmpdir}
            path = process_lock.lock_path("cli", env)
            self.assertEqual(path, Path(tmpdir) / "cli")
            status = process_lock.lock_is_stale(path)
            self.assertFalse(status)


if __name__ == "__main__":
    unittest.main()
