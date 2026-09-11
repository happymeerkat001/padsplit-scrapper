"""Directory lock with stale-PID recovery.

mkdir is the exclusive primitive. The lock directory holds a pid file.
If that PID is gone, the lock is stale and may be recovered.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/process_lock.py
    import runtime  # type: ignore


PID_FILENAME = "pid"


class LockError(Exception):
    """Raised when a lock cannot be acquired or released."""


@dataclass(frozen=True)
class LockAcquisition:
    path: Path
    name: str
    pid: int
    recovered_stale: bool = False

    def __enter__(self) -> "LockAcquisition":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        release_lock(self)
        return False


def lock_path(name: str, environ: Optional[os._Environ[str]] = None, *, directory: Optional[Path] = None) -> Path:
    if directory is not None:
        return Path(directory)
    return runtime.lock_dir(name, environ)


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


def lock_is_stale(path: Path) -> bool:
    """True when the lock directory exists but the recorded PID is missing or dead."""
    lock = Path(path)
    if not lock.exists():
        return False
    pid = read_lock_pid(lock)
    if pid is None:
        return True
    return not _pid_alive(pid)


def _remove_stale(path: Path) -> None:
    pid_file = path / PID_FILENAME
    try:
        if pid_file.exists():
            pid_file.unlink()
        path.rmdir()
    except OSError as exc:
        raise LockError(f"could not remove stale lock at {path}: {exc}") from exc


def acquire_lock(
    name: str,
    *,
    directory: Optional[Path] = None,
    environ: Optional[os._Environ[str]] = None,
    timeout: float = 0,
    poll_interval: float = 0.05,
) -> LockAcquisition:
    path = lock_path(name, environ, directory=directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, float(timeout))
    recovered = False
    while True:
        try:
            os.mkdir(path)
        except FileExistsError:
            if lock_is_stale(path):
                _remove_stale(path)
                recovered = True
                continue
            if time.monotonic() >= deadline:
                raise LockError(f"lock busy: {path}")
            time.sleep(max(0.01, float(poll_interval)))
            continue
        except OSError as exc:
            raise LockError(f"could not acquire lock {path}: {exc}") from exc

        pid = os.getpid()
        try:
            (path / PID_FILENAME).write_text(f"{pid}\n")
        except OSError as exc:
            try:
                path.rmdir()
            except OSError:
                pass
            raise LockError(f"could not write lock pid at {path}: {exc}") from exc
        return LockAcquisition(path=path, name=name, pid=pid, recovered_stale=recovered)


def release_lock(acquisition: LockAcquisition) -> None:
    path = Path(acquisition.path)
    current = read_lock_pid(path)
    if current is not None and current != acquisition.pid:
        raise LockError(f"lock at {path} is owned by pid {current}, not {acquisition.pid}")
    pid_file = path / PID_FILENAME
    try:
        if pid_file.exists():
            pid_file.unlink()
        if path.exists():
            path.rmdir()
    except OSError as exc:
        raise LockError(f"could not release lock at {path}: {exc}") from exc


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PadSplit directory lock helper")
    sub = parser.add_subparsers(dest="command", required=True)

    acquire_parser = sub.add_parser("acquire", help="acquire a named directory lock")
    acquire_parser.add_argument("name")
    acquire_parser.add_argument("--timeout", type=float, default=0)

    release_parser = sub.add_parser("release", help="release a named directory lock we own")
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
                "stale": lock_is_stale(path) if path.exists() else False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
