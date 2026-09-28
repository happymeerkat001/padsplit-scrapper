#!/usr/bin/env python3
"""Copy docs/codes.html into the Firebase Hosting public dir.

docs/codes.html is the only source. The Hosting dir is hosting/codes and
may contain only codes.html. CSS, script, and the Firebase config are
inline in that file. Do not copy docs/ or any data JSON.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "codes.html"
PUBLIC_DIR = ROOT / "hosting" / "codes"
DEST = PUBLIC_DIR / "codes.html"
ALLOWED = frozenset({"codes.html"})


def sync() -> None:
    if not SOURCE.is_file():
        raise SystemExit("docs/codes.html is missing")
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(PUBLIC_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PUBLIC_DIR).as_posix()
        if rel not in ALLOWED:
            path.unlink()
    DEST.write_bytes(SOURCE.read_bytes())
    for path in sorted(PUBLIC_DIR.rglob("*")):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


if __name__ == "__main__":
    sync()
