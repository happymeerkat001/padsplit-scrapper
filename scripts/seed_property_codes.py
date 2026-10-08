#!/usr/bin/env python3
"""One-time seed for Firestore collection property_codes.

Reads a local JSON file that is gitignored. Does not read docs/codes.html
or any other repo file for field values.

Default path: property_codes.local.json (repo root).

Expected shape (placeholders only):

{
  "example_house": {
    "front_door": "0000",
    "back_door": "0000",
    "front_back": "0000",
    "admin": "000000",
    "front_master": "000000",
    "back_master": "000000",
    "outside_master": "000000",
    "room_master": "000000",
    "wifi_primary": "network-name / placeholder",
    "wifi_tmobile": "network-name / placeholder",
    "wifi_alt": "network-name / placeholder",
    "thermo": "0000",
    "spectrum_code": "0000",
    "tmobile_line": "0000000000",
    "tenant_login": "name / placeholder",
    "r1": "0000",
    "lockbox_1": "0000",
    "keys_1": "0000"
  },
  "spanish_moss": {
    "back_door": "0000"
  }
}

Top-level keys are property slugs. Each value is an object of field key to
string. Empty strings are allowed. Reserved keys updatedAt, contentHash,
source, expireAt, createdAt, and fields are ignored. Other non-strings
abort the run without printing the value.

--dry-run prints property counts and field key names only.

Without --dry-run, each object is merge-written to property_codes/{slug}
with updatedAt set by this script. Omitted keys already in Firestore are
left in place. Requires FIREBASE_SERVICE_ACCOUNT_JSON or
GOOGLE_APPLICATION_CREDENTIALS (for example via the repo .env). Never
prints credential material or field values.

Merge the codes page change only after Firestore has been seeded and Ang says go.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "property_codes.local.json"
COLLECTION = "property_codes"
SLUG_RE = re.compile(r"[a-z0-9_]+")
KEY_RE = re.compile(r"[A-Za-z0-9_]+")
RESERVED_KEYS = {
    "updatedAt",
    "contentHash",
    "source",
    "expireAt",
    "createdAt",
    "fields",
}


def normalize_payload(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("root must be a non-empty object of property id to fields")
    out: dict[str, dict[str, str]] = {}
    for slug, fields in raw.items():
        if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
            raise ValueError("invalid property id")
        if not isinstance(fields, dict):
            raise ValueError(f"{slug} must be an object of field keys")
        cleaned: dict[str, str] = {}
        for key, value in fields.items():
            if key in RESERVED_KEYS:
                continue
            if not isinstance(key, str) or not KEY_RE.fullmatch(key):
                raise ValueError(f"{slug} has an invalid field key")
            if not isinstance(value, str):
                raise ValueError(f"{slug} field {key} must be a string")
            cleaned[key] = value
        if not cleaned:
            raise ValueError(f"{slug} has no code fields")
        out[slug] = cleaned
    if not out:
        raise ValueError("no properties")
    return out


def dry_run_report(payload: dict[str, dict[str, str]]) -> str:
    lines = [f"properties: {len(payload)}"]
    for slug in sorted(payload):
        keys = sorted(payload[slug])
        lines.append(f"{slug}: {len(keys)} keys")
        lines.append("keys: " + ", ".join(keys))
    return "\n".join(lines)


def load_local_payload(path: Path) -> dict[str, dict[str, str]]:
    resolved = path.expanduser().resolve()
    codes_page = (ROOT / "docs" / "codes.html").resolve()
    if resolved == codes_page or resolved.name == "codes.html":
        raise ValueError("refusing to read docs/codes.html")
    if not resolved.is_file():
        raise ValueError("local seed file is missing")
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ValueError("local seed file is not valid JSON") from None
    return normalize_payload(raw)


def write_payload(client: Any, payload: dict[str, dict[str, str]]) -> int:
    stamp = datetime.now(timezone.utc).isoformat()
    for slug, fields in payload.items():
        body = dict(fields)
        body["updatedAt"] = stamp
        client.collection(COLLECTION).document(slug).set(body, merge=True)
    return len(payload)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


def firestore_client() -> Any:
    _load_env()
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        google_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if service_account_json:
            try:
                info = json.loads(service_account_json)
            except json.JSONDecodeError:
                raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON") from None
            firebase_admin.initialize_app(credentials.Certificate(info))
        elif google_credentials:
            firebase_admin.initialize_app(credentials.Certificate(google_credentials))
        else:
            raise RuntimeError("Missing FIREBASE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS")
    return firestore.client()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed Firestore property_codes from a local JSON file.")
    parser.add_argument("--file", default=str(DEFAULT_PATH), help="gitignored local JSON file")
    parser.add_argument("--dry-run", action="store_true", help="print counts and keys only")
    args = parser.parse_args(argv)
    try:
        payload = load_local_payload(Path(args.file))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(dry_run_report(payload))
    if args.dry_run:
        return 0
    try:
        client = firestore_client()
        wrote = write_payload(client, payload)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("could not write property_codes", file=sys.stderr)
        return 1
    print(f"wrote: {wrote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
