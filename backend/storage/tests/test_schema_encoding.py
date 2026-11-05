import contextlib
import unittest
from backend.storage.schema import apply_migrations

class Cursor:
    def __init__(self,value):self.value=value
    def fetchone(self):return (self.value,)
class Connection:
    def __init__(self,value):self.value=value;self.queries=[]
    def transaction(self):return contextlib.nullcontext()
    def execute(self,query,*args):self.queries.append(query);return Cursor(self.value)

class SchemaEncodingTests(unittest.TestCase):
    def test_sql_ascii_rejected_before_any_schema_mutation(self):
        for encoding in (b'SQL_ASCII','LATIN1'):
            conn=Connection(encoding)
            with self.assertRaisesRegex(ValueError,'UTF8'):apply_migrations(conn)
            self.assertEqual(conn.queries,['SHOW server_encoding'])
