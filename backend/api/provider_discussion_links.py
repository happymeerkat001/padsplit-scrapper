"""Read-only reviewed links between provider observations and internal tasks."""
from fastapi import APIRouter,Depends,Query
from backend.api.repository import Repository,Forbidden
_ID=r'^[A-Za-z0-9_.:-]{1,200}$'

def links(repository,uid,property_id,limit,offset):
 with repository.connect() as conn:
  actor=Repository._identity(conn,uid,lock=True)
  if not actor:raise Forbidden()
  if property_id and actor['role']!='owner' and property_id not in actor['properties']:raise Forbidden()
  return conn.execute('''WITH latest AS (
   SELECT DISTINCT ON (task_key) id,task_key,property_id,source_generation FROM padsplit_task_observations
   ORDER BY task_key,observed_at DESC,id DESC)
   SELECT o.id AS task_observation_id,o.task_key,t.id AS internal_task_id,t.property_id
   FROM reviewed_provider_discussion_links l JOIN latest o ON o.id=l.task_observation_id
   JOIN tasks t ON t.id=l.internal_task_id
   WHERE o.property_id=l.property_id AND t.property_id=l.property_id
     AND o.source_generation=l.source_generation
     AND t.source_path IS NOT DISTINCT FROM l.expected_task_source_path
     AND (%s='owner' OR EXISTS (SELECT 1 FROM property_access p WHERE p.firebase_uid=%s AND p.property_id=l.property_id))
     AND (%s::text IS NULL OR l.property_id=%s)
   ORDER BY o.task_key,l.internal_task_id LIMIT %s OFFSET %s''',
   (actor['role'],uid,property_id,property_id,limit,offset)).fetchall()

def make_provider_discussion_router(*,actor_dependency,repository_provider,run):
 router=APIRouter()
 @router.get('/v1/provider-discussion-links')
 def list_links(property_id:str|None=Query(default=None,pattern=_ID),limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=10000),identity=Depends(actor_dependency)):
  return {'items':run(lambda:links(repository_provider(),identity['uid'],property_id,limit,offset)),
          'limit':limit,'offset':offset,'coverage':'reviewed_links_only','ingestion':'staging_only'}
 return router
