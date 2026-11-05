"""Conservative staging projection of notes, vendors and shared templates.

All source documents must first be verified against encrypted intake. Unknown
fields or ambiguous/malformed template pairs abort without writing anything.
"""
import argparse
import hashlib
import re
import sys
from psycopg.types.json import Jsonb
from backend.storage.migrate import key_from_env,read_backup,source_parts,staging_connect,verify_records

_ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')


def _fields(record):
    source_parts(record)
    data=record['data']
    if not isinstance(data.get('value'),dict):raise ValueError('Invalid source map')
    return data['value']


def rows_for(record):
    path=record['path'];parts=path.split('/')
    if parts[0] not in ('notes','vendors','templates'):return []
    if len(parts)!=2:raise ValueError('Nested collection requires explicit mapping')
    fields=_fields(record)
    if parts[0]=='notes':
        if not _ID.fullmatch(parts[1]) or set(fields)!={'text'} or not isinstance(fields['text'],str):
            raise ValueError('Note requires an exact string text field')
        return [('note',parts[1],fields['text'],path)]
    if parts[0]=='vendors':
        if set(fields)-{'name','specialty','contact','location'} or not isinstance(fields.get('name'),str) or not fields['name'].strip():
            raise ValueError('Vendor fields require explicit mapping')
        for k,v in fields.items():
            if not isinstance(v,str) and v is not None:raise ValueError('Invalid vendor value')
        data={k:v for k,v in fields.items() if k!='name'}
        return [('vendor','vendor_'+hashlib.sha256(path.encode()).hexdigest(),fields['name'],data,path)]
    if parts[1]!='shared':raise ValueError('Unsupported template source')
    # Explicitly reject unknown metadata, non-canonical indexes, partial pairs,
    # non-string values and indexes outside the current nine-slot UI.
    if any(not re.fullmatch(r'[nt][0-8]',k) for k in fields):raise ValueError('Unsupported template field')
    indexes=sorted({int(k[1:]) for k in fields})
    out=[]
    for i in indexes:
        n,t=f'n{i}',f't{i}'
        if n not in fields or t not in fields or not isinstance(fields[n],str) or not isinstance(fields[t],str):
            raise ValueError('Incomplete or invalid template pair')
        out.append(('template',f'template_shared_{i}',fields[n],fields[t],path,str(i)))
    return out


def project(conn,records,key,*,apply=False):
    rows=[row for record in records for row in rows_for(record)]
    conn.rollback()
    with conn.transaction():
        conn.execute('LOCK TABLE source_documents IN SHARE MODE')
        verify_records(conn,records,key)
        for row in rows:
            kind=row[0]
            if kind=='note':
                _,ident,text,path=row
                existing=conn.execute('SELECT id,text,version FROM notes WHERE source_path=%s FOR UPDATE',(path,)).fetchone()
                if existing and existing!=(ident,text,1):raise ValueError('Note conflict')
                if not existing and apply:conn.execute('INSERT INTO notes(id,text,source_path) VALUES (%s,%s,%s)',(ident,text,path))
            elif kind=='vendor':
                _,ident,name,data,path=row
                existing=conn.execute('SELECT id,name,data,version FROM vendors WHERE source_path=%s FOR UPDATE',(path,)).fetchone()
                if existing and existing!=(ident,name,data,1):raise ValueError('Vendor conflict')
                if not existing and apply:conn.execute('INSERT INTO vendors(id,name,data,source_path) VALUES (%s,%s,%s,%s)',(ident,name,Jsonb(data),path))
            else:
                _,ident,name,body,path,index=row
                existing=conn.execute('SELECT id,name,body,version FROM templates WHERE source_path=%s AND source_field_key=%s FOR UPDATE',(path,index)).fetchone()
                if existing and existing!=(ident,name,body,1):raise ValueError('Template conflict')
                if not existing and apply:conn.execute('INSERT INTO templates(id,name,body,source_path,source_field_key) VALUES (%s,%s,%s,%s,%s)',(ident,name,body,path,index))
    return len(rows)


def main(argv=None):
    p=argparse.ArgumentParser(description='Project selected shared documents into isolated staging')
    p.add_argument('--input',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args(argv)
    try:
        key=key_from_env();records=read_backup(a.input,key)
        with staging_connect() as conn:count=project(conn,records,key,apply=a.apply)
        print(f'{"Staging projection" if a.apply else "Projection dry run"}: {count} shared records validated.')
    except Exception as exc:
        print(f'Projection stopped ({type(exc).__name__}); source and target require review.',file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
