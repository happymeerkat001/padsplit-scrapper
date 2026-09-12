#!/usr/bin/env python3
"""Offline tests for flock lifetime locking (Mac + Linux)."""

import multiprocessing
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import process_lock


DEAD_PID = 999_999_999


def _legacy_rename_remove_stale(path: Path, *, before_rename=None) -> None:
    """Old check-then-rename reaper. Kept only to prove the race still exists
    in that design: a pause between the final stale check and rename lets
    another reaper retire the dir and a worker bind the canonical name;
    the delayed rename then steals that live lock.
    """
    if not process_lock.lock_is_stale(path):
        raise process_lock.LockError(f"lock not stale: {path}")
    observed = process_lock.read_lock_pid(path)
    if process_lock.read_lock_pid(path) != observed or not process_lock.lock_is_stale(path):
        raise process_lock.LockError(f"lock owner changed: {path}")
    if before_rename is not None:
        before_rename(path)
    trash = path.parent / f".{path.name}.stale-{os.getpid()}-{time.time_ns()}"
    os.rename(path, trash)


def _legacy_mkdir_acquire(path: Path) -> bool:
    """Old exclusive primitive: mkdir + pid write."""
    try:
        os.mkdir(path)
    except FileExistsError:
        return False
    (path / process_lock.PID_FILENAME).write_text(f"{os.getpid()}\n")
    return True


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
            self.assertTrue(process_lock.lock_is_held(path))
            inode = process_lock.flock_file(path).stat().st_ino
            process_lock.release_lock(held)
            self.assertTrue(process_lock.flock_file(path).exists())
            self.assertEqual(process_lock.flock_file(path).stat().st_ino, inode)
            self.assertIsNone(process_lock.read_lock_pid(path))
            self.assertFalse(process_lock.lock_is_held(path))

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
            inode_before = None
            flock = process_lock.flock_file(path)
            held = process_lock.acquire_lock("stale", directory=path)
            self.assertTrue(held.recovered_stale)
            self.assertEqual(held.pid, os.getpid())
            self.assertEqual(process_lock.read_lock_pid(path), os.getpid())
            self.assertFalse(process_lock.lock_is_stale(path))
            self.assertTrue(flock.exists())
            inode_before = flock.stat().st_ino
            process_lock.release_lock(held)
            self.assertEqual(flock.stat().st_ino, inode_before)

    def test_fresh_empty_lock_is_not_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.lock"
            path.mkdir()
            self.assertIsNone(process_lock.read_lock_pid(path))
            self.assertFalse(process_lock.lock_is_stale(path))
            # Young empty dir is not a held lock. Flock binds it; the
            # in-progress window is the flock, not a mkdir hole.
            held = process_lock.acquire_lock("empty", directory=path, timeout=0)
            self.assertTrue(process_lock.lock_is_held(path))
            process_lock.release_lock(held)

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
        """In-progress bind (flock held, pid not yet written) is not stolen."""
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

    def test_rename_reaper_interleave_cannot_double_acquire(self) -> None:
        """Deterministic interleave that produced 2 live owners on rename recovery.

        Old ``_remove_stale``: final stale check, then ``os.rename`` of the
        canonical path. Between those steps another reaper retires the stale
        dir and worker A binds the name; the delayed rename steals A's live
        lock; worker B binds the hole. Post-rename restore is too late.

        This test runs that interleave against the legacy reaper (must get
        two live binds) and the current ``acquire_lock`` (must get one).
        If rename-based recovery returns, the current path fails this test.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "reaper.lock"
            path.mkdir()
            (path / process_lock.PID_FILENAME).write_text(f"{DEAD_PID}\n")

            bound: list[str] = []
            proceed_rename = threading.Event()
            in_window = threading.Event()

            def before_rename(_lock_path: Path) -> None:
                in_window.set()
                self.assertTrue(proceed_rename.wait(2))

            def other_reaper_and_workers() -> None:
                self.assertTrue(in_window.wait(2))
                trash = path.parent / f".{path.name}.other-reaper"
                os.rename(path, trash)
                self.assertTrue(_legacy_mkdir_acquire(path), "worker A bound canonical")
                bound.append("A")
                proceed_rename.set()
                deadline = time.time() + 2
                while path.exists() and time.time() < deadline:
                    time.sleep(0.01)
                self.assertTrue(_legacy_mkdir_acquire(path), "worker B bound hole")
                bound.append("B")

            t = threading.Thread(target=other_reaper_and_workers)
            t.start()
            _legacy_rename_remove_stale(path, before_rename=before_rename)
            t.join(3)
            self.assertIn("A", bound)
            self.assertIn("B", bound)
            self.assertGreaterEqual(len(bound), 2)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "flock.lock"
            path.mkdir()
            (path / process_lock.PID_FILENAME).write_text(f"{DEAD_PID}\n")
            canonical_renames: list[tuple[str, str]] = []
            extra_binds: list[str] = []
            wins: list[process_lock.LockAcquisition] = []
            errors: list[BaseException] = []
            real_rename = os.rename

            def racing_rename(src, dst, *args, **kwargs):
                src_path = Path(src).resolve()
                if src_path in {path.resolve(), process_lock.flock_file(path).resolve()}:
                    canonical_renames.append((str(src), str(dst)))
                    real_rename(src, dst, *args, **kwargs)
                    try:
                        os.mkdir(path)
                        (path / process_lock.PID_FILENAME).write_text(f"{os.getpid()}\n")
                        extra_binds.append("post-rename")
                    except FileExistsError:
                        extra_binds.append("post-rename-occupied")
                    return None
                return real_rename(src, dst, *args, **kwargs)

            def worker() -> None:
                try:
                    wins.append(process_lock.acquire_lock("reaper", directory=path, timeout=0))
                except process_lock.LockError as exc:
                    errors.append(exc)

            with patch.object(process_lock.os, "rename", racing_rename):
                t1 = threading.Thread(target=worker)
                t2 = threading.Thread(target=worker)
                t1.start()
                t2.start()
                t1.join(3)
                t2.join(3)

            self.assertEqual(canonical_renames, [], canonical_renames)
            self.assertEqual(extra_binds, [])
            self.assertEqual(len(wins), 1, wins)
            self.assertTrue(process_lock.lock_is_held(path))
            self.assertEqual(process_lock.flock_file(path).stat().st_nlink, 1)
            process_lock.release_lock(wins[0])

    def test_context_manager_releases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ctx.lock"
            with process_lock.acquire_lock("ctx", directory=path) as held:
                self.assertTrue(process_lock.flock_file(path).exists())
                self.assertTrue(process_lock.lock_is_held(path))
                self.assertEqual(held.pid, os.getpid())
            self.assertFalse(process_lock.lock_is_held(path))
            self.assertTrue(process_lock.flock_file(path).exists())

    def test_multiprocess_overlap_and_stale_recovery(self) -> None:
        """Cross-process flock: overlap rejects; dead holder is recovered.

        ``fcntl.flock`` is the Mac+Linux kernel primitive. The flock inode
        is not unlinked, so recovery cannot open a mkdir hole.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = str(Path(tmpdir) / "mp.lock")
            ready = str(Path(tmpdir) / "ready")
            release = str(Path(tmpdir) / "release")
            child = multiprocessing.Process(
                target=_child_hold_lock,
                args=(lock_path, ready, release),
            )
            child.start()
            deadline = time.time() + 5
            while not Path(ready).exists() and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(Path(ready).exists())
            with self.assertRaises(process_lock.LockError):
                process_lock.acquire_lock("mp", directory=Path(lock_path), timeout=0)
            Path(release).write_text("go")
            child.join(5)
            self.assertEqual(child.exitcode, 0)
            inode = process_lock.flock_file(Path(lock_path)).stat().st_ino
            held = process_lock.acquire_lock("mp", directory=Path(lock_path), timeout=0)
            self.assertEqual(process_lock.flock_file(Path(lock_path)).stat().st_ino, inode)
            process_lock.release_lock(held)

            dead = multiprocessing.Process(target=_child_die_holding, args=(lock_path,))
            dead.start()
            dead.join(5)
            self.assertNotEqual(dead.exitcode, 0)
            recovered = process_lock.acquire_lock("mp", directory=Path(lock_path), timeout=1)
            self.assertTrue(recovered.recovered_stale)
            self.assertEqual(process_lock.flock_file(Path(lock_path)).stat().st_ino, inode)
            process_lock.release_lock(recovered)

    def test_cli_status_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PADSPLIT_LOCK_DIR": tmpdir}
            path = process_lock.lock_path("cli", env)
            self.assertEqual(path, Path(tmpdir) / "cli")
            status = process_lock.lock_is_stale(path)
            self.assertFalse(status)


def _child_hold_lock(lock_path: str, ready_path: str, release_path: str) -> None:
    held = process_lock.acquire_lock("mp", directory=Path(lock_path))
    Path(ready_path).write_text("ready")
    deadline = time.time() + 5
    while not Path(release_path).exists() and time.time() < deadline:
        time.sleep(0.05)
    process_lock.release_lock(held)


def _child_die_holding(lock_path: str) -> None:
    process_lock.acquire_lock("mp", directory=Path(lock_path))
    os._exit(17)


if __name__ == "__main__":
    unittest.main()
