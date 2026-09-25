#!/usr/bin/env python3
"""Snapshot contract and catch-up. Never print field values."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import codes_history


ROOT = Path(__file__).resolve().parent
RULES = (ROOT / "firestore.rules").read_text()
MORNING = (ROOT / "run_morning.sh").read_text()
AFTERNOON = (ROOT / "run_afternoon.sh").read_text()
WORKFLOW = (ROOT / ".github" / "workflows" / "scrape.yml").read_text()

STATS_UID = "TXSU0LOpmDWNBbbv0x3uHHBnZb12"
FAKE_DOOR = "FAKE_FRONT"
FAKE_EXTRA = "FAKE_EXTRA"


def _versions_block() -> str:
    start = RULES.index("match /code_versions/{id}")
    rest = RULES[start:]
    end = rest.find("match /notes/")
    if end == -1:
        end = rest.find("match /{document=**}")
    return rest[:end]


class SnapshotContractTests(unittest.TestCase):
    def test_rules_block_is_codes_uid_create_and_bounded_list(self) -> None:
        block = _versions_block()
        self.assertIn("isApprovedCodesUser()", block)
        self.assertNotIn("request.auth.uid ==", block)
        self.assertNotIn(STATS_UID, block)
        self.assertRegex(
            RULES,
            r"function approvedCodesUid\(\) \{\s*return '';\s*\}",
        )
        self.assertIn("allow get", block)
        self.assertIn("allow list", block)
        self.assertIn("allow create", block)
        self.assertIn("50", block)
        self.assertIn("allow update, delete: if false", block)
        self.assertNotIn("allow write", block)
        self.assertNotIn("request.auth != null", block)
        self.assertIn("allow read, write: if false", RULES)

    def test_copied_parent_write_grant_would_fail(self) -> None:
        block = _versions_block()
        self.assertNotIn("allow read, write", block)

    def test_hash_ignores_updated_at(self) -> None:
        left = {"front_door": FAKE_DOOR, "updatedAt": "2026-01-01T00:00:00Z"}
        right = {"front_door": FAKE_DOOR, "updatedAt": "2026-09-19T12:00:00Z"}
        self.assertEqual(codes_history.content_hash(left), codes_history.content_hash(right))

    def test_hash_keeps_empty_strings_and_extra_keys(self) -> None:
        empty = {"front_door": "", "lockbox_99": FAKE_EXTRA}
        filled = {"front_door": FAKE_DOOR, "lockbox_99": FAKE_EXTRA}
        self.assertNotEqual(codes_history.content_hash(empty), codes_history.content_hash(filled))
        self.assertIn("lockbox_99", codes_history.canonicalize_fields(empty))
        self.assertEqual(codes_history.canonicalize_fields(empty)["front_door"], "")

    def test_restore_payload_drops_wrapper_keys(self) -> None:
        fields = {
            "front_door": FAKE_DOOR,
            "contentHash": "nope",
            "source": "page",
            "expireAt": "x",
            "createdAt": "y",
            "fields": {"nested": "no"},
        }
        payload = codes_history.restore_payload(fields, updated_at="NOW")
        self.assertEqual(payload["front_door"], FAKE_DOOR)
        self.assertEqual(payload["updatedAt"], "NOW")
        self.assertNotIn("contentHash", payload)
        self.assertNotIn("source", payload)
        self.assertNotIn("expireAt", payload)
        self.assertNotIn("fields", payload)


class CatchupTests(unittest.TestCase):
    def test_hash_diff_writes_version_not_live(self) -> None:
        store = FakeStore()
        store.set_live("leana_6623", {"front_door": FAKE_DOOR})
        result = codes_history.catch_up(client=store, now=datetime(2026, 9, 19, tzinfo=timezone.utc))
        self.assertEqual(result["written"], 1)
        self.assertEqual(store.live["leana_6623"]["front_door"], FAKE_DOOR)
        self.assertEqual(len(store.versions["leana_6623"]), 1)
        self.assertEqual(store.versions["leana_6623"][0]["source"], "catchup")
        self.assertNotIn("updatedAt", store.versions["leana_6623"][0]["fields"])

    def test_same_hash_skips(self) -> None:
        store = FakeStore()
        store.set_live("leana_6623", {"front_door": FAKE_DOOR})
        now = datetime(2026, 9, 19, tzinfo=timezone.utc)
        codes_history.catch_up(client=store, now=now)
        result = codes_history.catch_up(client=store, now=now + timedelta(hours=1))
        self.assertEqual(result["written"], 0)
        self.assertEqual(len(store.versions["leana_6623"]), 1)

    def test_includes_spanish_moss(self) -> None:
        store = FakeStore()
        store.set_live("spanish_moss", {"back_door": FAKE_DOOR})
        codes_history.catch_up(client=store, now=datetime(2026, 9, 19, tzinfo=timezone.utc))
        self.assertIn("spanish_moss", store.versions)

    def test_prunes_non_latest_old_rows_only(self) -> None:
        store = FakeStore()
        store.set_live("leana_6623", {"front_door": FAKE_DOOR})
        now = datetime(2026, 9, 19, tzinfo=timezone.utc)
        store.add_version(
            "leana_6623",
            created_at=now - timedelta(days=100),
            fields={"front_door": "FAKE_OLD"},
        )
        store.add_version(
            "leana_6623",
            created_at=now - timedelta(days=10),
            fields={"front_door": FAKE_DOOR},
        )
        result = codes_history.catch_up(client=store, now=now)
        created = [row["createdAt"] for row in store.versions["leana_6623"]]
        self.assertEqual(len(created), 1)
        self.assertEqual(result["pruned"], 1)

    def test_keeps_latest_even_if_old(self) -> None:
        store = FakeStore()
        store.set_live("leana_6623", {"front_door": FAKE_DOOR})
        now = datetime(2026, 9, 19, tzinfo=timezone.utc)
        store.add_version(
            "leana_6623",
            created_at=now - timedelta(days=100),
            fields={"front_door": FAKE_DOOR},
        )
        codes_history.catch_up(client=store, now=now)
        self.assertEqual(len(store.versions["leana_6623"]), 1)

    def test_keeps_missing_created_at(self) -> None:
        store = FakeStore()
        store.set_live("leana_6623", {"front_door": FAKE_DOOR})
        store.add_version("leana_6623", created_at=None, fields={"front_door": FAKE_DOOR})
        now = datetime(2026, 9, 19, tzinfo=timezone.utc)
        codes_history.catch_up(client=store, now=now)
        self.assertEqual(len(store.versions["leana_6623"]), 1)

    def test_ci_does_no_reads_or_writes(self) -> None:
        store = FakeStore()
        store.set_live("leana_6623", {"front_door": FAKE_DOOR})
        with patch.dict(os.environ, {"CI": "1", "GITHUB_ACTIONS": "true"}):
            result = codes_history.catch_up(client=store)
        self.assertEqual(result["action"], "skip_ci")
        self.assertEqual(store.stream_calls, 0)
        self.assertEqual(store.versions, {})

    def test_missing_client_skips(self) -> None:
        result = codes_history.catch_up(client=None)
        self.assertEqual(result["action"], "skip_no_client")

    def test_module_writes_no_local_files(self) -> None:
        source = (ROOT / "padsplit_scraper" / "codes_history.py").read_text()
        self.assertNotIn("write_text", source)
        self.assertNotIn("open(", source)

    def test_scripts_wire_catchup_not_ci(self) -> None:
        self.assertIn("codes_history.py", MORNING)
        self.assertIn("codes_history.py", AFTERNOON)
        self.assertGreater(MORNING.index("lock_codes.py"), 0)
        self.assertGreater(MORNING.index("codes_history.py"), MORNING.index("lock_codes.py"))
        self.assertGreater(AFTERNOON.index("codes_history.py"), AFTERNOON.index("lock_codes.py"))
        self.assertNotIn("codes_history.py", WORKFLOW)
        morning_add = MORNING.split("git -C \"$WORKSPACE\" add", 1)[1].split("if git", 1)[0]
        afternoon_add = AFTERNOON.split("git -C \"$WORKSPACE\" add", 1)[1].split("if git", 1)[0]
        self.assertNotIn("codes_history.py", morning_add)
        self.assertNotIn("codes_history.py", afternoon_add)


class FakeSnap:
    def __init__(self, doc_id: str, data: dict | None) -> None:
        self.id = doc_id
        self._data = data

    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict:
        return dict(self._data or {})


class FakeVersionQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def order_by(self, *_args, **_kwargs):
        return self

    def stream(self):
        for row in self._rows:
            yield FakeSnap(row["id"], row)


class FakeVersions:
    def __init__(self, store: "FakeStore", slug: str) -> None:
        self.store = store
        self.slug = slug

    def document(self, doc_id: str | None = None):
        if not doc_id:
            doc_id = f"auto-{len(self.store.versions.get(self.slug, []))}"
        return FakeVersionDoc(self.store, self.slug, doc_id)

    def order_by(self, *_args, **_kwargs):
        rows = list(self.store.versions.get(self.slug, []))
        return FakeVersionQuery(rows)

    def stream(self):
        return FakeVersionQuery(list(self.store.versions.get(self.slug, []))).stream()


class FakeVersionDoc:
    def __init__(self, store: "FakeStore", slug: str, doc_id: str) -> None:
        self.store = store
        self.slug = slug
        self.id = doc_id

    def set(self, payload: dict, **_kwargs) -> None:
        row = dict(payload)
        row["id"] = self.id
        self.store.versions.setdefault(self.slug, []).append(row)

    def delete(self) -> None:
        rows = self.store.versions.get(self.slug, [])
        self.store.versions[self.slug] = [row for row in rows if row["id"] != self.id]


class FakeLiveDoc:
    def __init__(self, store: "FakeStore", slug: str) -> None:
        self.store = store
        self.slug = slug

    def get(self):
        data = self.store.live.get(self.slug)
        return FakeSnap(self.slug, data)

    def set(self, payload: dict, **_kwargs) -> None:
        self.store.live[self.slug] = dict(payload)

    def collection(self, name: str):
        if name != "code_versions":
            raise AssertionError("unexpected collection")
        return FakeVersions(self.store, self.slug)


class FakeCollection:
    def __init__(self, store: "FakeStore") -> None:
        self.store = store

    def document(self, slug: str):
        return FakeLiveDoc(self.store, slug)

    def stream(self):
        self.store.stream_calls += 1
        for slug, data in self.store.live.items():
            yield FakeSnap(slug, data)


class FakeStore:
    def __init__(self) -> None:
        self.live: dict[str, dict] = {}
        self.versions: dict[str, list[dict]] = {}
        self.stream_calls = 0

    def collection(self, name: str):
        if name != "property_codes":
            raise AssertionError("unexpected collection")
        return FakeCollection(self)

    def set_live(self, slug: str, data: dict) -> None:
        self.live[slug] = dict(data)

    def add_version(self, slug: str, *, created_at, fields: dict) -> None:
        rows = self.versions.setdefault(slug, [])
        rows.append(
            {
                "id": f"v{len(rows)}",
                "fields": dict(fields),
                "contentHash": codes_history.content_hash(fields),
                "createdAt": created_at,
                "source": "catchup",
            }
        )


if __name__ == "__main__":
    unittest.main()
