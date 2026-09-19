"""Durable JSON state under the private runtime state directory.

Atomic replace only. No network. Callers decide the filename.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from padsplit_scraper import runtime
except ModuleNotFoundError:  # python3 padsplit_scraper/state_store.py
    import runtime  # type: ignore


def state_path(name: str, environ: Optional[os._Environ[str]] = None) -> Path:
    filename = name if name.endswith(".json") else f"{name}.json"
    return runtime.state_dir(environ) / filename


def load_json(name: str, default: Optional[Dict[str, Any]] = None, *, environ=None) -> Dict[str, Any]:
    path = state_path(name, environ)
    if not path.exists():
        return dict(default or {})
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return dict(default or {})
    return payload if isinstance(payload, dict) else dict(default or {})


def save_json(name: str, payload: Dict[str, Any], *, environ=None) -> Path:
    path = state_path(name, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return path
