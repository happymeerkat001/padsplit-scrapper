"""Collection-only job runner.

Holds a process lock for the worker lifetime, writes to an isolated output
tree, and never publishes to git. No outbox drain and no send adapters.
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

    output_dir = isolate_outputs(env)
    try:
        held = process_lock.acquire_lock(
            LOCK_NAME,
            directory=lock_directory,
            environ=env,
            timeout=timeout,
        )
    except process_lock.LockError as exc:
        return {
            "action": "skipped_lock",
            "reason": str(exc),
            "exit_code": 0,
            "output_dir": str(output_dir),
            "git_published": False,
            "hooks": False,
        }

    worker = run_fn or scraper.run
    try:
        # Persist dirs are already isolated. Do not re-read os.environ here.
        exit_code = worker(messages_only)
        return {
            "action": "ok" if exit_code == 0 else "failed",
            "exit_code": int(exit_code),
            "output_dir": str(output_dir),
            "git_published": False,
            "hooks": runtime.action_hooks_enabled(env),
            "lock_pid": held.pid,
        }
    except Exception as exc:
        return {
            "action": "failed",
            "reason": str(exc),
            "exit_code": 1,
            "output_dir": str(output_dir),
            "git_published": False,
            "hooks": runtime.action_hooks_enabled(env),
        }
    finally:
        process_lock.release_lock(held)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PadSplit collection-only runner")
    parser.add_argument("--messages-only", action="store_true")
    args = parser.parse_args(argv)
    result = run_collection(messages_only=args.messages_only)
    print(json.dumps(result))
    return int(result.get("exit_code") or 0)


if __name__ == "__main__":
    sys.exit(main())
