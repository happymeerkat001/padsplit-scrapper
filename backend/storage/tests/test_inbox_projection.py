import datetime as dt
import json
import unittest
from backend.storage import migrate
from backend.storage.project_inbox import snapshot_from_records, projection_rows

def record(path, fields):
    return {'path':path, 'update_time':'2026-01-01T00:00:00+00:00', 'data':migrate.encode_value(fields)}

def fixture():
    payload={'scraped_at':'2026-01-01T00:00:00Z','tasks':{},'messages':[
      {'id':'chat-example','occupancy':{'user':{'firstName':'Example','lastName':None},'room':{'roomNumber':'2'}},'lastMessage':{'id':'message-example','text':'Hello','created':'2026-01-01T00:00:00Z','sender':{'displayName':'Example'}}}]}
    text=json.dumps(payload,ensure_ascii=True,separators=(',',':')); run='a'*32
    return [record('private_dashboard/current',{'schema_version':1,'run_id':run,'chunk_count':1,'scraped_at':payload['scraped_at']}),
            record('private_dashboard_runs/'+run+'/chunks/0',{'index':0,'data':text})]

class InboxProjectionShapeTests(unittest.TestCase):
    def test_source_display_is_bounded_and_preserves_null_and_missing(self):
        payload=snapshot_from_records(fixture()); chat=payload['messages'][0]
        chat['occupancy']={'user':{'firstName':'Example','lastName':None},'room':{'roomNumber':'2'}}
        chat['property']={'address':{'street1':'1 Fictional Street','city':{'name':'Exampleville'}}}
        display=projection_rows(payload,{'chat-example':None})[0][0]['display']
        self.assertEqual(display,{'first_name':'Example','last_name':None,'room_number':'2','street':'1 Fictional Street','city':'Exampleville'})
        self.assertNotIn('state',display)
        chat['occupancy']['user']['firstName']='https://private.invalid/token'
        with self.assertRaises(ValueError):projection_rows(payload,{'chat-example':None})
    def test_unread_value_is_preserved_and_missing_is_unknown(self):
        payload=snapshot_from_records(fixture());chat=payload['messages'][0]
        self.assertIsNone(projection_rows(payload,{'chat-example':None})[0][0]['unread'])
        chat['member']={'isUnread':True}
        self.assertIs(projection_rows(payload,{'chat-example':None})[0][0]['unread'],True)
        chat['member']['isUnread']='yes'
        with self.assertRaises(ValueError):projection_rows(payload,{'chat-example':None})
    def test_mapping_must_be_private_and_unique(self):
        import os, tempfile
        from pathlib import Path
        from backend.storage.project_inbox import load_bindings
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'mapping.json'
            source.write_text('{"version":1,"bindings":[{"chat_id":"a","property_id":null}]}')
            source.chmod(0o600)
            self.assertEqual(load_bindings(source),{'a':None})
            source.chmod(0o644)
            with self.assertRaises(ValueError):load_bindings(source)
            source.chmod(0o600)
            source.write_text('{"version":1,"bindings":[{"chat_id":"a","property_id":null},{"chat_id":"a","property_id":null}]}')
            with self.assertRaises(ValueError):load_bindings(source)
            alias=Path(directory)/'alias';alias.symlink_to(source)
            with self.assertRaises(ValueError):load_bindings(alias)
    def test_complete_snapshot_and_explicit_owner_reviewed_scope(self):
        payload=snapshot_from_records(fixture())
        conversations,messages=projection_rows(payload,{'chat-example':'property-example'})
        self.assertEqual(len(conversations),1);self.assertEqual(len(messages),1)
        self.assertEqual(conversations[0]['property_id'],'property-example')
        self.assertEqual(messages[0]['body'],'Hello');self.assertEqual(messages[0]['sender'],'Example')
        self.assertEqual(projection_rows(payload,{'chat-example':None})[0][0]['property_id'],None)
    def test_incomplete_chunks_or_missing_explicit_mapping_fail_closed(self):
        with self.assertRaises(ValueError):snapshot_from_records(fixture()[:1])
        with self.assertRaises(ValueError):projection_rows(snapshot_from_records(fixture()),{})
        with self.assertRaises(ValueError):projection_rows(snapshot_from_records(fixture()),{'chat-example':'property-example','unused':'property-example'})
    def test_duplicate_message_conflict_and_deleted_message_not_exposed(self):
        payload=snapshot_from_records(fixture()); chat=payload['messages'][0]
        chat['recent_messages']=[dict(chat['lastMessage'],text='different')]
        with self.assertRaises(ValueError):projection_rows(payload,{'chat-example':None})
        chat['recent_messages']=[];chat['lastMessage']['deleted']=True
        self.assertEqual(projection_rows(payload,{'chat-example':None})[1][0]['body'],'')

    def test_attachment_metadata_excludes_private_locations(self):
        payload=snapshot_from_records(fixture());msg=payload['messages'][0]['lastMessage']
        msg['attachments']=[{'id':'photo-id','mediaType':'image/jpeg','filename':'photo.jpg','deleted':False,'location':'https://private.invalid/token'}]
        _,messages=projection_rows(payload,{'chat-example':None})
        attachment=messages[0]['attachments'][0]
        self.assertEqual({k:v for k,v in attachment.items() if k!='id'}, {'mediaType':'image/jpeg','filename':'photo.jpg','deleted':False})
        self.assertTrue(attachment['id'].startswith('attachment_'))
        self.assertNotIn('private.invalid',repr(messages))

    def test_encoded_attachment_paths_are_not_projected(self):
        payload=snapshot_from_records(fixture());msg=payload['messages'][0]['lastMessage']
        msg['attachments']=[{'id':'photo-id','filename':'https%253A%252F%252Fprivate.invalid%252Fsecret','deleted':False},
                            {'id':'safe-id','filename':'100% complete.jpg','deleted':False}]
        _,messages=projection_rows(payload,{'chat-example':None})
        self.assertNotIn('filename',messages[0]['attachments'][0])
        self.assertEqual(messages[0]['attachments'][1]['filename'],'100% complete.jpg')

@unittest.skipUnless(__import__('os').environ.get('DATABASE_URL'),'isolated PostgreSQL test database not supplied')
class InboxProjectionDatabaseTests(unittest.TestCase):
    def setUp(self):
        from backend.storage.schema import apply_migrations
        self.conn=migrate.staging_connect();apply_migrations(self.conn);self.records=fixture();self.key=b'z'*32
        migrate.import_records(self.conn,self.records,self.key)
        self.conn.execute("INSERT INTO properties(id,name) VALUES ('property-example','Fictional property')")
        self.conn.commit()
    def tearDown(self):
        self.conn.rollback()
        self.conn.execute('DELETE FROM inbox_snapshot_runs')
        self.conn.execute("DELETE FROM messages WHERE source_path LIKE 'padsplit_message/%'")
        self.conn.execute("DELETE FROM conversations WHERE source_path LIKE 'padsplit_chat/%'")
        self.conn.execute("DELETE FROM properties WHERE id='property-example'")
        self.conn.execute("DELETE FROM source_documents WHERE source_path LIKE 'private_dashboard/%' OR source_path LIKE 'private_dashboard_runs/%'")
        self.conn.commit();self.conn.close()
    def test_dry_run_repeatable_import_scope_and_conflict_rollback(self):
        from backend.storage.project_inbox import project
        scope={'chat-example':'property-example'}
        self.assertEqual(project(self.conn,self.records,self.key,scope),(1,1))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM conversations').fetchone()[0],0);self.conn.rollback()
        self.assertEqual(project(self.conn,self.records,self.key,scope,apply=True),(1,1))
        self.assertEqual(project(self.conn,self.records,self.key,scope,apply=True),(1,1))
        self.assertEqual(self.conn.execute('SELECT sender_label,body FROM messages').fetchone(),('Example','Hello'))
        self.assertEqual(self.conn.execute('SELECT source_display FROM conversations').fetchone()[0],{'first_name':'Example','last_name':None,'room_number':'2'})
        self.conn.execute("UPDATE messages SET body='different'");self.conn.commit()
        with self.assertRaises(ValueError):project(self.conn,self.records,self.key,scope,apply=True)
        self.conn.rollback();self.assertEqual(self.conn.execute('SELECT body FROM messages').fetchone()[0],'different')
        self.conn.rollback()
        with self.assertRaises(ValueError):project(self.conn,self.records,self.key,{'chat-example':None},apply=True)
    def test_unknown_property_fails_without_partial_inserts(self):
        from backend.storage.project_inbox import project
        with self.assertRaises(ValueError):project(self.conn,self.records,self.key,{'chat-example':'missing-property'},apply=True)
        self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM conversations').fetchone()[0],0)
    def test_authenticated_baseline_catchup_is_atomic_idempotent_and_monotonic(self):
        import tempfile
        from pathlib import Path
        from backend.storage.project_inbox import project, catch_up
        scope={'chat-example':'property-example'}
        project(self.conn,self.records,self.key,scope,apply=True)
        payload=snapshot_from_records(self.records);payload['scraped_at']='2026-01-02T00:00:00Z';payload['messages'][0]['member']={'isUnread':True}
        previous=payload['messages'][0]['lastMessage']
        payload['messages'][0]['recent_messages']=[previous]
        payload['messages'][0]['lastMessage']={'id':'message-2','text':'Second','created':'2026-01-02T00:00:00Z'}
        def as_records(value,run):
            return [record('private_dashboard/current',{'schema_version':1,'run_id':run,'chunk_count':1,'scraped_at':value['scraped_at']}),
                    record('private_dashboard_runs/'+run+'/chunks/0',{'index':0,'data':json.dumps(value,ensure_ascii=True,separators=(',',':'))})]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def backup(name,records):
                path=root/name;migrate.encrypted_backup(records,path,self.key);return path
            baseline=backup('baseline',self.records);candidate=backup('candidate',as_records(payload,'b'*32))
            self.assertEqual(catch_up(self.conn,baseline,candidate,self.key,scope),(1,2))
            self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],1);self.conn.rollback()
            self.assertEqual(catch_up(self.conn,baseline,candidate,self.key,scope,apply=True),(1,2))
            self.assertEqual(catch_up(self.conn,baseline,candidate,self.key,scope,apply=True),(1,2))
            self.assertIs(self.conn.execute('SELECT source_unread FROM conversations').fetchone()[0],True)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],2)
            cipher=self.conn.execute('SELECT ciphertext FROM inbox_snapshot_runs').fetchone()[0]
            self.assertNotIn(b'Second',bytes(cipher));self.conn.rollback()
            with self.assertRaises(ValueError):catch_up(self.conn,baseline,baseline,self.key,scope,apply=True)
            with self.assertRaises(ValueError):catch_up(self.conn,baseline,backup('same-time',as_records(payload,'e'*32)),self.key,scope,apply=True)
            with self.assertRaises(ValueError):catch_up(self.conn,baseline,candidate,self.key,{'chat-example':None},apply=True)
            altered=bytearray(candidate.read_bytes());altered[-8] ^= 1;(root/'altered').write_bytes(altered)
            with self.assertRaises(ValueError):catch_up(self.conn,baseline,root/'altered',self.key,scope,apply=True)
            corrupt=bytearray(cipher);corrupt[-1] ^= 1
            self.conn.execute('UPDATE inbox_snapshot_runs SET ciphertext=%s',(bytes(corrupt),));self.conn.commit()
            with self.assertRaises(ValueError):catch_up(self.conn,baseline,candidate,self.key,scope,apply=True)
            self.conn.rollback();self.conn.execute('UPDATE inbox_snapshot_runs SET ciphertext=%s',(bytes(cipher),));self.conn.commit()
            changed=json.loads(json.dumps(payload));changed['scraped_at']='2026-01-03T00:00:00Z';changed['messages'][0]['recent_messages'][0]['text']='Changed'
            changed['messages'][0]['title']='Changed title'
            changed['messages'][0]['recent_messages'].insert(0,{'id':'message-3','text':'Must roll back','created':'2026-01-03T00:00:00Z'})
            with self.assertRaises(ValueError):catch_up(self.conn,baseline,backup('changed',as_records(changed,'c'*32)),self.key,scope,apply=True)
            self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM inbox_snapshot_runs').fetchone()[0],1)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],2)
            self.assertIsNone(self.conn.execute('SELECT source_title FROM conversations').fetchone()[0]);self.conn.rollback()
            empty={'scraped_at':'2026-01-04T00:00:00Z','messages':[],'tasks':{}}
            self.assertEqual(catch_up(self.conn,baseline,backup('empty',as_records(empty,'d'*32)),self.key,{},apply=True),(0,0))
            self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],2)
