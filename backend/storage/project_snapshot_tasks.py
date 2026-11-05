"""Stage property-bound provider task observations without enabling provider writes.

The encrypted snapshot remains authoritative and retains the full ticket; the
scoped read model deliberately excludes unrecognized fields (including codes).
No address or external property identifier ever grants access automatically.
"""
import argparse, hashlib, json, re, sys
from psycopg.types.json import Jsonb
from backend.storage.migrate import key_from_env,read_backup,staging_connect,verify_records
from backend.storage.project_inbox import snapshot_from_records,_timestamp
from backend.storage.project_occupancy import load_bindings
_ID=re.compile(r'^[A-Za-z0-9_.:-]{1,200}$')
_BINDING={'source_id','source_property_id','source_property_present','target_property_id','expected_property_name'}
_FIELDS=('status','category','details','room_number')

def _identity(value):
 if type(value) not in (str,int) or not str(value).strip() or len(str(value))>200 or any(ord(c)<32 for c in str(value)):raise ValueError('Invalid source task identity')
 return json.dumps(value,ensure_ascii=True,separators=(',',':'))

def projection_rows(payload,bindings):
 if not isinstance(payload,dict) or not isinstance(payload.get('tasks'),dict) or not isinstance(bindings,list):raise ValueError('Invalid snapshot tasks')
 observed=_timestamp(payload.get('scraped_at')); scopes={}
 for entry in bindings:
  if not isinstance(entry,dict) or set(entry)!=_BINDING:raise ValueError('Invalid scope binding')
  ident=_identity(entry['source_id']);source_prop=entry['source_property_id'];target=entry['target_property_id'];name=entry['expected_property_name']
  if type(entry['source_property_present']) is not bool or (not entry['source_property_present'] and source_prop is not None):raise ValueError('Invalid source property presence')
  if source_prop is not None:_identity(source_prop)
  if target is None:
   if name is not None:raise ValueError('Invalid owner-only scope')
  elif not isinstance(target,str) or not _ID.fullmatch(target) or not isinstance(name,str) or not name.strip() or len(name)>1000:raise ValueError('Invalid target property')
  if ident in scopes:raise ValueError('Duplicate scope binding')
  scopes[ident]=entry
 rows=[];seen=set();seen_bucket_tasks=set()
 for bucket,tasks in payload['tasks'].items():
  if not isinstance(bucket,str) or not bucket.strip() or len(bucket)>100 or not isinstance(tasks,list):raise ValueError('Invalid task bucket')
  for task in tasks:
   if not isinstance(task,dict):raise ValueError('Invalid task')
   ident=_identity(task.get('id'))
   if (ident,bucket) in seen_bucket_tasks or ident not in scopes:raise ValueError('Task needs unique reviewed scope')
   seen.add(ident);seen_bucket_tasks.add((ident,bucket));scope=scopes[ident]
   source_prop=task.get('property_id')
   if ('property_id' in task)!=scope['source_property_present']:raise ValueError('Source property presence changed')
   if source_prop is not None:_identity(source_prop)
   if type(source_prop) is not type(scope['source_property_id']) or source_prop!=scope['source_property_id']:raise ValueError('Source property changed')
   data={}
   for field in _FIELDS:
    if field not in task:continue
    value=task[field]
    if field=='room_number':
     if value is not None and (type(value) not in (str,int) or len(str(value))>100):raise ValueError('Invalid room number')
    elif value is not None and (not isinstance(value,str) or len(value)>10000):raise ValueError('Invalid task field')
    data[field]=value
   data['property_id']=scope['target_property_id']
   pair=json.dumps([ident,bucket],ensure_ascii=True,separators=(',',':'))
   task_key='provider_task_'+hashlib.sha256(pair.encode()).hexdigest()
   observation='provider_observation_'+hashlib.sha256((pair+'\0'+observed.isoformat()).encode()).hexdigest()
   rows.append({'id':observation,'task_key':task_key,'source_id':task['id'],'property_id':scope['target_property_id'],'expected_name':scope['expected_property_name'],'bucket':bucket,'data':data,'observed_at':observed})
 if seen!=set(scopes):raise ValueError('Unused scope binding')
 return rows

def project(conn,records,key,bindings,*,apply=False):
 snapshot=snapshot_from_records(records);rows=projection_rows(snapshot,bindings)
 run=next(r['data']['value']['run_id'] for r in records if r['path']=='private_dashboard/current');generation='private_dashboard_runs/'+run
 conn.rollback()
 with conn.transaction():
  conn.execute('LOCK TABLE source_documents IN SHARE MODE');verify_records(conn,records,key)
  for row in rows:
   if row['property_id'] is not None:
    prop=conn.execute('SELECT name FROM properties WHERE id=%s FOR UPDATE',(row['property_id'],)).fetchone()
    if not prop or prop[0]!=row['expected_name']:raise ValueError('Reviewed property mismatch')
   prior=conn.execute('SELECT id,source_id,property_id,bucket,data,source_generation FROM padsplit_task_observations WHERE task_key=%s AND observed_at=%s FOR UPDATE',(row['task_key'],row['observed_at'])).fetchone()
   expected=(row['id'],row['source_id'],row['property_id'],row['bucket'],row['data'],generation)
   if prior and prior!=expected:raise ValueError('Task observation conflict')
   if not prior and apply:conn.execute('INSERT INTO padsplit_task_observations(id,task_key,source_id,property_id,bucket,data,observed_at,source_generation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',(row['id'],row['task_key'],Jsonb(row['source_id']),row['property_id'],row['bucket'],Jsonb(row['data']),row['observed_at'],generation))
 return len(rows)

def main(argv=None):
 p=argparse.ArgumentParser(description='Stage reviewed private provider task observations');p.add_argument('--input',required=True);p.add_argument('--bindings',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args(argv)
 try:
  key=key_from_env();records=read_backup(a.input,key);bindings=load_bindings(a.bindings)
  with staging_connect() as conn:count=project(conn,records,key,bindings,apply=a.apply)
  print(f'{"Staging projection" if a.apply else "Projection dry run"}: {count} task observations validated.')
 except Exception as exc:
  print(f'Task projection stopped ({type(exc).__name__}); source and reviewed scope require reconciliation.',file=sys.stderr);return 1
 return 0
if __name__=='__main__':sys.exit(main())
