"""Firestore codes snapshots. Never write local files. Never log field values."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

try:
    from padsplit_scraper import persist
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/codes_history.py
    import persist  # type: ignore
    import runtime  # type: ignore


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"

CODES_COLLECTION = "property_codes"
VERSIONS_COLLECTION = "code_versions"
RESERVED_KEYS = frozenset(
    {"updatedAt", "contentHash", "source", "expireAt", "createdAt", "fields"}
)
RETENTION_DAYS = 90
LIST_LIMIT = 50


def canonicalize_fields(data: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in (data or {}).items():
        if key in RESERVED_KEYS:
            continue
        out[str(key)] = "" if value is None else str(value)
    return dict(sorted(out.items()))


def content_hash(data: Dict[str, Any]) -> str:
    blob = json.dumps(canonicalize_fields(data), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def restore_payload(fields: Dict[str, Any], updated_at: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = dict(canonicalize_fields(fields))
    payload["updatedAt"] = updated_at
    return payload


def _as_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


def _latest_row(rows: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    dated = []
    for row in rows:
        created = _as_dt(row.get("createdAt"))
        if created is not None:
            dated.append((created, row))
    if dated:
        return max(dated, key=lambda item: item[0])[1]
    return rows[-1] if rows else None


def _log(message: str) -> None:
    sys.stderr.write(f"[codes-history] {message}\n")


def load_environment() -> None:
    load_dotenv(ENV_PATH, override=False)


def catch_up(
    client: Any = None,
    now: Optional[datetime] = None,
    environ: Optional[os._Environ] = None,
) -> Dict[str, Any]:
    env = environ if environ is not None else os.environ
    if runtime.running_in_ci(env):
        _log("skip_ci")
        return {"action": "skip_ci", "written": 0, "pruned": 0}
    db = client if client is not None else persist._firestore_client_or_none()
    if db is None:
        _log("skip_no_client")
        return {"action": "skip_no_client", "written": 0, "pruned": 0}

    when = now or datetime.now(timezone.utc)
    cutoff = when - timedelta(days=RETENTION_DAYS)
    written = 0
    pruned = 0
    slugs = 0

    for snap in db.collection(CODES_COLLECTION).stream():
        slugs += 1
        slug = snap.id
        live = snap.to_dict() if hasattr(snap, "to_dict") else {}
        if not isinstance(live, dict):
            live = {}
        fields = canonicalize_fields(live)
        digest = content_hash(fields)
        versions = db.collection(CODES_COLLECTION).document(slug).collection(VERSIONS_COLLECTION)
        rows = []
        for version in versions.stream():
            data = version.to_dict() if hasattr(version, "to_dict") else {}
            if not isinstance(data, dict):
                data = {}
            data["id"] = getattr(version, "id", "")
            rows.append(data)
        latest = _latest_row(rows)
        if not latest or latest.get("contentHash") != digest:
            versions.document().set(
                {
                    "fields": fields,
                    "contentHash": digest,
                    "createdAt": when,
                    "expireAt": when + timedelta(days=RETENTION_DAYS),
                    "source": "catchup",
                }
            )
            written += 1
            rows.append(
                {
                    "id": f"new-{written}",
                    "fields": fields,
                    "contentHash": digest,
                    "createdAt": when,
                    "source": "catchup",
                }
            )
        latest = _latest_row(rows)
        latest_id = latest.get("id") if latest else None
        for row in rows:
            if row.get("id") == latest_id:
                continue
            created = _as_dt(row.get("createdAt"))
            if created is None:
                continue
            if created < cutoff and row.get("id"):
                versions.document(row["id"]).delete()
                pruned += 1

    _log(f"ok slugs={slugs} written={written} pruned={pruned}")
    return {"action": "ok", "written": written, "pruned": pruned}


def main() -> int:
    load_environment()
    result = catch_up()
    return 0 if result["action"] in {"ok", "skip_ci", "skip_no_client"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
