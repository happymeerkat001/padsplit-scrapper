"""Read-only, property-scoped staged climate observations."""
from fastapi import APIRouter,Depends,Query
from backend.api.repository import Repository,Forbidden

_ID=r'^[A-Za-z0-9_.:-]{1,200}$'
_FIELDS={'location_name','name','temp','heat_setpoint','cool_setpoint','humidity','outdoor_temp','mode','equipment_status'}

def climate(repository,uid,property_id,limit,offset):
    with repository.connect() as conn:
        actor=Repository._identity(conn,uid,lock=True)
        if not actor:raise Forbidden()
        if property_id and actor['role']!='owner' and property_id not in actor['properties']:raise Forbidden()
        rows=conn.execute('''WITH latest AS (
          SELECT DISTINCT ON (device_key) device_key,property_id,observed_at,data
          FROM climate_observations ORDER BY device_key,observed_at DESC,id DESC)
          SELECT latest.device_key,latest.property_id,p.name AS property_name,latest.observed_at,latest.data
          FROM latest JOIN properties p ON p.id=latest.property_id
          WHERE (%s='owner' OR EXISTS (SELECT 1 FROM property_access a WHERE a.firebase_uid=%s AND a.property_id=latest.property_id))
            AND (%s::text IS NULL OR latest.property_id=%s)
          ORDER BY p.name,p.id,latest.device_key LIMIT %s OFFSET %s''',
          (actor['role'],uid,property_id,property_id,limit,offset)).fetchall()
        result=[]
        for row in rows:
            if not isinstance(row['data'],dict):raise ValueError('Invalid climate observation')
            result.append({**row,'data':{key:value for key,value in row['data'].items() if key in _FIELDS}})
        return result

def make_climate_router(*,actor_dependency,repository_provider,run):
    router=APIRouter()
    @router.get('/v1/climate')
    def list_climate(property_id:str|None=Query(default=None,pattern=_ID),limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0,le=10000),identity=Depends(actor_dependency)):
        return {'items':run(lambda:climate(repository_provider(),identity['uid'],property_id,limit,offset)),'limit':limit,'offset':offset,'ingestion':'staging_only'}
    return router
