import copy
import datetime as dt
import os
import unittest
from padsplit_scraper.codes_history import content_hash
from backend.storage import migrate
from backend.storage.project_code_history import history_rows
from backend.storage.tests.test_codes_projection import records as base_records, record
from backend.storage.project_codes import rows_for

STAMP=dt.datetime(2026,1,1,0,0,0,123456,tzinfo=dt.timezone.utc)
def histories():
    values={'door':'fictional-old-value','legacy':'fictional-old-extra'}
    version={'fields':values,'contentHash':content_hash(values),'createdAt':STAMP,'expireAt':STAMP+dt.timedelta(days=90),'source':'catchup'}
    return base_records()+[record('property_codes/fixture_home/code_versions/historical-id',version)]

class HistoryShapeTests(unittest.TestCase):
    def test_valid_history_is_recognized_without_leaking_source_values(self):
        rows=history_rows(histories());self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['source_path'],'property_codes/fixture_home/code_versions/historical-id')
        self.assertEqual(rows[0]['values']['door'],'fictional-old-value')
        self.assertEqual(len(rows_for(histories())),1)
    def test_invalid_content_timestamp_parent_and_descendants_fail_closed(self):
        invalid=histories();invalid[-1]['data']['value']['contentHash']='0'*64
        with self.assertRaises(ValueError):history_rows(invalid)
        invalid=histories();invalid[-1]['data']['value']['createdAt']='2026-01-01'
        with self.assertRaises(ValueError):history_rows(invalid)
        invalid=histories();invalid[-1]['data']['value']['extra']='unknown'
        with self.assertRaises(ValueError):history_rows(invalid)
        invalid=histories();invalid[-1]['path']='property_codes/missing_home/code_versions/historical-id'
        with self.assertRaises(ValueError):history_rows(invalid)
        invalid=histories();invalid[-1]['path'] += '/nested/id'
        with self.assertRaises(ValueError):history_rows(invalid)

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated PostgreSQL database required')
class HistoryPostgresTests(unittest.TestCase):
    def test_import_repeat_verify_and_owner_restore(self):
        import base64
        import uuid
        from unittest.mock import patch
        import psycopg
        from psycopg import sql
        from fastapi.testclient import TestClient
        from backend.api.app import create_app
        from backend.api.repository import Repository
        from backend.storage.schema import apply_migrations
        from backend.storage.project_codes import project as project_current
        from backend.storage.project_code_history import project
        from backend.storage.secrets import decrypt_codes
        backup_key=b'b'*32;codes_key=b'c'*32;version='fixture-v1';schema='history_test_'+uuid.uuid4().hex[:12];role='history_reader_'+uuid.uuid4().hex[:10]
        with migrate.staging_connect() as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)));admin.commit()
        scoped=psycopg.conninfo.make_conninfo(os.environ['DATABASE_URL'],options='-c search_path='+schema)
        try:
            with psycopg.connect(scoped) as conn:
                apply_migrations(conn);migrate.import_records(conn,histories(),backup_key)
                project_current(conn,histories(),backup_key,codes_key,version,apply=True)
                self.assertEqual(project(conn,histories(),backup_key,codes_key,version),1)
                self.assertEqual(conn.execute('SELECT count(*) FROM code_history').fetchone()[0],0);conn.rollback()
                self.assertEqual(project(conn,histories(),backup_key,codes_key,version,apply=True),1)
                self.assertEqual(project(conn,histories(),backup_key,codes_key,version,apply=True),1)
                self.assertEqual(project(conn,histories(),backup_key,codes_key,version,verify_only=True),1)
                row=conn.execute('SELECT id,ciphertext,nonce,source_created_nanos,source_path,source_version FROM code_history').fetchone()
                self.assertEqual(row[3],123456000);self.assertEqual(row[4],'property_codes/fixture_home/code_versions/historical-id');self.assertEqual(row[5],1)
                self.assertNotIn(b'fictional-old-value',bytes(row[1]))
                self.assertEqual(decrypt_codes(bytes(row[1]),bytes(row[2]),property_id='fixture_home',version=1,key_version=version,key=codes_key)['door'],'fictional-old-value')
                history_id=row[0]
                conn.execute("INSERT INTO app_users(firebase_uid) VALUES ('fixture-owner'),('fixture-viewer')")
                conn.execute("INSERT INTO user_roles(firebase_uid,role) VALUES ('fixture-owner','owner'),('fixture-viewer','viewer')")
                conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
                for statement in (
                    'GRANT SELECT (firebase_uid,active), UPDATE (auth_lock_guard) ON app_users TO {}',
                    'GRANT SELECT (firebase_uid,role), UPDATE (auth_lock_guard) ON user_roles TO {}',
                    'GRANT SELECT (firebase_uid,property_id), UPDATE (auth_lock_guard) ON property_access TO {}',
                    'GRANT SELECT (id,property_id,ciphertext,nonce,key_version,source_version) ON code_history TO {}',
                    'GRANT INSERT (actor_uid,action,target_type,target_id,redacted_metadata) ON audit_events TO {}',
                    'GRANT USAGE ON SEQUENCE audit_events_id_seq TO {}'):
                    conn.execute(sql.SQL(statement).format(sql.Identifier(role)))
                conn.commit()
            client=TestClient(create_app(repository=Repository(scoped),verify_token=lambda token:token,allowed_origins=['https://example.invalid']))
            with patch.dict(os.environ,{'PADSPLIT_CODES_KEY':base64.b64encode(codes_key).decode(),'PADSPLIT_CODES_KEY_VERSION':version}):
                owner={'Authorization':'Bearer fixture-owner'};viewer={'Authorization':'Bearer fixture-viewer'}
                base='/v1/properties/fixture_home/codes'
                listing=client.get(base+'/history',headers=owner)
                self.assertEqual(listing.status_code,200);self.assertEqual(len(listing.json()['items']),1)
                self.assertNotIn('fictional-old-value',listing.text)
                self.assertEqual(client.get(base+'/history',headers=viewer).status_code,403)
                reader_dsn=psycopg.conninfo.make_conninfo(os.environ['DATABASE_URL'],options=f'-c search_path={schema} -c role={role}')
                reader=TestClient(create_app(repository=Repository(reader_dsn),verify_token=lambda token:token,allowed_origins=['https://example.invalid']))
                revealed=reader.post(base+'/history/'+history_id+'/reveal',headers=owner)
                self.assertEqual(revealed.status_code,200);self.assertEqual(revealed.json()['fields']['door'],'fictional-old-value')
                self.assertEqual(reader.post(base+'/history/'+history_id+'/reveal',headers=viewer).status_code,403)
                with psycopg.connect(reader_dsn) as restricted:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):restricted.execute("UPDATE code_history SET source_version=2")
                with psycopg.connect(scoped) as inspection:
                    audit=inspection.execute("SELECT action,redacted_metadata::text FROM audit_events WHERE action='codes.history_reveal'").fetchall()
                    self.assertEqual(len(audit),1);self.assertNotIn('fictional-old-value',repr(audit))
                result=client.post(base+'/history/'+history_id+'/restore',headers=owner,json={'expected_version':1})
                self.assertEqual(result.status_code,200);self.assertNotIn('fictional-old-value',result.text)
                self.assertEqual(client.post(base+'/reveal',headers=owner).json()['fields']['door'],'fictional-old-value')
            with psycopg.connect(scoped) as conn:
                with self.assertRaises(Exception):project(conn,histories(),backup_key,codes_key,version,apply=True)
                conn.rollback()
        finally:
            with migrate.staging_connect() as admin:
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
                admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)));admin.commit()
