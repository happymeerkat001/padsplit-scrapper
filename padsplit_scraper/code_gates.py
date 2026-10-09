"""Shared gates for code reply, digest sync, and change notices.

Every new outbound action stays off until its flag is set. CI and
collection-only never send. Spanish Moss is the designated first live
house: set CODE_AUTOMATION_TEST_HOUSES to that slug before a wider enable.
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python padsplit_scraper/code_gates.py
    import runtime  # type: ignore


def _env(environ: Optional[os._Environ[str]]) -> os._Environ[str]:
    return environ if environ is not None else os.environ


def dry_run_flag(name: str, environ: Optional[os._Environ[str]] = None) -> bool:
    """Default on. An explicit 0/false/no/off is the only way to leave dry-run."""
    parsed = runtime.flag_value(_env(environ).get(name))
    if parsed is None:
        return True
    return parsed


def test_house_allowlist(environ: Optional[os._Environ[str]] = None) -> Optional[frozenset[str]]:
    """None means no house limit. A set means only those slugs may act."""
    raw = (_env(environ).get("CODE_AUTOMATION_TEST_HOUSES") or "").strip()
    if not raw:
        return None
    slugs = []
    for part in raw.replace(";", ",").split(","):
        slug = part.strip()
        if slug:
            slugs.append(slug)
    return frozenset(slugs)


def house_allowed(slug: str, environ: Optional[os._Environ[str]] = None) -> bool:
    allow = test_house_allowlist(environ)
    if allow is None:
        return True
    return bool(slug) and slug in allow


def action_live(action: str, dry_run_name: str, environ: Optional[os._Environ[str]] = None) -> bool:
    env = _env(environ)
    if not runtime.send_enabled(action, env):
        return False
    return not dry_run_flag(dry_run_name, env)
