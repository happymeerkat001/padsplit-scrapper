"""Manually apply a reviewed encrypted inbox capture to local staging only.

This command never contacts the provider and never schedules itself. The
original authenticated baseline must already be imported and projected.
"""
import argparse,hashlib,hmac,ipaddress,json,os,re,stat,sys
from padsplit_scraper import runtime
from padsplit_scraper.inbox_collection import _private_path
from backend.storage.migrate import canonical,key_from_env,read_backup,staging_connect
from backend.storage.project_inbox import catch_up_records,load_bindings,validate_provider_capture

_MAX_BACKUP_BYTES=100_000_000

def _target_identity(conn):
    """Read stable server/database/schema identity without storing its values."""
    row=conn.execute('''SELECT (pg_control_system()).system_identifier::text,
        d.oid::text,n.oid::text,host(inet_server_addr()),inet_server_port(),d.datname,
        extract(epoch from pg_postmaster_start_time())::text
        FROM pg_catalog.pg_database d JOIN pg_catalog.pg_namespace n ON n.nspname=current_schema()
        WHERE d.datname=current_database()''').fetchone()
    if not row or len(row)!=7:raise ValueError('Staging database identity unavailable')
    cluster,database,schema,address,port,name,started=row
    try:loopback=bool(address and ipaddress.ip_address(address).is_loopback)
    except ValueError:loopback=False
    if (not all(isinstance(x,str) and x.isdecimal() for x in (cluster,database,schema)) or
            not loopback or type(port) is not int or not 1<=port<=65535 or
            not isinstance(name,str) or not name.startswith('padsplit_stage_') or
            not isinstance(started,str) or not re.fullmatch(r'\d+(?:\.\d+)?',started)):
        raise ValueError('Staging database identity unavailable')
    return (cluster,database,schema,address,port,started)

def _review_digest(key,baseline,capture,bindings,baseline_bytes,capture_bytes,target):
    """Bind review to exact encrypted inputs, authenticated records and mapping."""
    if not isinstance(key,bytes) or len(key)!=32:raise ValueError('Invalid review key')
    digest=hmac.new(key,b'padsplit-inbox-review-v1\0',hashlib.sha256)
    digest.update(baseline_bytes);digest.update(capture_bytes)
    target_bytes=canonical(target);digest.update(len(target_bytes).to_bytes(8,'big'));digest.update(target_bytes)
    for group in (baseline,capture,[bindings]):
        digest.update(len(group).to_bytes(8,'big'))
        for item in group:
            data=canonical(item);digest.update(len(data).to_bytes(8,'big'));digest.update(data)
    return digest.hexdigest()

def _write_review_receipt(path,digest):
    target=_private_path(str(path))
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(target,flags,0o600)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as stream:
            fd=-1;json.dump({'version':1,'digest':digest},stream,separators=(',',':'))
            stream.flush();os.fsync(stream.fileno())
    except Exception:
        if fd!=-1:os.close(fd)
        try:target.unlink()
        except OSError:pass
        raise

def _read_review_receipt(path):
    target=_private_path(str(path));fd=os.open(target,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>256:
            raise ValueError('Private review receipt required')
        def unique_pairs(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('Duplicate receipt field')
                result[key]=value
            return result
        with os.fdopen(fd,'r',encoding='utf8') as stream:
            fd=-1;record=json.load(stream,object_pairs_hook=unique_pairs)
        if not isinstance(record,dict) or set(record)!={'version','digest'} or type(record['version']) is not int or record['version']!=1 or not isinstance(record['digest'],str) or not re.fullmatch(r'[a-f0-9]{64}',record['digest']):
            raise ValueError('Invalid review receipt')
        return record['digest']
    finally:
        if fd!=-1:os.close(fd)

def _read_private_backup(path,key):
    """Open a bounded owner-only regular file without following a final symlink."""
    target=_private_path(str(path))
    fd=os.open(target,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or
                info.st_mode & 0o077 or info.st_size>_MAX_BACKUP_BYTES):
            raise ValueError('Private bounded backup required')
        def file_digest():
            os.lseek(fd,0,os.SEEK_SET);digest=hashlib.sha256();total=0
            while True:
                part=os.read(fd,1024*1024)
                if not part:break
                total+=len(part)
                if total>_MAX_BACKUP_BYTES:raise ValueError('Private backup exceeds limit')
                digest.update(part)
            return digest.digest()
        before=file_digest();os.lseek(fd,0,os.SEEK_SET)
        # read_backup owns and closes its descriptor; keep this verified fd open
        # to ensure it reads the same file even if the pathname is replaced.
        records=read_backup(os.dup(fd),key)
        if not hmac.compare_digest(before,file_digest()):raise ValueError('Private backup changed during read')
        return records,before
    finally:
        os.close(fd)


def stage(conn,baseline_path,capture_path,mapping_path,key,*,apply=False,review_receipt=None):
    if type(apply) is not bool:raise ValueError('Invalid apply mode')
    if apply and not review_receipt:raise ValueError('Review receipt required')
    baseline,baseline_bytes=_read_private_backup(baseline_path,key)
    capture,capture_bytes=_read_private_backup(capture_path,key)
    validate_provider_capture(capture)
    bindings=load_bindings(mapping_path)
    digest=_review_digest(key,baseline,capture,bindings,baseline_bytes,capture_bytes,_target_identity(conn))
    if apply and not hmac.compare_digest(_read_review_receipt(review_receipt),digest):
        raise ValueError('Reviewed inputs changed')
    counts=catch_up_records(conn,baseline,capture,key,bindings,apply=apply)
    if not apply and review_receipt:_write_review_receipt(review_receipt,digest)
    return counts


def main(argv=None):
    parser=argparse.ArgumentParser(description='Rehearse one reviewed encrypted inbox capture in local staging')
    parser.add_argument('--enable-staging-inbox',action='store_true')
    parser.add_argument('--baseline',required=True);parser.add_argument('--capture',required=True);parser.add_argument('--mapping',required=True)
    parser.add_argument('--review-receipt',help='Owner-only review receipt outside the repository')
    parser.add_argument('--apply',action='store_true');args=parser.parse_args(argv)
    if not args.enable_staging_inbox or runtime.flag_value(os.getenv('PADSPLIT_COLLECTION_ONLY')) is not True or runtime.running_in_ci() or (args.apply and not args.review_receipt):
        print('Staging inbox projection is disabled; explicit collection-only mode is required.',file=sys.stderr);return 2
    try:
        key=key_from_env()
        with staging_connect() as conn:counts=stage(conn,args.baseline,args.capture,args.mapping,key,apply=args.apply,review_receipt=args.review_receipt)
        print(f'{"Staging projection" if args.apply else "Staging dry run"}: {counts[0]} conversations and {counts[1]} messages validated. Source coverage remains filtered and non-atomic.')
        return 0
    except Exception:
        print('Staging inbox projection stopped; review private inputs and configuration.',file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
