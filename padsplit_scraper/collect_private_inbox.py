"""Explicit manual read-only collection into encrypted staging artifacts.

This does not write PostgreSQL, schedule itself, send messages, or update live
dashboard snapshots. Source identity must be supplied through private env vars.
"""
import argparse,datetime,hashlib,hmac,os,sys
from padsplit_scraper import runtime
from padsplit_scraper.inbox_collection import (_private_path,advance_checkpoint,
                                                export_candidate,graphql_transport,read_checkpoint)
from backend.storage.migrate import key_from_env

def main(argv=None,*,session_factory=None,login_func=None):
    parser=argparse.ArgumentParser(description='Collect an encrypted, partial private inbox candidate for staging review')
    parser.add_argument('--enable-source-read',action='store_true')
    parser.add_argument('--checkpoint',required=True);parser.add_argument('--candidate',required=True)
    parser.add_argument('--max-pages',type=int,default=10)
    args=parser.parse_args(argv)
    if not args.enable_source_read or runtime.flag_value(os.getenv('PADSPLIT_COLLECTION_ONLY')) is not True or runtime.running_in_ci():
        print('Private source collection is disabled; explicit collection-only mode is required.',file=sys.stderr);return 2
    session=None
    try:
        checkpoint=_private_path(args.checkpoint);candidate=_private_path(args.candidate)
        if checkpoint==candidate or candidate.exists() or not 1<=args.max_pages<=100:raise ValueError('Invalid private output')
        key=key_from_env();email=os.getenv('PADSPLIT_EMAIL','').strip();password=os.getenv('PADSPLIT_PASSWORD','')
        if not email or len(email)>320 or not password:raise ValueError('Private source credentials required')
        tag=hmac.new(key,b'padsplit-inbox-account\0'+email.casefold().encode('utf8'),hashlib.sha256).hexdigest()
        if checkpoint.exists():
            state=read_checkpoint(checkpoint,key)
            if not isinstance(state.get('account_tag'),str) or not hmac.compare_digest(state['account_tag'],tag):raise ValueError('Checkpoint account mismatch')
            if state['phase']=='complete':
                export_candidate(checkpoint,candidate,key);print('Encrypted staging candidate prepared; source coverage remains partial and non-atomic.');return 0
        if session_factory is None:
            import requests
            session_factory=requests.Session
        if login_func is None:
            from padsplit_scraper.scraper import login
            login_func=login
        session=session_factory();login_func(session,email,password)
        result=advance_checkpoint(graphql_transport(session),checkpoint,key,account_tag=tag,max_pages=args.max_pages,
                                  observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        if not result['complete']:
            print('Encrypted checkpoint saved; rerun to continue. History coverage is unverified.');return 2
        export_candidate(checkpoint,candidate,key)
        print('Encrypted staging candidate prepared; source coverage remains partial and non-atomic.');return 0
    except Exception:
        print('Private source collection stopped; review private configuration and retry without sharing credentials.',file=sys.stderr);return 1
    finally:
        if session is not None:
            try:session.close()
            except Exception:pass

if __name__=='__main__':raise SystemExit(main())
