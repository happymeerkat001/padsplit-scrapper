"""Only reviewed provider observations may be linked to internal discussions."""
import json, os, unittest
from backend.storage import migrate
from backend.storage.schema import apply_migrations
from backend.storage.tests.test_inbox_projection import record
from backend.storage.project_provider_discussion_links import project

@unittest.skipUnless(os.getenv('DATABASE_URL'),'isolated staging PostgreSQL required')
class ProviderDiscussionProjectionTests(unittest.TestCase):
 def setUp(self):
  self.db=migrate.staging_connect();apply_migrations(self.db);self.key=b'z'*32
  stamp='2026-01-01T00:00:00Z';run='e'*32
  self.records=[record('private_dashboard/current',{'schema_version':1,'run_id':run,'chunk_count':1,'scraped_at':stamp}),record('private_dashboard_runs/'+run+'/chunks/0',{'index':0,'data':json.dumps({'scraped_at':stamp,'tasks':{},'messages':[]})})]
  migrate.import_records(self.db,self.records,self.key);self.gen='private_dashboard_runs/'+run
  self.db.execute("INSERT INTO properties(id,name) VALUES ('discussion-a','Example A'),('discussion-b','Example B')")
  self.db.execute("INSERT INTO tasks(id,property_id,title,source_path) VALUES ('internal-a','discussion-a','Example','manual_tasks/a'),('internal-b','discussion-b','Other','manual_tasks/b')")
  self.db.execute("INSERT INTO padsplit_task_observations(id,task_key,source_id,property_id,bucket,data,observed_at,source_generation) VALUES ('obs-discussion','provider-discussion','123','discussion-a','Open','{}','2026-01-01',%s)",(self.gen,));self.db.commit()
  self.link={'task_observation_id':'obs-discussion','internal_task_id':'internal-a','expected_property_id':'discussion-a','expected_generation':self.gen,'expected_task_source_path':'manual_tasks/a'}
 def tearDown(self):
  self.db.rollback();self.db.execute('DELETE FROM reviewed_provider_discussion_links');self.db.execute("DELETE FROM padsplit_task_observations WHERE id='obs-discussion'");self.db.execute("DELETE FROM tasks WHERE id IN ('internal-a','internal-b')");self.db.execute("DELETE FROM properties WHERE id IN ('discussion-a','discussion-b')");self.db.execute("DELETE FROM source_documents WHERE source_path LIKE 'private_dashboard/%' OR source_path LIKE 'private_dashboard_runs/%'");self.db.commit();self.db.close()
 def test_dry_run_apply_idempotency_and_scope(self):
  self.assertEqual(project(self.db,self.records,self.key,[self.link]),1)
  self.assertEqual(self.db.execute('SELECT count(*) FROM reviewed_provider_discussion_links').fetchone()[0],0);self.db.rollback()
  self.assertEqual(project(self.db,self.records,self.key,[self.link],apply=True),1)
  self.assertEqual(project(self.db,self.records,self.key,[self.link],apply=True),1)
  for bad in ({**self.link,'internal_task_id':'internal-b','expected_task_source_path':'manual_tasks/b'}, {**self.link,'expected_task_source_path':'manual_tasks/wrong'},{**self.link,'expected_generation':'wrong'}):
   with self.assertRaises(ValueError):project(self.db,self.records,self.key,[bad],apply=True)
  with self.assertRaises(ValueError):project(self.db,self.records,self.key,[self.link,self.link],apply=True)
