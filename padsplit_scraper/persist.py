"""JSON persistence and run-status payload helpers for the PadSplit scraper."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEGRADED_EXIT_CODE = 2


@dataclass(frozen=True)
class RunOutcome:
    """Structured worker result. ``exit_code`` is the runner contract.

    ``process_exit_code`` stays 0 for ok and degraded so ``scraper.main``
    keeps its existing Mac/CI degraded-is-zero mapping.
    """

    action: str
    exit_code: int
    run_status: Dict[str, Any] = field(default_factory=dict)
    process_exit_code: int = 0

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


def configure_output_dirs(*, output_dir: Path, docs_data_dir: Optional[Path] = None) -> None:
    """Point persist writes at an explicit tree. Used by the isolated runner."""
    global OUTPUT_DIR, DOCS_DATA_DIR
    OUTPUT_DIR = Path(output_dir)
    DOCS_DATA_DIR = Path(docs_data_dir) if docs_data_dir is not None else OUTPUT_DIR / "docs-data"


def snapshot_output_dirs() -> Dict[str, Path]:
    """Capture persist globals so a runner can restore them after isolation."""
    return {
        "OUTPUT_DIR": Path(OUTPUT_DIR),
        "DOCS_DATA_DIR": Path(DOCS_DATA_DIR),
    }


def restore_output_dirs(snapshot: Dict[str, Path]) -> None:
    """Restore persist globals captured by ``snapshot_output_dirs``."""
    global OUTPUT_DIR, DOCS_DATA_DIR
    OUTPUT_DIR = Path(snapshot["OUTPUT_DIR"])
    DOCS_DATA_DIR = Path(snapshot["DOCS_DATA_DIR"])


def prior_last_complete_success(
    previous: Optional[Dict[str, Any]],
    *,
    this_run_scraped_at: str,
) -> Optional[str]:
    """Last complete success from a prior payload, rejecting stale run health.

    Never treat a previous ``run_status`` as this run. If prior
    ``run_scraped_at`` is missing, equal to, or newer than this run, the
    structured health is stale and is ignored.
    """
    if not isinstance(previous, dict):
        return None
    prior = previous.get("run_status")
    prior_run_at = None
    if isinstance(prior, dict):
        prior_run_at = prior.get("run_scraped_at")
        if not prior_run_at or str(prior_run_at) >= this_run_scraped_at:
            return None
        last = prior.get("last_complete_success")
        if last:
            return str(last)
        if prior.get("state") == "ok":
            return str(prior_run_at or previous.get("scraped_at") or "") or None
        return None
    scraped = previous.get("scraped_at")
    if scraped and str(scraped) < this_run_scraped_at:
        return str(scraped)
    return None


def _build_run_status(
    *,
    state: str,
    mode: str,
    run_scraped_at: str,
    failed_phase: Optional[str] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    fallback_used: bool = False,
    sources: Optional[Dict[str, Any]] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    host: Optional[str] = None,
    counts: Optional[Dict[str, Any]] = None,
    last_complete_success: Optional[str] = None,
    omissions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "state": state,
        "mode": mode,
        "failed_phase": failed_phase,
        "error_type": error_type,
        "error_message": error_message,
        "fallback_used": fallback_used,
        "run_scraped_at": run_scraped_at,
    }
    if started_at:
        payload["started_at"] = started_at
    if finished_at:
        payload["finished_at"] = finished_at
    if host:
        payload["host"] = host
    if counts is not None:
        payload["counts"] = counts
    if last_complete_success:
        payload["last_complete_success"] = last_complete_success
    if omissions:
        payload["omissions"] = list(omissions)
    if sources:
        payload["sources"] = sources
    return payload


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
