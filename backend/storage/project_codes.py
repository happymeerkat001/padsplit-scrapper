"""Conservative, encrypted access-code staging projection.

The original Firestore export remains encrypted separately. Schema layouts and
complete string maps are encrypted under the distinct server-managed codes key.
No production database or remote writes are accepted by the staging connector.
"""
import argparse
import datetime as dt
import os
import re
import sys
from backend.storage.migrate import key_from_env,read_backup,source_parts,staging_connect,verify_records
from backend.storage.secrets import load_key,encrypt_codes,decrypt_codes,encrypt_code_schema,decrypt_code_schema
from padsplit_scraper.private_codes_schema import validate_schema


def _decode_schema(v):
    if isinstance(v,(str,bool)) or v is None:return v
    if isinstance(v,list):return [_decode_schema(x) for x in v]
    if isinstance(v,dict) and set(v)=={'@type','value'} and v['@type']=='map' and isinstance(v['value'],dict):
        return {k:_decode_schema(x) for k,x in v['value'].items()}
    raise ValueError('Unsupported schema value')


def _timestamp(v):
    if not isinstance(v,dict) or set(v)!={'@type','value','nanosecond'} or v.get('@type')!='timestamp' or not isinstance(v['value'],str):
        raise ValueError('Unsupported source timestamp')
    parsed=dt.datetime.fromisoformat(v['value'].replace('Z','+00:00'));nanos=v['nanosecond']
    if parsed.tzinfo is None or type(nanos) is not int or not 0<=nanos<=999999999 or parsed.microsecond != nanos//1000:
        raise ValueError('Invalid source timestamp')
    return parsed,nanos


def rows_for(records):
    schema=None;docs={};history_checked=False
    for rec in records:
        source_parts(rec);path=rec['path'];parts=path.split('/')
        if path=='private_configuration/codes_schema':
            if schema is not None:raise ValueError('Duplicate schema')
            schema=_decode_schema(rec['data'])
        elif parts[:2]==['private_configuration','codes_schema']:
            raise ValueError('Schema descendants need explicit handling')
        elif parts[0]=='property_codes':
            if len(parts)!=2:
                if len(parts)!=4 or parts[2]!='code_versions':raise ValueError('Unknown code descendant')
                if not history_checked:
                    from backend.storage.project_code_history import history_rows
                    history_rows(records);history_checked=True
                continue
            if parts[1] in docs:raise ValueError('Duplicate code source')
            source=rec['data'].get('value')
            if not isinstance(source,dict) or 'updatedAt' not in source:raise ValueError('Code timestamp missing')
            updated,nanos=_timestamp(source['updatedAt'])
            values={k:v for k,v in source.items() if k!='updatedAt'}
            if any(not isinstance(k,str) or not isinstance(v,str) for k,v in values.items()):raise ValueError('Unsupported code field')
            docs[parts[1]]=(values,updated,nanos,path)
    if schema is None:raise ValueError('Schema missing')
    validate_schema(schema)
    schemas={p['slug']:p for p in schema['properties']}
    if set(schemas)!=set(docs):raise ValueError('Schema and code source IDs differ')
    return [(slug,schemas[slug],*docs[slug]) for slug in sorted(schemas)]


def project(conn,records,migration_key,codes_key,key_version,*,apply=False,verify_only=False):
    if migration_key==codes_key:raise ValueError('Codes and backup keys must differ')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',key_version):raise ValueError('Invalid key version')
    rows=rows_for(records)
    conn.rollback()
    with conn.transaction():
        conn.execute('LOCK TABLE source_documents IN SHARE MODE')
        verify_records(conn,records,migration_key)
        for prop,schema,values,updated,nanos,path in rows:
            property_row=conn.execute('SELECT name FROM properties WHERE id=%s FOR UPDATE',(prop,)).fetchone()
            if property_row and property_row[0]!=schema['address']:raise ValueError('Property conflict')
            if not property_row and verify_only:raise ValueError('Property missing')
            if not property_row and apply:conn.execute('INSERT INTO properties(id,name) VALUES (%s,%s)',(prop,schema['address']))
            layout=conn.execute('SELECT ciphertext,nonce,key_version,version,source_path FROM access_code_schemas WHERE property_id=%s FOR UPDATE',(prop,)).fetchone()
            if layout:
                ciphertext,nonce,stored_key,version,source_path=layout
                if stored_key!=key_version or version!=1 or source_path!='private_configuration/codes_schema':raise ValueError('Schema metadata conflict')
                if decrypt_code_schema(bytes(ciphertext),bytes(nonce),property_id=prop,version=1,key_version=key_version,key=codes_key)!=schema:raise ValueError('Schema content conflict')
            elif verify_only:raise ValueError('Schema missing')
            elif apply:
                nonce,cipher=encrypt_code_schema(schema,property_id=prop,version=1,key_version=key_version,key=codes_key)
                conn.execute('INSERT INTO access_code_schemas(property_id,ciphertext,nonce,key_version,version,source_path) VALUES (%s,%s,%s,%s,1,%s)',(prop,cipher,nonce,key_version,'private_configuration/codes_schema'))
            current=conn.execute('SELECT ciphertext,nonce,key_version,version,source_path,updated_at,source_updated_nanos FROM access_codes WHERE property_id=%s FOR UPDATE',(prop,)).fetchone()
            if current:
                ciphertext,nonce,stored_key,version,source_path,stored_time,stored_nanos=current
                if stored_key!=key_version or version!=1 or source_path!=path or stored_time!=updated or stored_nanos!=nanos:raise ValueError('Codes metadata conflict')
                if decrypt_codes(bytes(ciphertext),bytes(nonce),property_id=prop,version=1,key_version=key_version,key=codes_key)!=values:raise ValueError('Codes content conflict')
            elif verify_only:raise ValueError('Codes missing')
            elif apply:
                nonce,cipher=encrypt_codes(values,property_id=prop,version=1,key_version=key_version,key=codes_key)
                conn.execute('INSERT INTO access_codes(property_id,ciphertext,nonce,key_version,version,source_path,updated_at,source_updated_nanos) VALUES (%s,%s,%s,%s,1,%s,%s,%s)',(prop,cipher,nonce,key_version,path,updated,nanos))
    return len(rows)


def main(argv=None):
    p=argparse.ArgumentParser(description='Project private codes and layouts into isolated staging')
    p.add_argument('--input',required=True);mode=p.add_mutually_exclusive_group();mode.add_argument('--apply',action='store_true');mode.add_argument('--verify',action='store_true');a=p.parse_args(argv)
    try:
        migration_key=key_from_env();codes_key=load_key();version=os.environ.get('PADSPLIT_CODES_KEY_VERSION','')
        records=read_backup(a.input,migration_key)
        with staging_connect() as conn:count=project(conn,records,migration_key,codes_key,version,apply=a.apply,verify_only=a.verify)
        print(f'{"Verified" if a.verify else "Staging projection" if a.apply else "Projection dry run"}: {count} code documents and schemas checked.')
    except Exception as exc:
        print(f'Codes projection stopped ({type(exc).__name__}); source and target require private review.',file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
