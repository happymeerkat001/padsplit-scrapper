"""Shared repo-root paths for padsplit_scraper scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "docs" / "data"
SCRAPER_OUTPUT_DIR = REPO_ROOT / "padsplit_scraper" / "output"


def load_latest_payload() -> Dict[str, Any]:
    for path in (DATA_DIR / "latest.json", SCRAPER_OUTPUT_DIR / "latest.json"):
        if path.exists():
            return json.loads(path.read_text())
    raise FileNotFoundError(
        f"Could not find latest.json in {DATA_DIR} or {SCRAPER_OUTPUT_DIR}"
    )
