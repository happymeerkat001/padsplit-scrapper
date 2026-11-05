import os
import unittest
from backend.storage import migrate
from backend.storage.schema import apply_migrations
from backend.storage.project_shared import project,rows_for

KEY=b'z'*32

def record(path,fields):return {'path':path,'update_time':'2026-01-01T00:00:00+00:00','data':migrate.encode_value(fields)}

def records():return [
 record('notes/dashboard',{'text':'Synthetic note'}),
 record('vendors/fixture',{'name':'Example vendor','contact':None,'location':'Example'}),
 record('templates/shared',{'n0':'Welcome','t0':'Example text','n3':'Follow up','t3':''}),
]

class SharedShapeTests(unittest.TestCase):
    def test_missing_null_and_unknown_fields_do_not_get_lost(self):
        self.assertEqual(rows_for(record('vendors/fixture',{'name':'Example','contact':None}))[0][3],{'contact':None})
        self.assertEqual(rows_for(record('vendors/fixture',{'name':'Example'}))[0][3],{})
        for source in [record('notes/dashboard',{'text':None}),record('vendors/fixture',{'name':'Example','extra':'unknown'}),record('templates/shared',{'n0':'unpaired'}),record('templates/shared',{'n9':'unsupported','t9':'unsupported'}),record('templates/other',{})]:
            with self.assertRaises(ValueError):rows_for(source)

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated PostgreSQL test database not supplied')
class SharedProjectionTests(unittest.TestCase):
    def setUp(self):
        self.conn=migrate.staging_connect();apply_migrations(self.conn);migrate.import_records(self.conn,records(),KEY)
    def tearDown(self):
        self.conn.rollback()
        for table in ('notes','vendors','templates'):self.conn.execute(f"DELETE FROM {table} WHERE source_path IN ('notes/dashboard','vendors/fixture','templates/shared')")
        self.conn.execute("DELETE FROM source_documents WHERE source_path IN ('notes/dashboard','vendors/fixture','templates/shared')")
        self.conn.commit();self.conn.close()
    def test_repeatable_projection_and_conflicts(self):
        self.assertEqual(project(self.conn,records(),KEY),4)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM notes').fetchone()[0],0);self.conn.rollback()
        self.assertEqual(project(self.conn,records(),KEY,apply=True),4)
        self.assertEqual(project(self.conn,records(),KEY,apply=True),4)
        self.assertEqual(self.conn.execute('SELECT text FROM notes').fetchone()[0],'Synthetic note')
        self.assertEqual(self.conn.execute('SELECT data FROM vendors').fetchone()[0],{'contact':None,'location':'Example'})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM templates').fetchone()[0],2)
        self.conn.execute("UPDATE notes SET text='changed' WHERE id='dashboard'");self.conn.commit()
        with self.assertRaises(ValueError):project(self.conn,records(),KEY,apply=True)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT text FROM notes').fetchone()[0],'changed')
