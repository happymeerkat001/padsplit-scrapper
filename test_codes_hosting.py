#!/usr/bin/env python3
"""Hosting public dir may contain only the four synced private pages."""

import json
import re
import unittest
from pathlib import Path

from scripts.sync_codes_hosting import ALLOWED, PAGES, PUBLIC_DIR, SOURCE, sync


ROOT = Path(__file__).resolve().parent
FIREBASE_JSON = ROOT / "firebase.json"
EXPECTED = sorted(PAGES)
FETCH_MARKERS = (
    "fetch(",
    "./data/stats.json",
    "./data/latest.json",
    "./data/occupancy.json",
    "./data/monthly_history.json",
    "docs/data/",
    "raw.githubusercontent.com",
    "github.io/",
    "firestore.googleapis.com",
)
PRIVATE_PAGES = ("templates.html", "vendors.html", "stats.html")
DIGIT_RUN_RE = re.compile(r"\d{4,}")
VALUE_STRING_RE = re.compile(
    r"""\bvalue\s*:\s*(?P<q>["'])(?P<body>(?:\\.|(?!(?P=q)).)*)(?P=q)"""
)


def public_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*") if path.is_file())


def assert_codes_public_dir(directory: Path) -> None:
    files = public_files(directory)
    names = [path.relative_to(directory).as_posix() for path in files]
    unexpected = [name for name in names if name not in ALLOWED]
    if unexpected:
        raise AssertionError(f"hosting public dir has unexpected files: {unexpected}")
    if names != EXPECTED:
        raise AssertionError(f"hosting public dir must contain {EXPECTED}, found {names}")
    for path in files:
        if path.suffix.lower() == ".json" or "json" in path.name.lower():
            raise AssertionError(f"hosting public dir has data JSON: {path.name}")
        text = path.read_text(encoding="utf-8")
        for match in VALUE_STRING_RE.finditer(text):
            body = match.group("body")
            if body != "":
                raise AssertionError("hosting file has a non-empty value string")
        defaults = ""
        if "const DEFAULTS = " in text:
            defaults = text.split("const DEFAULTS = ", 1)[1].split("const gateWrap", 1)[0]
        kept = []
        for line in defaults.splitlines():
            if re.search(r"\b(?:slug|address)\s*:", line):
                continue
            kept.append(line)
        if DIGIT_RUN_RE.search("\n".join(kept)):
            raise AssertionError("hosting file has a digit run in the codes config")


class CodesHostingTests(unittest.TestCase):
    def test_public_dir_is_only_synced_private_pages(self) -> None:
        sync()
        assert_codes_public_dir(PUBLIC_DIR)
        for name in PAGES:
            hosted = (PUBLIC_DIR / name).read_bytes()
            self.assertEqual(hosted, (ROOT / "docs" / name).read_bytes())
            text = hosted.decode("utf-8")
            self.assertNotIn("github.io/", text)
            self.assertIn("location.hostname.endsWith('github.io')", text)
            self.assertIn('from "https://www.gstatic.com/firebasejs/', text)
        self.assertEqual((PUBLIC_DIR / "codes.html").read_bytes(), SOURCE.read_bytes())
        sync()
        assert_codes_public_dir(PUBLIC_DIR)

    def test_hosted_pages_have_no_public_data_fetches(self) -> None:
        for name in PAGES:
            text = (ROOT / "docs" / name).read_text(encoding="utf-8")
            for marker in FETCH_MARKERS:
                self.assertNotIn(marker, text, msg=f"{name} contains {marker}")
        for name in PRIVATE_PAGES:
            text = (ROOT / "docs" / name).read_text(encoding="utf-8")
            self.assertIn('doc(db, "private_pages", pageId)', text)
            self.assertIn("isApprovedCodesUser", text)
            self.assertIn("signInWithEmailAndPassword", text)
            self.assertIn("function approvedCodesUids()", text)
            self.assertIn("return [];", text)
            self.assertIn("approvedCodesUids().includes(user.uid)", text)
            callback = text.split("onAuthStateChanged", 1)[1]
            self.assertLess(callback.index("isApprovedCodesUser"), callback.index("loadPrivatePage("))
            self.assertIn("id=\"gate-wrap\"", text)
            self.assertIn("id=\"app-wrap\"", text)

    def test_firebase_json_hosts_only_that_dir(self) -> None:
        config = json.loads(FIREBASE_JSON.read_text(encoding="utf-8"))
        hosting = config["hosting"]
        self.assertEqual(hosting["target"], "codes")
        self.assertEqual(hosting["public"], "hosting/codes")
        self.assertNotIn("docs", hosting["public"])
        headers = hosting["headers"]
        for name in PAGES:
            page_headers = next(item for item in headers if item["source"] == f"/{name}")
            got = {item["key"]: item["value"] for item in page_headers["headers"]}
            self.assertEqual(got["Cache-Control"], "no-store")
            self.assertEqual(got["X-Robots-Tag"], "noindex")
            self.assertEqual(got["X-Frame-Options"], "DENY")
        rc = json.loads((ROOT / ".firebaserc").read_text(encoding="utf-8"))
        self.assertEqual(rc["projects"]["default"], "padsplit-scrapper")
        self.assertEqual(rc["targets"]["padsplit-scrapper"]["hosting"]["codes"], ["padsplit-scrapper"])

    def test_rejects_extra_json_and_code_values(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "codes.html"
            page.write_text(SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
            extra = root / "latest.json"
            extra.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(AssertionError):
                assert_codes_public_dir(root)
            extra.unlink()
            page.write_text('value: "9999"\n', encoding="utf-8")
            with self.assertRaises(AssertionError):
                assert_codes_public_dir(root)


if __name__ == "__main__":
    unittest.main()
