"""Durable outbound outbox. Records intent only; never sends.

Stage A–B placeholder. A later job_runner drains these records.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    from padsplit_scraper import state_store
except ModuleNotFoundError:  # python3 padsplit_scraper/outbox.py
    import state_store  # type: ignore


OUTBOX_NAME = "outbox"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(environ: Optional[os._Environ[str]] = None) -> Dict[str, Any]:
    payload = state_store.load_json(OUTBOX_NAME, {"items": []}, environ=environ)
    items = payload.get("items")
    if not isinstance(items, list):
        payload["items"] = []
    return payload


def enqueue(
    kind: str,
    payload: Dict[str, Any],
    *,
    environ: Optional[os._Environ[str]] = None,
) -> Dict[str, Any]:
    box = load(environ)
    item = {
        "id": uuid.uuid4().hex,
        "kind": kind,
        "payload": dict(payload),
        "status": "pending",
        "created_at": _now(),
    }
    box["items"].append(item)
    state_store.save_json(OUTBOX_NAME, box, environ=environ)
    return item


def pending(environ: Optional[os._Environ[str]] = None) -> List[Dict[str, Any]]:
    return [item for item in load(environ)["items"] if item.get("status") == "pending"]


def mark_done(item_id: str, *, environ: Optional[os._Environ[str]] = None) -> bool:
    box = load(environ)
    found = False
    for item in box["items"]:
        if item.get("id") == item_id:
            item["status"] = "done"
            item["done_at"] = _now()
            found = True
            break
    if found:
        state_store.save_json(OUTBOX_NAME, box, environ=environ)
    return found
