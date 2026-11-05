import datetime as dt
import os
import unittest
from backend.storage import migrate
from backend.storage.schema import apply_migrations
from backend.storage.project_manual_tasks import project

KEY=b'x'*32

def records():
    return [{'path':'manual_tasks/fictional-id','update_time':'2026-01-02T00:00:00+00:00',
      'data':migrate.encode_value({'who':'Fictional operator','source':'Internal','notes':'Fixture task detail','created':dt.datetime(2026,1,1,0,0,0,123456,tzinfo=dt.timezone.utc)})}]

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated PostgreSQL test database not supplied')
class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.conn=migrate.staging_connect();apply_migrations(self.conn);migrate.import_records(self.conn,records(),KEY)
        self.conn.execute("DELETE FROM tasks WHERE source_path='manual_tasks/fictional-id'");self.conn.commit()
    def tearDown(self):
        self.conn.rollback()
        self.conn.execute("DELETE FROM tasks WHERE source_path='manual_tasks/fictional-id'")
        self.conn.execute("DELETE FROM source_documents WHERE source_path='manual_tasks/fictional-id'")
        self.conn.commit();self.conn.close()
    def test_manual_task_projection_dry_run_repeat_and_conflict(self):
        self.assertEqual(project(self.conn,records(),KEY),1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],0);self.conn.rollback()
        self.assertEqual(project(self.conn,records(),KEY,apply=True),1)
        row=self.conn.execute('SELECT property_id,title,assigned_label,source_type,source_created_nanos FROM tasks').fetchone()
        self.assertEqual(row,(None,'Fixture task detail','Fictional operator','Internal',123456000));self.conn.rollback()
        self.assertEqual(project(self.conn,records(),KEY,apply=True),1)
        self.conn.execute("UPDATE tasks SET title='changed' WHERE source_path='manual_tasks/fictional-id'");self.conn.commit()
        with self.assertRaises(ValueError):project(self.conn,records(),KEY,apply=True)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT title FROM tasks').fetchone()[0],'changed')

    def test_missing_creation_timestamp_fails_without_target_write(self):
        bad=records();del bad[0]['data']['value']['created']
        with self.assertRaises(ValueError):
            from backend.storage.project_manual_tasks import task_row
            task_row(bad[0])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
