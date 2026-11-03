"""Resolve what the watcher/CLI status should do for one unit."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from smarthome import clocks, intent

FLOOR_F = 74
NIGHT_OFF_HOUR = 1
NIGHT_ON_HOUR = 6


def in_night_off(now: datetime) -> bool:
    return NIGHT_OFF_HOUR <= now.hour < NIGHT_ON_HOUR


def resolve_action(
    name: str,
    now: Optional[datetime] = None,
    *,
    intent_path: Optional[Path] = None,
    schedules_path: Optional[Path] = None,
    clocks_path: Optional[Path] = None,
) -> Dict[str, Any]:
    when = now or datetime.now()
    intent_path = intent_path or intent.INTENT_PATH
    state = intent.unit_state(intent.load_intent(intent_path), name)
    house = clocks.resolve_house(name)
    if state.get("sticky_off"):
        return {"kind": "off", "house": house}
    if in_night_off(when):
        return {"kind": "night_off", "house": house}
    return {"kind": "floor", "f": FLOOR_F, "house": house}
