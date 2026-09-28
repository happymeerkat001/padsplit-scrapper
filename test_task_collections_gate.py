#!/usr/bin/env python3
"""Readers of task_messages and manual_tasks stay on the approved-user gate."""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {".git", "node_modules", "venv", "__pycache__"}
KNOWN = {
    "docs/index.html",
    "firestore.rules",
    "padsplit_scraper/firestore_status_monitor.py",
    "test_firestore_rules.mjs",
    "test_task_collections_gate.py",
}


def _rules_block(rules: str, name: str) -> str:
    start = rules.index(f"match /{name}/{{id}}")
    end = rules.index("match /", start + 1)
    return rules[start:end]


class TaskCollectionsGateTests(unittest.TestCase):
    def test_only_known_files_name_the_collections(self) -> None:
        hits = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".html", ".js", ".mjs", ".rules"}:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            text = path.read_text(errors="ignore")
            if "task_messages" in text or "manual_tasks" in text:
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(hits), sorted(KNOWN))

    def test_rules_are_approved_user_only(self) -> None:
        rules = (ROOT / "firestore.rules").read_text()
        self.assertNotIn("allow read: if true;", rules)
        for name in ("task_messages", "manual_tasks"):
            body = _rules_block(rules, name)
            self.assertIn("isApprovedCodesUser()", body)
            self.assertNotIn("if true", body)
            self.assertNotIn("request.auth.uid ==", body)

    def test_dashboard_listeners_start_after_codes_sign_in(self) -> None:
        index = (ROOT / "docs" / "index.html").read_text()
        self.assertIn("occupancy.json", index)
        self.assertIn("function isApprovedCodesUser(user)", index)
        self.assertIn("function approvedCodesUids()", index)
        self.assertIn("return [];", index)
        self.assertIn("approvedCodesUids().includes(user.uid)", index)
        self.assertNotIn("function approvedCodesUid()", index)
        before, after = index.split("onAuthStateChanged(auth, (user)", 1)
        self.assertNotIn("initFirestore();", before)
        self.assertIn("if (!isApprovedCodesUser(user))", after)
        self.assertLess(after.index("isApprovedCodesUser(user)"), after.index("initFirestore();"))

    def test_status_monitor_uses_admin_sdk(self) -> None:
        src = (ROOT / "padsplit_scraper" / "firestore_status_monitor.py").read_text()
        self.assertIn("firebase_admin", src)
        self.assertIn("FIREBASE_SERVICE_ACCOUNT_JSON", src)
        self.assertIn('collection("task_messages")', src)
        self.assertNotIn("manual_tasks", src)
        self.assertNotIn("firestore.googleapis.com", src)
        self.assertNotIn("initializeApp", src)


if __name__ == "__main__":
    unittest.main()
