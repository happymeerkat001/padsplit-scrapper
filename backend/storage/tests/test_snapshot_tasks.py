"""Synthetic source tasks only: explicit scopes and safe API fields."""
import copy, json, os, unittest
from backend.storage import migrate
from backend.storage.project_snapshot_tasks import projection_rows
from backend.storage.tests.test_inbox_projection import record
STAMP='2026-01-01T00:00:00Z'
def payload():return {'scraped_at':STAMP,'tasks':{'Open':[{'id':123,'status':'accepted','category':'maintenance','details':'Fixture','room_number':1,'property_id':456,'room_code':'fictional-secret','unrecognized':{'private':'fixture'}}]},'messages':[]}
def bindings():return [{'source_id':123,'source_property_id':456,'source_property_present':True,'target_property_id':'fixture-property','expected_property_name':'Fixture property'}]
def records():
 p=payload(); text=json.dumps(p,ensure_ascii=True,separators=(',',':'));run='d'*32
 return [record('private_dashboard/current',{'schema_version':1,'run_id':run,'chunk_count':1,'scraped_at':STAMP}),record('private_dashboard_runs/'+run+'/chunks/0',{'index':0,'data':text})]
class ShapeTests(unittest.TestCase):
 def test_exact_reviewed_scope_preserves_id_and_minimizes_response(self):
  rows=projection_rows(payload(),bindings());self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source_id'],123);self.assertEqual(rows[0]['bucket'],'Open');self.assertEqual(rows[0]['data']['room_number'],1);self.assertNotIn('room_code',rows[0]['data']);self.assertNotIn('unrecognized',rows[0]['data'])
 def test_no_guess_duplicate_unused_or_changed_identity(self):
  for scope in ([],bindings()+bindings(),bindings()+[dict(bindings()[0],source_id=999)]):
   with self.assertRaises(ValueError):projection_rows(payload(),scope)
  bad=payload();bad['tasks']['Open'][0]['property_id']=999
  with self.assertRaises(ValueError):projection_rows(bad,bindings())
  bad=payload();bad['tasks']['Open'].append(copy.deepcopy(bad['tasks']['Open'][0]))
  with self.assertRaises(ValueError):projection_rows(bad,bindings())
 def test_owner_only_scope_is_explicit(self):
  scope=[dict(bindings()[0],target_property_id=None,expected_property_name=None)]
  self.assertIsNone(projection_rows(payload(),scope)[0]['property_id'])
 def test_same_source_task_in_different_buckets_is_preserved(self):
  source=payload();source['tasks']['In Progress']=[dict(source['tasks']['Open'][0],status='in_progress')]
  rows=projection_rows(source,bindings())
  self.assertEqual({r['bucket'] for r in rows},{'Open','In Progress'})
  self.assertEqual(len({r['task_key'] for r in rows}),2)
 def test_missing_and_null_source_property_are_distinct(self):
  source=payload();del source['tasks']['Open'][0]['property_id']
  null_scope=[dict(bindings()[0],source_property_id=None)]
  with self.assertRaises(ValueError):projection_rows(source,null_scope)
  missing_scope=[dict(null_scope[0],source_property_present=False)]
  self.assertEqual(len(projection_rows(source,missing_scope)),1)
@unittest.skipUnless(os.getenv('DATABASE_URL'),'isolated database required')
class ProjectionDbTests(unittest.TestCase):
 def setUp(self):
  from backend.storage.schema import apply_migrations
  self.conn=migrate.staging_connect();apply_migrations(self.conn);self.records=records();self.key=b'h'*32;migrate.import_records(self.conn,self.records,self.key);self.conn.execute("INSERT INTO properties(id,name) VALUES ('fixture-property','Fixture property')");self.conn.commit()
 def tearDown(self):
  self.conn.rollback();self.conn.execute('DELETE FROM padsplit_task_observations');self.conn.execute("DELETE FROM properties WHERE id='fixture-property'");self.conn.execute("DELETE FROM source_documents WHERE source_path LIKE 'private_dashboard/%' OR source_path LIKE 'private_dashboard_runs/%'");self.conn.commit();self.conn.close()
 def test_repeatable_transaction_and_property_name_reconciliation(self):
  from backend.storage.project_snapshot_tasks import project
  self.assertEqual(project(self.conn,self.records,self.key,bindings()),1);self.assertEqual(self.conn.execute('SELECT count(*) FROM padsplit_task_observations').fetchone()[0],0);self.conn.rollback()
  self.assertEqual(project(self.conn,self.records,self.key,bindings(),apply=True),1);self.assertEqual(project(self.conn,self.records,self.key,bindings(),apply=True),1)
  self.conn.execute("UPDATE properties SET name='Changed'");self.conn.commit()
  with self.assertRaises(ValueError):project(self.conn,self.records,self.key,bindings(),apply=True)
