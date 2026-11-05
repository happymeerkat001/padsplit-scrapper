"""Firebase-authenticated operations API. Only explicitly implemented routes exist."""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

import firebase_admin
from firebase_admin import auth
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from backend.api.repository import Conflict, Forbidden, NotFound, Repository

_ID = r'^[A-Za-z0-9_.:-]{1,200}$'


class AuthUnavailable(Exception): pass


class RedactedErrorBoundary:
    """Contain HTTP exceptions before the server can log sensitive details.

    Once headers have been sent, leave the response incomplete rather than
    falsely reporting success or trying to send a second response.
    """
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        started=False
        async def observed_send(message):
            nonlocal started
            if message['type']=='http.response.start':started=True
            await send(message)
        try:
            await self.app(scope,receive,observed_send)
        except Exception:
            if not started:
                try:await JSONResponse({'error':'Service unavailable'},status_code=503,headers={'Cache-Control':'no-store'})(scope,receive,send)
                except Exception:pass


class BoundedBodyMiddleware:
    """Bound actual ASGI bytes, including clients with a false Content-Length."""
    def __init__(self, app): self.app=app
    async def __call__(self, scope, receive, send):
        if scope['type']!='http' or scope['method'] not in {'POST','PUT','PATCH'}:
            return await self.app(scope,receive,send)
        headers=scope.get('headers',[])
        lengths=[v for k,v in headers if k.lower()==b'content-length']
        if len(lengths)!=1 or not lengths[0].isdigit() or int(lengths[0])>16384 or any(k.lower()==b'transfer-encoding' for k,v in headers):
            return await JSONResponse({'error':'Request body too large or unbounded'},status_code=413,headers={'Cache-Control':'no-store'})(scope,receive,send)
        expected=int(lengths[0]); chunks=[]; size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect': return
            chunk=message.get('body',b'');size+=len(chunk)
            if size>16384 or size>expected:
                return await JSONResponse({'error':'Request body too large or unbounded'},status_code=413,headers={'Cache-Control':'no-store'})(scope,receive,send)
            chunks.append(chunk)
            if not message.get('more_body',False):break
        if size!=expected:
            return await JSONResponse({'error':'Invalid request body length'},status_code=400,headers={'Cache-Control':'no-store'})(scope,receive,send)
        delivered=False
        async def bounded_receive():
            nonlocal delivered
            if delivered:return await receive()
            delivered=True;return {'type':'http.request','body':b''.join(chunks),'more_body':False}
        return await self.app(scope,bounded_receive,send)


def valid_origins(values):
    """Require explicit, exact HTTPS origins; localhost HTTP is for local development."""
    clean=[]
    for raw in values:
        origin=raw.strip()
        if not origin or '*' in origin: raise ValueError('Explicit origins are required')
        p=urlsplit(origin)
        if (not p.hostname or p.username is not None or p.password is not None or p.path or p.query or p.fragment
                or (p.scheme!='https' and not (p.scheme=='http' and p.hostname in {'localhost','127.0.0.1','::1'}))):
            raise ValueError('Invalid frontend origin')
        try: p.port
        except ValueError: raise ValueError('Invalid frontend origin') from None
        clean.append(origin)
    return clean


class RateLimiter:
    """Per-process limit; deployment must use one worker and proxy limits as well."""
    def __init__(self, limit):
        if not 1<=limit<=1000: raise ValueError('Invalid rate limit')
        self.limit=limit; self.entries={}; self.lock=threading.Lock()
    def check(self, uid):
        now=time.monotonic()
        with self.lock:
            # Do not allow arbitrary authenticated identities to grow memory forever.
            for key in list(self.entries):
                if not self.entries[key] or now-self.entries[key][-1]>=60: del self.entries[key]
            if uid not in self.entries and len(self.entries)>=10000: return False
            q=self.entries.setdefault(uid,deque())
            while q and now-q[0]>=60: q.popleft()
            if len(q)>=self.limit: return False
            q.append(now);return True


def firebase_verifier(project_id):
    if not project_id: raise ValueError('FIREBASE_PROJECT_ID is required')
    if os.getenv('FIREBASE_AUTH_EMULATOR_HOST'):
        raise ValueError('Auth emulator is forbidden for the production API')
    name='operations-api'
    try:
        instance=firebase_admin.get_app(name)
        if instance.project_id!=project_id: raise ValueError('Firebase project mismatch')
    except ValueError as exc:
        if str(exc)=='Firebase project mismatch': raise
        instance=firebase_admin.initialize_app(options={'projectId':project_id},name=name)
    def verify(token):
        try: decoded=auth.verify_id_token(token,app=instance,check_revoked=True)
        except (auth.InvalidIdTokenError,auth.UserDisabledError,auth.UserNotFoundError,ValueError): return None
        except Exception: raise AuthUnavailable() from None
        return decoded.get('uid')
    return verify


class UpdateTask(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title: str | None=Field(default=None,min_length=1,max_length=200)
    status: Literal['open','in_progress','done']
    expected_version: int=Field(ge=1,strict=True)
    @model_validator(mode='before')
    @classmethod
    def title_not_null(cls, value):
        if isinstance(value,dict) and 'title' in value and value['title'] is None: raise ValueError('Title cannot be null')
        return value
    @field_validator('title')
    @classmethod
    def title_not_empty(cls, value):
        if value is None: return value
        if not value.strip(): raise ValueError('Title is required')
        return value.strip()


class CreateTask(BaseModel):
    model_config=ConfigDict(extra='forbid')
    property_id: str=Field(pattern=_ID)
    title: str=Field(min_length=1,max_length=200)
    status: Literal['open','in_progress','done']='open'
    priority: Literal['low','normal','high','urgent']='normal'
    @field_validator('title')
    @classmethod
    def title_not_empty(cls, value):
        if not value.strip(): raise ValueError('Title is required')
        return value.strip()


class Discussion(BaseModel):
    model_config=ConfigDict(extra='forbid')
    body: str=Field(min_length=1,max_length=10000)
    role_label: StrictStr | None=Field(default=None,min_length=1,max_length=100)
    status_label: StrictStr | None=Field(default=None,min_length=1,max_length=100)
    @field_validator('body')
    @classmethod
    def body_not_empty(cls, value):
        if not value.strip(): raise ValueError('Body is required')
        return value.strip()

    @field_validator('role_label','status_label')
    @classmethod
    def label_not_empty(cls,value):
        if value is None:return value
        if not value.strip() or any(ord(c)<32 for c in value):raise ValueError('Invalid label')
        return value.strip()


class CodesUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    fields: dict[str,StrictStr]=Field(max_length=200)
    expected_version: int=Field(ge=0,strict=True)
    @field_validator('fields')
    @classmethod
    def validate_fields(cls, values):
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key) or len(value)>300 for key,value in values.items()):
            raise ValueError('Invalid code fields')
        return values


class RestoreVersion(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version: int=Field(ge=1,strict=True)


class VendorInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=200)
    specialty: str | None=Field(default='',max_length=500)
    contact: str | None=Field(default='',max_length=500)
    location: str | None=Field(default='',max_length=500)
    @field_validator('name')
    @classmethod
    def name_not_empty(cls,value):
        if not value.strip():raise ValueError('Name required')
        return value.strip()


class VendorChange(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str | None=Field(default=None,min_length=1,max_length=200)
    specialty: str | None=Field(default=None,max_length=500)
    contact: str | None=Field(default=None,max_length=500)
    location: str | None=Field(default=None,max_length=500)
    expected_version: int=Field(ge=1,strict=True)
    @field_validator('name')
    @classmethod
    def valid_name(cls,value):
        if value is None or not value.strip():raise ValueError('Name required')
        return value.strip()
    @model_validator(mode='after')
    def has_changes(self):
        if not (self.model_fields_set - {'expected_version'}): raise ValueError('No fields supplied')
        return self


class TemplateInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    # The existing nine-slot Firestore editor permits an empty stored title.
    name: str=Field(max_length=200)
    body: str=Field(max_length=10000)


class TemplateChange(TemplateInput):
    expected_version: int=Field(ge=1,strict=True)


class NotesUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text: str=Field(max_length=10000)
    expected_version: int=Field(ge=0,strict=True)


def create_app(*, repository=None, verify_token=None, allowed_origins=None, rate_limit=None,
               team_enabled=None, team_repository=None, verify_team_target=None):
    if team_enabled is None: team_enabled=os.getenv('PADSPLIT_TEAM_ADMIN_ENABLE')=='1'
    if type(team_enabled) is not bool: raise ValueError('Invalid team configuration')
    origins=valid_origins(allowed_origins if allowed_origins is not None else [x.strip() for x in os.getenv('API_ALLOWED_ORIGINS','').split(',') if x.strip()])
    limiter=RateLimiter(rate_limit if rate_limit is not None else int(os.getenv('API_RATE_LIMIT_PER_MINUTE','60')))
    @asynccontextmanager
    async def lifespan(app):
        if app.state.repository is None:
            if not origins: raise RuntimeError('API_ALLOWED_ORIGINS is required')
            app.state.repository=Repository(os.getenv('DATABASE_URL'))
        if app.state.verify_token is None:
            app.state.verify_token=firebase_verifier(os.getenv('FIREBASE_PROJECT_ID'))
        if team_enabled:
            if app.state.team_repository is None:
                dsn=os.getenv('TEAM_DATABASE_URL')
                if not dsn: raise RuntimeError('Separate team database connection required')
                app.state.team_repository=Repository(dsn)
            if app.state.verify_team_target is None:
                def firebase_team_target(uid):
                    record=auth.get_user(uid,app=firebase_admin.get_app('operations-api'))
                    return {'uid':record.uid,'disabled':record.disabled}
                app.state.verify_team_target=firebase_team_target
        yield
    app=FastAPI(title='Operations API',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.repository=repository;app.state.verify_token=verify_token
    app.state.team_repository=team_repository;app.state.verify_team_target=verify_team_target
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,
                       allow_methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'],allow_headers=['Authorization','Content-Type','Idempotency-Key'])
    app.add_middleware(BoundedBodyMiddleware)

    @app.middleware('http')
    async def security_headers(request,call_next):
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    # Add last so it wraps all user middleware and route execution.
    app.add_middleware(RedactedErrorBoundary)

    @app.exception_handler(RequestValidationError)
    async def invalid(_request,_error):
        return JSONResponse(status_code=422,content={'error':'Invalid request'})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request,error):
        return JSONResponse(status_code=error.status_code,content={'error':str(error.detail)},headers=error.headers)

    @app.exception_handler(Exception)
    async def unknown(_request,_error):
        return JSONResponse(status_code=503,content={'error':'Service unavailable'})

    def actor(request: Request, authorization: str | None=Header(default=None)):
        if len(request.headers.getlist('authorization'))!=1: raise HTTPException(401,'Authentication required')
        parts=(authorization or '').split()
        if len(parts)!=2 or parts[0].lower()!='bearer' or len(parts[1])>8192:
            raise HTTPException(401,'Authentication required')
        try: uid=app.state.verify_token(parts[1])
        except Exception: raise HTTPException(503,'Authentication unavailable') from None
        if not isinstance(uid,str) or not re.fullmatch(_ID,uid): raise HTTPException(401,'Authentication required')
        try: identity=app.state.repository.identity(uid)
        except Exception: raise HTTPException(503,'Service unavailable') from None
        if not identity: raise HTTPException(403,'Access denied')
        return identity

    def limited(identity=Depends(actor)):
        if not limiter.check(identity['uid']): raise HTTPException(429,'Rate limit exceeded',headers={'Retry-After':'60'})
        return identity

    if team_enabled:
        from backend.api.team import make_team_router
        app.include_router(make_team_router(
            actor_dependency=limited,
            auth_repository_provider=lambda: app.state.repository,
            team_repository_provider=lambda: app.state.team_repository,
            verify_target=lambda uid: app.state.verify_team_target(uid)))

    def run(operation):
        try: return operation()
        except Forbidden: raise HTTPException(403,'Access denied') from None
        except NotFound: raise HTTPException(404,'Not found') from None
        except Conflict: raise HTTPException(409,'Update conflict') from None
        except Exception: raise HTTPException(503,'Service unavailable') from None

    from backend.api.inbox import make_inbox_router
    app.include_router(make_inbox_router(actor_dependency=limited, repository_provider=lambda: app.state.repository, run=run))
    from backend.api.occupancy import make_occupancy_router
    app.include_router(make_occupancy_router(actor_dependency=limited, repository_provider=lambda: app.state.repository, run=run))
    from backend.api.snapshot_tasks import make_snapshot_tasks_router
    app.include_router(make_snapshot_tasks_router(actor_dependency=limited, repository_provider=lambda: app.state.repository, run=run))
    from backend.api.reviewed_links import make_reviewed_links_router
    app.include_router(make_reviewed_links_router(actor_dependency=limited, repository_provider=lambda: app.state.repository, run=run))
    from backend.api.climate import make_climate_router
    app.include_router(make_climate_router(actor_dependency=limited, repository_provider=lambda: app.state.repository, run=run))
    from backend.api.weather import make_weather_router
    app.include_router(make_weather_router(actor_dependency=limited, repository_provider=lambda: app.state.repository, run=run))
    from backend.api.provider_discussion_links import make_provider_discussion_router
    app.include_router(make_provider_discussion_router(actor_dependency=limited, repository_provider=lambda: app.state.repository, run=run))

    @app.get('/health/live')
    def live(): return {'status':'ok'}
    @app.get('/health/ready')
    def ready():
        if not app.state.repository or not app.state.repository.ready(): raise HTTPException(503,'Service unavailable')
        return {'status':'ready'}
    @app.get('/v1/me')
    def me(identity=Depends(limited)): return identity
    @app.get('/v1/properties')
    def properties(identity=Depends(limited)): return {'items':run(lambda:app.state.repository.properties(identity['uid']))}
    @app.get('/v1/report-access')
    def report_access(identity=Depends(limited)):
        return {'allowed':run(lambda:app.state.repository.report_access(identity['uid']))}

    @app.get('/v1/reports/{ident}')
    def report(ident: str,identity=Depends(limited)):
        if ident not in ('latest','monthly_history'):raise HTTPException(404,'Not found')
        return run(lambda:app.state.repository.report(identity['uid'],ident))
    @app.get('/v1/notes/{ident}')
    def note_get(ident: str,identity=Depends(limited)):
        if not re.fullmatch(_ID,ident):raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.note_get(identity['uid'],ident))
    @app.put('/v1/notes/{ident}')
    def note_put(ident: str,body: NotesUpdate,identity=Depends(limited)):
        if not re.fullmatch(_ID,ident):raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.note_put(identity['uid'],ident,body.text,body.expected_version))
    @app.get('/v1/vendors')
    def vendors(limit: int=Query(default=50,ge=1,le=100),offset: int=Query(default=0,ge=0,le=10000),identity=Depends(limited)):
        return {'items':run(lambda:app.state.repository.shared_records(identity['uid'],'vendors',limit,offset)),'limit':limit,'offset':offset}
    @app.get('/v1/templates')
    def templates(limit: int=Query(default=50,ge=1,le=100),offset: int=Query(default=0,ge=0,le=10000),identity=Depends(limited)):
        return {'items':run(lambda:app.state.repository.shared_records(identity['uid'],'templates',limit,offset)),'limit':limit,'offset':offset}
    @app.post('/v1/vendors',status_code=201)
    def vendor_create(body: VendorInput,idempotency_key: str=Header(pattern=_ID),identity=Depends(limited)):
        return run(lambda:app.state.repository.shared_create(identity['uid'],'vendors',body.model_dump(),idempotency_key))
    @app.patch('/v1/vendors/{ident}')
    def vendor_update(ident: str,body: VendorChange,identity=Depends(limited)):
        if not re.fullmatch(_ID,ident):raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.shared_update(identity['uid'],'vendors',ident,body.model_dump(exclude_unset=True,exclude={'expected_version'}),body.expected_version))
    @app.delete('/v1/vendors/{ident}')
    def vendor_delete(ident: str,expected_version: int=Query(ge=1),identity=Depends(limited)):
        if not re.fullmatch(_ID,ident):raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.shared_delete(identity['uid'],'vendors',ident,expected_version))
    @app.post('/v1/templates',status_code=201)
    def template_create(body: TemplateInput,idempotency_key: str=Header(pattern=_ID),identity=Depends(limited)):
        return run(lambda:app.state.repository.shared_create(identity['uid'],'templates',body.model_dump(),idempotency_key))
    @app.patch('/v1/templates/{ident}')
    def template_update(ident: str,body: TemplateChange,identity=Depends(limited)):
        if not re.fullmatch(_ID,ident):raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.shared_update(identity['uid'],'templates',ident,body.model_dump(exclude={'expected_version'}),body.expected_version))
    @app.delete('/v1/templates/{ident}')
    def template_delete(ident: str,expected_version: int=Query(ge=1),identity=Depends(limited)):
        if not re.fullmatch(_ID,ident):raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.shared_delete(identity['uid'],'templates',ident,expected_version))
    @app.get('/v1/tasks')
    def tasks(property_id: str | None=Query(default=None,pattern=_ID),limit: int=Query(default=50,ge=1,le=100),offset: int=Query(default=0,ge=0,le=10000),identity=Depends(limited)):
        return {'items':run(lambda:app.state.repository.tasks_list(identity['uid'],property_id,limit,offset)), 'limit':limit,'offset':offset}
    @app.get('/v1/tasks/{task_id}')
    def task(task_id: str,identity=Depends(limited)):
        if not re.fullmatch(_ID,task_id):raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.task_get(identity['uid'],task_id))
    @app.patch('/v1/tasks/{task_id}')
    def update(task_id: str, body: UpdateTask,identity=Depends(limited)):
        if not re.fullmatch(_ID,task_id): raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.task_update(identity['uid'],task_id,body.title,body.status,body.expected_version))
    @app.post('/v1/tasks',status_code=201)
    def create(body: CreateTask, idempotency_key: str=Header(pattern=_ID),identity=Depends(limited)):
        return run(lambda:app.state.repository.task_create(identity['uid'],body.model_dump(),idempotency_key))
    @app.delete('/v1/tasks/{task_id}')
    def delete(task_id: str, expected_version: int=Query(ge=1),identity=Depends(limited)):
        if not re.fullmatch(_ID,task_id): raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.task_delete(identity['uid'],task_id,expected_version))
    @app.get('/v1/tasks/{task_id}/discussion')
    def discussion(task_id: str,limit: int=Query(default=50,ge=1,le=100),offset: int=Query(default=0,ge=0,le=10000),identity=Depends(limited)):
        if not re.fullmatch(_ID,task_id): raise HTTPException(422,'Invalid request')
        return {'items':run(lambda:app.state.repository.discussion_list(identity['uid'],task_id,limit,offset)),'limit':limit,'offset':offset}
    @app.post('/v1/tasks/{task_id}/discussion',status_code=201)
    def discuss(task_id: str,body: Discussion,idempotency_key: str=Header(pattern=_ID),identity=Depends(limited)):
        if not re.fullmatch(_ID,task_id): raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.discussion_create(identity['uid'],task_id,body.body,idempotency_key,body.role_label,body.status_label))
    @app.delete('/v1/tasks/{task_id}/discussion/{discussion_id}')
    def delete_discussion(task_id: str,discussion_id: str,identity=Depends(limited)):
        if not re.fullmatch(_ID,task_id) or not re.fullmatch(_ID,discussion_id):raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.discussion_delete(identity['uid'],task_id,discussion_id))
    @app.get('/v1/properties/{property_id}/codes')
    def codes(property_id: str, identity=Depends(limited)):
        if not re.fullmatch(_ID,property_id): raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.codes_metadata(identity['uid'],property_id))
    @app.get('/v1/codes/schema')
    def codes_schema(identity=Depends(limited)):
        return run(lambda:app.state.repository.codes_schema(identity['uid']))
    @app.post('/v1/properties/{property_id}/codes/reveal')
    def reveal_codes(property_id: str, identity=Depends(limited)):
        if not re.fullmatch(_ID,property_id): raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.codes_reveal(identity['uid'],property_id))
    @app.put('/v1/properties/{property_id}/codes')
    def save_codes(property_id: str, body: CodesUpdate, identity=Depends(limited)):
        if not re.fullmatch(_ID,property_id): raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.codes_save(identity['uid'],property_id,body.fields,body.expected_version))
    @app.get('/v1/properties/{property_id}/codes/history')
    def code_history(property_id: str,limit: int=Query(default=50,ge=1,le=100),offset: int=Query(default=0,ge=0,le=10000),identity=Depends(limited)):
        if not re.fullmatch(_ID,property_id): raise HTTPException(422,'Invalid request')
        return {'items':run(lambda:app.state.repository.codes_history(identity['uid'],property_id,limit,offset)),'limit':limit,'offset':offset}
    @app.post('/v1/properties/{property_id}/codes/history/{history_id}/reveal')
    def reveal_code_history(property_id: str,history_id: str,identity=Depends(limited)):
        if not re.fullmatch(_ID,property_id) or not re.fullmatch(_ID,history_id): raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.codes_history_reveal(identity['uid'],property_id,history_id))
    @app.post('/v1/properties/{property_id}/codes/history/{history_id}/restore')
    def restore_codes(property_id: str,history_id: str,body: RestoreVersion,identity=Depends(limited)):
        if not re.fullmatch(_ID,property_id) or not re.fullmatch(_ID,history_id): raise HTTPException(422,'Invalid request')
        return run(lambda:app.state.repository.codes_restore(identity['uid'],property_id,history_id,body.expected_version))
    return app


app=create_app()
