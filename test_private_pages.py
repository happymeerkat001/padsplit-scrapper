#!/usr/bin/env python3
"""private_pages writer: flag off, missing credentials, and chunk splits."""

import json
import os
import unittest
from unittest.mock import patch

from padsplit_scraper import private_pages


class _FakeDoc:
    def __init__(self, doc_id: str) -> None:
        self.id = doc_id
        self.payload = None
        self._collections: dict[str, "_FakeCollection"] = {}

    def set(self, payload):
        self.payload = payload

    def collection(self, name: str) -> "_FakeCollection":
        return self._collections.setdefault(name, _FakeCollection())

    def get(self):
        return self

    @property
    def exists(self) -> bool:
        return self.payload is not None

    def to_dict(self):
        return self.payload

    def delete(self) -> None:
        self.payload = None


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, _FakeDoc] = {}

    def document(self, name: str) -> _FakeDoc:
        return self.docs.setdefault(name, _FakeDoc(name))

    def stream(self):
        return [snap for snap in self.docs.values() if snap.payload is not None]


class _PartSnap:
    def __init__(self, doc: _FakeDoc) -> None:
        self.id = doc.id
        self.reference = doc


class _PartCollection(_FakeCollection):
    def stream(self):
        return [_PartSnap(doc) for doc in self.docs.values() if doc.payload is not None]


class _ParentDoc(_FakeDoc):
    def collection(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _PartCollection())


class _PrivateCollection(_FakeCollection):
    def document(self, name: str) -> _FakeDoc:
        return self.docs.setdefault(name, _ParentDoc(name))


class _FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def collection(self, name: str) -> _FakeCollection:
        if name == "private_pages":
            return self.collections.setdefault(name, _PrivateCollection())
        return self.collections.setdefault(name, _FakeCollection())


class PrivatePagesWriterTests(unittest.TestCase):
    def test_flag_off_skips_without_touching_firestore(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != private_pages.PRIVATE_PAGES_FLAG}
        with patch.dict(os.environ, env, clear=True), patch.object(
            private_pages, "_firestore_client_or_none"
        ) as client:
            self.assertFalse(private_pages.upload_private_pages(stats_payload={"kpis": {"score": 1}}))
            client.assert_not_called()

    def test_missing_credentials_skips(self) -> None:
        with patch.dict(os.environ, {private_pages.PRIVATE_PAGES_FLAG: "1"}), patch.object(
            private_pages, "_firestore_client_or_none", return_value=None
        ) as client:
            self.assertFalse(
                private_pages.upload_private_pages(stats_payload={"kpis": {"score": 1}}, updated_at="t")
            )
            client.assert_called_once()

    def test_flag_off_when_value_is_not_one(self) -> None:
        with patch.dict(os.environ, {private_pages.PRIVATE_PAGES_FLAG: "true"}), patch.object(
            private_pages, "_firestore_client_or_none"
        ) as client:
            self.assertFalse(private_pages.upload_private_pages(stats_payload={"a": 1}))
            client.assert_not_called()

    def test_split_keeps_utf8_roundtrip(self) -> None:
        text = json.dumps({"note": "é" * 30}, separators=(",", ":"))
        chunks = private_pages.split_json_text(text, max_bytes=12)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.encode("utf-8")), 12 + 3)

    def test_writes_single_doc_or_parts(self) -> None:
        client = _FakeClient()
        payload = {"kpis": {"score": 1}, "note": "n" * 50}
        with patch.dict(os.environ, {private_pages.PRIVATE_PAGES_FLAG: "1"}), patch.object(
            private_pages, "CHUNK_MAX_BYTES", 40
        ), patch.object(
            private_pages, "_read_templates_payload", return_value={"fields": {"t0": "hello"}}
        ), patch.object(
            private_pages, "_read_vendors_payload", return_value={"vendors": []}
        ):
            self.assertTrue(
                private_pages.upload_private_pages(
                    stats_payload=payload,
                    updated_at="2026-09-28T00:00:00Z",
                    client=client,
                )
            )
        stats_doc = client.collections["private_pages"].docs["stats"]
        self.assertGreater(stats_doc.payload["chunk_count"], 1)
        self.assertNotIn("json", stats_doc.payload)
        self.assertEqual(stats_doc.payload["page"], "stats")
        self.assertEqual(stats_doc.payload["updated_at"], "2026-09-28T00:00:00Z")
        parts = stats_doc.collection("parts")
        joined = "".join(parts.docs[str(index)].payload["json"] for index in range(stats_doc.payload["chunk_count"]))
        self.assertEqual(json.loads(joined), payload)

        templates_doc = client.collections["private_pages"].docs["templates"]
        self.assertEqual(templates_doc.payload["chunk_count"], 1)
        self.assertEqual(json.loads(templates_doc.payload["json"]), {"fields": {"t0": "hello"}})
        vendors_doc = client.collections["private_pages"].docs["vendors"]
        self.assertEqual(json.loads(vendors_doc.payload["json"]), {"vendors": []})

    def test_write_failure_does_not_raise(self) -> None:
        class Boom:
            def collection(self, _name):
                raise RuntimeError("boom")

        with patch.dict(os.environ, {private_pages.PRIVATE_PAGES_FLAG: "1"}):
            self.assertFalse(private_pages.upload_private_pages(stats_payload={"a": 1}, client=Boom()))


if __name__ == "__main__":
    unittest.main()
