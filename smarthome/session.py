"""Cross-process cloud session lock and 65027 cooldown."""

from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT_DIR / "logs" / "smarthome_session.lock"
COOLDOWN_PATH = ROOT_DIR / "logs" / "smarthome_cooldown.json"
COOLDOWN_SECONDS = 3600


@contextmanager
def session_lock(path: Optional[Path] = None) -> Iterator[None]:
    path = path or LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def cooldown_active(
    path: Optional[Path] = None,
    *,
    now: float | None = None,
    fingerprint: Optional[str] = None,
) -> bool:
    path = path or COOLDOWN_PATH
    try:
        data = json.loads(path.read_text())
        started = float(data["started"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, OSError):
        return False
    stored = data.get("fingerprint")
    if fingerprint is not None and stored is not None and fingerprint != stored:
        return False
    return (now if now is not None else time.time()) - started < COOLDOWN_SECONDS


def start_cooldown(
    path: Optional[Path] = None,
    *,
    now: float | None = None,
    fingerprint: Optional[str] = None,
) -> None:
    path = path or COOLDOWN_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"started": now if now is not None else time.time()}
    if fingerprint is not None:
        payload["fingerprint"] = fingerprint
    path.write_text(json.dumps(payload) + "\n")
