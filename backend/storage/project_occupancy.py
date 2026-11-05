"""Owner-reviewed, staging-only projection of private snapshot occupancy rows.

A source address or PadSplit ID never grants property access. Every exact source
identity must be independently bound to an existing reviewed property and label.
No missing row is interpreted as a deletion; incompatible changes fail closed.
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
from psycopg.types.json import Jsonb
from backend.storage.migrate import key_from_env, read_backup, staging_connect, verify_records
from backend.storage.project_inbox import snapshot_from_records, _timestamp, _unique_pairs

_ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
_FIELDS={'property_id','address','room_number','occupant_present','listed_move_out','next_move_in',
 'open_turn_ticket_ids','open_eviction_ticket_ids','open_hold_ticket_ids','move_out_photos','vacant',
 'turned','rent_ready','days_vacant','seo_eligible'}
_BOOL={'occupant_present','vacant','turned','rent_ready','seo_eligible'}
_LIST={'open_turn_ticket_ids','open_eviction_ticket_ids','open_hold_ticket_ids'}
_BINDING={'property_id','address','room_number','target_property_id','expected_property_name','room_label'}

def _source_identity(item):
 prop=item.get('property_id');address=item.get('address');room=item.get('room_number')
 if prop is not None and (type(prop) not in (str,int) or len(str(prop))>200):raise ValueError('Invalid source property')
 if not isinstance(address,str) or len(address)>1000 or not address.strip():raise ValueError('Invalid source address')
 if room is not None and (type(room) not in (str,int) or not str(room).strip() or len(str(room))>100):raise ValueError('Invalid source room')
 return json.dumps([prop,address,room],ensure_ascii=True,separators=(',',':'))

def projection_rows(occupancy,bindings):
 if not isinstance(occupancy,dict) or not isinstance(occupancy.get('rooms'),list) or not isinstance(bindings,list):raise ValueError('Invalid occupancy input')
 observed=_timestamp(occupancy.get('scraped_at'))
 scopes={}; targets=set()
 for entry in bindings:
  if not isinstance(entry,dict) or set(entry)!=_BINDING:raise ValueError('Invalid reviewed binding')
  ident=_source_identity(entry);prop=entry['target_property_id'];name=entry['expected_property_name'];label=entry['room_label']
  if not isinstance(prop,str) or not _ID.fullmatch(prop) or not isinstance(name,str) or not name.strip() or len(name)>1000 or not isinstance(label,str) or not label.strip() or len(label)>200:raise ValueError('Invalid binding target')
  if ident in scopes or (prop,label) in targets:raise ValueError('Duplicate reviewed binding')
  scopes[ident]=entry;targets.add((prop,label))
 rows=[];seen=set()
 for item in occupancy['rooms']:
  if not isinstance(item,dict) or set(item)!=_FIELDS:raise ValueError('Unsupported occupancy fields')
  ident=_source_identity(item)
  if ident in seen or ident not in scopes:raise ValueError('Occupancy needs unique reviewed scope')
  seen.add(ident)
  if any(type(item[k]) is not bool for k in _BOOL):raise ValueError('Invalid occupancy flag')
  if any(type(item[k]) is not int or item[k]<0 for k in ('move_out_photos','days_vacant')):raise ValueError('Invalid occupancy count')
  for key in _LIST:
   if not isinstance(item[key],list) or len(item[key])>10000 or any(type(v) not in (str,int) or len(str(v))>200 for v in item[key]):raise ValueError('Invalid task identities')
  for key in ('listed_move_out','next_move_in'):
   value=item[key]
   if value is not None:
    try:
     if not isinstance(value,str) or dt.date.fromisoformat(value).isoformat()!=value:raise ValueError()
    except ValueError:raise ValueError('Invalid occupancy date') from None
  binding=scopes[ident];digest=hashlib.sha256(ident.encode()).hexdigest()
  rows.append({'room_id':'occupancy_'+digest,'property_id':binding['target_property_id'],'expected_name':binding['expected_property_name'],'room_label':binding['room_label'],'observed':observed,'occupant_present':item['occupant_present'],'rent_ready':item['rent_ready'],'payload':item})
 if seen!=set(scopes):raise ValueError('Unused reviewed binding')
 return rows

def load_bindings(path):
 source=Path(path)
 if source.is_symlink():raise ValueError('Mapping file must not be a symlink')
 resolved=source.resolve(strict=True);repo=Path(__file__).resolve().parents[2];parent=resolved.parent.stat()
 if resolved.is_relative_to(repo) or not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.geteuid() or parent.st_mode & 0o077:raise ValueError('Mapping directory must be private')
 fd=os.open(resolved,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
 try:
  meta=os.fstat(fd)
  if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.geteuid() or meta.st_mode & 0o077 or meta.st_size>1000000:raise ValueError('Mapping file must be private and bounded')
  with os.fdopen(fd,'r',encoding='utf8') as stream:
   fd=-1;value=json.load(stream,object_pairs_hook=_unique_pairs)
 finally:
  if fd!=-1:os.close(fd)
 if not isinstance(value,dict) or set(value)!={'version','bindings'} or type(value['version']) is not int or value['version']!=1 or not isinstance(value['bindings'],list) or len(value['bindings'])>10000:raise ValueError('Invalid reviewed mapping')
 return value['bindings']

def project(conn,records,key,bindings,*,apply=False):
 snapshot=snapshot_from_records(records);occupancy=snapshot.get('occupancy')
 if occupancy is None:raise ValueError('Snapshot does not contain occupancy')
 rows=projection_rows(occupancy,bindings)
 run=next(r['data']['value']['run_id'] for r in records if r['path']=='private_dashboard/current')
 generation='private_dashboard_runs/'+run
 conn.rollback()
 with conn.transaction():
  conn.execute('LOCK TABLE source_documents IN SHARE MODE')
  verify_records(conn,records,key)
  for row in rows:
   prop=conn.execute('SELECT name FROM properties WHERE id=%s FOR UPDATE',(row['property_id'],)).fetchone()
   if not prop or prop[0]!=row['expected_name']:raise ValueError('Reviewed property mismatch')
   existing=conn.execute('SELECT id,property_id,label FROM rooms WHERE id=%s OR (property_id=%s AND label=%s) FOR UPDATE',(row['room_id'],row['property_id'],row['room_label'])).fetchall()
   if existing and (len(existing)!=1 or existing[0]!=(row['room_id'],row['property_id'],row['room_label'])):raise ValueError('Room identity conflict')
   if not existing and apply:conn.execute('INSERT INTO rooms(id,property_id,label,source_path) VALUES (%s,%s,%s,%s)',(row['room_id'],row['property_id'],row['room_label'],'padsplit_occupancy/'+row['room_id']))
   prior=conn.execute('SELECT occupant_present,rent_ready,source_payload,source_generation FROM occupancy_observations WHERE room_id=%s AND observed_at=%s FOR UPDATE',(row['room_id'],row['observed'])).fetchone()
   expected=(row['occupant_present'],row['rent_ready'],row['payload'],generation)
   if prior and prior!=expected:raise ValueError('Occupancy observation conflict')
   if not prior and apply:conn.execute('INSERT INTO occupancy_observations(room_id,observed_at,occupant_present,rent_ready,source_path,source_payload,source_generation) VALUES (%s,%s,%s,%s,%s,%s,%s)',(row['room_id'],row['observed'],row['occupant_present'],row['rent_ready'],'padsplit_occupancy/'+row['room_id'],Jsonb(row['payload']),generation))
 return len(rows)

def main(argv=None):
 p=argparse.ArgumentParser(description='Project explicitly scoped occupancy to staging only');p.add_argument('--input',required=True);p.add_argument('--bindings',required=True);p.add_argument('--apply',action='store_true');args=p.parse_args(argv)
 try:
  key=key_from_env();records=read_backup(args.input,key);bindings=load_bindings(args.bindings)
  with staging_connect() as conn:count=project(conn,records,key,bindings,apply=args.apply)
  print(f'{"Staging projection" if args.apply else "Projection dry run"}: {count} occupancy rows validated.')
 except Exception as exc:
  print(f'Occupancy projection stopped ({type(exc).__name__}); source and reviewed scope require reconciliation.',file=sys.stderr);return 1
 return 0
if __name__=='__main__':sys.exit(main())
