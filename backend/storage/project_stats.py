"""Conservative, staging-only projection of Firestore stats JSON into PostgreSQL.

The encrypted source record remains authoritative. JSON numbers are passed to
PostgreSQL as their original lexical text so monetary precision is not rounded
through binary floats. Unknown fields and duplicate keys fail closed.
"""
import argparse
import datetime as dt
import decimal
import json
import sys
from backend.storage.migrate import key_from_env, read_backup, source_parts, staging_connect, verify_records


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('Duplicate JSON key')
        result[key]=value
    return result


def _reject_constant(_value):
    raise ValueError('Non-finite JSON number')


def stats_row(record):
    source_parts(record)
    path=record['path']
    if not path.startswith('stats/'): return None
    parts=path.split('/')
    if len(parts)!=2 or parts[1] not in {'latest','monthly_history'}: raise ValueError('Unknown statistics source')
    fields=record['data']['value']
    if set(fields)!={'json','updated_at'}: raise ValueError('Unexpected statistics fields')
    payload,stamp=fields['json'],fields['updated_at']
    if not isinstance(payload,str) or len(payload)>2_000_000: raise ValueError('Invalid statistics JSON')
    try:
        value=json.loads(payload,parse_float=decimal.Decimal,parse_constant=_reject_constant,object_pairs_hook=_unique_object)
    except (ValueError,TypeError,decimal.InvalidOperation): raise ValueError('Invalid statistics JSON') from None
    if not isinstance(value,dict): raise ValueError('Statistics must be an object')
    try: observed=dt.datetime.fromisoformat(stamp.replace('Z','+00:00')) if isinstance(stamp,str) else None
    except ValueError: observed=None
    if observed is None or observed.tzinfo is None: raise ValueError('Invalid statistics timestamp')
    return parts[1],payload,observed,path


def project(conn,records,key,*,apply=False):
    rows=[row for record in records if (row:=stats_row(record)) is not None]
    conn.rollback()
    with conn.transaction():
        conn.execute('LOCK TABLE source_documents IN SHARE MODE')
        conn.execute('LOCK TABLE statistics IN SHARE ROW EXCLUSIVE MODE')
        verify_records(conn,records,key)
        for ident,payload,observed,path in rows:
            row=conn.execute('SELECT id, data = %s::jsonb, observed_at, source_path FROM statistics WHERE id=%s OR source_path=%s FOR UPDATE',(payload,ident,path)).fetchall()
            if row:
                if len(row)!=1 or row[0]!=(ident,True,observed,path): raise ValueError('Statistics conflict')
            elif apply:
                conn.execute('INSERT INTO statistics(id,data,observed_at,source_path) VALUES (%s,%s::jsonb,%s,%s)',(ident,payload,observed,path))
    return len(rows)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Project validated statistics into local staging')
    parser.add_argument('--input',required=True);parser.add_argument('--apply',action='store_true');args=parser.parse_args(argv)
    try:
        key=key_from_env();records=read_backup(args.input,key)
        with staging_connect() as conn: count=project(conn,records,key,apply=args.apply)
        print(f'{"Staging projection" if args.apply else "Projection dry run"}: {count} statistics documents validated.')
    except Exception as exc:
        print(f'Statistics projection stopped ({type(exc).__name__}); source and target require review.',file=sys.stderr);return 1
    return 0


if __name__=='__main__':sys.exit(main())
