"""Explicit, isolated staging DB integration test; never auto-connects to production."""
import concurrent.futures
import os
import uuid
import unittest
from urllib.parse import urlsplit
import psycopg
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Repository
from backend.storage.schema import apply_migrations
from psycopg import sql

DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')

@unittest.skipUnless(DSN,'Set PADSPLIT_API_TEST_DATABASE_URL to an isolated local rehearsal DB')
class PostgreSQLApiTests(unittest.TestCase):
    def test_scoping_versions_idempotency_and_redacted_audit(self):
        u=urlsplit(DSN)
        if u.hostname not in ('127.0.0.1','localhost','::1') or not u.path.lstrip('/').startswith('padsplit_stage'):
            self.fail('Test requires an isolated local staging database')
        schema='api_test_'+uuid.uuid4().hex[:12]
        with psycopg.connect(DSN,autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        def cleanup_schema():
            with psycopg.connect(DSN,autocommit=True) as admin:
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        self.addCleanup(cleanup_schema)
        scoped_dsn=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
        self.assertFalse(Repository(scoped_dsn).ready(), 'A reachable but unmigrated database must not be ready')
        with psycopg.connect(scoped_dsn) as db: apply_migrations(db)
        self.assertTrue(Repository(scoped_dsn).ready(), 'The migrated synthetic schema must be ready')
        prefix='apitest'+uuid.uuid4().hex[:12]
        created_shared={'vendors':[],'templates':[]}
        owner=prefix+'owner'; operator=prefix+'operator'; viewer=prefix+'viewer'
        a=prefix+'a'; b=prefix+'b'
        with psycopg.connect(scoped_dsn) as db:
            for uid,role in ((owner,'owner'),(operator,'operator'),(viewer,'viewer')):
                db.execute('INSERT INTO app_users(firebase_uid) VALUES (%s)',(uid,))
                db.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)',(uid,role))
            db.execute('INSERT INTO properties(id,name) VALUES (%s,%s),(%s,%s)',(a,'Example A',b,'Example B'))
            for uid in (operator,viewer): db.execute('INSERT INTO property_access(firebase_uid,property_id) VALUES (%s,%s)',(uid,a))
            db.execute("INSERT INTO vendors(id,name,data) VALUES (%s,'Example vendor',%s::jsonb)",(prefix,'{"contact":null,"extension":"keep"}'))
            db.execute("INSERT INTO templates(id,name,body) VALUES (%s,'Example template','Example body')",(prefix,))
        try:
            repo=Repository(scoped_dsn)
            client=TestClient(create_app(repository=repo,verify_token=lambda t:t,allowed_origins=['https://example.invalid']))
            h=lambda uid,key:{'Authorization':'Bearer '+uid,'Idempotency-Key':key}
            partial='/v1/vendors/'+prefix
            self.assertEqual(client.patch(partial,headers=h(owner,'unused'),json={'name':'Renamed','expected_version':1}).status_code,200)
            with psycopg.connect(scoped_dsn) as db:
                data=db.execute('SELECT data FROM vendors WHERE id=%s',(prefix,)).fetchone()[0]
                self.assertIsNone(data['contact']);self.assertNotIn('specialty',data);self.assertEqual(data['extension'],'keep')
            self.assertEqual(client.patch(partial,headers=h(owner,'unused'),json={'contact':'','expected_version':2}).status_code,200)
            with psycopg.connect(scoped_dsn) as db:
                data=db.execute('SELECT data FROM vendors WHERE id=%s',(prefix,)).fetchone()[0]
                self.assertEqual(data['contact'],'');self.assertNotIn('specialty',data)
            note='/v1/notes/'+prefix
            self.assertEqual(client.get(note,headers=h(viewer,'unused')).status_code,403)
            self.assertEqual(client.get(note,headers=h(owner,'unused')).json()['version'],0)
            note_data={'text':'fictional-private-note','expected_version':0}
            self.assertEqual(client.put(note,headers=h(owner,'unused'),json=note_data).json()['version'],1)
            self.assertEqual(client.put(note,headers=h(owner,'unused'),json=note_data).status_code,409)
            self.assertEqual(client.get(note,headers=h(owner,'unused')).json()['text'],'fictional-private-note')
            for kind in ('vendors','templates'):
                self.assertEqual(client.get('/v1/'+kind,headers=h(viewer,'unused')).status_code,403)
                self.assertEqual(client.get('/v1/'+kind,headers=h(owner,'unused')).status_code,200)
                data=({'name':'fictional-private-vendor','specialty':'general','contact':'','location':''} if kind=='vendors'
                      else {'name':'fictional-private-template','body':'fictional-private-body'})
                self.assertEqual(client.post('/v1/'+kind,headers=h(viewer,'deny-'+kind),json=data).status_code,403)
                first=client.post('/v1/'+kind,headers=h(owner,'create-'+kind),json=data)
                self.assertEqual(first.status_code,201); ident=first.json()['id'];created_shared[kind].append(ident)
                self.assertEqual(client.post('/v1/'+kind,headers=h(owner,'create-'+kind),json=data).json(),first.json())
                self.assertEqual(client.post('/v1/'+kind,headers=h(owner,'create-'+kind),json={**data,'name':'different'}).status_code,409)
                changed={**data,'name':'fictional-private-changed','expected_version':1}
                self.assertEqual(client.patch('/v1/'+kind+'/'+ident,headers=h(owner,'unused'),json=changed).json()['version'],2)
                self.assertEqual(client.patch('/v1/'+kind+'/'+ident,headers=h(owner,'unused'),json=changed).status_code,409)
                self.assertEqual(client.delete('/v1/'+kind+'/'+ident+'?expected_version=1',headers=h(owner,'unused')).status_code,409)
                self.assertEqual(client.delete('/v1/'+kind+'/'+ident+'?expected_version=2',headers=h(owner,'unused')).status_code,200)
            p={'property_id':a,'title':'fictional-task-private-text','status':'open','priority':'normal'}
            def create():return client.post('/v1/tasks',headers=h(operator,'same-key'),json=p)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                responses=list(pool.map(lambda _:create(),range(2)))
            self.assertEqual([r.status_code for r in responses],[201,201])
            self.assertEqual(responses[0].json(),responses[1].json())
            task=responses[0].json()['id']
            self.assertEqual(client.get('/v1/tasks/'+task,headers=h(viewer,'unused')).json()['id'],task)
            with psycopg.connect(scoped_dsn) as db:
                db.execute('INSERT INTO tasks(id,property_id,title,status) VALUES (%s,%s,%s,%s)',(prefix+'other',b,'fictional','open'))
            self.assertEqual(client.get('/v1/tasks/'+prefix+'other',headers=h(operator,'unused')).status_code,403)
            self.assertEqual(client.post('/v1/tasks',headers=h(operator,'same-key'),json={**p,'title':'different'}).status_code,409)
            self.assertEqual(client.post('/v1/tasks',headers=h(operator,'other'),json={**p,'property_id':b}).status_code,403)
            self.assertEqual(client.post('/v1/tasks',headers=h(viewer,'v1'),json=p).status_code,403)
            update={'title':'updated-fictional-private-text','status':'done','expected_version':1}
            self.assertEqual(client.patch('/v1/tasks/'+task,headers=h(operator,'unused'),json=update).status_code,200)
            self.assertEqual(client.patch('/v1/tasks/'+task,headers=h(operator,'unused'),json=update).status_code,409)
            discussion={'body':'fictional-private-discussion'}
            result=client.post('/v1/tasks/'+task+'/discussion',headers=h(operator,'disc-key'),json=discussion)
            self.assertEqual(result.status_code,201)
            self.assertEqual(client.post('/v1/tasks/'+task+'/discussion',headers=h(operator,'disc-key'),json=discussion).json(),result.json())
            self.assertEqual(client.post('/v1/tasks/'+task+'/discussion',headers=h(viewer,'v2'),json=discussion).status_code,403)
            with psycopg.connect(scoped_dsn) as db:
                db.execute("INSERT INTO task_discussions(id,task_id,author_uid,body,source_role,source_status,source_task_ref_type,source_task_ref_string,source_present_fields) VALUES (%s,%s,NULL,'','fictional-role','fictional-status','string','fixture-task',ARRAY['status','role','task_id'])",(prefix+'legacy',task))
            events=client.get('/v1/tasks/'+task+'/discussion',headers=h(operator,'unused')).json()['items']
            legacy=next(item for item in events if item['id']==prefix+'legacy')
            self.assertIsNone(legacy['author_uid']);self.assertEqual(legacy['body'],'');self.assertEqual(legacy['source_role'],'fictional-role');self.assertEqual(legacy['source_status'],'fictional-status')
            self.assertEqual(client.delete('/v1/tasks/'+task+'?expected_version=2',headers=h(owner,'unused')).status_code,409)
            with psycopg.connect(scoped_dsn) as db:
                audits=db.execute("SELECT action,redacted_metadata::text FROM audit_events WHERE actor_uid IN (%s,%s,%s) AND action IN ('task.create','task.update','discussion.create')",(owner,operator,viewer)).fetchall()
                self.assertEqual(len(audits),3)
                self.assertNotIn('private',repr(audits))
                self.assertNotIn('private',repr(db.execute('SELECT action,redacted_metadata::text FROM audit_events WHERE actor_uid=%s',(owner,)).fetchall()))
                self.assertEqual(db.execute('SELECT count(*) FROM task_discussions WHERE task_id=%s',(task,)).fetchone()[0],2)
                long_title='legacy text ' * 30
                db.execute('INSERT INTO tasks(id,property_id,title,status) VALUES (%s,%s,%s,%s)',(prefix+'long',a,long_title,'open'))
            long_path='/v1/tasks/'+prefix+'long'
            status_update={'status':'done','expected_version':1}
            self.assertEqual(client.patch(long_path,headers=h(viewer,'unused'),json=status_update).status_code,403)
            self.assertEqual(client.patch(long_path,headers=h(operator,'unused'),json=status_update).status_code,200)
            self.assertEqual(client.patch(long_path,headers=h(operator,'unused'),json=status_update).status_code,409)
            with psycopg.connect(scoped_dsn) as db:
                saved=db.execute('SELECT title,status,version FROM tasks WHERE id=%s',(prefix+'long',)).fetchone()
                self.assertEqual(saved,(long_title,'done',2))
                audit=db.execute("SELECT redacted_metadata FROM audit_events WHERE target_id=%s AND action='task.update'",(prefix+'long',)).fetchone()[0]
                self.assertEqual(audit,{'fields':['status']})
            labeled={'body':'fictional-private-event','role_label':'Repair','status_label':'In Process'}
            url='/v1/tasks/'+task+'/discussion'
            created=client.post(url,headers=h(operator,'labeled-event'),json=labeled)
            self.assertEqual(created.status_code,201); event_id=created.json()['id']
            self.assertEqual(client.post(url,headers=h(operator,'labeled-event'),json=labeled).json(),created.json())
            self.assertEqual(client.post(url,headers=h(operator,'labeled-event'),json={**labeled,'status_label':'Done'}).status_code,409)
            events=client.get(url,headers=h(operator,'unused')).json()['items']
            event=next(row for row in events if row['id']==event_id)
            self.assertEqual((event['source_role'],event['source_status']),('Repair','In Process'))
            event_url=url+'/'+event_id
            self.assertEqual(client.delete(event_url,headers=h(viewer,'unused')).status_code,403)
            self.assertEqual(client.delete('/v1/tasks/'+prefix+'other/discussion/'+event_id,headers=h(operator,'unused')).status_code,403)
            self.assertEqual(client.delete(event_url,headers=h(operator,'unused')).status_code,200)
            self.assertEqual(client.delete(event_url,headers=h(operator,'unused')).status_code,404)
            self.assertNotIn(event_id,[row['id'] for row in client.get(url,headers=h(operator,'unused')).json()['items']])
            with psycopg.connect(scoped_dsn) as db:
                preserved=db.execute('SELECT body,deleted_at FROM task_discussions WHERE id=%s',(event_id,)).fetchone()
                self.assertEqual(preserved[0],labeled['body']);self.assertIsNotNone(preserved[1])
                audits=db.execute("SELECT redacted_metadata::text FROM audit_events WHERE action='discussion.delete' AND target_id=%s",(task,)).fetchall()
                self.assertEqual(len(audits),1);self.assertNotIn('private',repr(audits))
        finally:
            pass  # addCleanup drops only the isolated synthetic schema.
