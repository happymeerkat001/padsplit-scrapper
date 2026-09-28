#!/usr/bin/env python3
"""Copy the private Hosting pages into the Firebase Hosting public dir.

Sources are docs/codes.html, docs/templates.html, docs/vendors.html, and
docs/stats.html. The Hosting dir is hosting/codes and may contain only
those four files. CSS, script, and the Firebase config are inline. Do not
copy docs/data or any other JSON.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = ROOT / "hosting" / "codes"
PAGES = ("codes.html", "templates.html", "vendors.html", "stats.html")
ALLOWED = frozenset(PAGES)
SOURCE = ROOT / "docs" / "codes.html"


def sync() -> None:
    missing = [name for name in PAGES if not (ROOT / "docs" / name).is_file()]
    if missing:
        raise SystemExit("missing docs pages: " + ", ".join(missing))
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(PUBLIC_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(PUBLIC_DIR).as_posix()
        if rel not in ALLOWED:
            path.unlink()
    for name in PAGES:
        (PUBLIC_DIR / name).write_bytes((ROOT / "docs" / name).read_bytes())
    for path in sorted(PUBLIC_DIR.rglob("*")):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


if __name__ == "__main__":
    sync()
