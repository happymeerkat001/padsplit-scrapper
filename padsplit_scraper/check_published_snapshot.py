#!/usr/bin/env python3
"""Fail if a published snapshot still has room codes or code-like phrases.

Every string value is scanned. Keyword-plus-token hits fail regardless of
the key. Standalone 4-8 digit runs fail unless the key or path is on the
bare-number allowlist in ``publish_sanitize``, or the run is a 4-5 digit
street number followed within 1-4 words by a street suffix. The placeholder
``[code hidden, see ops page]`` is not a hit. Prints counts and JSON paths
only. Never prints field values.

    python3 padsplit_scraper/check_published_snapshot.py docs/data/latest.json

Exit 0 when clean, 1 when violations are present, 2 when a file cannot be read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Sequence

try:
    from padsplit_scraper.publish_sanitize import find_violations, format_violation_report
except ModuleNotFoundError:  # python3 padsplit_scraper/check_published_snapshot.py
    from publish_sanitize import find_violations, format_violation_report


def check_path(path: Path) -> int:
    if not path.is_file():
        sys.stdout.write(f"path: {path}\nroom_code_keys: 0\nsensitive_texts: 0\nerror: missing\n")
        return 2
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        sys.stdout.write(f"path: {path}\nroom_code_keys: 0\nsensitive_texts: 0\nerror: unreadable\n")
        return 2
    violations = find_violations(payload)
    sys.stdout.write(format_violation_report(str(path), violations))
    return 1 if violations else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stdout.write("usage: check_published_snapshot.py <snapshot.json> [...]\n")
        return 2
    status = 0
    for raw in args:
        result = check_path(Path(raw))
        if result != 0:
            status = result if status == 0 else status
            if result == 1:
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
