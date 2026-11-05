"""Stage only explicit operator-reviewed links between exact source observations.

Neither a room label nor an address is used to infer a relationship. Stale
observations do not inherit a prior link, and mismatched scope fails closed.
"""
import argparse
import re
import sys
from backend.storage.migrate import key_from_env,read_backup,staging_connect,verify_records
from backend.storage.project_inbox import snapshot_from_records,_run_id
from backend.storage.project_occupancy import load_bindings

_ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
_FIELDS={'task_observation_id','occupancy_observation_id','expected_property_id','expected_generation'}

def project(conn,records,key,bindings,*,apply=False):
 snapshot_from_records(records)
 generation='private_dashboard_runs/'+_run_id(records)
 if not isinstance(bindings,list) or len(bindings)>10000:raise ValueError('Invalid reviewed links')
 seen=set()
 for entry in bindings:
  if not isinstance(entry,dict) or set(entry)!=_FIELDS:raise ValueError('Invalid reviewed link')
  task=entry['task_observation_id'];occupancy=entry['occupancy_observation_id'];prop=entry['expected_property_id']
  if (not isinstance(task,str) or not _ID.fullmatch(task) or type(occupancy) is not int or occupancy<1 or
      not isinstance(prop,str) or not _ID.fullmatch(prop) or entry['expected_generation']!=generation or task in seen):
   raise ValueError('Invalid reviewed scope or generation')
  seen.add(task)
 conn.rollback()
 with conn.transaction():
  conn.execute('LOCK TABLE source_documents IN SHARE MODE')
  conn.execute('LOCK TABLE reviewed_room_task_links IN SHARE ROW EXCLUSIVE MODE')
  verify_records(conn,records,key)
  for entry in bindings:
   task=conn.execute('SELECT property_id,source_generation FROM padsplit_task_observations WHERE id=%s FOR SHARE',(entry['task_observation_id'],)).fetchone()
   room=conn.execute('''SELECT r.property_id,o.source_generation FROM occupancy_observations o
      JOIN rooms r ON r.id=o.room_id WHERE o.id=%s FOR SHARE OF o,r''',(entry['occupancy_observation_id'],)).fetchone()
   expected=(entry['expected_property_id'],generation)
   if task!=expected or room!=expected:raise ValueError('Reviewed observations differ in scope or generation')
   stored=conn.execute('''SELECT occupancy_observation_id,property_id,source_generation FROM reviewed_room_task_links
      WHERE task_observation_id=%s FOR UPDATE''',(entry['task_observation_id'],)).fetchone()
   value=(entry['occupancy_observation_id'],entry['expected_property_id'],generation)
   if stored and stored!=value:raise ValueError('Existing reviewed link conflicts')
   if not stored and apply:
    conn.execute('''INSERT INTO reviewed_room_task_links(task_observation_id,occupancy_observation_id,property_id,source_generation)
      VALUES (%s,%s,%s,%s)''',(entry['task_observation_id'],*value))
 return len(bindings)

def main(argv=None):
 parser=argparse.ArgumentParser(description='Stage explicit reviewed room/task observation links')
 parser.add_argument('--input',required=True);parser.add_argument('--bindings',required=True);parser.add_argument('--apply',action='store_true');args=parser.parse_args(argv)
 try:
  key=key_from_env();records=read_backup(args.input,key);bindings=load_bindings(args.bindings)
  with staging_connect() as conn:count=project(conn,records,key,bindings,apply=args.apply)
  print(f'{"Staging projection" if args.apply else "Projection dry run"}: {count} reviewed links validated.')
 except Exception as exc:
  print(f'Reviewed linking stopped ({type(exc).__name__}); source and scope require reconciliation.',file=sys.stderr);return 1
 return 0

if __name__=='__main__':raise SystemExit(main())
