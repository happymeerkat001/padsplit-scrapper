"""Helpers for labeling stale stats.json in operator surfaces."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


STALE_AFTER = timedelta(hours=48)


def stats_freshness(payload: Optional[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    run = data.get("run_status") if isinstance(data.get("run_status"), dict) else {}
    scraped = _parse_ts(data.get("scraped_at"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elif scraped is not None and scraped.tzinfo is None:
        scraped = scraped.replace(tzinfo=timezone.utc)
    degraded = run.get("state") == "degraded" or run.get("fallback_used") is True
    missing = scraped is None
    age = None if missing else now - scraped
    stale = degraded or missing or (age is not None and age > STALE_AFTER)
    return {
        "stale": stale,
        "degraded": degraded,
        "missing_scraped_at": missing,
        "scraped_at": data.get("scraped_at"),
        "run_scraped_at": run.get("run_scraped_at"),
        "failed_phase": run.get("failed_phase"),
        "state": run.get("state"),
        "fallback_used": bool(run.get("fallback_used")),
    }


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
