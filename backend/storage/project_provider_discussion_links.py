"""Project explicit reviewed provider observation -> internal task bindings into staging.

Never infer associations from addresses, names, ticket IDs, or generated task IDs.
The operator must verify both objects against authoritative private evidence.
"""
import argparse,re,sys
from backend.storage.migrate import key_from_env,read_backup,staging_connect,verify_records
from backend.storage.project_inbox import snapshot_from_records,_run_id
from backend.storage.project_occupancy import load_bindings

_ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
_FIELDS={'task_observation_id','internal_task_id','expected_property_id','expected_generation','expected_task_source_path'}

def project(conn,records,key,bindings,*,apply=False):
 snapshot_from_records(records);generation='private_dashboard_runs/'+_run_id(records)
 if not isinstance(bindings,list) or len(bindings)>10000:raise ValueError('Invalid reviewed links')
 seen=set()
 for row in bindings:
  if not isinstance(row,dict) or set(row)!=_FIELDS:raise ValueError('Invalid reviewed link')
  source=row['expected_task_source_path']
  if (any(not isinstance(row[k],str) or not _ID.fullmatch(row[k]) or row[k] in ('.','..') for k in ('task_observation_id','internal_task_id','expected_property_id'))
      or row['expected_generation']!=generation or row['task_observation_id'] in seen
      or (source is not None and (not isinstance(source,str) or not source or len(source)>4096))):raise ValueError('Invalid reviewed scope or generation')
  seen.add(row['task_observation_id'])
 conn.rollback()
 with conn.transaction():
  conn.execute('LOCK TABLE source_documents IN SHARE MODE')
  conn.execute('LOCK TABLE reviewed_provider_discussion_links IN SHARE ROW EXCLUSIVE MODE')
  verify_records(conn,records,key)
  for row in bindings:
   obs=conn.execute('SELECT property_id,source_generation FROM padsplit_task_observations WHERE id=%s FOR SHARE',(row['task_observation_id'],)).fetchone()
   target=conn.execute('SELECT property_id,source_path FROM tasks WHERE id=%s FOR SHARE',(row['internal_task_id'],)).fetchone()
   if obs!=(row['expected_property_id'],generation) or target!=(row['expected_property_id'],row['expected_task_source_path']):raise ValueError('Reviewed observation or task differs in scope or identity')
   stored=conn.execute('SELECT internal_task_id,property_id,source_generation,expected_task_source_path FROM reviewed_provider_discussion_links WHERE task_observation_id=%s FOR UPDATE',(row['task_observation_id'],)).fetchone()
   value=(row['internal_task_id'],row['expected_property_id'],generation,row['expected_task_source_path'])
   if stored and stored!=value:raise ValueError('Existing reviewed binding conflicts')
   if not stored and apply:conn.execute('INSERT INTO reviewed_provider_discussion_links(task_observation_id,internal_task_id,property_id,source_generation,expected_task_source_path) VALUES (%s,%s,%s,%s,%s)',(row['task_observation_id'],*value))
 return len(bindings)

def main(argv=None):
 parser=argparse.ArgumentParser(description='Stage explicit reviewed provider discussion bindings')
 parser.add_argument('--input',required=True);parser.add_argument('--bindings',required=True);parser.add_argument('--apply',action='store_true');args=parser.parse_args(argv)
 try:
  key=key_from_env();records=read_backup(args.input,key);bindings=load_bindings(args.bindings)
  with staging_connect() as conn:count=project(conn,records,key,bindings,apply=args.apply)
  print(f'{"Staging projection" if args.apply else "Projection dry run"}: {count} reviewed bindings validated.')
 except Exception as exc:
  print(f'Reviewed binding stopped ({type(exc).__name__}); source and scope require reconciliation.',file=sys.stderr);return 1
 return 0
if __name__=='__main__':sys.exit(main())
