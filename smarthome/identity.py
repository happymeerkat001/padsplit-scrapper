"""Persisted Mac SmartHome client identity. Gitignored JSON under logs/."""

from __future__ import annotations

import json
from pathlib import Path
from secrets import token_hex, token_urlsafe
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
IDENTITY_PATH = ROOT_DIR / "logs" / "smarthome_identity.json"
LIBRARY_DEFAULT_DEVICE_ID = "c1acad8939ac0d7d"
DEFAULT_FINGERPRINT = "msmarthome"
SMARTHOME_APPNAME = "MSmartHome"
SMARTHOME_API_URL = "https://mp-prod.appsmb.com/mas/v5/app/proxy?alias="
FORBIDDEN_APPNAMES = frozenset(
    {"NetHome Plus", "Midea Air", "Ariston Clima", "Ariston Clima EU", "OS Comfort"}
)
FINGERPRINTS: Dict[str, Dict[str, Any]] = {
    "msmarthome": {
        "appname": SMARTHOME_APPNAME,
        "appid": 1010,
        "apiurl": SMARTHOME_API_URL,
        "slim": False,
    },
    "msmarthome-slim": {
        "appname": SMARTHOME_APPNAME,
        "appid": 1010,
        "apiurl": SMARTHOME_API_URL,
        "slim": True,
    },
}


class UnknownFingerprintError(ValueError):
    """Fingerprint is not a published SmartHome-silo identity."""


def _mint() -> Dict[str, str]:
    device_id = token_hex(8)
    while device_id == LIBRARY_DEFAULT_DEVICE_ID:
        device_id = token_hex(8)
    return {
        "fingerprint": DEFAULT_FINGERPRINT,
        "device_id": device_id,
        "pushToken": token_urlsafe(120),
    }


def _read(path: Path) -> Optional[Dict[str, str]]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    device_id = data.get("device_id")
    push_token = data.get("pushToken")
    if not device_id or not push_token:
        return None
    device_id = str(device_id)
    if device_id == LIBRARY_DEFAULT_DEVICE_ID:
        return None
    fingerprint = str(data.get("fingerprint") or DEFAULT_FINGERPRINT)
    return {
        "fingerprint": fingerprint,
        "device_id": device_id,
        "pushToken": str(push_token),
    }


def save_identity(payload: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = path or IDENTITY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)


def load_or_create(path: Optional[Path] = None) -> Dict[str, str]:
    path = path or IDENTITY_PATH
    existing = _read(path)
    if existing is not None:
        return existing
    record = _mint()
    save_identity(record, path)
    return record


def current_fingerprint(path: Optional[Path] = None) -> str:
    record = _read(path or IDENTITY_PATH)
    if record is None:
        return DEFAULT_FINGERPRINT
    return record["fingerprint"]


def fingerprint_spec(name: str) -> Dict[str, Any]:
    try:
        spec = FINGERPRINTS[name]
    except KeyError as exc:
        raise UnknownFingerprintError(f"Unknown SmartHome fingerprint {name!r}") from exc
    if spec["appname"] in FORBIDDEN_APPNAMES:
        raise UnknownFingerprintError(f"Forbidden appname {spec['appname']!r}")
    return spec


def select_fingerprint(name: str, path: Optional[Path] = None) -> Dict[str, str]:
    fingerprint_spec(name)
    path = path or IDENTITY_PATH
    record = load_or_create(path)
    record = {
        "fingerprint": name,
        "device_id": record["device_id"],
        "pushToken": record["pushToken"],
    }
    save_identity(record, path)
    return record

