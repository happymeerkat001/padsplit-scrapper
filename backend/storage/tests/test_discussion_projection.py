import datetime as dt
import os
import unittest
from backend.storage import migrate
from backend.storage.schema import apply_migrations
from backend.storage.project_manual_tasks import project as project_tasks
from backend.storage.project_discussions import project,discussion_row

KEY=b'm'*32

def rec(path,fields):return {'path':path,'update_time':'2026-01-01T00:00:00+00:00','data':migrate.encode_value(fields)}
def records():
    when=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
    return [rec('manual_tasks/fixture',{'notes':'Synthetic task','created':when}),rec('task_messages/fixture-note',{'taskId':'fixture','text':'Synthetic comment','role':'Manager','status':'Done','created':when})]

class DiscussionShapeTests(unittest.TestCase):
    def test_rejects_ambiguous_references_and_unknown_fields(self):
        for fields in ({'taskId':1,'text':'a'}, {'taskId':'bad/slash','text':'a'}, {'taskId':'x','text':'a','extra':'unknown'}):
            with self.assertRaises(ValueError): discussion_row(rec('task_messages/fixture',fields))

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated PostgreSQL test database not supplied')
class DiscussionProjectionTests(unittest.TestCase):
    def setUp(self):
        self.conn=migrate.staging_connect();apply_migrations(self.conn);migrate.import_records(self.conn,records(),KEY)
    def tearDown(self):
        self.conn.rollback()
        self.conn.execute("DELETE FROM task_discussions WHERE source_path='task_messages/fixture-note'")
        self.conn.execute("DELETE FROM tasks WHERE source_path='manual_tasks/fixture'")
        self.conn.execute("DELETE FROM source_documents WHERE source_path IN ('manual_tasks/fixture','task_messages/fixture-note')")
        self.conn.commit();self.conn.close()
    def test_requires_projected_task_and_is_idempotent(self):
        with self.assertRaises(ValueError):project(self.conn,records(),KEY,apply=True)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM task_discussions').fetchone()[0],0);self.conn.rollback()
        self.assertEqual(project_tasks(self.conn,records(),KEY,apply=True),1)
        self.assertEqual(project(self.conn,records(),KEY),1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM task_discussions').fetchone()[0],0);self.conn.rollback()
        self.assertEqual(project(self.conn,records(),KEY,apply=True),1)
        self.assertEqual(project(self.conn,records(),KEY,apply=True),1)
        row=self.conn.execute('SELECT body,source_role,source_status,author_uid FROM task_discussions').fetchone()
        self.assertEqual(row,('Synthetic comment','Manager','Done',None))
        self.conn.execute("UPDATE task_discussions SET body='changed' WHERE source_path='task_messages/fixture-note'");self.conn.commit()
        with self.assertRaises(ValueError):project(self.conn,records(),KEY,apply=True)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT body FROM task_discussions').fetchone()[0],'changed')

class MappingShapeTests(unittest.TestCase):
    def test_typed_mapping_rejects_duplicates_and_type_mismatch(self):
        from backend.storage.project_discussions import validate_mappings
        one={'source_type':'integer','source_value':42,'target_task_id':'mapped_integer','expected_property_id':'example','expected_source_path':'external/fixture'}
        other={'source_type':'string','source_value':'42','target_task_id':'mapped_string','expected_property_id':None,'expected_source_path':None}
        result=validate_mappings({'version':1,'bindings':[one,other]})
        self.assertEqual(len(result),2)
        with self.assertRaises(ValueError):validate_mappings({'version':1,'bindings':[one,one]})
        with self.assertRaises(ValueError):validate_mappings({'version':1,'bindings':[{**one,'source_value':'42'}]})
        with self.assertRaises(ValueError):validate_mappings({'version':1,'bindings':[{**one,'source_value':True}]})

    def test_mapping_file_requires_private_permissions_and_no_symlink(self):
        import json,tempfile
        from pathlib import Path
        from backend.storage.project_discussions import load_mappings
        item={'source_type':'integer','source_value':42,'target_task_id':'mapped_integer','expected_property_id':None,'expected_source_path':None}
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp);f=parent/'mapping.json';f.write_text(json.dumps({'version':1,'bindings':[item]}));f.chmod(0o600)
            self.assertEqual(len(load_mappings(f)),1)
            f.chmod(0o644)
            with self.assertRaises(ValueError):load_mappings(f)
            f.chmod(0o600);parent.chmod(0o755)
            with self.assertRaises(ValueError):load_mappings(f)
            parent.chmod(0o700);link=parent/'link';link.symlink_to(f)
            with self.assertRaises(ValueError):load_mappings(link)
            f.write_text('{"version":1,"version":1,"bindings":[]}')
            with self.assertRaises(ValueError):load_mappings(f)
        with self.assertRaises(ValueError):load_mappings(Path(__file__).resolve())

    def test_preserves_empty_text_and_rejects_missing_or_null(self):
        when=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
        row=discussion_row(rec('task_messages/fixture',{'taskId':42,'text':'','status':'Done','created':when}))
        self.assertEqual(row[1],('integer',42));self.assertEqual(row[2],'')
        for data in ({'taskId':42,'status':'Done','created':when},{'taskId':42,'text':None,'created':when}):
            with self.assertRaises(ValueError):discussion_row(rec('task_messages/fixture',data))

@unittest.skipUnless(os.environ.get('DATABASE_URL'),'isolated PostgreSQL test database not supplied')
class ExternalDiscussionProjectionTests(unittest.TestCase):
    def setUp(self):
        from backend.storage.project_discussions import validate_mappings
        self.conn=migrate.staging_connect();apply_migrations(self.conn)
        when=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
        self.docs=[rec('task_messages/int-fixture',{'taskId':42,'text':'','status':'Done','created':when}),rec('task_messages/str-fixture',{'taskId':'42','text':'Synthetic text','role':None,'created':when})]
        self.bindings=validate_mappings({'version':1,'bindings':[
            {'source_type':'integer','source_value':42,'target_task_id':'mapped_integer','expected_property_id':'example_prop','expected_source_path':'external/fixture-int'},
            {'source_type':'string','source_value':'42','target_task_id':'mapped_string','expected_property_id':None,'expected_source_path':None},
        ]})
        migrate.import_records(self.conn,self.docs,KEY)
        self.conn.execute("INSERT INTO properties(id,name) VALUES ('example_prop','Fictional property')")
        self.conn.execute("INSERT INTO tasks(id,property_id,title,source_path) VALUES ('mapped_integer','example_prop','Synthetic external','external/fixture-int'),('mapped_string',NULL,'Synthetic global',NULL)")
        self.conn.commit()
    def tearDown(self):
        self.conn.rollback()
        self.conn.execute("DELETE FROM task_discussions WHERE source_path IN ('task_messages/int-fixture','task_messages/str-fixture')")
        self.conn.execute("DELETE FROM tasks WHERE id IN ('mapped_integer','mapped_string')")
        self.conn.execute("DELETE FROM properties WHERE id='example_prop'")
        self.conn.execute("DELETE FROM source_documents WHERE source_path IN ('task_messages/int-fixture','task_messages/str-fixture')")
        self.conn.commit();self.conn.close()
    def test_mapping_dry_run_repeat_verify_and_status_only(self):
        with self.assertRaises(ValueError):project(self.conn,self.docs,KEY,apply=True)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM task_discussions').fetchone()[0],0);self.conn.rollback()
        self.assertEqual(project(self.conn,self.docs,KEY,mappings=self.bindings),2)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM task_discussions').fetchone()[0],0);self.conn.rollback()
        with self.assertRaises(ValueError):project(self.conn,self.docs,KEY,mappings=self.bindings,verify_only=True)
        self.assertEqual(project(self.conn,self.docs,KEY,mappings=self.bindings,apply=True),2)
        self.assertEqual(project(self.conn,self.docs,KEY,mappings=self.bindings,apply=True),2)
        self.assertEqual(project(self.conn,self.docs,KEY,mappings=self.bindings,verify_only=True),2)
        rows=self.conn.execute("SELECT task_id,body,source_task_ref_type,source_task_ref_string,source_task_ref_integer,source_present_fields FROM task_discussions ORDER BY task_id").fetchall()
        self.assertEqual(rows[0],('mapped_integer','','integer',None,42,['created','status','taskId','text']))
        self.assertEqual(rows[1],('mapped_string','Synthetic text','string','42',None,['created','role','taskId','text']))
        self.conn.rollback()
    def test_wrong_scope_or_unmapped_and_changed_target_fail_closed(self):
        bad=dict(self.bindings);bad[('integer',42)]=('mapped_integer','wrong_scope','external/fixture-int')
        with self.assertRaises(ValueError):project(self.conn,self.docs,KEY,mappings=bad,apply=True)
        extra=dict(self.bindings);extra[('integer',999)]=('mapped_integer','example_prop','external/fixture-int')
        with self.assertRaises(ValueError):project(self.conn,self.docs,KEY,mappings=extra,apply=True)
        self.conn.rollback();self.assertEqual(self.conn.execute('SELECT count(*) FROM task_discussions').fetchone()[0],0);self.conn.rollback()
        project(self.conn,self.docs,KEY,mappings=self.bindings,apply=True)
        self.conn.execute("UPDATE task_discussions SET body='changed' WHERE task_id='mapped_integer'");self.conn.commit()
        with self.assertRaises(ValueError):project(self.conn,self.docs,KEY,mappings=self.bindings,apply=True)
        self.conn.rollback();self.assertEqual(self.conn.execute("SELECT body FROM task_discussions WHERE task_id='mapped_integer'").fetchone()[0],'changed')

    def test_database_rejects_partial_source_reference_shape(self):
        import psycopg
        statements=[
            "INSERT INTO task_discussions(id,task_id,body,source_task_ref_string) VALUES ('bad-one','mapped_integer','','orphan')",
            "INSERT INTO task_discussions(id,task_id,body,source_task_ref_type,source_task_ref_integer) VALUES ('bad-two','mapped_integer','','integer',42)",
        ]
        for statement in statements:
            with self.assertRaises(psycopg.errors.CheckViolation):
                with self.conn.transaction():self.conn.execute(statement)
