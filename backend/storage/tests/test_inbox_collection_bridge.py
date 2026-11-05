"""Synthetic collector-checkpoint -> encrypted candidate -> staging catch-up."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.storage import migrate
from backend.storage.schema import apply_migrations
from backend.storage.project_inbox import project,catch_up,snapshot_from_records,combine_initial_capture
from backend.storage import stage_inbox_capture
from backend.storage.tests.test_inbox_projection import fixture
from padsplit_scraper.inbox_collection import advance_checkpoint,export_candidate

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated PostgreSQL database required')
class InboxCollectionBridgeTests(unittest.TestCase):
    def test_initial_capture_combines_authenticated_source_without_overwriting_snapshot(self):
        key=b'z'*32;private=fixture();payload=snapshot_from_records(private);chat=payload['messages'][0]
        from backend.storage.tests.test_inbox_projection import record
        baseline_records=[record('notes/example',{'text':'fictional'})]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);baseline=root/'source.enc';candidate=root/'candidate.enc';checkpoint=root/'checkpoint.enc';combined=root/'combined.enc'
            migrate.encrypted_backup(baseline_records,baseline,key)
            with patch('padsplit_scraper.inbox_collection.chat_page',return_value={'items':[chat],'next_cursor':None,'exhausted':True}),patch('padsplit_scraper.inbox_collection.message_page',return_value={'items':[chat['lastMessage']],'next_cursor':None,'exhausted':True}):
                advance_checkpoint(lambda *_:None,checkpoint,key,max_pages=2,account_tag='a'*64,observed_at='2026-01-02T00:00:00Z')
            export_candidate(checkpoint,candidate,key);combine_initial_capture(baseline,candidate,combined,key)
            records=migrate.read_backup(combined,key);self.assertIn('notes/example',[r['path'] for r in records]);self.assertEqual(len(snapshot_from_records(records)['messages']),1)
            with self.assertRaises(FileExistsError):combine_initial_capture(baseline,candidate,combined,key)
            old=root/'old.enc';migrate.encrypted_backup(private,old,key)
            with self.assertRaises(ValueError):combine_initial_capture(old,candidate,root/'rejected.enc',key)
            self.assertFalse((root/'rejected.enc').exists())
    def test_complete_encrypted_collector_candidate_imports_idempotently(self):
        key=b'z'*32;records=fixture();scope={'chat-example':'property-example'}
        with migrate.staging_connect() as conn:
            apply_migrations(conn);migrate.import_records(conn,records,key)
            conn.execute("INSERT INTO properties(id,name) VALUES ('property-example','Fictional property')");conn.commit()
            try:
                project(conn,records,key,scope,apply=True)
                with tempfile.TemporaryDirectory() as d:
                    root=Path(d);baseline=root/'baseline.enc';candidate=root/'candidate.enc';checkpoint=root/'checkpoint.enc'
                    migrate.encrypted_backup(records,baseline,key)
                    chat=snapshot_from_records(records)['messages'][0]
                    old=chat['lastMessage'];new={'id':'message-2','text':'Example second','created':'2026-01-02T00:00:00Z'}
                    with patch('padsplit_scraper.inbox_collection.chat_page',return_value={'items':[chat],'next_cursor':None,'exhausted':True}),patch('padsplit_scraper.inbox_collection.message_page',return_value={'items':[old,new],'next_cursor':None,'exhausted':True}):
                        result=advance_checkpoint(lambda *_:None,checkpoint,key,max_pages=2,account_tag='a'*64,observed_at='2026-01-02T00:00:00Z')
                    self.assertTrue(result['complete']);export_candidate(checkpoint,candidate,key)
                    with self.assertRaises(ValueError):catch_up(conn,baseline,candidate,key,scope,apply=True)
                    captured=migrate.read_backup(candidate,key)
                    marker=next(r for r in captured if r['path']=='inbox_collection/source')
                    for name,extra in [('malformed',dict(marker,data=migrate.encode_value({'kind':None}))),
                                       ('renamed',dict(marker,path='other/provenance'))]:
                        changed=[r for r in captured if r['path']!='inbox_collection/source']+[extra]
                        altered=root/(name+'.enc');migrate.encrypted_backup(changed,altered,key)
                        with self.assertRaises(ValueError):catch_up(conn,baseline,altered,key,scope,apply=True)
                    conn.rollback();self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0],1);conn.rollback()
                    mapping=root/'mapping.json';receipt=root/'receipt.json'
                    mapping.write_text(json.dumps({'version':1,'bindings':[{'chat_id':'chat-example','property_id':'property-example'}]}));mapping.chmod(0o600)
                    self.assertEqual(stage_inbox_capture.stage(conn,baseline,candidate,mapping,key,review_receipt=receipt),(1,2))
                    self.assertEqual(stage_inbox_capture.stage(conn,baseline,candidate,mapping,key,review_receipt=receipt,apply=True),(1,2))
                    self.assertEqual(stage_inbox_capture.stage(conn,baseline,candidate,mapping,key,review_receipt=receipt,apply=True),(1,2))
                    self.assertEqual(conn.execute('SELECT count(*) FROM messages').fetchone()[0],2);conn.rollback()
            finally:
                conn.rollback();conn.execute('DELETE FROM inbox_snapshot_runs');conn.execute("DELETE FROM messages WHERE source_path LIKE 'padsplit_message/%'");conn.execute("DELETE FROM conversations WHERE source_path LIKE 'padsplit_chat/%'");conn.execute("DELETE FROM properties WHERE id='property-example'");conn.execute("DELETE FROM source_documents WHERE source_path LIKE 'private_dashboard/%' OR source_path LIKE 'private_dashboard_runs/%'");conn.commit()
