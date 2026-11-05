"""Conservative manual-task projection for isolated staging rehearsals.

The source JSON remains in encrypted source_documents. Missing/invalid notes or
creation timestamps stop the entire projection; no property is guessed. Active
Firestore manual task documents are represented as open because the source UI
removes the document to close it and stores no explicit status field.
"""
import argparse
import datetime as dt
import hashlib
import sys
from backend.storage.migrate import key_from_env,read_backup,source_parts,staging_connect,verify_records


def task_row(record):
    source_parts(record)
    path=record['path']
    if not path.startswith('manual_tasks/') or len(path.split('/'))!=2: return None
    fields=record['data']['value']
    notes=fields.get('notes');who=fields.get('who');source=fields.get('source');stamp=fields.get('created')
    if not isinstance(notes,str) or not notes.strip():raise ValueError('Task lacks valid notes')
    if who is not None and not isinstance(who,str):raise ValueError('Invalid task assignee label')
    if source is not None and not isinstance(source,str):raise ValueError('Invalid task source')
    if not isinstance(stamp,dict) or stamp.get('@type')!='timestamp':raise ValueError('Task lacks a valid creation timestamp')
    created=dt.datetime.fromisoformat(stamp['value'].replace('Z','+00:00'))
    nanos=stamp.get('nanosecond')
    if created.tzinfo is None or type(nanos) is not int or not 0<=nanos<=999999999:raise ValueError('Invalid task timestamp')
    # PostgreSQL stores microseconds; the original nanoseconds are retained.
    if created.microsecond != nanos//1000:raise ValueError('Inconsistent task timestamp')
    return ('manual_'+hashlib.sha256(path.encode()).hexdigest(),notes,who,source,created,nanos,path)


def project(conn,records,key,*,apply=False):
    rows=[row for r in records if (row:=task_row(r)) is not None]
    conn.rollback()
    with conn.transaction():
        # Keep imported source fixed through comparison and projection.
        conn.execute('LOCK TABLE source_documents IN SHARE MODE')
        verify_records(conn,records,key)
        for ident,title,who,source,created,nanos,path in rows:
            existing=conn.execute('SELECT id, title, assigned_label, source_type, created_at, source_created_nanos, property_id, status, version FROM tasks WHERE source_path=%s FOR UPDATE',(path,)).fetchone()
            if existing:
                if existing!=(ident,title,who,source,created,nanos,None,'open',1):raise ValueError('Existing task differs; reconciliation required')
            elif apply:
                conn.execute('INSERT INTO tasks(id,property_id,title,status,assigned_label,source_type,created_at,updated_at,source_created_nanos,source_path) VALUES (%s,NULL,%s,\'open\',%s,%s,%s,%s,%s,%s)',(ident,title,who,source,created,created,nanos,path))
    return len(rows)


def main(argv=None):
    p=argparse.ArgumentParser(description='Project validated manual tasks into local staging')
    p.add_argument('--input',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args(argv)
    try:
        key=key_from_env();records=read_backup(a.input,key)
        with staging_connect() as conn: count=project(conn,records,key,apply=a.apply)
        print(f'{"Staging projection" if a.apply else "Projection dry run"}: {count} manual tasks validated.')
    except Exception as exc:
        print(f'Projection stopped ({type(exc).__name__}); source and target require review.',file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
