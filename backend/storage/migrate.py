"""Read-only Firestore export and encrypted, staging-only PostgreSQL intake.

The intake preserves source documents; it does not populate domain tables or
perform a production cutover. Export files and database payloads are encrypted.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b'PADFIRE1\n'
KEY_VERSION = 'v1'
MAX_RECORD_BYTES = 2_000_000


def key_from_env(name='PADSPLIT_MIGRATION_KEY'):
    try:
        key = base64.b64decode(os.environ[name], validate=True)
    except (KeyError, ValueError):
        raise ValueError('PADSPLIT_MIGRATION_KEY must contain a base64-encoded 32-byte key') from None
    if len(key) != 32:
        raise ValueError('PADSPLIT_MIGRATION_KEY must contain a base64-encoded 32-byte key')
    return key


def encode_value(value):
    """Encode Firestore values without conflating missing fields and nulls."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not __import__('math').isfinite(value):
            raise ValueError('Non-finite numbers require explicit migration handling')
        return {'@type':'float','value':value.hex()}
    if isinstance(value, dt.datetime):
        if value.tzinfo is None: raise ValueError('Naive timestamp')
        # Some DatetimeWithNanoseconds instances expose zero nanoseconds while
        # carrying a nonzero datetime microsecond. Preserve the actual value.
        nanos=getattr(value,'nanosecond',None)
        if nanos is None or (nanos == 0 and value.microsecond):nanos=value.microsecond*1000
        if type(nanos) is not int or nanos//1000!=value.microsecond:raise ValueError('Inconsistent source timestamp')
        return {'@type':'timestamp','value':value.isoformat(),'nanosecond':nanos}
    if isinstance(value, bytes):
        return {'@type':'bytes','value':base64.b64encode(value).decode('ascii')}
    if isinstance(value, list): return [encode_value(x) for x in value]
    if isinstance(value, dict):
        if any(not isinstance(k,str) for k in value): raise ValueError('Non-string field')
        # Tag maps too, so an original @type field cannot collide with our tags.
        return {'@type':'map','value':{k:encode_value(v) for k,v in value.items()}}
    cls=type(value).__name__
    if cls == 'DocumentReference' and hasattr(value,'path'):
        return {'@type':'reference','value':value.path}
    if cls == 'GeoPoint' and hasattr(value,'latitude') and hasattr(value,'longitude'):
        return {'@type':'geopoint','latitude':float(value.latitude).hex(),'longitude':float(value.longitude).hex()}
    raise ValueError('Unsupported Firestore value; export stopped')


def canonical(value):
    return json.dumps(value,ensure_ascii=True,separators=(',',':'),sort_keys=True).encode('utf8')


def encrypted_backup(records, destination, key):
    """Write exclusive-created authenticated frames and a required manifest."""
    aes=AESGCM(key); digest=hashlib.sha256(); count=0
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(MAGIC)
            for rec in records:
                payload=canonical({'kind':'record','record':rec})
                if len(payload)>MAX_RECORD_BYTES: raise ValueError('Document too large')
                nonce=secrets.token_bytes(12); aad=str(count).encode('ascii')
                frame=base64.b64encode(nonce+aes.encrypt(nonce,payload,aad))+b'\n'
                f.write(frame); digest.update(frame); count+=1
            manifest=canonical({'kind':'manifest','count':count,'sha256':digest.hexdigest()})
            nonce=secrets.token_bytes(12); f.write(base64.b64encode(nonce+aes.encrypt(nonce,manifest,str(count).encode()))+b'\n')
            f.flush(); os.fsync(f.fileno())
    except BaseException:
        # Incomplete encrypted file is deliberately retained for diagnosis and never accepted as a backup.
        raise
    return count


def read_backup(path,key):
    """Authenticate all frames and detect truncation before returning records."""
    aes=AESGCM(key); digest=hashlib.sha256(); records=[]; terminal=False
    with open(path,'rb') as f:
        if f.readline()!=MAGIC: raise ValueError('Invalid backup header')
        for raw in f:
            if terminal or len(raw)>MAX_RECORD_BYTES*2 or not raw.endswith(b'\n'): raise ValueError('Invalid backup framing')
            try:
                blob=base64.b64decode(raw.strip(),validate=True); obj=json.loads(aes.decrypt(blob[:12],blob[12:],str(len(records)).encode()))
            except Exception: raise ValueError('Backup authentication failed') from None
            if obj.get('kind')=='record':
                digest.update(raw); records.append(obj['record'])
            elif obj.get('kind')=='manifest':
                if obj.get('count')!=len(records) or obj.get('sha256')!=digest.hexdigest(): raise ValueError('Invalid backup manifest')
                terminal=True
            else: raise ValueError('Invalid backup record')
    if not terminal: raise ValueError('Backup manifest missing')
    return records


def walk_firestore(db):
    """Enumerate root documents and every discovered nested subcollection read-only."""
    def visit(collection):
        # list_documents uses show_missing=True, so descendants of deleted
        # parent documents are traversed instead of silently disappearing.
        for ref in collection.list_documents():
            doc=ref.get()
            path=ref.path
            if not isinstance(path,str) or len(path.split('/'))%2: raise ValueError('Invalid source path')
            if doc.exists:
                yield {'path':path,'update_time':doc.update_time.isoformat() if doc.update_time else None,
                       'data':encode_value(doc.to_dict())}
            for child in ref.collections():
                yield from visit(child)
    for collection in db.collections(): yield from visit(collection)


def staging_connect():
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ.get('DATABASE_URL',''); parts=conninfo_to_dict(dsn)
    if parts.get('host') not in ('127.0.0.1','localhost','::1') or not parts.get('dbname','').startswith('padsplit_stage_'):
        raise ValueError('Only loopback padsplit_stage_* databases are supported by this rehearsal tool')
    # libpq uses hostaddr in preference to resolving host, including PGHOSTADDR
    # from the environment. Force a numeric loopback target, and reject an
    # explicit conflicting hostaddr rather than silently changing operator intent.
    address='::1' if parts['host']=='::1' else '127.0.0.1'
    if parts.get('hostaddr') not in (None,address):
        raise ValueError('Only loopback padsplit_stage_* databases are supported by this rehearsal tool')
    return psycopg.connect(dsn,hostaddr=address)


def source_parts(rec):
    path=rec.get('path'); parts=path.split('/') if isinstance(path,str) else []
    if len(parts)<2 or len(parts)%2 or any(not p for p in parts): raise ValueError('Invalid source path')
    if not isinstance(rec.get('data'),dict) or rec['data'].get('@type')!='map': raise ValueError('Invalid source document')
    stamp=rec.get('update_time')
    if stamp is not None:
        parsed=dt.datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if parsed.tzinfo is None: raise ValueError('Invalid source timestamp')
    return '/'.join(parts[:-1]),parts[-1]


def compare_records(baseline,candidate):
    """Count whole-document differences without exposing paths or content."""
    def index(records):
        result={}
        for rec in records:
            source_parts(rec)
            if rec['path'] in result:raise ValueError('Duplicate source document in backup')
            result[rec['path']]=canonical(rec)
        return result
    before=index(baseline);after=index(candidate)
    common=before.keys() & after.keys()
    unchanged=sum(before[path]==after[path] for path in common)
    return {'added':len(after.keys()-before.keys()),'changed':len(common)-unchanged,
            'deleted':len(before.keys()-after.keys()),'unchanged':unchanged}


def import_records(conn,records,key,dry_run=False):
    """Idempotent batches. Changed source records fail closed for later reconciliation."""
    prepared=[]; paths=set()
    for rec in records:
        collection,docid=source_parts(rec); path=rec['path']
        if path in paths: raise ValueError('Duplicate source document in backup')
        paths.add(path); plain=canonical(rec)
        digest=hmac.new(key,b'source-fingerprint\0'+plain,hashlib.sha256).digest()
        nonce=secrets.token_bytes(12); ciphertext=AESGCM(key).encrypt(nonce,plain,path.encode())
        prepared.append((path,collection,docid,rec.get('update_time'),ciphertext,nonce,KEY_VERSION,digest))
    changed=0
    # Check every existing row before writing any batch. Concurrent changes are
    # checked again inside each transaction with a conditional conflict clause.
    for row in prepared:
        current=conn.execute('SELECT fingerprint FROM source_documents WHERE source_path=%s',(row[0],)).fetchone()
        if current and not hmac.compare_digest(bytes(current[0]),row[-1]): raise ValueError('Source conflict; reconcile explicitly before import')
    conn.rollback()
    if dry_run: return len(prepared)
    for start in range(0,len(prepared),100):
        with conn.transaction():
            for row in prepared[start:start+100]:
                result=conn.execute('''INSERT INTO source_documents(source_path,collection_path,document_id,source_update_time,payload_ciphertext,payload_nonce,key_version,fingerprint) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(source_path) DO UPDATE SET source_path=EXCLUDED.source_path
                   WHERE source_documents.fingerprint=EXCLUDED.fingerprint RETURNING source_path''',row).fetchone()
                if result is None: raise ValueError('Concurrent source conflict')
                changed+=1
    return changed


def verify_records(conn,records,key):
    expected={}
    for rec in records:
        source_parts(rec); path=rec['path']
        if path in expected: raise ValueError('Duplicate source document in backup')
        expected[path]=rec
    if conn.execute('SELECT count(*) FROM source_documents').fetchone()[0] != len(expected):
        raise ValueError('Source and target document counts differ')
    for path,rec in expected.items():
        collection,docid=source_parts(rec)
        row=conn.execute('''SELECT payload_ciphertext,payload_nonce,fingerprint,collection_path,document_id,key_version,
                            source_update_time IS NOT DISTINCT FROM %s::timestamptz
                            FROM source_documents WHERE source_path=%s''',(rec.get('update_time'),path)).fetchone()
        if not row: raise ValueError('Missing imported document')
        if (row[3],row[4],row[5],row[6])!=(collection,docid,KEY_VERSION,True):
            raise ValueError('Imported source metadata mismatch')
        plain=AESGCM(key).decrypt(bytes(row[1]),bytes(row[0]),path.encode())
        if not hmac.compare_digest(bytes(row[2]),hmac.new(key,b'source-fingerprint\0'+canonical(rec),hashlib.sha256).digest()) or plain!=canonical(rec):
            raise ValueError('Imported document mismatch')
    return len(expected)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Encrypted Firestore export and staging intake')
    sub=parser.add_subparsers(dest='command',required=True)
    export=sub.add_parser('export'); export.add_argument('--output',required=True); export.add_argument('--project',required=True); export.add_argument('--read-live',action='store_true')
    imp=sub.add_parser('import'); imp.add_argument('--input',required=True); imp.add_argument('--apply',action='store_true')
    verify=sub.add_parser('verify');verify.add_argument('--input',required=True)
    compare=sub.add_parser('compare');compare.add_argument('--baseline',required=True);compare.add_argument('--candidate',required=True)
    args=parser.parse_args(argv)
    try:
        key=key_from_env()
        if args.command=='export':
            if not args.read_live or args.project!='padsplit-scrapper': raise ValueError('Explicit source project and --read-live are required')
            destination=pathlib.Path(args.output).resolve()
            repo=pathlib.Path(__file__).resolve().parents[2]
            if destination.is_relative_to(repo) or not destination.parent.is_dir() or destination.parent.stat().st_mode & 0o077:
                raise ValueError('Backup must be outside the repository in a private directory')
            import firebase_admin
            from firebase_admin import firestore
            app=firebase_admin.initialize_app(options={'projectId':args.project})
            count=encrypted_backup(walk_firestore(firestore.client(app=app)),destination,key)
            print(f'Encrypted export complete: {count} documents. Source was read without a cross-collection snapshot.')
        elif args.command=='compare':
            baseline=read_backup(args.baseline,key)
            candidate_key=key_from_env('PADSPLIT_COMPARE_KEY') if 'PADSPLIT_COMPARE_KEY' in os.environ else key
            candidate=read_backup(args.candidate,candidate_key)
            counts=compare_records(baseline,candidate)
            print('Authenticated backup comparison: '+', '.join(f'{name}={count}' for name,count in counts.items()))
        else:
            records=read_backup(args.input,key)
            with staging_connect() as conn:
                if args.command=='import':
                    count=import_records(conn,records,key,dry_run=not args.apply)
                    print(f'{"Staging import" if args.apply else "Dry run"}: {count} documents checked.')
                else:
                    print(f'Encrypted staging verification: {verify_records(conn,records,key)} documents matched.')
    except Exception as exc:
        # Never print provider exception messages, DSNs, document paths or payloads.
        print(f'Migration stopped ({type(exc).__name__}); review configuration and private diagnostics.',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__': sys.exit(main())
