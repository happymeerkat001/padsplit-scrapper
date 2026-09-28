"""Write templates, vendors, and stats snapshots to private_pages.

The Hosting pages read these docs after codes sign-in. Client writes are
denied in firestore.rules; this module uses the Admin SDK, which bypasses
rules. Set PRIVATE_PAGES_FIRESTORE_WRITE=1 to enable. Missing credentials
or a failed write logs a skip and never raises, so a scrape still finishes.

Document shape:

    private_pages/{templates|vendors|stats}
      page, updated_at, chunk_count
      json                  # only when chunk_count == 1

    private_pages/{page}/parts/{0..}
      index, json           # UTF-8 slices, only when chunk_count > 1

Payloads:
  stats: the stats object also stored in output/stats.json
  templates: {"fields": <templates/shared snapshot>}
  vendors: {"vendors": [{id, name, specialty, contact, location}, ...]}
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

try:
    from padsplit_scraper.persist import _firestore_client_or_none
except ModuleNotFoundError:  # python3 padsplit_scraper/scraper.py
    from persist import _firestore_client_or_none

PRIVATE_PAGES_COLLECTION = "private_pages"
PRIVATE_PAGES_FLAG = "PRIVATE_PAGES_FIRESTORE_WRITE"
# Stay under Firestore's 1 MiB document limit after field names and protobuf.
CHUNK_MAX_BYTES = 800_000
PAGE_IDS = ("templates", "vendors", "stats")


def private_pages_write_enabled() -> bool:
    return os.getenv(PRIVATE_PAGES_FLAG, "").strip() == "1"


def split_json_text(text: str, max_bytes: int = CHUNK_MAX_BYTES) -> list[str]:
    """Split a JSON string on UTF-8 boundaries into pieces of at most max_bytes.

    A single code point longer than max_bytes is kept whole so each piece
    stays valid UTF-8. Callers use a max far below the 1 MiB document limit.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return [text]
    chunks: list[str] = []
    start = 0
    total = len(encoded)
    while start < total:
        end = min(start + max_bytes, total)
        while end > start and end < total and (encoded[end] & 0xC0) == 0x80:
            end -= 1
        if end == start:
            end = start + 1
            while end < total and (encoded[end] & 0xC0) == 0x80:
                end += 1
        chunks.append(encoded[start:end].decode("utf-8"))
        start = end
    return chunks


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            return str(value)
    return str(value)


def _read_templates_payload(db: Any) -> dict[str, Any]:
    snap = db.collection("templates").document("shared").get()
    data = snap.to_dict() if getattr(snap, "exists", False) else {}
    if not isinstance(data, dict):
        data = {}
    return {"fields": _plain(data)}


def _read_vendors_payload(db: Any) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for snap in db.collection("vendors").stream():
        data = snap.to_dict() or {}
        if not isinstance(data, dict):
            data = {}
        rows.append(
            {
                "id": str(getattr(snap, "id", "") or ""),
                "name": str(data.get("name") or ""),
                "specialty": str(data.get("specialty") or ""),
                "contact": str(data.get("contact") or ""),
                "location": str(data.get("location") or ""),
            }
        )
    rows.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    return {"vendors": rows}


def _delete_stale_parts(parts: Any, keep: int) -> None:
    stream = getattr(parts, "stream", None)
    if not callable(stream):
        return
    stale = []
    for existing in stream():
        doc_id = str(getattr(existing, "id", ""))
        if not doc_id.isdigit() or int(doc_id) >= keep:
            ref = getattr(existing, "reference", None)
            if ref is not None:
                stale.append(ref)
    for ref in stale:
        ref.delete()


def _write_private_page(db: Any, page_id: str, payload: Any, updated_at: str) -> None:
    text = json.dumps(payload, separators=(",", ":"), default=str)
    chunks = split_json_text(text, CHUNK_MAX_BYTES)
    parent = db.collection(PRIVATE_PAGES_COLLECTION).document(page_id)
    body: dict[str, Any] = {
        "page": page_id,
        "updated_at": updated_at,
        "chunk_count": len(chunks),
    }
    parts = parent.collection("parts")
    if len(chunks) == 1:
        body["json"] = chunks[0]
        parent.set(body)
        _delete_stale_parts(parts, 0)
        return
    parent.set(body)
    for index, chunk in enumerate(chunks):
        parts.document(str(index)).set({"index": index, "json": chunk})
    _delete_stale_parts(parts, len(chunks))


def upload_private_pages(
    *,
    stats_payload: dict[str, Any] | None = None,
    updated_at: str = "",
    client: Any = None,
) -> bool:
    """Upsert private page docs. Never raises. Returns False when skipped."""
    if not private_pages_write_enabled():
        sys.stderr.write(
            "# private_pages Firestore write skipped (PRIVATE_PAGES_FIRESTORE_WRITE is off)\n"
        )
        return False
    try:
        db = client if client is not None else _firestore_client_or_none()
        if db is None:
            sys.stderr.write("# private_pages Firestore write skipped (no credentials)\n")
            return False
        stamp = str(updated_at or "")
        if stats_payload is not None:
            _write_private_page(db, "stats", stats_payload, stamp)
        _write_private_page(db, "templates", _read_templates_payload(db), stamp)
        _write_private_page(db, "vendors", _read_vendors_payload(db), stamp)
        return True
    except Exception as exc:
        sys.stderr.write(f"# private_pages Firestore write failed: {exc.__class__.__name__}\n")
        return False
