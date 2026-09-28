#!/usr/bin/env python3
"""Readers of task_messages and manual_tasks stay on the approved-user gate."""
import subprocess
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
        self.assertLess(after.index("render();"), after.index("initFirestore();"))
        signed_out = after.split("if (!isApprovedCodesUser(user))", 1)[0]
        self.assertNotIn("initFirestore();", signed_out)

    def test_task_sections_hide_until_approved(self) -> None:
        index = (ROOT / "docs" / "index.html").read_text()
        sign_in = "tasks are on the ops pages (sign-in required)."
        empty = "no open tasks."
        self.assertEqual(index.count(sign_in), 1)
        self.assertEqual(index.count(empty), 1)
        self.assertNotIn(f">{sign_in}</a>", index)
        self.assertNotIn(f'href="{sign_in}"', index)
        self.assertIn("line.textContent = text", index)
        self.assertIn("onTaskListenerError", index)
        self.assertEqual(index.count("onTaskListenerError"), 3)
        start = index.index("// begin task-section-state")
        end = index.index("// end task-section-state")
        source = index[start:end]
        script = source + """
const cases = [
  [false, false, 4, TASKS_SIGN_IN],
  [false, true, 0, TASKS_SIGN_IN],
  [true, true, 0, TASKS_SIGN_IN],
  [true, false, 0, TASKS_EMPTY],
  [true, false, 2, ""],
];
for (const [approved, denied, openCount, expected] of cases) {
  if (taskSectionMessage(approved, denied, openCount) !== expected) process.exit(1);
}
if (taskSectionMessage(true, true, 0) === TASKS_EMPTY) process.exit(1);
"""
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
