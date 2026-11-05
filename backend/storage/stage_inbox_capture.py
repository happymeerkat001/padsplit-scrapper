"""Manually apply a reviewed encrypted inbox capture to local staging only.

This command never contacts the provider and never schedules itself. The
original authenticated baseline must already be imported and projected.
"""
import argparse,os,stat,sys
from padsplit_scraper import runtime
from padsplit_scraper.inbox_collection import _private_path
from backend.storage.migrate import key_from_env,read_backup,staging_connect
from backend.storage.project_inbox import catch_up_records,load_bindings,validate_provider_capture

_MAX_BACKUP_BYTES=100_000_000

def _read_private_backup(path,key):
    """Open a bounded owner-only regular file without following a final symlink."""
    target=_private_path(str(path))
    fd=os.open(target,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or
                info.st_mode & 0o077 or info.st_size>_MAX_BACKUP_BYTES):
            raise ValueError('Private bounded backup required')
        # read_backup owns and closes its descriptor; keep this verified fd open
        # to ensure it reads the same file even if the pathname is replaced.
        return read_backup(os.dup(fd),key)
    finally:
        os.close(fd)


def stage(conn,baseline_path,capture_path,mapping_path,key,*,apply=False):
    if type(apply) is not bool:raise ValueError('Invalid apply mode')
    baseline=_read_private_backup(baseline_path,key)
    capture=_read_private_backup(capture_path,key)
    validate_provider_capture(capture)
    bindings=load_bindings(mapping_path)
    return catch_up_records(conn,baseline,capture,key,bindings,apply=apply)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Rehearse one reviewed encrypted inbox capture in local staging')
    parser.add_argument('--enable-staging-inbox',action='store_true')
    parser.add_argument('--baseline',required=True);parser.add_argument('--capture',required=True);parser.add_argument('--mapping',required=True)
    parser.add_argument('--apply',action='store_true');args=parser.parse_args(argv)
    if not args.enable_staging_inbox or runtime.flag_value(os.getenv('PADSPLIT_COLLECTION_ONLY')) is not True or runtime.running_in_ci():
        print('Staging inbox projection is disabled; explicit collection-only mode is required.',file=sys.stderr);return 2
    try:
        key=key_from_env()
        with staging_connect() as conn:counts=stage(conn,args.baseline,args.capture,args.mapping,key,apply=args.apply)
        print(f'{"Staging projection" if args.apply else "Staging dry run"}: {counts[0]} conversations and {counts[1]} messages validated. Source coverage remains filtered and non-atomic.')
        return 0
    except Exception:
        print('Staging inbox projection stopped; review private inputs and configuration.',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
