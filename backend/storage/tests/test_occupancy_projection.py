"""Fictional snapshots only; all property links are operator-reviewed."""
import copy, datetime as dt, json, os, tempfile, unittest
from pathlib import Path
from backend.storage import migrate
from backend.storage.project_occupancy import projection_rows, load_bindings

STAMP='2026-01-01T00:00:00Z'
def sample():
 return {'scraped_at':STAMP,'rooms':[{'property_id':123,'address':'Example Street','room_number':1,'occupant_present':True,'listed_move_out':None,'next_move_in':None,'open_turn_ticket_ids':[],'open_eviction_ticket_ids':[],'open_hold_ticket_ids':[],'move_out_photos':0,'vacant':False,'turned':False,'rent_ready':False,'days_vacant':0,'seo_eligible':False}]}
def mapping():
 return [{'property_id':123,'address':'Example Street','room_number':1,'target_property_id':'property-example','expected_property_name':'Fictional property','room_label':'1'}]
class OccupancyShapeTests(unittest.TestCase):
 def test_explicit_exact_mapping_preserves_observation_and_zero(self):
  rows=projection_rows(sample(),mapping());self.assertEqual(len(rows),1)
  self.assertEqual(rows[0]['property_id'],'property-example');self.assertEqual(rows[0]['payload']['days_vacant'],0);self.assertIs(rows[0]['occupant_present'],True)
 def test_no_scope_inference_or_silent_missing_rows(self):
  for bindings in ([],mapping()+mapping(),mapping()+[dict(mapping()[0],room_number=2)]):
   with self.assertRaises(ValueError):projection_rows(sample(),bindings)
  bad=sample();bad['rooms'][0]['address']='Different';
  with self.assertRaises(ValueError):projection_rows(bad,mapping())
 def test_invalid_fields_or_duplicate_rooms_fail(self):
  for mutate in (lambda x:x['rooms'][0].update(vacant='false'),lambda x:x['rooms'][0].update(days_vacant=-1),lambda x:x['rooms'][0].update(surprise='hidden'),lambda x:x['rooms'].append(copy.deepcopy(x['rooms'][0]))):
   data=sample();mutate(data)
   with self.assertRaises(ValueError):projection_rows(data,mapping())
 def test_mapping_file_requires_private_directory_and_permissions(self):
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'map.json';path.write_text(json.dumps({'version':1,'bindings':mapping()}));path.chmod(0o600)
   self.assertEqual(load_bindings(path),mapping());path.chmod(0o644)
   with self.assertRaises(ValueError):load_bindings(path)

@unittest.skipUnless(os.getenv('DATABASE_URL'),'isolated PostgreSQL test database not supplied')
class OccupancyDatabaseTests(unittest.TestCase):
 def setUp(self):
  from backend.storage.schema import apply_migrations
  from backend.storage.tests.test_inbox_projection import record
  self.conn=migrate.staging_connect();apply_migrations(self.conn);self.key=b'q'*32;payload={'scraped_at':STAMP,'tasks':{},'messages':[],'occupancy':sample()}; text=json.dumps(payload,ensure_ascii=True,separators=(',',':'));run='b'*32
  self.records=[record('private_dashboard/current',{'schema_version':1,'run_id':run,'chunk_count':1,'scraped_at':STAMP}),record('private_dashboard_runs/'+run+'/chunks/0',{'index':0,'data':text})]
  migrate.import_records(self.conn,self.records,self.key);self.conn.execute("INSERT INTO properties(id,name) VALUES ('property-example','Fictional property')");self.conn.commit()
 def tearDown(self):
  self.conn.rollback();self.conn.execute("DELETE FROM occupancy_observations");self.conn.execute("DELETE FROM rooms");self.conn.execute("DELETE FROM properties WHERE id='property-example'");self.conn.execute("DELETE FROM source_documents WHERE source_path LIKE 'private_dashboard/%' OR source_path LIKE 'private_dashboard_runs/%'");self.conn.commit();self.conn.close()
 def test_dry_run_apply_repeat_and_changed_target_conflict(self):
  from backend.storage.project_occupancy import project
  self.assertEqual(project(self.conn,self.records,self.key,mapping()),1);self.assertEqual(self.conn.execute('SELECT count(*) FROM rooms').fetchone()[0],0);self.conn.rollback()
  self.assertEqual(project(self.conn,self.records,self.key,mapping(),apply=True),1);self.assertEqual(project(self.conn,self.records,self.key,mapping(),apply=True),1)
  self.assertEqual(self.conn.execute('SELECT occupant_present, rent_ready, source_payload->>\'days_vacant\' FROM occupancy_observations').fetchone(),(True,False,'0'));self.conn.rollback()
  self.conn.execute("UPDATE properties SET name='Different' WHERE id='property-example'");self.conn.commit()
  with self.assertRaises(ValueError):project(self.conn,self.records,self.key,mapping(),apply=True)
