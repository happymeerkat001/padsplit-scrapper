"""Collection-only job runner.

Holds a process lock for the worker lifetime, writes to an isolated output
tree, and never publishes to git. No outbox drain and no send adapters.

The real scraper worker receives an immutable collection-only policy. Ambient
and dotenv action-hook flags are not consulted on this path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from padsplit_scraper import persist
    from padsplit_scraper import process_lock
    from padsplit_scraper import runtime
    from padsplit_scraper import scraper
except ModuleNotFoundError:  # python3 padsplit_scraper/job_runner.py
    import persist  # type: ignore
    import process_lock  # type: ignore
    import runtime  # type: ignore
    import scraper  # type: ignore


LOCK_NAME = "collection"


def isolate_outputs(environ: Optional[os._Environ[str]] = None) -> Path:
    output_dir = runtime.collection_output_dir(environ)
    persist.configure_output_dirs(output_dir=output_dir)
    return output_dir


def _collection_worker(messages_only: bool = False, **_kwargs):
    """Real scraper entry used by the collection runner.

    Policy is explicit and frozen. Do not read PADSPLIT_ENABLE_ACTION_HOOKS
    (or dotenv) to decide hooks on this path.
    """
    return scraper.run(messages_only=messages_only, policy=runtime.COLLECTION_ONLY_POLICY)


def _interpret_worker_result(raw: Any) -> tuple[str, int, Dict[str, Any]]:
    action = getattr(raw, "action", None)
    exit_code = getattr(raw, "exit_code", None)
    run_status = getattr(raw, "run_status", None)
    if action is not None and exit_code is not None:
        status = run_status if isinstance(run_status, dict) else {}
        return str(action), int(exit_code), status
    code = int(raw or 0)
    return ("ok" if code == 0 else "failed"), code, {}


def _result(
    *,
    action: str,
    exit_code: int,
    output_dir: Path,
    hooks: bool = False,
    run_status: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
    lock_pid: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "action": action,
        "exit_code": int(exit_code),
        "output_dir": str(output_dir),
        "git_published": False,
        "hooks": bool(hooks),
        "run_status": run_status or {},
    }
    if reason is not None:
        payload["reason"] = reason
    if lock_pid is not None:
        payload["lock_pid"] = lock_pid
    return payload


def run_collection(
    *,
    messages_only: bool = False,
    environ: Optional[os._Environ[str]] = None,
    lock_directory: Optional[Path] = None,
    timeout: float = 0,
    run_fn=None,
) -> Dict[str, Any]:
    """Run the scraper under a worker-lifetime lock. Never git-publishes."""
    env = environ if environ is not None else os.environ
    if runtime.git_publish_enabled(env):
        sys.stderr.write("[job-runner] PADSPLIT_GIT_PUBLISH is set; collection runner still will not git-publish\n")

    snapshot = persist.snapshot_output_dirs()
    intended_output = runtime.collection_output_dir(env)
    held = None
    try:
        try:
            held = process_lock.acquire_lock(
                LOCK_NAME,
                directory=lock_directory,
                environ=env,
                timeout=timeout,
            )
        except process_lock.LockError as exc:
            return _result(
                action="skipped_lock",
                exit_code=0,
                output_dir=intended_output,
                reason=str(exc),
            )

        output_dir = isolate_outputs(env)
        worker = run_fn or _collection_worker
        try:
            raw = worker(messages_only)
            action, exit_code, run_status = _interpret_worker_result(raw)
            return _result(
                action=action,
                exit_code=exit_code,
                output_dir=output_dir,
                run_status=run_status,
                lock_pid=held.pid,
            )
        except Exception as exc:
            return _result(
                action="failed",
                exit_code=1,
                output_dir=output_dir,
                reason=str(exc),
            )
    finally:
        try:
            if held is not None:
                process_lock.release_lock(held)
        finally:
            persist.restore_output_dirs(snapshot)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PadSplit collection-only runner")
    parser.add_argument("--messages-only", action="store_true")
    args = parser.parse_args(argv)
    result = run_collection(messages_only=args.messages_only)
    print(json.dumps(result))
    return int(result.get("exit_code") or 0)


if __name__ == "__main__":
    sys.exit(main())
