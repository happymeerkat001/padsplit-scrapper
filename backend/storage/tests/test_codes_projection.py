import copy
import datetime as dt
import os
import unittest
from cryptography.exceptions import InvalidTag
from backend.storage import migrate
from backend.storage.schema import apply_migrations
from backend.storage.project_codes import project,rows_for
from backend.storage.secrets import decrypt_codes,decrypt_code_schema,encrypt_code_schema,decrypt_codes as decrypt_code_map

BACKUP_KEY=b'b'*32
CODES_KEY=b'c'*32
SECRET='fictional-sensitive-value'


def record(path,data):return {'path':path,'update_time':'2026-01-02T00:00:00+00:00','data':migrate.encode_value(data)}

def records():
    return [record('private_configuration/codes_schema',{'properties':[{'slug':'fixture_home','address':'Fictional Home','sections':[{'label':'Front door','fields':[{'key':'door','label':'Door','value':''}]}]}]}),
            record('property_codes/fixture_home',{'door':SECRET,'legacy_shared_extra':'fictional-legacy-value','updatedAt':dt.datetime(2026,1,1,0,0,0,123456,tzinfo=dt.timezone.utc)})]

class CodesShapeTests(unittest.TestCase):
    def test_rejects_mismatches_histories_and_unsupported_fields(self):
        bad=records();bad[1]['path']='property_codes/another_home'
        with self.assertRaises(ValueError):rows_for(bad)
        bad=records();bad.append(record('property_codes/fixture_home/code_versions/v1',{}))
        with self.assertRaises(ValueError):rows_for(bad)
        bad=records();bad[1]=record('property_codes/fixture_home',{'door':{'unexpected':'map'},'updatedAt':dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)})
        with self.assertRaises(ValueError):rows_for(bad)
        bad=records();bad[1]=record('property_codes/fixture_home',{'door':'value','updatedAt':'not-a-timestamp'})
        with self.assertRaises(ValueError):rows_for(bad)
        bad=records();bad[1]['data']['value']['updatedAt']['nanosecond']=0
        with self.assertRaises(ValueError):rows_for(bad)
        bad=records();del bad[0]['data']['value']['properties'][0]['value']['sections'][0]['value']['fields'][0]['value']['value']
        # Missing empty default is supported by the source validator; nonempty is not.
        bad[0]['data']['value']['properties'][0]['value']['sections'][0]['value']['fields'][0]['value']['value']='unsafe'
        with self.assertRaises(ValueError):rows_for(bad)

    def test_schema_ciphertext_context_is_separate_from_codes(self):
        schema={'slug':'fictional','address':'Fictional Home'}
        nonce,cipher=encrypt_code_schema(schema,property_id='fictional',version=1,key_version='v1',key=CODES_KEY)
        self.assertNotIn(b'Fictional Home',cipher)
        self.assertEqual(decrypt_code_schema(cipher,nonce,property_id='fictional',version=1,key_version='v1',key=CODES_KEY),schema)
        with self.assertRaises(InvalidTag):decrypt_code_schema(cipher,nonce,property_id='other',version=1,key_version='v1',key=CODES_KEY)
        with self.assertRaises(InvalidTag):decrypt_code_map(cipher,nonce,property_id='fictional',version=1,key_version='v1',key=CODES_KEY)

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated staging PostgreSQL not supplied')
class CodesProjectionTests(unittest.TestCase):
    def setUp(self):
        self.conn=migrate.staging_connect();apply_migrations(self.conn);migrate.import_records(self.conn,records(),BACKUP_KEY)
    def tearDown(self):
        self.conn.rollback();self.conn.execute("DELETE FROM access_code_schemas WHERE property_id='fixture_home'")
        self.conn.execute("DELETE FROM access_codes WHERE property_id='fixture_home'")
        self.conn.execute("DELETE FROM properties WHERE id='fixture_home'")
        self.conn.execute("DELETE FROM source_documents WHERE source_path IN ('private_configuration/codes_schema','property_codes/fixture_home')")
        self.conn.commit();self.conn.close()
    def test_dry_run_apply_repeat_verify_and_preserve_extra_fields(self):
        self.assertEqual(project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v1'),1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM access_codes').fetchone()[0],0);self.conn.rollback()
        self.assertEqual(project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v1',apply=True),1)
        self.assertEqual(project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v1',apply=True),1)
        self.assertEqual(project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v1',verify_only=True),1)
        cipher,nonce,nanos=self.conn.execute('SELECT ciphertext,nonce,source_updated_nanos FROM access_codes').fetchone()
        self.assertNotIn(SECRET.encode(),bytes(cipher));self.assertEqual(nanos,123456000)
        values=decrypt_codes(bytes(cipher),bytes(nonce),property_id='fixture_home',version=1,key_version='v1',key=CODES_KEY)
        self.assertEqual(values,{'door':SECRET,'legacy_shared_extra':'fictional-legacy-value'})
        schema_cipher=self.conn.execute('SELECT ciphertext FROM access_code_schemas').fetchone()[0]
        self.assertNotIn(b'Fictional Home',bytes(schema_cipher));self.conn.rollback()
        with self.assertRaises(ValueError):project(self.conn,records(),BACKUP_KEY,BACKUP_KEY,'v1',apply=True)
        with self.assertRaises(ValueError):project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v2',apply=True)
        self.conn.rollback()
    def test_mismatched_existing_property_fails_without_writing_codes(self):
        self.conn.execute("INSERT INTO properties(id,name) VALUES ('fixture_home','Different')");self.conn.commit()
        with self.assertRaises(ValueError):project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v1',apply=True)
        self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM access_codes').fetchone()[0],0)
    def test_verify_fails_if_not_projected(self):
        with self.assertRaises(ValueError):project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v1',verify_only=True)
    def test_changed_target_version_is_never_overwritten(self):
        project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v1',apply=True)
        self.conn.execute("UPDATE access_codes SET version=2 WHERE property_id='fixture_home'");self.conn.commit()
        with self.assertRaises(ValueError):project(self.conn,records(),BACKUP_KEY,CODES_KEY,'v1',apply=True)
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT version FROM access_codes WHERE property_id='fixture_home'").fetchone()[0],2)
