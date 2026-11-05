import os
import unittest
from backend.storage.migrate import encode_value, import_records, staging_connect
from backend.storage.project_stats import project, stats_row
from backend.storage.schema import apply_migrations

class StatsProjectionTests(unittest.TestCase):
    def record(self, text='{"rent":123456789012345678.123456789,"missing":null}', updated='2026-01-01T00:00:00Z'):
        return {'path':'stats/latest','update_time':'2026-01-01T00:00:00Z','data':encode_value({'json':text,'updated_at':updated})}
    def test_preserves_raw_number_text_and_source_id(self):
        row=stats_row(self.record())
        self.assertEqual(row[0],'latest'); self.assertIn('123456789012345678.123456789',row[1]); self.assertEqual(row[3],'stats/latest')
    def test_fails_closed_for_duplicate_keys_nonfinite_and_naive_time(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}'):
            with self.assertRaises(ValueError):stats_row(self.record(text))
        with self.assertRaises(ValueError):stats_row(self.record(updated='2026-01-01T00:00:00'))
    def test_unknown_fields_stop_projection(self):
        rec=self.record();rec['data']['value']['unexpected']='x'
        with self.assertRaises(ValueError):stats_row(rec)

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated PostgreSQL test database not supplied')
class StatsPostgresTests(unittest.TestCase):
    def test_precision_idempotency_and_conflict(self):
        key=b's'*32; conn=staging_connect()
        try:
            apply_migrations(conn)
            record=StatsProjectionTests().record();records=[record]
            import_records(conn,records,key)
            self.assertEqual(project(conn,records,key),1)
            self.assertEqual(conn.execute('SELECT count(*) FROM statistics').fetchone()[0],0);conn.rollback()
            self.assertEqual(project(conn,records,key,apply=True),1)
            self.assertEqual(project(conn,records,key,apply=True),1)
            self.assertEqual(conn.execute("SELECT data->>'rent' FROM statistics WHERE id='latest'").fetchone()[0], '123456789012345678.123456789')
            conn.rollback()
            conn.execute("UPDATE statistics SET data='{}'::jsonb WHERE id='latest'");conn.commit()
            with self.assertRaises(ValueError):project(conn,records,key,apply=True)
        finally:
            conn.rollback();conn.execute("DELETE FROM statistics WHERE id='latest'");conn.execute("DELETE FROM source_documents WHERE source_path='stats/latest'");conn.commit();conn.close()
