"""Project staged discussions with explicit mappings for external task IDs.

This accepts legacy empty-string text without fabricating a message. Every
external reference must map to an existing, explicitly identified target task
and expected scope/source path. All source fields remain in encrypted staging.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from backend.storage.migrate import key_from_env,read_backup,source_parts,staging_connect,verify_records

_TARGET_ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')


def _ref_kind_value(value):
    if isinstance(value,str) and 1<=len(value)<=2048:return ('string',value)
    if type(value) is int and -(2**63)<=value<2**63:return ('integer',value)
    raise ValueError('Invalid source reference')


def validate_mappings(data):
    if not isinstance(data,dict) or set(data)!={'version','bindings'} or type(data['version']) is not int or data['version']!=1 or not isinstance(data['bindings'],list) or len(data['bindings'])>10000:
        raise ValueError('Invalid mapping document')
    result={}
    for item in data['bindings']:
        if not isinstance(item,dict) or set(item)!={'source_type','source_value','target_task_id','expected_property_id','expected_source_path'}:
            raise ValueError('Invalid mapping binding')
        source=_ref_kind_value(item['source_value'])
        if item['source_type']!=source[0] or source in result:raise ValueError('Duplicate or inconsistent mapping binding')
        target=item['target_task_id'];prop=item['expected_property_id'];path=item['expected_source_path']
        if not isinstance(target,str) or not _TARGET_ID.fullmatch(target):raise ValueError('Invalid mapped target')
        if (prop is not None and (not isinstance(prop,str) or not prop or len(prop)>2048)) or (path is not None and (not isinstance(path,str) or not path or len(path)>4096)):
            raise ValueError('Invalid expected target identity')
        result[source]=(target,prop,path)
    return result


def load_mappings(path):
    source=Path(path)
    if source.is_symlink():raise ValueError('Mapping file must not be a symlink')
    resolved=source.resolve(strict=True);repo=Path(__file__).resolve().parents[2]
    parent_stat=resolved.parent.stat()
    if resolved.is_relative_to(repo) or not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid!=os.geteuid() or parent_stat.st_mode & 0o077:
        raise ValueError('Mapping directory must be private and outside the repository')
    flags=os.O_RDONLY | getattr(os,'O_NOFOLLOW',0)
    fd=os.open(resolved,flags)
    try:
        metadata=os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.geteuid() or metadata.st_mode & 0o077 or metadata.st_size>1_000_000:
            raise ValueError('Mapping file must be private and bounded')
        with os.fdopen(fd,'r',encoding='utf8') as stream:
            fd=-1
            def unique_pairs(pairs):
                result={}
                for key,value in pairs:
                    if key in result:raise ValueError('Duplicate mapping field')
                    result[key]=value
                return result
            return validate_mappings(json.load(stream,object_pairs_hook=unique_pairs))
    finally:
        if fd!=-1:os.close(fd)


def discussion_row(rec):
    path=rec['path']; parts=path.split('/')
    if parts[0]!='task_messages':return None
    if len(parts)!=2:raise ValueError('Nested discussions require explicit mapping')
    source_parts(rec)
    fields=rec['data'].get('value')
    if not isinstance(fields,dict) or set(fields)-{'taskId','role','status','text','created'}:
        raise ValueError('Unknown discussion fields')
    ref=_ref_kind_value(fields.get('taskId'));text=fields.get('text');role=fields.get('role');status=fields.get('status');stamp=fields.get('created')
    if not isinstance(text,str):raise ValueError('Invalid discussion text')
    if (role is not None and not isinstance(role,str)) or (status is not None and not isinstance(status,str)):raise ValueError('Invalid discussion labels')
    if not isinstance(stamp,dict) or stamp.get('@type')!='timestamp':raise ValueError('Discussion timestamp missing')
    created=dt.datetime.fromisoformat(stamp['value'].replace('Z','+00:00')); nanos=stamp.get('nanosecond')
    if created.tzinfo is None or type(nanos) is not int or not 0<=nanos<=999999999 or created.microsecond!=nanos//1000:raise ValueError('Invalid discussion timestamp')
    ident='discussion_'+hashlib.sha256(path.encode()).hexdigest()
    return (ident,ref,text,role,status,created,nanos,path,sorted(fields))


def _resolve_target(conn,ref,bindings):
    explicit=bindings.get(ref)
    manual=None
    if ref[0]=='string' and '/' not in ref[1]:
        manual_path='manual_tasks/'+ref[1]
        manual_id='manual_'+hashlib.sha256(manual_path.encode()).hexdigest()
        manual_row=conn.execute('SELECT id,property_id,source_path FROM tasks WHERE source_path=%s',(manual_path,)).fetchone()
        if manual_row:
            if manual_row!=(manual_id,None,manual_path):raise ValueError('Inconsistent manual task mapping')
            manual=manual_row
    if explicit:
        actual=conn.execute('SELECT id,property_id,source_path FROM tasks WHERE id=%s',(explicit[0],)).fetchone()
        if actual!=explicit:raise ValueError('Mapped task does not match expected identity and scope')
        if manual and manual!=explicit:raise ValueError('Conflicting task mapping')
        return actual[0]
    if manual:return manual[0]
    raise ValueError('Source reference needs an explicit mapping')


def project(conn,records,key,*,mappings=None,apply=False,verify_only=False):
    rows=[row for r in records if (row:=discussion_row(r)) is not None]
    bindings={} if mappings is None else mappings
    if set(bindings)-{row[1] for row in rows}:raise ValueError('Mapping contains unused references')
    conn.rollback()
    with conn.transaction():
        conn.execute('LOCK TABLE source_documents IN SHARE MODE')
        conn.execute('LOCK TABLE tasks IN SHARE MODE')
        conn.execute('LOCK TABLE task_discussions IN SHARE ROW EXCLUSIVE MODE')
        verify_records(conn,records,key)
        if verify_only and conn.execute("SELECT count(*) FROM task_discussions WHERE source_path ~ '^task_messages/'").fetchone()[0]!=len(rows):
            raise ValueError('Discussion source and target counts differ')
        for ident,ref,text,role,status,created,nanos,path,present in rows:
            task_id=_resolve_target(conn,ref,bindings)
            existing=conn.execute('SELECT id,task_id,body,source_role,source_status,created_at,source_created_nanos,source_task_ref_type,source_task_ref_string,source_task_ref_integer,source_present_fields FROM task_discussions WHERE source_path=%s FOR UPDATE',(path,)).fetchone()
            ref_string=ref[1] if ref[0]=='string' else None
            ref_integer=ref[1] if ref[0]=='integer' else None
            expected=(ident,task_id,text,role,status,created,nanos,ref[0],ref_string,ref_integer,present)
            if existing and existing!=expected:raise ValueError('Discussion conflict')
            if not existing and verify_only:raise ValueError('Discussion missing')
            if not existing and apply:
                conn.execute('INSERT INTO task_discussions(id,task_id,body,source_role,source_status,created_at,source_created_nanos,source_path,source_task_ref_type,source_task_ref_string,source_task_ref_integer,source_present_fields) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(ident,task_id,text,role,status,created,nanos,path,ref[0],ref_string,ref_integer,present))
    return len(rows)


def main(argv=None):
    p=argparse.ArgumentParser(description='Project mapped task discussions into isolated staging')
    p.add_argument('--input',required=True);p.add_argument('--mapping',help='Private mapping file outside repository')
    mode=p.add_mutually_exclusive_group();mode.add_argument('--apply',action='store_true');mode.add_argument('--verify',action='store_true');a=p.parse_args(argv)
    try:
        key=key_from_env();records=read_backup(a.input,key);mappings=load_mappings(a.mapping) if a.mapping else {}
        with staging_connect() as conn:count=project(conn,records,key,mappings=mappings,apply=a.apply,verify_only=a.verify)
        print(f'{"Verified" if a.verify else "Staging projection" if a.apply else "Projection dry run"}: {count} discussions validated.')
    except Exception as exc:
        print(f'Projection stopped ({type(exc).__name__}); source and target require private review.',file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
