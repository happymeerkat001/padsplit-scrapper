#!/usr/bin/env python3
"""DOM checks for hosted nav and page states. The node script never prints field values."""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class HostedPageStateTests(unittest.TestCase):
    def test_nav_and_page_states(self) -> None:
        result = subprocess.run(
            ["node", str(ROOT / "test_hosted_page_states.mjs")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("hosted page states ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
