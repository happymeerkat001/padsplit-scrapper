import datetime as dt
import base64
import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from cryptography.exceptions import InvalidTag
from backend.storage import migrate
from backend.storage.schema import apply_migrations

KEY=b'k'*32
SECRET='fixture-secret-do-not-publish'

def sample():
    return [
        {'path':'notes/fixture','update_time':'2026-01-01T00:00:00+00:00','data':migrate.encode_value({'text':SECRET,'null':None,'bytes':b'fake','price':0.1,'@type':'ordinary'})},
        {'path':'notes/fixture/children/item','update_time':'2026-01-02T00:00:00+00:00','data':migrate.encode_value({'time':dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)})},
    ]

class BackupTests(unittest.TestCase):
    def test_compare_backups_counts_added_changed_and_deleted_without_paths(self):
        initial=sample()
        changed=sample()[0].copy();changed['data']=migrate.encode_value({'text':'new fixture'})
        candidate=[changed,{'path':'notes/new','update_time':None,'data':migrate.encode_value({'text':'fixture'})}]
        self.assertEqual(migrate.compare_records(initial,candidate),{'added':1,'changed':1,'deleted':1,'unchanged':0})
        self.assertEqual(migrate.compare_records(initial,initial),{'added':0,'changed':0,'deleted':0,'unchanged':2})
        with self.assertRaises(ValueError):migrate.compare_records(initial,initial+initial[:1])

    def test_compare_command_authenticates_both_files_and_redacts_paths(self):
        with tempfile.TemporaryDirectory() as d:
            old=Path(d)/'old';new=Path(d)/'new';new_key=b'n'*32
            migrate.encrypted_backup(sample(),old,KEY);migrate.encrypted_backup(sample()[:1],new,new_key)
            env={'PADSPLIT_MIGRATION_KEY':base64.b64encode(KEY).decode(),'PADSPLIT_COMPARE_KEY':base64.b64encode(new_key).decode()}
            output=io.StringIO()
            with patch.dict(os.environ,env,clear=True),contextlib.redirect_stdout(output):
                self.assertEqual(migrate.main(['compare','--baseline',str(old),'--candidate',str(new)]),0)
            self.assertIn('deleted=1',output.getvalue())
            self.assertNotIn('notes/fixture',output.getvalue())
            new.write_bytes(new.read_bytes()[:-10])
            with patch.dict(os.environ,env,clear=True),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(migrate.main(['compare','--baseline',str(old),'--candidate',str(new)]),1)

    def test_staging_connection_rejects_remote_hostaddr_before_connect(self):
        dsn='host=127.0.0.1 hostaddr=192.0.2.5 dbname=padsplit_stage_fixture'
        with patch.dict(os.environ,{'DATABASE_URL':dsn}), patch('psycopg.connect') as connect:
            with self.assertRaises(ValueError):migrate.staging_connect()
            connect.assert_not_called()

    def test_staging_connection_forces_loopback_despite_environment_hostaddr(self):
        dsn='host=localhost dbname=padsplit_stage_fixture'
        with patch.dict(os.environ,{'DATABASE_URL':dsn,'PGHOSTADDR':'192.0.2.5'}), patch('psycopg.connect') as connect:
            migrate.staging_connect()
            self.assertEqual(connect.call_args.kwargs.get('hostaddr'),'127.0.0.1')

    def test_staging_connection_rejects_multiple_hosts_or_addresses(self):
        for dsn in ('host=127.0.0.1,192.0.2.5 dbname=padsplit_stage_fixture',
                    'host=127.0.0.1 hostaddr=127.0.0.1,192.0.2.5 dbname=padsplit_stage_fixture'):
            with self.subTest(dsn=dsn),patch.dict(os.environ,{'DATABASE_URL':dsn}),patch('psycopg.connect') as connect:
                with self.assertRaises(ValueError):migrate.staging_connect()
                connect.assert_not_called()

    def test_staging_connection_forces_hostaddr_over_service_and_pgservice(self):
        dsn='service=fictional host=127.0.0.1 dbname=padsplit_stage_fixture'
        with patch.dict(os.environ,{'DATABASE_URL':dsn,'PGSERVICE':'fictional','PGSERVICEFILE':'/fictional/pg_service.conf'}),patch('psycopg.connect') as connect:
            migrate.staging_connect()
            self.assertEqual(connect.call_args.kwargs.get('hostaddr'),'127.0.0.1')

    def test_encrypted_authenticated_backup_and_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'backup'; self.assertEqual(migrate.encrypted_backup(sample(),p,KEY),2)
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            self.assertNotIn(SECRET.encode(),p.read_bytes())
            self.assertEqual(migrate.read_backup(p,KEY),sample())
            with self.assertRaises(ValueError): migrate.read_backup(p,b'x'*32)
            lines=p.read_bytes().splitlines(keepends=True); p.write_bytes(b''.join(lines[:-1]))
            with self.assertRaises(ValueError): migrate.read_backup(p,KEY)
            with self.assertRaises(FileExistsError): migrate.encrypted_backup(sample(),p,KEY)

    def test_unknown_values_fail_closed_and_map_tags_do_not_collide(self):
        with self.assertRaises(ValueError):migrate.encode_value(object())
        self.assertEqual(migrate.encode_value({'@type':'test'})['@type'],'map')
        with self.assertRaises(ValueError):migrate.encode_value(float('nan'))
        with self.assertRaises(ValueError):migrate.source_parts({'path':'incomplete','data':{}})

    def test_timestamp_microseconds_when_library_exposes_zero_nanoseconds(self):
        class FirebaseTimestamp(dt.datetime):
            @property
            def nanosecond(self):return 0
        stamp=FirebaseTimestamp(2026,1,1,0,0,0,123456,tzinfo=dt.timezone.utc)
        self.assertEqual(migrate.encode_value(stamp)['nanosecond'],123456000)

    def test_orphan_subcollections_are_visited(self):
        class Snap:
            def __init__(self,exists):self.exists=exists;self.update_time=None
            def to_dict(self):return {'test':True}
        class Ref:
            def __init__(self,path,exists,children=()):self.path=path;self.exists=exists;self.children=children
            def get(self):return Snap(self.exists)
            def collections(self):return self.children
        class Col:
            def __init__(self,refs):self.refs=refs
            def list_documents(self):return self.refs
        child=Ref('top/deleted/nested/child',True)
        root=Col([Ref('top/deleted',False,[Col([child])])])
        class DB:
            def collections(self):return [root]
        self.assertEqual([r['path'] for r in migrate.walk_firestore(DB())],['top/deleted/nested/child'])

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated PostgreSQL test database not supplied')
class PostgresTests(unittest.TestCase):
    def setUp(self):
        self.conn=migrate.staging_connect(); apply_migrations(self.conn)
    def tearDown(self):
        self.conn.rollback()
        self.conn.execute("DELETE FROM source_documents WHERE source_path IN ('notes/fixture','notes/fixture/children/item')")
        self.conn.commit();self.conn.close()
    def test_import_idempotency_conflict_and_encrypted_values(self):
        rows=sample(); self.assertEqual(migrate.import_records(self.conn,rows,KEY,dry_run=True),2)
        self.assertEqual(self.conn.execute('select count(*) from source_documents').fetchone()[0],0);self.conn.rollback()
        self.assertEqual(migrate.import_records(self.conn,rows,KEY),2)
        self.assertEqual(migrate.import_records(self.conn,rows,KEY),2)
        self.assertEqual(migrate.verify_records(self.conn,rows,KEY),2)
        ct=self.conn.execute('select payload_ciphertext from source_documents limit 1').fetchone()[0]
        self.assertNotIn(SECRET.encode(),bytes(ct));self.conn.rollback()
        changed=sample();changed[0]['data']=migrate.encode_value({'text':'changed'})
        with self.assertRaises(ValueError):migrate.import_records(self.conn,changed,KEY)
        self.conn.rollback()
        self.assertEqual(migrate.verify_records(self.conn,rows,KEY),2)

    def test_verify_detects_corrupted_source_metadata(self):
        rows=sample();migrate.import_records(self.conn,rows,KEY)
        self.conn.execute("UPDATE source_documents SET collection_path='wrong' WHERE source_path='notes/fixture'")
        self.conn.commit()
        with self.assertRaises(ValueError):migrate.verify_records(self.conn,rows,KEY)

    def test_service_and_environment_cannot_override_actual_loopback_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=Path(tmp)/'services.conf'
            service.write_text('[fixture]\nhost=192.0.2.5\nhostaddr=192.0.2.5\ndbname=unsafe\n')
            with patch.dict(os.environ,{'PGSERVICE':'fixture','PGSERVICEFILE':str(service),'PGHOSTADDR':'192.0.2.5'}):
                with migrate.staging_connect() as conn:
                    database,address=conn.execute('select current_database(),inet_server_addr()::text').fetchone()
                    self.assertTrue(database.startswith('padsplit_stage_'))
                    self.assertIn(address,('127.0.0.1/32','::1/128','127.0.0.1','::1'))

    def test_access_code_history_is_immutable_and_keys_are_encrypted(self):
        self.conn.execute("insert into properties(id,name) values ('fixture-house','Fictional')")
        self.conn.execute("insert into code_history(id,property_id,ciphertext,nonce,key_version,created_at,source_version) values ('fixture-version','fixture-house',%s,%s,'v1',now(),1)",(b'ciphertext',b'x'*12))
        with self.assertRaises(Exception):
            with self.conn.transaction():self.conn.execute("delete from code_history where id='fixture-version'")
        self.assertEqual(self.conn.execute("select count(*) from code_history where id='fixture-version'").fetchone()[0],1)

class AccessCodeCryptoTests(unittest.TestCase):
    def test_context_bound_encrypt_and_wrong_context(self):
        from backend.storage.secrets import encrypt_codes,decrypt_codes
        nonce,cipher=encrypt_codes({'door':'fictional-secret'},property_id='fictional',version=1,key_version='v1',key=KEY)
        self.assertNotIn(b'fictional-secret',cipher)
        self.assertEqual(decrypt_codes(cipher,nonce,property_id='fictional',version=1,key_version='v1',key=KEY),{'door':'fictional-secret'})
        with self.assertRaises(InvalidTag):decrypt_codes(cipher,nonce,property_id='other',version=1,key_version='v1',key=KEY)
        with self.assertRaises(InvalidTag):decrypt_codes(cipher,nonce,property_id='fictional',version=2,key_version='v1',key=KEY)

    def test_migration_and_code_keys_must_differ(self):
        import base64
        from backend.storage.secrets import load_key
        b64=base64.b64encode(KEY).decode()
        with self.assertRaises(ValueError):load_key({'PADSPLIT_CODES_KEY':b64,'PADSPLIT_MIGRATION_KEY':b64})
        self.assertEqual(load_key({'PADSPLIT_CODES_KEY':b64}),KEY)
