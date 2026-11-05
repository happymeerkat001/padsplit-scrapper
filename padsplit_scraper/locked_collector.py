"""Explicit collection-only worker entry point with a local process lock.

This does not configure a schedule, send actions, or publish to PostgreSQL.
Every worker sharing a state directory must use this same entry point.
"""
import os
from pathlib import Path
import stat
import sys
from padsplit_scraper import process_lock, scraper

_GATES={'PADSPLIT_COLLECTION_ONLY':'1','PADSPLIT_ENABLE_ACTION_HOOKS':'0',
        'PADSPLIT_DISABLE_FIRESTORE_UPLOAD':'1','PADSPLIT_GIT_PUBLISH':'0'}
_PATHS=('PADSPLIT_LOCK_DIR','PADSPLIT_OUTPUT_DIR','PADSPLIT_STATE_DIR')
_ROOT=Path(__file__).resolve().parents[1]

def main(*, environ=None):
    env=os.environ if environ is None else environ
    if any(env.get(name)!=value for name,value in _GATES.items()):
        print('Collection-only worker gates are not configured.',file=sys.stderr);return 2
    for name in _PATHS:
        value=env.get(name,'')
        if not value or not Path(value).is_absolute():
            print('Private worker paths must be absolute.',file=sys.stderr);return 2
        target=Path(value).resolve()
        if target==_ROOT or _ROOT in target.parents:
            print('Private worker paths must be outside the checkout.',file=sys.stderr);return 2
        try:
            info=Path(value).lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode & 0o077:raise ValueError()
        except (OSError,ValueError):
            print('Worker directories must exist and be owner-only.',file=sys.stderr);return 2
    try:
        with process_lock.acquire_lock('padsplit-collector',environ=env,timeout=0):
            return int(scraper.run(isolate_output=True).process_exit_code)
    except process_lock.LockError:
        print('Collection worker is already running or its lock is unavailable.',file=sys.stderr);return 75
    except Exception:
        print('Collection worker failed; inspect protected diagnostics.',file=sys.stderr);return 1

if __name__=='__main__':
    raise SystemExit(main())
