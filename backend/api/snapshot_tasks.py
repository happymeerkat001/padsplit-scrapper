"""Read-only provider task observations; no provider actions or raw ticket exposure."""
from fastapi import APIRouter,Depends,Query
from backend.api.repository import Repository,Forbidden
_ID=r'^[A-Za-z0-9_.:-]{1,200}$'
_FIELDS=frozenset(('status','category','details','room_number','property_id'))

def source_tasks(repository,uid,property_id,limit,offset):
 with repository.connect() as conn:
  actor=Repository._identity(conn,uid,lock=True)
  if not actor:raise Forbidden()
  if property_id and actor['role']!='owner' and property_id not in actor['properties']:raise Forbidden()
  rows=conn.execute('''WITH latest AS (
   SELECT DISTINCT ON (task_key) id,task_key,source_id,property_id,bucket,data,observed_at
   FROM padsplit_task_observations ORDER BY task_key,observed_at DESC,id DESC)
   SELECT id AS observation_id,task_key,source_id,property_id,bucket,data,observed_at FROM latest
   WHERE (%s='owner' OR EXISTS (SELECT 1 FROM property_access p WHERE p.firebase_uid=%s AND p.property_id=latest.property_id))
     AND (%s::text IS NULL OR latest.property_id=%s)
   ORDER BY observed_at DESC,task_key LIMIT %s OFFSET %s''',(actor['role'],uid,property_id,property_id,limit,offset)).fetchall()
  result=[]
  for row in rows:
   if not isinstance(row['data'],dict):raise ValueError('Invalid projected task')
   data={key:value for key,value in row['data'].items() if key in _FIELDS}
   data['property_id']=row['property_id']
   result.append({**row,'data':data})
  return result

def make_snapshot_tasks_router(*,actor_dependency,repository_provider,run):
 router=APIRouter()
 @router.get('/v1/provider-tasks')
 def list_tasks(property_id:str|None=Query(default=None,pattern=_ID),limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=10000),identity=Depends(actor_dependency)):
  return {'items':run(lambda:source_tasks(repository_provider(),identity['uid'],property_id,limit,offset)), 'limit':limit,'offset':offset,'ingestion':'staging_only'}
 return router
