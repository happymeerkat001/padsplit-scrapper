"""Only fictional thermostat observations and explicit property mappings."""
import copy, os, tempfile, unittest
from pathlib import Path
from backend.storage.project_climate import projection_rows, private_json

STAMP='2026-01-01T00:00:00Z'
def snapshot():
    return {'scraped_at':STAMP,'locations':[{'id':12,'name':'Fictional property','devices':[{'id':34,'name':'Bedroom','temp':0,'heat_setpoint':None,'cool_setpoint':72,'humidity':None,'outdoor_temp':None,'mode':'cool','equipment_status':None}]}]}
def bindings():
    return [{'source_location_id':12,'target_property_id':'property-example','expected_property_name':'Fictional property'}]

class ClimateShapeTests(unittest.TestCase):
    def test_reviewed_scope_preserves_null_and_zero(self):
        row=projection_rows(snapshot(),bindings())[0]
        self.assertEqual(row['property_id'],'property-example');self.assertEqual(row['data']['temp'],0);self.assertIsNone(row['data']['heat_setpoint']);self.assertRegex(row['source_fingerprint'],r'^[a-f0-9]{64}$')
    def test_unmapped_duplicate_or_unknown_content_fails(self):
        for mapping in ([],bindings()+bindings(),[dict(bindings()[0],source_location_id='12')]):
            with self.subTest(mapping=mapping),self.assertRaises(ValueError):projection_rows(snapshot(),mapping)
        for mutation in (lambda p:p['locations'][0]['devices'][0].update(temp=float('nan')),
                         lambda p:p['locations'][0]['devices'][0].update(secret='not allowed'),
                         lambda p:p['locations'][0]['devices'][0].pop('heat_setpoint'),
                         lambda p:p['locations'][0]['devices'].append(copy.deepcopy(p['locations'][0]['devices'][0]))):
            payload=snapshot();mutation(payload)
            with self.assertRaises(ValueError):projection_rows(payload,bindings())

    def test_private_input_permissions_and_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'snapshot.json';path.write_text('{"a":1}')
            path.chmod(0o644)
            with self.assertRaises(ValueError):private_json(path)
            path.chmod(0o600);self.assertEqual(private_json(path),{'a':1})
            path.write_text('{"a":1,"a":2}')
            with self.assertRaises(ValueError):private_json(path)

@unittest.skipUnless(os.getenv('DATABASE_URL'),'isolated staging database required')
class ClimateDatabaseTests(unittest.TestCase):
    def test_transactional_apply_repeat_and_property_conflict(self):
        from backend.storage import migrate
        from backend.storage.schema import apply_migrations
        from backend.storage.project_climate import project
        with migrate.staging_connect() as conn:
            apply_migrations(conn)
            conn.execute("INSERT INTO properties(id,name) VALUES ('property-example','Fictional property'), ('property-two','Other fictional property')");conn.commit()
            try:
                self.assertEqual(project(conn,snapshot(),bindings()),1)
                self.assertEqual(conn.execute('SELECT count(*) FROM climate_observations').fetchone()[0],0);conn.rollback()
                self.assertEqual(project(conn,snapshot(),bindings(),apply=True),1)
                self.assertEqual(project(conn,snapshot(),bindings(),apply=True),1)
                changed=snapshot();changed['locations'][0]['devices'][0]['temp']=20
                with self.assertRaises(ValueError):project(conn,changed,bindings(),apply=True)
                later=snapshot();later['scraped_at']='2026-01-02T00:00:00Z'
                reassigned=[dict(bindings()[0],target_property_id='property-two',expected_property_name='Other fictional property')]
                with self.assertRaises(ValueError):project(conn,later,reassigned,apply=True)
            finally:
                conn.rollback();conn.execute('DELETE FROM climate_observations');conn.execute("DELETE FROM properties WHERE id IN ('property-example','property-two')");conn.commit()
