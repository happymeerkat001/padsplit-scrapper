"""JSON persistence and run-status payload helpers for the PadSplit scraper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from padsplit_scraper.kpis import _extract_earnings_rows, _to_num, compute_monthly_kpis
except ModuleNotFoundError:  # Support execution through padsplit_scraper/scraper.py.
    from kpis import _extract_earnings_rows, _to_num, compute_monthly_kpis

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DOCS_DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"


def _latest_output_path() -> Path:
    return OUTPUT_DIR / "latest.json"


def _stats_output_path() -> Path:
    return OUTPUT_DIR / "stats.json"


def _occupancy_output_path() -> Path:
    return OUTPUT_DIR / "occupancy.json"


def _docs_occupancy_path() -> Path:
    return DOCS_DATA_DIR / "occupancy.json"


def _persist_occupancy_payload(payload: Dict[str, Any]) -> None:
    _write_json(_occupancy_output_path(), payload)
    _write_json(_docs_occupancy_path(), payload)


def _timestamped_output_path(scraped_at: str) -> Path:
    return OUTPUT_DIR / f"{scraped_at.replace(':', '-')}.json"


def _monthly_history_path() -> Path:
    return DOCS_DATA_DIR / "monthly_history.json"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _load_json_if_exists(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _build_run_status(
    *,
    state: str,
    mode: str,
    run_scraped_at: str,
    failed_phase: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    fallback_used: bool = False,
) -> Dict[str, Any]:
    return {
        "state": state,
        "mode": mode,
        "failed_phase": failed_phase,
        "error_type": error_type,
        "error_message": error_message,
        "fallback_used": fallback_used,
        "run_scraped_at": run_scraped_at,
    }


def _attach_run_status(payload: Dict[str, Any], run_status: Dict[str, Any]) -> Dict[str, Any]:
    next_payload = dict(payload)
    next_payload["run_status"] = run_status
    return next_payload


def _persist_latest_payload(
    payload: Dict[str, Any],
    *,
    scraped_at: str,
    run_status: Optional[Dict[str, Any]] = None,
    write_timestamped: bool = False,
) -> Path:
    latest_payload = _attach_run_status(payload, run_status) if run_status else payload
    latest_path = _latest_output_path()
    _write_json(latest_path, latest_payload)
    out_path = _timestamped_output_path(scraped_at)
    if write_timestamped:
        _write_json(out_path, latest_payload)
        return out_path
    return out_path


def _load_score_history() -> List[Dict[str, Any]]:
    previous_stats = _load_json_if_exists(_stats_output_path()) or {}
    score_history = (previous_stats.get("kpis") or {}).get("score_history", [])
    return score_history if isinstance(score_history, list) else []


def _build_monthly_history_payload(
    *,
    performance_history: Dict[str, Dict[str, Any]],
    kpis: Dict[str, Any],
    scraped_at: str,
) -> Dict[str, Any]:
    existing_months_map: Dict[str, Dict[str, Any]] = {}
    monthly_prev = _load_json_if_exists(_monthly_history_path()) or {}
    monthly_prev_list = monthly_prev.get("months", []) if isinstance(monthly_prev, dict) else []
    for item in monthly_prev_list:
        if isinstance(item, dict) and item.get("month"):
            existing_months_map[str(item["month"])] = item

    months_map: Dict[str, Dict[str, Any]] = dict(existing_months_map)
    for month_key, raw in performance_history.items():
        if month_key < "2025-04":
            continue
        monthly_kpis = compute_monthly_kpis(raw)
        months_map[month_key] = {
            "month": month_key,
            "avg_flip_days": round(_to_num(raw.get("avg_flip_days")), 1),
            "occupancy_pct": round(_to_num(raw.get("occupancy_pct")), 1),
            "avg_tenure_days": round(_to_num(raw.get("avg_tenure_days")), 1),
            "partial": monthly_kpis["partial"],
            "bonuses": monthly_kpis["bonuses"],
            "penalties": monthly_kpis["penalties"],
            "score": monthly_kpis["score"],
            "note": monthly_kpis["note"],
        }

    current_month = scraped_at[:7]
    months_map[current_month] = {
        "month": current_month,
        "avg_flip_days": round(_to_num(kpis.get("avg_flip_days")), 1),
        "occupancy_pct": round(_to_num(kpis.get("occupancy_pct")), 1),
        "avg_tenure_days": round(_to_num(kpis.get("avg_tenure_days")), 1),
        "partial": False,
        "bonuses": kpis.get("bonuses", []),
        "penalties": kpis.get("penalties", []),
        "score": int(_to_num(kpis.get("score"))),
    }

    monthly_history_months = [
        months_map[month_key]
        for month_key in sorted(months_map.keys())
        if isinstance(months_map[month_key], dict) and month_key >= "2025-04"
    ]
    return {
        "updated_at": scraped_at,
        "months": monthly_history_months,
    }


def _build_stats_payload(
    *,
    scraped_at: str,
    rooms: List[Dict[str, Any]],
    properties: List[Dict[str, Any]],
    earnings_payload: Dict[str, Any],
    kpis: Dict[str, Any],
    run_status: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "scraped_at": scraped_at,
        "rooms": rooms,
        "properties": properties,
        "earnings": _extract_earnings_rows(earnings_payload),
        "kpis": kpis,
        "run_status": run_status,
    }
