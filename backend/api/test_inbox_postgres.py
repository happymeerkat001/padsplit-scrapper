"""Synthetic isolated PostgreSQL API test, never auto-connects to a remote database."""
import os,unittest,uuid
from urllib.parse import urlsplit
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient
from backend.api.app import create_app
from backend.api.repository import Repository
from backend.storage.schema import apply_migrations
DSN=os.getenv('PADSPLIT_API_TEST_DATABASE_URL','')
@unittest.skipUnless(DSN,'Set PADSPLIT_API_TEST_DATABASE_URL to a disposable local staging database')
class InboxPostgresTests(unittest.TestCase):
 def test_scoped_read_pagination_and_empty_state(self):
  url=urlsplit(DSN)
  if url.hostname not in ('127.0.0.1','localhost','::1') or not url.path.lstrip('/').startswith('padsplit_stage'):self.fail('Synthetic loopback staging database required')
  schema='inbox_test_'+uuid.uuid4().hex[:12]
  with psycopg.connect(DSN,autocommit=True) as conn:conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
  def cleanup():
   with psycopg.connect(DSN,autocommit=True) as conn:conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
  self.addCleanup(cleanup)
  dsn=psycopg.conninfo.make_conninfo(DSN,options='-c search_path='+schema)
  with psycopg.connect(dsn) as db:
   apply_migrations(db)
   for uid,role in [('owner','owner'),('scoped','viewer'),('unscoped','viewer')]:
    db.execute('INSERT INTO app_users(firebase_uid) VALUES (%s)',(uid,));db.execute('INSERT INTO user_roles(firebase_uid,role) VALUES (%s,%s)',(uid,role))
   db.execute("INSERT INTO properties(id,name) VALUES ('a','Example A'),('b','Example B')")
   db.execute("INSERT INTO property_access(firebase_uid,property_id) VALUES ('scoped','a')")
   db.execute("INSERT INTO conversations(id,property_id) VALUES ('con-a','a'),('con-b','b'),('con-null',NULL)")
   db.execute("INSERT INTO messages(id,conversation_id,body) VALUES ('msg-a1','con-a','Example one'),('msg-a2','con-a','Example two'),('msg-b','con-b','Never visible to scoped')")
   db.execute("UPDATE conversations SET source_title='Example thread',source_unread=true WHERE id='con-a'")
   db.execute("UPDATE conversations SET source_display='{\"first_name\":\"Fictional\",\"last_name\":null,\"room_number\":\"42\"}'::jsonb WHERE id='con-a'")
   db.execute("UPDATE messages SET sender_label='Example sender',source_deleted=true WHERE id='msg-a1'")
   db.execute("UPDATE messages SET sent_at='2026-01-02' WHERE id='msg-a1'")
   db.execute("UPDATE messages SET sent_at='2026-01-01' WHERE id='msg-a2'")
   db.execute("UPDATE messages SET source_attachments='[{\"filename\":\"photo.jpg\",\"mediaType\":\"image/jpeg\",\"deleted\":false,\"location\":\"https://private.invalid/secret\"}]'::jsonb WHERE id='msg-a2'")
  with TestClient(create_app(repository=Repository(dsn),verify_token=lambda t:t if t in ('owner','scoped','unscoped') else None,allowed_origins=['http://localhost:9999'])) as client:
   h=lambda uid:{'Authorization':'Bearer '+uid}
   self.assertEqual(client.get('/v1/inbox/conversations').status_code,401)
   result=client.get('/v1/inbox/conversations',headers=h('scoped'));self.assertEqual(result.status_code,200);self.assertEqual([x['id'] for x in result.json()['items']],['con-a']);self.assertEqual(result.headers['cache-control'],'no-store');self.assertEqual(result.json()['ingestion'],'not_configured')
   self.assertEqual(result.json()['items'][0]['source_title'],'Example thread')
   self.assertIs(result.json()['items'][0]['source_unread'],True)
   self.assertEqual(result.json()['items'][0]['source_display'],{'first_name':'Fictional','last_name':None,'room_number':'42'})
   self.assertIsNone(result.json()['items'][0]['last_message_preview']) # latest message is deleted
   def search(uid,q,unread=False,limit=100,offset=0):return client.post('/v1/inbox/search',headers=h(uid),json={'q':q,'unread':unread,'limit':limit,'offset':offset})
   self.assertEqual([x['id'] for x in search('scoped','two').json()['items']],['con-a'])
   self.assertEqual(search('scoped','Never').json()['items'],[])
   self.assertEqual(search('scoped','one').json()['items'],[]) # deleted text is not searchable
   self.assertEqual([x['id'] for x in search('owner','Example',True,1).json()['items']],['con-a'])
   self.assertEqual(search('owner','Example',True,1,1).json()['items'],[])
   self.assertEqual([x['id'] for x in search('scoped','Example A').json()['items']],['con-a'])
   self.assertEqual(search('scoped','Example B').json()['items'],[])
   self.assertEqual([x['id'] for x in search('scoped','Fictional 42').json()['items']],['con-a'])
   self.assertEqual(search('owner','%').json()['items'],[])
   oversized=search('owner','x'*101);self.assertEqual(oversized.status_code,422);self.assertNotIn('x'*101,oversized.text)
   self.assertEqual(client.get('/v1/inbox/conversations',headers=h('owner')).json()['items'].__len__(),3)
   self.assertEqual(client.get('/v1/inbox/conversations',headers=h('unscoped')).json()['items'],[])
   self.assertEqual(client.get('/v1/inbox/conversations/con-b/messages',headers=h('scoped')).status_code,403)
   self.assertEqual(client.get('/v1/inbox/conversations/con-null/messages',headers=h('scoped')).status_code,403)
   page=client.get('/v1/inbox/conversations/con-a/messages?limit=1&offset=0',headers=h('scoped'));self.assertEqual(page.status_code,200);self.assertEqual(page.json()['items'][0]['body'],'Example two')
   self.assertEqual(page.json()['items'][0]['attachments'],[{'filename':'photo.jpg','mediaType':'image/jpeg','deleted':False}]);self.assertNotIn('private.invalid',page.text)
   page2=client.get('/v1/inbox/conversations/con-a/messages?limit=1&offset=1',headers=h('scoped'));self.assertEqual(page2.json()['items'][0]['body'],'')
   self.assertEqual(page2.json()['items'][0]['sender_label'],'Example sender');self.assertIs(page2.json()['items'][0]['source_deleted'],True)
   newest=client.get('/v1/inbox/conversations/con-a/messages?limit=1&offset=0&order=desc',headers=h('scoped'));self.assertEqual(newest.status_code,200);self.assertEqual(newest.json()['items'][0]['id'],'msg-a1');self.assertEqual(newest.json()['items'][0]['body'],'')
   older=client.get('/v1/inbox/conversations/con-a/messages?limit=1&offset=1&order=desc',headers=h('scoped'));self.assertEqual(older.json()['items'][0]['id'],'msg-a2');self.assertEqual(older.json()['items'][0]['body'],'Example two')
   self.assertEqual(client.get('/v1/inbox/conversations/con-a/messages?order=invalid',headers=h('scoped')).status_code,422)
   self.assertEqual(client.get('/v1/inbox/conversations/con-a/messages?limit=101',headers=h('scoped')).status_code,422)
   self.assertEqual(client.post('/v1/inbox/conversations/con-a/messages',headers=h('owner'),json={'body':'No outbound action'}).status_code,405)
   # A new message arriving before the current page must not shift an older
   # page and duplicate a row or cause the next message to be skipped.
   with psycopg.connect(dsn) as db:
    db.execute("INSERT INTO conversations(id,property_id) VALUES ('cursor-con','a')")
    db.execute("INSERT INTO messages(id,conversation_id,body,sent_at) VALUES ('cursor-1','cursor-con','one','2026-01-05'),('cursor-2','cursor-con','two','2026-01-04'),('cursor-3','cursor-con','three','2026-01-03'),('cursor-4','cursor-con','four',NULL),('cursor-5','cursor-con','five',NULL)")
   path='/v1/inbox/conversations/cursor-con/messages'
   first=client.get(path+'?limit=2&order=desc',headers=h('scoped'))
   self.assertEqual([x['id'] for x in first.json()['items']],['cursor-1','cursor-2'])
   self.assertEqual(first.json()['next_before'],'cursor-2')
   with psycopg.connect(dsn) as db:db.execute("INSERT INTO messages(id,conversation_id,body,sent_at) VALUES ('cursor-new','cursor-con','new','2026-01-06')")
   second=client.get(path+'?limit=2&order=desc&before=cursor-2',headers=h('scoped'))
   self.assertEqual([x['id'] for x in second.json()['items']],['cursor-3','cursor-5'])
   third=client.get(path+'?limit=2&order=desc&before=cursor-5',headers=h('scoped'))
   self.assertEqual([x['id'] for x in third.json()['items']],['cursor-4'])
   self.assertIsNone(third.json()['next_before'])
   self.assertEqual(client.get(path+'?order=desc&before=msg-b',headers=h('scoped')).status_code,422)
   self.assertEqual(client.get(path+'?order=asc&before=cursor-2',headers=h('scoped')).status_code,422)
   self.assertEqual(client.get(path+'?order=desc&offset=1&before=cursor-2',headers=h('scoped')).status_code,422)
   self.assertEqual(client.get(path+'?order=desc&before=cursor-2',headers=h('unscoped')).status_code,403)
  role='inbox_role_'+uuid.uuid4().hex[:10]
  with psycopg.connect(dsn,autocommit=True) as db:
   db.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
   def cleanup_role():
    with psycopg.connect(DSN,autocommit=True) as admin:
     admin.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)));admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
   self.addCleanup(cleanup_role)
   db.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
   for statement in ['GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}','GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}','GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}','GRANT SELECT (id,property_id,updated_at,source_title,source_observed_at,source_unread,source_display), UPDATE (auth_lock_guard) ON conversations TO {}','GRANT SELECT (id,name) ON properties TO {}','GRANT SELECT (id,conversation_id,body,sent_at,sender_label,source_deleted,source_attachments) ON messages TO {}']:
    db.execute(sql.SQL(statement).format(sql.Identifier(role)))
  class Restricted(Repository):
   def connect(self):
    db=super().connect();db.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)));return db
  with TestClient(create_app(repository=Restricted(dsn),verify_token=lambda t:t,allowed_origins=['http://localhost:9999'])) as client:
   self.assertEqual(client.get('/v1/inbox/conversations',headers=h('scoped')).status_code,200)
   self.assertEqual(client.get('/v1/inbox/conversations/con-a/messages',headers=h('scoped')).status_code,200)
   self.assertEqual(client.get('/v1/inbox/conversations/con-b/messages',headers=h('scoped')).status_code,403)
  with Restricted(dsn).connect() as db:
   with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute("UPDATE conversations SET property_id='b' WHERE id='con-a'")
  with Restricted(dsn).connect() as locked:
   locked.execute("SELECT id FROM conversations WHERE id='con-a' FOR SHARE")
   with psycopg.connect(dsn) as writer:
    writer.execute("SET LOCAL lock_timeout='100ms'")
    with self.assertRaises(psycopg.errors.LockNotAvailable):writer.execute("UPDATE conversations SET property_id='b' WHERE id='con-a'")
