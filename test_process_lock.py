#!/usr/bin/env python3
"""Offline tests for stale-PID directory lock recovery."""

import os
import tempfile
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

    def test_missing_pid_file_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.lock"
            path.mkdir()
            self.assertIsNone(process_lock.read_lock_pid(path))
            self.assertTrue(process_lock.lock_is_stale(path))
            held = process_lock.acquire_lock("empty", directory=path)
            self.assertTrue(held.recovered_stale)
            process_lock.release_lock(held)

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
