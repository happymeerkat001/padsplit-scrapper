"""Read only reviewed links between currently selected source observations."""
from fastapi import APIRouter,Depends,Query
from backend.api.repository import Repository,Forbidden
_ID=r'^[A-Za-z0-9_.:-]{1,200}$'

def reviewed_links(repository,uid,property_id,limit,offset):
 with repository.connect() as conn:
  actor=Repository._identity(conn,uid,lock=True)
  if not actor:raise Forbidden()
  if property_id and actor['role']!='owner' and property_id not in actor['properties']:raise Forbidden()
  return conn.execute('''WITH latest_rooms AS (
   SELECT DISTINCT ON (room_id) id,room_id,source_generation FROM occupancy_observations
   WHERE source_payload IS NOT NULL ORDER BY room_id,observed_at DESC,id DESC),
   latest_tasks AS (SELECT DISTINCT ON (task_key) id,task_key,property_id,source_generation
   FROM padsplit_task_observations ORDER BY task_key,observed_at DESC,id DESC)
   SELECT r.id AS room_id,t.task_key,o.id::text AS occupancy_observation_id,t.id AS task_observation_id FROM reviewed_room_task_links l
   JOIN latest_rooms o ON o.id=l.occupancy_observation_id
   JOIN rooms r ON r.id=o.room_id
   JOIN latest_tasks t ON t.id=l.task_observation_id
   WHERE l.property_id=r.property_id AND l.property_id=t.property_id
     AND l.source_generation=o.source_generation AND l.source_generation=t.source_generation
     AND (%s='owner' OR EXISTS (SELECT 1 FROM property_access p WHERE p.firebase_uid=%s AND p.property_id=r.property_id))
     AND (%s::text IS NULL OR r.property_id=%s)
   ORDER BY r.id,t.task_key LIMIT %s OFFSET %s''',
   (actor['role'],uid,property_id,property_id,limit,offset)).fetchall()

def make_reviewed_links_router(*,actor_dependency,repository_provider,run):
 router=APIRouter()
 @router.get('/v1/room-task-links')
 def list_links(property_id:str|None=Query(default=None,pattern=_ID),limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=10000),identity=Depends(actor_dependency)):
  return {'items':run(lambda:reviewed_links(repository_provider(),identity['uid'],property_id,limit,offset)),
          'limit':limit,'offset':offset,'coverage':'reviewed_links_only','ingestion':'staging_only'}
 return router
