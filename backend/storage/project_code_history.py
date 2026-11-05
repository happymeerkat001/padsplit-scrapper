"""Project authenticated Firestore code history into isolated staging.

Legacy history has no monotonic version. `source_version=1` is used solely as
an encryption AAD context; ordering and source identities are retained
separately. No secrets, source identifiers, or values are printed.
"""
import argparse
import hashlib
import hmac
import os
import re
import sys
from backend.storage.migrate import key_from_env,read_backup,source_parts,staging_connect,verify_records
from backend.storage.project_codes import _timestamp
from backend.storage.secrets import load_key,encrypt_codes,decrypt_codes,decrypt_code_schema
from padsplit_scraper.codes_history import content_hash, RESERVED_KEYS

_VERSION=1


def history_rows(records):
    parents=set();seen=set();histories=[]
    for record in records:
        source_parts(record);path=record['path'];parts=path.split('/')
        if path in seen:raise ValueError('Duplicate source document')
        seen.add(path)
        if parts[0]!='property_codes':continue
        if len(parts)==2:
            parents.add(parts[1]);continue
        if len(parts)!=4 or parts[2]!='code_versions':raise ValueError('Unsupported code history descendant')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}',parts[1]) or len(parts[3])>1500:raise ValueError('Invalid history identity')
        values=record['data'].get('value')
        if not isinstance(values,dict) or set(values)!={'fields','contentHash','createdAt','expireAt','source'}:raise ValueError('Unrecognized history fields')
        encoded=values['fields']
        if not isinstance(encoded,dict) or set(encoded)!={'@type','value'} or encoded.get('@type')!='map' or not isinstance(encoded['value'],dict):raise ValueError('Invalid history code map')
        fields=encoded['value']
        if len(fields)>200 or any(not isinstance(k,str) or not k or len(k)>200 or k in RESERVED_KEYS or not isinstance(v,str) or len(v)>10000 for k,v in fields.items()):raise ValueError('Invalid history code values')
        digest=values['contentHash'];origin=values['source']
        if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest) or not hmac.compare_digest(digest,content_hash(fields)):
            raise ValueError('History content hash mismatch')
        if origin not in ('page','catchup'):raise ValueError('Unknown history origin')
        created,nanos=_timestamp(values['createdAt']);expires,expiry_nanos=_timestamp(values['expireAt'])
        if expires<=created:raise ValueError('Invalid history expiry')
        histories.append({'id':'legacy_'+hashlib.sha256(path.encode()).hexdigest(),'property_id':parts[1],
                          'source_path':path,'values':fields,'created_at':created,'created_nanos':nanos,
                          'expire_at':expires,'expire_nanos':expiry_nanos,'origin':origin})
    if any(row['property_id'] not in parents for row in histories):raise ValueError('History parent missing')
    return histories


def project(conn,records,migration_key,codes_key,key_version,*,apply=False,verify_only=False):
    from backend.storage.project_codes import rows_for
    if migration_key==codes_key or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',key_version):raise ValueError('Invalid encryption configuration')
    history=history_rows(records);parents=rows_for(records)
    parent_map={row[0]:row for row in parents}
    conn.rollback()
    with conn.transaction():
        conn.execute('LOCK TABLE source_documents IN SHARE MODE')
        conn.execute('LOCK TABLE properties IN SHARE MODE')
        conn.execute('LOCK TABLE access_codes IN SHARE MODE')
        conn.execute('LOCK TABLE access_code_schemas IN SHARE MODE')
        conn.execute('LOCK TABLE code_history IN SHARE ROW EXCLUSIVE MODE')
        verify_records(conn,records,migration_key)
        if verify_only and conn.execute("SELECT count(*) FROM code_history WHERE source_path ~ '^property_codes/[^/]+/code_versions/[^/]+$'").fetchone()[0]!=len(history):
            raise ValueError('History source and target counts differ')
        for prop in {h['property_id'] for h in history}:
            schema,values,updated,nanos,path=parent_map[prop][1:]
            name=conn.execute('SELECT name FROM properties WHERE id=%s',(prop,)).fetchone()
            current=conn.execute('SELECT ciphertext,nonce,key_version,version,source_path,updated_at,source_updated_nanos FROM access_codes WHERE property_id=%s',(prop,)).fetchone()
            layout=conn.execute('SELECT ciphertext,nonce,key_version,version,source_path FROM access_code_schemas WHERE property_id=%s',(prop,)).fetchone()
            if name!=(schema['address'],) or not current or not layout:raise ValueError('Verified parent projection missing')
            if current[2:]!=(key_version,1,path,updated,nanos) or decrypt_codes(bytes(current[0]),bytes(current[1]),property_id=prop,version=1,key_version=key_version,key=codes_key)!=values:raise ValueError('Current codes mismatch')
            if layout[2:]!=(key_version,1,'private_configuration/codes_schema') or decrypt_code_schema(bytes(layout[0]),bytes(layout[1]),property_id=prop,version=1,key_version=key_version,key=codes_key)!=schema:raise ValueError('Code schema mismatch')
        for row in history:
            existing=conn.execute('''SELECT id,property_id,ciphertext,nonce,key_version,source_version,created_at,source_path,
                source_created_nanos,source_expire_at,source_expire_nanos,source_origin
                FROM code_history WHERE id=%s OR source_path=%s''',(row['id'],row['source_path'])).fetchall()
            if existing:
                if len(existing)!=1:raise ValueError('History identity conflict')
                item=existing[0]
                if (item[0],item[1],item[4],item[5],item[6],item[7],item[8],item[9],item[10],item[11]) != (row['id'],row['property_id'],key_version,_VERSION,row['created_at'],row['source_path'],row['created_nanos'],row['expire_at'],row['expire_nanos'],row['origin']):raise ValueError('History metadata conflict')
                if decrypt_codes(bytes(item[2]),bytes(item[3]),property_id=row['property_id'],version=_VERSION,key_version=key_version,key=codes_key)!=row['values']:raise ValueError('History content conflict')
            elif verify_only:raise ValueError('History missing')
            elif apply:
                nonce,cipher=encrypt_codes(row['values'],property_id=row['property_id'],version=_VERSION,key_version=key_version,key=codes_key)
                conn.execute('''INSERT INTO code_history(id,property_id,ciphertext,nonce,key_version,source_version,created_at,source_path,source_created_nanos,source_expire_at,source_expire_nanos,source_origin)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(row['id'],row['property_id'],cipher,nonce,key_version,_VERSION,row['created_at'],row['source_path'],row['created_nanos'],row['expire_at'],row['expire_nanos'],row['origin']))
    return len(history)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Project private code history into isolated staging')
    parser.add_argument('--input',required=True);mode=parser.add_mutually_exclusive_group();mode.add_argument('--apply',action='store_true');mode.add_argument('--verify',action='store_true');args=parser.parse_args(argv)
    try:
        migration_key=key_from_env();codes_key=load_key();version=os.environ.get('PADSPLIT_CODES_KEY_VERSION','')
        records=read_backup(args.input,migration_key)
        with staging_connect() as conn:count=project(conn,records,migration_key,codes_key,version,apply=args.apply,verify_only=args.verify)
        print(f'{"Verified" if args.verify else "Staging projection" if args.apply else "Projection dry run"}: {count} history documents checked.')
    except Exception as exc:
        print(f'Code-history projection stopped ({type(exc).__name__}); source and target require private review.',file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
