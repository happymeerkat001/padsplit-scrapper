"""Offline staging-only collector capture -> reviewed projection tests."""
import contextlib,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.storage import migrate
from backend.storage.tests.test_inbox_projection import fixture
from padsplit_scraper.inbox_collection import advance_checkpoint,export_candidate
from backend.storage.project_inbox import project,snapshot_from_records
from backend.storage.schema import apply_migrations
from backend.storage import stage_inbox_capture as workflow

class GateTests(unittest.TestCase):
 def test_review_digest_includes_staging_target_identity(self):
  records=[{'path':'fictional'}];mapping={'fictional':None};key=b'z'*32
  first=workflow._review_digest(key,records,records,mapping,b'a'*32,b'b'*32,('cluster-a','db-a','schema-a','127.0.0.1',1,'1.0'))
  second=workflow._review_digest(key,records,records,mapping,b'a'*32,b'b'*32,('cluster-b','db-a','schema-a','127.0.0.1',1,'1.0'))
  self.assertNotEqual(first,second)
  restarted=workflow._review_digest(key,records,records,mapping,b'a'*32,b'b'*32,('cluster-a','db-a','schema-a','127.0.0.1',1,'2.0'))
  self.assertNotEqual(first,restarted)
 def test_cli_requires_explicit_gate_before_connecting_or_reading(self):
  inputs=['--baseline','/private/a','--capture','/private/b','--mapping','/private/c','--apply']
  for flags,value,ci in [([], '1',False),(['--enable-staging-inbox'],'0',False),(['--enable-staging-inbox'],'1',True)]:
   with self.subTest(flags=flags,value=value,ci=ci),patch.dict(os.environ,{'PADSPLIT_COLLECTION_ONLY':value},clear=True),patch.object(workflow.runtime,'running_in_ci',return_value=ci),patch.object(workflow,'staging_connect') as connect,contextlib.redirect_stderr(io.StringIO()):
    self.assertEqual(workflow.main([*flags,*inputs]),2);connect.assert_not_called()
  with patch.dict(os.environ,{'PADSPLIT_COLLECTION_ONLY':'1'},clear=True),patch.object(workflow.runtime,'running_in_ci',return_value=False),patch.object(workflow,'staging_connect') as connect,contextlib.redirect_stderr(io.StringIO()):
   self.assertEqual(workflow.main(['--enable-staging-inbox',*inputs]),2);connect.assert_not_called()
 def test_errors_are_generic_and_never_echo_details(self):
  with patch.dict(os.environ,{'PADSPLIT_COLLECTION_ONLY':'1'},clear=True),patch.object(workflow.runtime,'running_in_ci',return_value=False),patch.object(workflow,'key_from_env',side_effect=ValueError('fixture-private-details')),contextlib.redirect_stderr(io.StringIO()) as out:
   self.assertEqual(workflow.main(['--enable-staging-inbox','--baseline','/private/a','--capture','/private/b','--mapping','/private/c']),1)
   self.assertNotIn('fixture-private-details',out.getvalue())
 def test_explicit_apply_is_offline_and_reports_only_counts(self):
  class Conn:
   def __enter__(self):return self
   def __exit__(self,*args):return False
  args=['--enable-staging-inbox','--baseline','/private/example-baseline','--capture','/private/example-capture','--mapping','/private/example-mapping','--review-receipt','/private/example-receipt','--apply']
  with patch.dict(os.environ,{'PADSPLIT_COLLECTION_ONLY':'1'},clear=True),patch.object(workflow.runtime,'running_in_ci',return_value=False),patch.object(workflow,'key_from_env',return_value=b'z'*32),patch.object(workflow,'staging_connect',return_value=Conn()),patch.object(workflow,'stage',return_value=(1,2)) as stage,patch('padsplit_scraper.collect_private_inbox.main',side_effect=AssertionError('provider must not run')) as collector,contextlib.redirect_stdout(io.StringIO()) as out:
   self.assertEqual(workflow.main(args),0);self.assertTrue(stage.call_args.kwargs['apply']);collector.assert_not_called();self.assertNotIn('/private/',out.getvalue());self.assertIn('filtered and non-atomic',out.getvalue())

@unittest.skipUnless(os.getenv('DATABASE_URL'),'isolated PostgreSQL database required')
class CaptureProjectionTests(unittest.TestCase):
 def setUp(self):
  self.key=b'z'*32;self.records=fixture();self.conn=migrate.staging_connect();apply_migrations(self.conn);migrate.import_records(self.conn,self.records,self.key)
  self.conn.execute("INSERT INTO properties(id,name) VALUES ('property-example','Fictional property')");self.conn.commit();project(self.conn,self.records,self.key,{'chat-example':'property-example'},apply=True)
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.baseline=self.root/'baseline.enc';self.capture=self.root/'capture.enc';self.checkpoint=self.root/'checkpoint.enc';self.mapping=self.root/'mapping.json'
  migrate.encrypted_backup(self.records,self.baseline,self.key);self.mapping.write_text(json.dumps({'version':1,'bindings':[{'chat_id':'chat-example','property_id':'property-example'}]}));self.mapping.chmod(0o600)
  chat=snapshot_from_records(self.records)['messages'][0];old=chat['lastMessage'];new={'id':'message-2','text':'Example second','created':'2026-01-02T00:00:00Z'}
  with patch('padsplit_scraper.inbox_collection.chat_page',return_value={'items':[chat],'next_cursor':None,'exhausted':True}),patch('padsplit_scraper.inbox_collection.message_page',return_value={'items':[old,new],'next_cursor':None,'exhausted':True}):
   advance_checkpoint(lambda *_:None,self.checkpoint,self.key,max_pages=2,account_tag='a'*64,observed_at='2026-01-02T00:00:00Z')
  export_candidate(self.checkpoint,self.capture,self.key)
 def tearDown(self):
  self.conn.rollback();self.conn.execute('DELETE FROM inbox_snapshot_runs');self.conn.execute("DELETE FROM messages WHERE source_path LIKE 'padsplit_message/%'");self.conn.execute("DELETE FROM conversations WHERE source_path LIKE 'padsplit_chat/%'");self.conn.execute("DELETE FROM properties WHERE id='property-example'");self.conn.execute("DELETE FROM source_documents WHERE source_path LIKE 'private_dashboard/%' OR source_path LIKE 'private_dashboard_runs/%'");self.conn.commit();self.conn.close()
 def test_dry_run_apply_repeat_and_no_scope_guessing(self):
  args=(self.conn,self.baseline,self.capture,self.mapping,self.key);receipt=self.root/'review.receipt'
  with self.assertRaises(ValueError):workflow.stage(*args,apply=True)
  self.assertEqual(workflow.stage(*args,review_receipt=receipt),(1,2));self.assertEqual(receipt.stat().st_mode&0o777,0o600);self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],1);self.conn.rollback()
  original=self.capture.read_bytes();repacked=self.root/'repacked.enc'
  migrate.encrypted_backup(migrate.read_backup(self.capture,self.key),repacked,self.key);self.capture.write_bytes(repacked.read_bytes());self.capture.chmod(0o600)
  with self.assertRaises(ValueError):workflow.stage(*args,apply=True,review_receipt=receipt)
  self.capture.write_bytes(original);self.capture.chmod(0o600)
  self.mapping.write_text(json.dumps({'version':1,'bindings':[{'chat_id':'chat-example','property_id':None}]}))
  with self.assertRaises(ValueError):workflow.stage(*args,apply=True,review_receipt=receipt)
  self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],1);self.conn.rollback()
  self.mapping.write_text(json.dumps({'version':1,'bindings':[{'chat_id':'chat-example','property_id':'property-example'}]}))
  self.assertEqual(workflow.stage(*args,apply=True,review_receipt=receipt),(1,2));self.assertEqual(workflow.stage(*args,apply=True,review_receipt=receipt),(1,2));self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],2);self.conn.rollback()
  self.mapping.write_text(json.dumps({'version':1,'bindings':[{'chat_id':'chat-example','property_id':None}]}))
  with self.assertRaises(ValueError):workflow.stage(*args,apply=True,review_receipt=receipt)
  self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],2);self.conn.rollback()
 def test_review_receipt_is_private_and_tamper_evident(self):
  args=(self.conn,self.baseline,self.capture,self.mapping,self.key);receipt=self.root/'review.receipt'
  workflow.stage(*args,review_receipt=receipt)
  original=receipt.read_bytes()
  with self.assertRaises(FileExistsError):workflow.stage(*args,review_receipt=receipt)
  self.assertEqual(receipt.read_bytes(),original)
  alias=self.root/'receipt-alias';alias.symlink_to(receipt)
  with self.assertRaises((ValueError,RuntimeError,OSError)):workflow.stage(*args,apply=True,review_receipt=alias)
  receipt.chmod(0o644)
  with self.assertRaises(ValueError):workflow.stage(*args,apply=True,review_receipt=receipt)
  receipt.chmod(0o600);receipt.write_text('{"version":1,"digest":"'+('0'*64)+'"}')
  with self.assertRaises(ValueError):workflow.stage(*args,apply=True,review_receipt=receipt)
  self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],1);self.conn.rollback()
 def test_capture_changed_during_authenticated_read_is_rejected(self):
  original=workflow.read_backup
  def mutate(fd,key):
   records=original(fd,key)
   with self.baseline.open('ab') as stream:stream.write(b'changed')
   return records
  with patch.object(workflow,'read_backup',side_effect=mutate):
   with self.assertRaises(ValueError):workflow.stage(self.conn,self.baseline,self.capture,self.mapping,self.key,review_receipt=self.root/'review.receipt')
  self.assertFalse((self.root/'review.receipt').exists());self.conn.rollback()
  self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],1);self.conn.rollback()
 @unittest.skipUnless(os.getenv('PADSPLIT_API_TEST_SECOND_DATABASE_URL'),'second isolated PostgreSQL cluster required')
 def test_receipt_rejects_same_database_name_on_another_cluster(self):
  import psycopg
  from urllib.parse import urlsplit
  second=os.environ['PADSPLIT_API_TEST_SECOND_DATABASE_URL'];first=urlsplit(os.environ['DATABASE_URL']);target=urlsplit(second)
  if target.hostname not in ('127.0.0.1','localhost','::1') or not target.path.lstrip('/').startswith('padsplit_stage_') or target.path!=first.path or target.port==first.port:self.fail('Two isolated clusters with same staging database name required')
  receipt=self.root/'target-review.receipt';args=(self.baseline,self.capture,self.mapping,self.key)
  workflow.stage(self.conn,*args,review_receipt=receipt)
  data=receipt.read_text();self.assertEqual(set(json.loads(data)),{'version','digest'});self.assertNotIn(target.path.lstrip('/'),data);self.assertNotIn('127.0.0.1',data)
  with psycopg.connect(second) as other:
   apply_migrations(other);migrate.import_records(other,self.records,self.key)
   other.execute("INSERT INTO properties(id,name) VALUES ('property-example','Fictional property')");other.commit()
   project(other,self.records,self.key,{'chat-example':'property-example'},apply=True)
   with self.assertRaises(ValueError):workflow.stage(other,*args,apply=True,review_receipt=receipt)
   other.rollback();self.assertEqual(other.execute('SELECT count(*) FROM messages').fetchone()[0],1)
 def test_unmarked_capture_and_nonprivate_files_fail(self):
  receipt=self.root/'review.receipt';workflow.stage(self.conn,self.baseline,self.capture,self.mapping,self.key,review_receipt=receipt)
  forged=self.root/'unmarked.enc';migrate.encrypted_backup(self.records,forged,self.key)
  with self.assertRaises(ValueError):workflow.stage(self.conn,self.baseline,forged,self.mapping,self.key,apply=True,review_receipt=receipt)
  self.capture.chmod(0o644)
  with self.assertRaises(ValueError):workflow.stage(self.conn,self.baseline,self.capture,self.mapping,self.key,apply=True,review_receipt=receipt)
  self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],1);self.conn.rollback()
 def test_corrupt_capture_and_symlink_fail_without_writes(self):
  receipt=self.root/'review.receipt';workflow.stage(self.conn,self.baseline,self.capture,self.mapping,self.key,review_receipt=receipt)
  alias=self.root/'alias.enc';alias.symlink_to(self.capture)
  with self.assertRaises((ValueError,RuntimeError,OSError)):workflow.stage(self.conn,self.baseline,alias,self.mapping,self.key,apply=True,review_receipt=receipt)
  data=self.capture.read_bytes();self.capture.write_bytes(data[:-1]);self.capture.chmod(0o600)
  with self.assertRaises(ValueError):workflow.stage(self.conn,self.baseline,self.capture,self.mapping,self.key,apply=True,review_receipt=receipt)
  self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0],1);self.conn.rollback()
