"""Offline HTTP boundary tests: all identity and storage are fictional."""
import unittest
import asyncio
import os
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from fastapi.responses import StreamingResponse
from backend.api.app import AuthUnavailable, BoundedBodyMiddleware, RedactedErrorBoundary, create_app, firebase_verifier
from backend.api.repository import Conflict, Forbidden, NotFound


class FakeRepository:
    def __init__(self):
        self.users = {'owner': {'uid':'owner','role':'owner','properties':[]},
                      'operator': {'uid':'operator','role':'operator','properties':['house-a']},
                      'viewer': {'uid':'viewer','role':'viewer','properties':['house-a']}}
        self.tasks = {'task-a': {'id':'task-a','property_id':'house-a','title':'Example', 'status':'open','priority':'normal','assignee_uid':None,'version':1,'created_at':None,'updated_at':None},
                      'task-b': {'id':'task-b','property_id':'house-b','title':'Private', 'status':'open','priority':'high','assignee_uid':None,'version':1,'created_at':None,'updated_at':None}}
    def ready(self): return True
    def identity(self, uid): return self.users.get(uid)
    def properties(self, uid): return [{'id':'house-a','name':'Example'}] if uid in self.users else []
    def shared_records(self, uid, kind, limit, offset):
        if self.users[uid]['role']!='owner':raise Forbidden()
        return [{'id':'example','name':'Example'}]
    def tasks_list(self, uid, property_id, limit, offset):
        user=self.users[uid]
        if property_id and user['role']!='owner' and property_id not in user['properties']: raise Forbidden()
        values=[x for x in self.tasks.values() if (user['role']=='owner' or x['property_id'] in user['properties']) and (not property_id or x['property_id']==property_id)]
        return values[offset:offset+limit]
    def task_get(self, uid, task_id):
        task=self.tasks.get(task_id)
        if not task:raise NotFound()
        user=self.users[uid]
        if user['role']!='owner' and task['property_id'] not in user['properties']:raise Forbidden()
        return task.copy()
    def task_update(self, uid, task_id, title, status, expected_version):
        task=self.tasks.get(task_id)
        if not task: raise NotFound()
        user=self.users[uid]
        if user['role'] not in ('owner','manager','operator') or (user['role']!='owner' and task['property_id'] not in user['properties']): raise Forbidden()
        if task['version']!=expected_version: raise Conflict()
        task.update(status=status,version=task['version']+1)
        if title is not None: task['title']=title
        return task.copy()
    def task_create(self, uid, data, key):
        user=self.users[uid]
        if user['role'] not in ('owner','manager','operator') or (user['role']!='owner' and data['property_id'] not in user['properties']): raise Forbidden()
        if not hasattr(self,'keys'): self.keys={}
        if key in self.keys:
            if self.keys[key][0]!=data: raise Conflict()
            return self.keys[key][1]
        result={'id':'new-task','version':1};self.keys[key]=(data,result);return result
    def task_delete(self, uid, task_id, expected_version):
        user=self.users[uid];task=self.tasks.get(task_id)
        if not task: raise NotFound()
        if user['role'] not in ('owner','manager') or (user['role']!='owner' and task['property_id'] not in user['properties']): raise Forbidden()
        if task['version']!=expected_version: raise Conflict()
        del self.tasks[task_id];return {'ok':True}
    def discussion_list(self, uid, task_id, limit, offset):
        task=self.tasks.get(task_id);user=self.users[uid]
        if not task: raise NotFound()
        if user['role']!='owner' and task['property_id'] not in user['properties']: raise Forbidden()
        return []
    def discussion_create(self, uid, task_id, body, key, role_label=None, status_label=None):
        self.discussion_list(uid,task_id,10,0)
        if self.users[uid]['role']=='viewer': raise Forbidden()
        return {'id':'discussion-id'}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.repo=FakeRepository()
        self.client=TestClient(create_app(repository=self.repo, verify_token=lambda token: token if token in self.repo.users else None,
                                          allowed_origins=['https://dashboard.example.invalid'], rate_limit=100))
    def req(self, uid): return {'Authorization':'Bearer '+uid}
    def test_health_and_missing_or_invalid_identity(self):
        self.assertEqual(self.client.get('/health/live').status_code,200)
        self.assertEqual(self.client.get('/health/ready').status_code,200)
        self.assertEqual(self.client.get('/v1/me').status_code,401)
        self.assertEqual(self.client.get('/v1/me',headers=self.req('bad')).status_code,401)
        self.assertEqual(self.client.get('/v1/me',headers=self.req('operator')).json()['role'],'operator')
    def test_task_scope_and_denial(self):
        got=self.client.get('/v1/tasks',headers=self.req('operator')).json()['items']
        self.assertEqual([item['id'] for item in got],['task-a'])
        self.assertEqual(self.client.get('/v1/tasks?property_id=house-b',headers=self.req('operator')).status_code,403)
        self.assertEqual(len(self.client.get('/v1/tasks',headers=self.req('owner')).json()['items']),2)
        self.assertEqual(self.client.get('/v1/tasks/task-a',headers=self.req('operator')).json()['id'],'task-a')
        self.assertEqual(self.client.get('/v1/tasks/task-b',headers=self.req('operator')).status_code,403)
        self.assertEqual(self.client.get('/v1/tasks/not-present',headers=self.req('owner')).status_code,404)
        for route in ('vendors','templates'):
            self.assertEqual(self.client.get('/v1/'+route,headers=self.req('viewer')).status_code,403)
            self.assertEqual(self.client.get('/v1/'+route,headers=self.req('owner')).status_code,200)
    def test_update_version_role_and_validation(self):
        payload={'title':'Updated','status':'done','expected_version':1}
        self.assertEqual(self.client.patch('/v1/tasks/task-a',headers=self.req('viewer'),json=payload).status_code,403)
        self.assertEqual(self.client.patch('/v1/tasks/task-b',headers=self.req('operator'),json=payload).status_code,403)
        self.assertEqual(self.client.patch('/v1/tasks/task-a',headers=self.req('operator'),json=payload).json()['version'],2)
        self.assertEqual(self.client.patch('/v1/tasks/task-a',headers=self.req('operator'),json=payload).status_code,409)
        self.assertEqual(self.client.patch('/v1/tasks/task-a',headers=self.req('operator'),json={'title':'x','status':'done','expected_version':1,'role':'owner'}).status_code,422)
    def test_sensitive_rate_limit(self):
        self.client=TestClient(create_app(repository=self.repo, verify_token=lambda token: token if token in self.repo.users else None,
                                          allowed_origins=['https://dashboard.example.invalid'], rate_limit=2))
        for _ in range(2): self.assertEqual(self.client.get('/v1/me',headers=self.req('operator')).status_code,200)
        self.assertEqual(self.client.get('/v1/me',headers=self.req('operator')).status_code,429)
        self.assertEqual(self.client.get('/v1/me',headers=self.req('owner')).status_code,200)
    def test_status_only_preserves_long_imported_title_and_checks_version(self):
        original='example-' * 40
        self.repo.tasks['task-a']['title']=original
        url='/v1/tasks/task-a'; data={'status':'done','expected_version':1}
        self.assertEqual(self.client.patch(url,headers=self.req('viewer'),json=data).status_code,403)
        result=self.client.patch(url,headers=self.req('operator'),json=data)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['title'],original)
        self.assertEqual(self.client.patch(url,headers=self.req('operator'),json=data).status_code,409)
        self.assertEqual(self.client.patch(url,headers=self.req('operator'),json={'title':None,'status':'open','expected_version':2}).status_code,422)
        self.assertEqual(self.client.patch(url,headers=self.req('operator'),json={'title':'x'*201,'status':'open','expected_version':2}).status_code,422)
    def test_cors_restricted_and_no_secret_echo(self):
        good=self.client.options('/v1/me',headers={'Origin':'https://dashboard.example.invalid','Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization'})
        bad=self.client.options('/v1/me',headers={'Origin':'https://attacker.example.invalid','Access-Control-Request-Method':'GET'})
        self.assertEqual(good.headers['access-control-allow-origin'],'https://dashboard.example.invalid')
        self.assertNotIn('access-control-allow-origin',bad.headers)
        response=self.client.get('/v1/me',headers=self.req('fictional-secret'))
        self.assertNotIn('fictional-secret',response.text)
    def test_create_delete_and_discussion_controls(self):
        p={'property_id':'house-a','title':'New','status':'open','priority':'normal'}
        h={**self.req('operator'),'Idempotency-Key':'request-1'}
        self.assertEqual(self.client.post('/v1/tasks',headers=self.req('operator'),json=p).status_code,422)
        self.assertEqual(self.client.post('/v1/tasks',headers=h,json=p).status_code,201)
        self.assertEqual(self.client.post('/v1/tasks',headers=h,json=p).status_code,201)
        self.assertEqual(self.client.post('/v1/tasks',headers=h,json={**p,'title':'changed'}).status_code,409)
        self.assertEqual(self.client.post('/v1/tasks',headers={**self.req('viewer'),'Idempotency-Key':'v1'},json=p).status_code,403)
        self.assertEqual(self.client.get('/v1/tasks/task-b/discussion',headers=self.req('operator')).status_code,403)
        self.assertEqual(self.client.post('/v1/tasks/task-a/discussion',headers={**self.req('viewer'),'Idempotency-Key':'v2'},json={'body':'note'}).status_code,403)
        self.assertEqual(self.client.post('/v1/tasks/task-a/discussion',headers={**self.req('operator'),'Idempotency-Key':'d1'},json={'body':'note'}).status_code,201)
        self.assertEqual(self.client.delete('/v1/tasks/task-a?expected_version=1',headers=self.req('operator')).status_code,403)
        self.assertEqual(self.client.delete('/v1/tasks/task-a?expected_version=2',headers=self.req('owner')).status_code,409)
        self.assertEqual(self.client.delete('/v1/tasks/task-a?expected_version=1',headers=self.req('owner')).status_code,200)
    def test_body_limit_counts_actual_bytes(self):
        async def check():
            calls=[];messages=[{'type':'http.request','body':b'abc','more_body':False}]
            async def receive(): return messages.pop(0)
            async def send(msg): calls.append(msg)
            async def next_app(scope,recv,send): self.fail('Oversized body reached application')
            middleware=BoundedBodyMiddleware(next_app)
            await middleware({'type':'http','method':'POST','headers':[(b'content-length',b'1')]},receive,send)
            self.assertEqual(calls[0]['status'],413)
        asyncio.run(check())
    def test_auth_backend_failure_does_not_claim_bad_credentials(self):
        def broken(_): raise RuntimeError('fictional backend error')
        client=TestClient(create_app(repository=self.repo,verify_token=broken,allowed_origins=['https://dashboard.example.invalid']))
        response=client.get('/v1/me',headers=self.req('fictional-secret'))
        self.assertEqual(response.status_code,503)
        self.assertNotIn('fictional-secret',response.text)
    def test_unexpected_exceptions_do_not_escape_or_enter_response(self):
        async def check(started):
            messages=[]
            async def receive(): return {'type':'http.request','body':b'','more_body':False}
            async def send(item): messages.append(item)
            async def broken(scope,recv,send):
                if started: await send({'type':'http.response.start','status':200,'headers':[]})
                raise RuntimeError('fictional-private-exception')
            await RedactedErrorBoundary(broken)({'type':'http','method':'GET','headers':[]},receive,send)
            self.assertNotIn('fictional-private-exception',repr(messages))
            if not started:self.assertEqual(messages[0]['status'],503)
            else:self.assertEqual(len(messages),1)  # incomplete response, not false success
        asyncio.run(check(False));asyncio.run(check(True))
        app=create_app(repository=self.repo,verify_token=lambda token:token,allowed_origins=['https://dashboard.example.invalid'])
        @app.get('/synthetic-failure')
        def fail():raise RuntimeError('fictional-private-exception')
        response=TestClient(app,raise_server_exceptions=True).get('/synthetic-failure')
        self.assertEqual(response.status_code,503);self.assertNotIn('fictional-private-exception',response.text)
        @app.get('/synthetic-stream')
        def stream():
            async def generate():
                yield b'fictional-prefix'
                raise RuntimeError('fictional-private-exception')
            return StreamingResponse(generate())
        async def check_stream():
            sent=[];first=True;wait=asyncio.Event()
            async def receive():
                nonlocal first
                if first:first=False;return {'type':'http.request','body':b'','more_body':False}
                await wait.wait()
            async def send(event):sent.append(event)
            scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET','scheme':'http','path':'/synthetic-stream','raw_path':b'/synthetic-stream','query_string':b'','headers':[],'client':('127.0.0.1',1),'server':('test',80),'root_path':''}
            await asyncio.wait_for(app(scope,receive,send),5)
            self.assertNotIn('fictional-private-exception',repr(sent))
            self.assertTrue(any(item['type']=='http.response.start' for item in sent))
        asyncio.run(check_stream())
    def test_firebase_verifier_checks_revocation_and_rejects_emulator(self):
        instance=SimpleNamespace(project_id='test-project')
        with patch.dict(os.environ,{},clear=True), patch('backend.api.app.firebase_admin.get_app',return_value=instance), patch('backend.api.app.auth.verify_id_token',return_value={'uid':'fictional-user'}) as verify:
            fn=firebase_verifier('test-project')
            self.assertEqual(fn('fictional-token'),'fictional-user')
            verify.assert_called_once_with('fictional-token',app=instance,check_revoked=True)
            verify.side_effect=ValueError('invalid')
            self.assertIsNone(fn('invalid'))
            verify.side_effect=RuntimeError('unavailable')
            with self.assertRaises(AuthUnavailable):fn('anything')
        with patch.dict(os.environ,{'FIREBASE_AUTH_EMULATOR_HOST':'127.0.0.1:9999'}):
            with self.assertRaises(ValueError):firebase_verifier('test-project')

if __name__=='__main__': unittest.main()
