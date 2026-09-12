"""Explicit runtime gates for collection, sends, and private state paths.

Every outbound action is off until a named flag is set. CI never sends.
Darwin is not an implicit send permission.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Iterable, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_DIRNAME = ".runtime-state"

ACTION_FLAGS = {
    "new_booking": ("PADSPLIT_SEND_NEW_BOOKING",),
    "field_mms": ("PADSPLIT_SEND_FIELD_MMS", "FIELD_MMS_ENABLE"),
    "lockout": ("PADSPLIT_SEND_LOCKOUT", "LOCKOUT_REPLY_ENABLE"),
    "leak": ("PADSPLIT_SEND_LEAK", "LEAK_REPLY_ENABLE"),
    "lock_codes": ("PADSPLIT_SEND_LOCK_CODES", "LOCK_CODES_ENABLE"),
    "seo": ("PADSPLIT_SEND_SEO", "SEO_MONTHLY_ENABLE"),
    "discord_digest": ("PADSPLIT_SEND_DISCORD_DIGEST",),
    "discord_summary": ("PADSPLIT_SEND_DISCORD_SUMMARY",),
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def running_in_ci(environ: Optional[os._Environ[str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return bool((env.get("GITHUB_ACTIONS") or "").strip() or (env.get("CI") or "").strip())


def flag_value(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    text = raw.strip().lower()
    if not text:
        return None
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def send_enabled(action: str, environ: Optional[os._Environ[str]] = None) -> bool:
    """Return True only when CI is off, collection-only is off, and a flag is on."""

    env = environ if environ is not None else os.environ
    names = ACTION_FLAGS.get(action)
    if not names:
        raise KeyError(f"unknown send action: {action}")
    if running_in_ci(env) or collection_only(env):
        return False
    for name in names:
        parsed = flag_value(env.get(name))
        if parsed is False:
            return False
        if parsed is True:
            return True
    return False


def collection_only(environ: Optional[os._Environ[str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    forced = flag_value(env.get("PADSPLIT_COLLECTION_ONLY"))
    if forced is True or running_in_ci(env):
        return True
    if forced is False and action_hooks_enabled(env):
        return False
    return not action_hooks_enabled(env)


def action_hooks_enabled(environ: Optional[os._Environ[str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    if running_in_ci(env):
        return False
    if flag_value(env.get("PADSPLIT_COLLECTION_ONLY")) is True:
        return False
    return flag_value(env.get("PADSPLIT_ENABLE_ACTION_HOOKS")) is True


def git_publish_enabled(environ: Optional[os._Environ[str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    if running_in_ci(env):
        return False
    return flag_value(env.get("PADSPLIT_GIT_PUBLISH")) is True


def collection_output_dir(environ: Optional[os._Environ[str]] = None) -> Path:
    """Isolated collection output. Never the live git-tracked docs/data tree."""
    env = environ if environ is not None else os.environ
    raw = (env.get("PADSPLIT_OUTPUT_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return state_dir(env) / "collection"


def state_dir(environ: Optional[os._Environ[str]] = None) -> Path:
    env = environ if environ is not None else os.environ
    raw = (env.get("PADSPLIT_STATE_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return REPO_ROOT / DEFAULT_STATE_DIRNAME


def lock_dir(name: str, environ: Optional[os._Environ[str]] = None) -> Path:
    env = environ if environ is not None else os.environ
    raw = (env.get("PADSPLIT_LOCK_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser() / name
    return Path(env.get("TMPDIR") or "/tmp") / f"padsplit-{name}.lock"


def host_identity() -> str:
    return socket.gethostname()


def enabled_send_actions(environ: Optional[os._Environ[str]] = None) -> Iterable[str]:
    env = environ if environ is not None else os.environ
    return tuple(action for action in ACTION_FLAGS if send_enabled(action, env))
