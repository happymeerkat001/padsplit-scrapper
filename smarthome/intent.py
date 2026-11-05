"""Sticky-off and named-hold store. Gitignored JSON under logs/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
INTENT_PATH = ROOT_DIR / "logs" / "smarthome_intent.json"


def _empty() -> Dict[str, Any]:
    return {"units": {}}


def load_intent(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or INTENT_PATH
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty()


def save_intent(payload: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or INTENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)


def unit_state(payload: Dict[str, Any], name: str) -> Dict[str, Any]:
    units = payload.setdefault("units", {})
    return units.setdefault(name, {"sticky_off": False, "hold_f": None, "hold_slot_key": None})


def record_hold(name: str, fahrenheit: float, slot_key: Optional[str], *, path: Optional[Path] = None) -> None:
    payload = load_intent(path)
    state = unit_state(payload, name)
    state["sticky_off"] = False
    state["hold_f"] = fahrenheit
    state["hold_slot_key"] = slot_key
    save_intent(payload, path)


def record_off(name: str, *, path: Optional[Path] = None) -> None:
    payload = load_intent(path)
    state = unit_state(payload, name)
    state["sticky_off"] = True
    state["hold_f"] = None
    state["hold_slot_key"] = None
    save_intent(payload, path)


def clear_hold(name: str, *, path: Optional[Path] = None) -> None:
    payload = load_intent(path)
    state = unit_state(payload, name)
    state["hold_f"] = None
    state["hold_slot_key"] = None
    save_intent(payload, path)


def is_sticky_off(name: str, *, path: Optional[Path] = None) -> bool:
    return bool(unit_state(load_intent(path), name).get("sticky_off"))
