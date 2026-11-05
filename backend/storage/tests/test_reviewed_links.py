"""Fictional, explicitly reviewed links; no address-based inference."""
import json, os, unittest
from backend.storage import migrate
from backend.storage.project_reviewed_links import project
from backend.storage.schema import apply_migrations
from backend.storage.tests.test_inbox_projection import record

@unittest.skipUnless(os.getenv('DATABASE_URL'),'isolated staging PostgreSQL required')
class ReviewedLinkTests(unittest.TestCase):
 def setUp(self):
  self.db=migrate.staging_connect();apply_migrations(self.db);self.key=b'r'*32
  stamp='2026-01-01T00:00:00Z';run='f'*32
  snapshot={'scraped_at':stamp,'tasks':{},'messages':[]};text=json.dumps(snapshot,separators=(',',':'))
  self.records=[record('private_dashboard/current',{'schema_version':1,'run_id':run,'chunk_count':1,'scraped_at':stamp}),record('private_dashboard_runs/'+run+'/chunks/0',{'index':0,'data':text})]
  migrate.import_records(self.db,self.records,self.key)
  self.gen='private_dashboard_runs/'+run
  self.db.execute("INSERT INTO properties(id,name) VALUES ('review-a','Fixture A'),('review-b','Fixture B')")
  self.db.execute("INSERT INTO rooms(id,property_id,label) VALUES ('review-room','review-a','1'),('review-room-other','review-b','1')")
  self.obs=self.db.execute("INSERT INTO occupancy_observations(room_id,observed_at,source_generation,source_payload) VALUES ('review-room','2026-01-01',%s,'{}') RETURNING id",(self.gen,)).fetchone()[0]
  self.other=self.db.execute("INSERT INTO occupancy_observations(room_id,observed_at,source_generation,source_payload) VALUES ('review-room-other','2026-01-01',%s,'{}') RETURNING id",(self.gen,)).fetchone()[0]
  self.db.execute("INSERT INTO padsplit_task_observations(id,task_key,source_id,property_id,bucket,data,observed_at,source_generation) VALUES ('review-task-obs','review-task','123','review-a','Open','{}','2026-01-01',%s)",(self.gen,));self.db.commit()
  self.bindings=[{'task_observation_id':'review-task-obs','occupancy_observation_id':self.obs,'expected_property_id':'review-a','expected_generation':self.gen}]
 def tearDown(self):
  self.db.rollback();self.db.execute('DELETE FROM reviewed_room_task_links');self.db.execute("DELETE FROM padsplit_task_observations WHERE id='review-task-obs'");self.db.execute("DELETE FROM occupancy_observations WHERE room_id IN ('review-room','review-room-other')");self.db.execute("DELETE FROM rooms WHERE id IN ('review-room','review-room-other')");self.db.execute("DELETE FROM properties WHERE id IN ('review-a','review-b')");self.db.execute("DELETE FROM source_documents WHERE source_path LIKE 'private_dashboard/%' OR source_path LIKE 'private_dashboard_runs/%'");self.db.commit();self.db.close()
 def test_dry_run_apply_replay_and_wrong_scope_fails(self):
  self.assertEqual(project(self.db,self.records,self.key,self.bindings),1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM reviewed_room_task_links').fetchone()[0],0);self.db.rollback()
  self.assertEqual(project(self.db,self.records,self.key,self.bindings,apply=True),1)
  self.assertEqual(project(self.db,self.records,self.key,self.bindings,apply=True),1)
  with self.assertRaises(ValueError):project(self.db,self.records,self.key,[{**self.bindings[0],'occupancy_observation_id':self.other}],apply=True)
  self.assertEqual(self.db.execute('SELECT count(*) FROM reviewed_room_task_links').fetchone()[0],1)
 def test_rejects_mismatched_generation_and_duplicate_mapping(self):
  with self.assertRaises(ValueError):project(self.db,self.records,self.key,[{**self.bindings[0],'expected_generation':'wrong'}],apply=True)
  with self.assertRaises(ValueError):project(self.db,self.records,self.key,self.bindings*2,apply=True)
