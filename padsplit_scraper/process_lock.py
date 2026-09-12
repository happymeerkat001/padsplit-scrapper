"""Kernel flock lock on a stable inode that is never unlinked.

Exclusivity is ``fcntl.flock`` on ``<lock-dir>/flock`` (Mac + Linux).
The flock file is created once and left in place: release unlocks and
closes the fd but does not rename or unlink that inode. Stale owners
are recovered by the kernel (flock drops when the holder exits), not
by check-then-rename of the canonical path.

Same-process / same-thread re-entry is rejected via an in-process
registry. A second open+LOCK_UN must not be used to drop another
holder's flock (BSD releases LOCK_UN on any descriptor).
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

try:
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/process_lock.py
    import runtime  # type: ignore


PID_FILENAME = "pid"
FLOCK_FILENAME = "flock"
# Empty/missing PID is not immediately stale for status. A concurrent
# acquirer may have the flock and not yet have written its pid.
STALE_EMPTY_SECONDS = 2.0

_REGISTRY_GUARD = threading.Lock()
_HELD: Dict[str, int] = {}


class LockError(Exception):
    """Raised when a lock cannot be acquired or released."""


@dataclass(frozen=True)
class LockAcquisition:
    path: Path
    name: str
    pid: int
    recovered_stale: bool = False
    fd: int = -1

    def __enter__(self) -> "LockAcquisition":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        release_lock(self)
        return False


def lock_path(name: str, environ: Optional[os._Environ[str]] = None, *, directory: Optional[Path] = None) -> Path:
    if directory is not None:
        return Path(directory)
    return runtime.lock_dir(name, environ)


def flock_file(path: Path) -> Path:
    """Stable lock inode. Never unlinked for the life of the lock name."""
    return Path(path) / FLOCK_FILENAME


def _registry_key(path: Path) -> str:
    return os.path.realpath(str(path))


def read_lock_pid(path: Path) -> Optional[int]:
    pid_file = Path(path) / PID_FILENAME
    if not pid_file.is_file():
        return None
    try:
        raw = pid_file.read_text().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        pid = int(raw.split()[0])
    except ValueError:
        return None
    if pid <= 0:
        return None
    return pid


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_age_seconds(path: Path) -> Optional[float]:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _flock_busy_errno(exc: OSError) -> bool:
    return exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK)


def lock_is_held(path: Path) -> bool:
    """True when this process or another process holds the flock."""
    lock = Path(path)
    key = _registry_key(lock)
    with _REGISTRY_GUARD:
        if key in _HELD:
            return True
    node = flock_file(lock)
    if not node.is_file():
        return False
    try:
        fd = os.open(str(node), os.O_RDWR)
    except OSError:
        return False
    held = False
    unlocked_probe = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        unlocked_probe = True
    except OSError as exc:
        held = _flock_busy_errno(exc)
    finally:
        if unlocked_probe:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass
    return held


def lock_is_stale(path: Path, *, empty_grace: float = STALE_EMPTY_SECONDS) -> bool:
    """True when a leftover lock name exists and no live owner holds it.

    A held flock is never stale. Dead PIDs without a flock (process
    crashed; kernel already dropped the lock) are stale. A missing PID
    is treated as in-progress for ``empty_grace`` seconds.
    """
    lock = Path(path)
    if not lock.exists():
        return False
    if lock_is_held(lock):
        return False
    pid = read_lock_pid(lock)
    if pid is None:
        age = _lock_age_seconds(lock)
        if age is None:
            return False
        return age > max(0.0, float(empty_grace))
    return not _pid_alive(pid)


def _write_pid(path: Path, pid: int) -> None:
    pid_path = Path(path) / PID_FILENAME
    fd = os.open(str(pid_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
    try:
        os.write(fd, f"{pid}\n".encode())
    finally:
        os.close(fd)


def _clear_pid(path: Path) -> None:
    pid_file = Path(path) / PID_FILENAME
    try:
        if pid_file.exists():
            pid_file.unlink()
    except OSError:
        pass


def _try_flock_exclusive(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if _flock_busy_errno(exc):
            return False
        raise LockError(f"could not flock: {exc}") from exc
    return True


def acquire_lock(
    name: str,
    *,
    directory: Optional[Path] = None,
    environ: Optional[os._Environ[str]] = None,
    timeout: float = 0,
    poll_interval: float = 0.05,
    after_mkdir: Optional[Callable[[Path], None]] = None,
) -> LockAcquisition:
    path = lock_path(name, environ, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(timeout))
    key = _registry_key(path)

    while True:
        with _REGISTRY_GUARD:
            if key in _HELD:
                if time.monotonic() >= deadline:
                    raise LockError(f"lock busy: {path}")
            else:
                break
        time.sleep(max(0.01, float(poll_interval)))
        if time.monotonic() >= deadline and key in _HELD:
            raise LockError(f"lock busy: {path}")

    dir_existed = path.is_dir()
    try:
        path.mkdir(exist_ok=True)
    except OSError as exc:
        raise LockError(f"could not create lock dir {path}: {exc}") from exc

    prior_pid = read_lock_pid(path)
    node = flock_file(path)
    try:
        fd = os.open(str(node), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise LockError(f"could not open flock at {node}: {exc}") from exc

    while True:
        try:
            got = _try_flock_exclusive(fd)
        except LockError:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        if got:
            break
        if time.monotonic() >= deadline:
            try:
                os.close(fd)
            except OSError:
                pass
            raise LockError(f"lock busy: {path}")
        time.sleep(max(0.01, float(poll_interval)))

    with _REGISTRY_GUARD:
        if key in _HELD:
            # Already held in this process. Do not LOCK_UN: on BSD that
            # would drop the original holder's flock.
            try:
                os.close(fd)
            except OSError:
                pass
            raise LockError(f"lock busy: {path}")
        _HELD[key] = fd

    if after_mkdir is not None:
        after_mkdir(path)

    pid = os.getpid()
    try:
        _write_pid(path, pid)
    except OSError as exc:
        release_lock(LockAcquisition(path=path, name=name, pid=pid, fd=fd))
        raise LockError(f"could not write lock pid at {path}: {exc}") from exc

    recovered = bool(
        (prior_pid is not None and prior_pid != pid and not _pid_alive(prior_pid))
        or (dir_existed and prior_pid is None)
    )
    return LockAcquisition(path=path, name=name, pid=pid, recovered_stale=recovered, fd=fd)


def release_lock(acquisition: LockAcquisition) -> None:
    path = Path(acquisition.path)
    key = _registry_key(path)
    current = read_lock_pid(path)
    if current is not None and current != acquisition.pid:
        raise LockError(f"lock at {path} is owned by pid {current}, not {acquisition.pid}")

    with _REGISTRY_GUARD:
        held_fd = _HELD.pop(key, None)
    fd = acquisition.fd if acquisition.fd >= 0 else -1
    if held_fd is not None:
        fd = held_fd

    _clear_pid(path)

    if fd >= 0:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError as exc:
            raise LockError(f"could not release lock at {path}: {exc}") from exc
    # Never unlink flock_file(path) or the lock directory.


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PadSplit flock lock helper")
    sub = parser.add_subparsers(dest="command", required=True)

    acquire_parser = sub.add_parser("acquire", help="acquire a named flock lock")
    acquire_parser.add_argument("name")
    acquire_parser.add_argument("--timeout", type=float, default=0)

    release_parser = sub.add_parser("release", help="release a named lock we own")
    release_parser.add_argument("name")

    status_parser = sub.add_parser("status", help="print lock pid / stale state")
    status_parser.add_argument("name")

    args = parser.parse_args(argv)
    if args.command == "acquire":
        held = acquire_lock(args.name, timeout=args.timeout)
        print(
            json.dumps(
                {
                    "path": str(held.path),
                    "pid": held.pid,
                    "recovered_stale": held.recovered_stale,
                    "flock": str(flock_file(held.path)),
                }
            )
        )
        return 0
    if args.command == "release":
        path = lock_path(args.name)
        pid = read_lock_pid(path)
        if pid is None:
            raise LockError(f"no lock pid at {path}")
        release_lock(LockAcquisition(path=path, name=args.name, pid=pid))
        return 0

    path = lock_path(args.name)
    pid = read_lock_pid(path)
    print(
        json.dumps(
            {
                "path": str(path),
                "pid": pid,
                "exists": path.exists(),
                "flock": flock_file(path).exists(),
                "held": lock_is_held(path) if path.exists() else False,
                "stale": lock_is_stale(path) if path.exists() else False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
