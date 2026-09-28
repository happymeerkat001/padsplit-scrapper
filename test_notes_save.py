#!/usr/bin/env python3
"""Run the notes-pad save tests. The node script never prints note text."""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class NotesSaveTests(unittest.TestCase):
    def test_notes_save_failures(self) -> None:
        result = subprocess.run(
            ["node", "--test", str(ROOT / "test_notes_save.mjs")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("# fail 0", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
