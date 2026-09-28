#!/usr/bin/env python3
"""Fail if a published snapshot still has secrets or private keys.

Every string value is scanned. Keyword-plus-token hits fail regardless of
the key. Standalone 4-8 digit runs fail unless the key or path is on the
bare-number allowlist in ``publish_sanitize``, or the run is a 4-5 digit
street number whose suffix is the next word or follows one or two name
words. ``st`` and ``ct`` count only with a period before whitespace or the
end, or at end of line. The placeholder
``[code hidden, see ops page]`` is not a hit.

Name, money, and score keys fail anywhere they are nested. ``title`` fails
only under ``messages`` or ``tasks``. ``name`` fails only under
``reported_by`` or ``sender``. See ``publish_sanitize.private_key_kind``.

Prints counts and JSON paths only. Never prints field values.

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


def _error_report(path: Path, reason: str) -> str:
    return (
        f"path: {path}\n"
        "room_code_keys: 0\n"
        "sensitive_texts: 0\n"
        "bare_numbers: 0\n"
        "name_keys: 0\n"
        "money_keys: 0\n"
        "score_keys: 0\n"
        f"error: {reason}\n"
    )


def check_path(path: Path) -> int:
    if not path.is_file():
        sys.stdout.write(_error_report(path, "missing"))
        return 2
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        sys.stdout.write(_error_report(path, "unreadable"))
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
