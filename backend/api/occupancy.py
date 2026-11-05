"""Read-only, property-scoped projected occupancy; live ingestion is separate."""
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.api.repository import Repository, Forbidden

_ID=r'^[A-Za-z0-9_.:-]{1,200}$'

def occupancy(repository,uid,property_id,limit,offset):
 with repository.connect() as conn:
  actor=Repository._identity(conn,uid,lock=True)
  if not actor:raise Forbidden()
  if property_id and actor['role']!='owner' and property_id not in actor['properties']:raise Forbidden()
  return conn.execute('''WITH latest AS (
   SELECT DISTINCT ON (o.room_id) o.id,o.room_id,o.observed_at,o.source_payload
   FROM occupancy_observations o WHERE o.source_payload IS NOT NULL
   ORDER BY o.room_id,o.observed_at DESC,o.id DESC)
   SELECT r.id AS room_id,r.property_id,p.name AS property_name,r.label,latest.id::text AS observation_id,
          latest.observed_at,latest.source_payload AS data
   FROM rooms r JOIN properties p ON p.id=r.property_id JOIN latest ON latest.room_id=r.id
   WHERE (%s='owner' OR EXISTS (SELECT 1 FROM property_access pa WHERE pa.firebase_uid=%s AND pa.property_id=r.property_id))
     AND (%s::text IS NULL OR r.property_id=%s)
   ORDER BY p.name,p.id,r.label,r.id LIMIT %s OFFSET %s''',
   (actor['role'],uid,property_id,property_id,limit,offset)).fetchall()

def make_occupancy_router(*,actor_dependency,repository_provider,run):
 router=APIRouter()
 @router.get('/v1/occupancy')
 def list_occupancy(property_id:str | None=Query(default=None,pattern=_ID),limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=10000),identity=Depends(actor_dependency)):
  return {'items':run(lambda:occupancy(repository_provider(),identity['uid'],property_id,limit,offset)),
          'limit':limit,'offset':offset,'ingestion':'staging_only'}
 return router
