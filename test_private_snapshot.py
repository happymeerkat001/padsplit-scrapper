"""Private snapshot writer tests use only fictional data and an in-memory Firestore double."""
import json
import unittest
from padsplit_scraper.private_snapshot import _write_private_snapshot

class Doc:
    def __init__(self, db, path): self.db, self.path = db, path
    def collection(self, name): return Collection(self.db, self.path + '/' + name)
    def set(self, value):
        if self.db.fail and '/chunks/' in self.path: raise RuntimeError('simulated')
        self.db.writes.append((self.path, value))
class Collection:
    def __init__(self, db, path): self.db, self.path = db, path
    def document(self, name): return Doc(self.db, self.path + '/' + name)
class FakeDB:
    def __init__(self, fail=False): self.writes=[]; self.fail=fail; self.project="padsplit-scrapper"
    def collection(self, name): return Collection(self, name)

class PrivateSnapshotTests(unittest.TestCase):
    def test_disabled_is_noop(self):
        db=FakeDB(); self.assertFalse(_write_private_snapshot(db, {}, enabled=False)); self.assertEqual(db.writes,[])
    def test_pointer_published_after_chunks_and_data_round_trips(self):
        db=FakeDB(); raw={'scraped_at':'2026-01-01T00:00:00Z','messages':[{'text':'fictional'}],'tasks':{'Requests':[{'id':7}]},'secret_extra':'omit'}
        self.assertTrue(_write_private_snapshot(db, raw, enabled=True, chunk_bytes=30, run_id='a'*32))
        pointer_path,pointer=db.writes[-1]; self.assertEqual(pointer_path,'private_dashboard/current')
        chunks=db.writes[:-1]; self.assertEqual(pointer['chunk_count'],len(chunks))
        self.assertTrue(all(len(row['data'].encode('utf8')) <= 30 for _,row in chunks))
        rebuilt=json.loads(''.join(row['data'] for _,row in chunks))
        self.assertEqual(rebuilt['messages'],raw['messages']); self.assertEqual(rebuilt['tasks'],raw['tasks']); self.assertNotIn('secret_extra',rebuilt)
    def test_failed_chunks_never_replace_pointer(self):
        db=FakeDB(fail=True)
        with self.assertRaises(RuntimeError): _write_private_snapshot(db, {'scraped_at':'2026-01-01T00:00:00Z','messages':[], 'tasks':{}},enabled=True)
        self.assertFalse(any(p=='private_dashboard/current' for p,_ in db.writes))
    def test_task_buckets_must_be_arrays(self):
        db=FakeDB()
        with self.assertRaises(ValueError):
            _write_private_snapshot(db, {'scraped_at':'2026-01-01T00:00:00Z','messages':[], 'tasks':{'Requests':'invalid'}},enabled=True)
        self.assertEqual(db.writes,[])
    def test_private_occupancy_is_optional_and_round_trips(self):
        db=FakeDB(); occupancy={'scraped_at':'2026-01-01T00:00:00Z','rooms':[{'address':'Fictional House','room_number':'1'}]}
        payload={'scraped_at':'2026-01-01T00:00:00Z','messages':[],'tasks':{},'occupancy':occupancy}
        self.assertTrue(_write_private_snapshot(db,payload,enabled=True))
        rebuilt=json.loads(''.join(row['data'] for _,row in db.writes[:-1]))
        self.assertEqual(rebuilt['occupancy'],occupancy)

    def test_private_occupancy_must_have_room_objects(self):
        db=FakeDB(); payload={'scraped_at':'2026-01-01T00:00:00Z','messages':[],'tasks':{},'occupancy':{'rooms':[None]}}
        with self.assertRaises(ValueError): _write_private_snapshot(db,payload,enabled=True)
        self.assertEqual(db.writes,[])
    def test_bad_inputs_fail_before_writes(self):
        db=FakeDB()
        with self.assertRaises(ValueError): _write_private_snapshot(db, {'messages':[],'tasks':{}},enabled=True)
        self.assertEqual(db.writes,[])

class PublisherGateTests(unittest.TestCase):
    def test_gate_blocks_disabled_ci_and_collection_policy_before_access(self):
        from unittest.mock import Mock
        from padsplit_scraper.private_snapshot import run_publisher
        from padsplit_scraper.runtime import COLLECTION_ONLY_POLICY
        loader, client = Mock(), Mock()
        for env,policy in [({},None),({'PADSPLIT_PRIVATE_SNAPSHOT_ENABLE':'1','PADSPLIT_COLLECTION_ONLY':'0','CI':'1'},None),({'PADSPLIT_PRIVATE_SNAPSHOT_ENABLE':'1','PADSPLIT_COLLECTION_ONLY':'1'},None),({'PADSPLIT_PRIVATE_SNAPSHOT_ENABLE':'1','PADSPLIT_COLLECTION_ONLY':'0'},COLLECTION_ONLY_POLICY)]:
            self.assertFalse(run_publisher(loader,client,environ=env,policy=policy))
        loader.assert_not_called(); client.assert_not_called()

    def test_wrong_project_is_rejected_before_writing(self):
        import tempfile
        from pathlib import Path
        from padsplit_scraper.private_snapshot import run_publisher
        env={'PADSPLIT_PRIVATE_SNAPSHOT_ENABLE':'1','PADSPLIT_COLLECTION_ONLY':'0'}
        payload={'scraped_at':'2026-01-01T00:00:00Z','messages':[],'tasks':{}}
        db=FakeDB(); db.project='other-project'
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): run_publisher(lambda:payload,lambda:db,environ=env,lock_directory=Path(d)/'lock')
        self.assertEqual(db.writes,[])

    def test_lock_blocks_concurrent_publisher_and_success_releases_it(self):
        import tempfile
        from pathlib import Path
        from padsplit_scraper import process_lock
        from padsplit_scraper.private_snapshot import run_publisher
        env={'PADSPLIT_PRIVATE_SNAPSHOT_ENABLE':'1','PADSPLIT_COLLECTION_ONLY':'0'}
        payload={'scraped_at':'2026-01-01T00:00:00Z','messages':[],'tasks':{}}
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/'lock'; held=process_lock.acquire_lock('private-snapshot',directory=lock)
            try:
                with self.assertRaises(process_lock.LockError): run_publisher(lambda:payload,lambda:FakeDB(),environ=env,lock_directory=lock)
            finally: process_lock.release_lock(held)
            db=FakeDB(); self.assertTrue(run_publisher(lambda:payload,lambda:db,environ=env,lock_directory=lock))
            self.assertFalse(process_lock.lock_is_held(lock)); self.assertEqual(db.writes[-1][0],'private_dashboard/current')

class InvalidMessagesTests(unittest.TestCase):
    def test_rejects_invalid_message_before_writes(self):
        db = FakeDB()
        with self.assertRaises(ValueError):
            _write_private_snapshot(db, {'scraped_at': '2026-01-01T00:00:00Z', 'messages': [None], 'tasks': {}}, enabled=True)
        self.assertEqual(db.writes, [])

if __name__ == "__main__":
    unittest.main()
