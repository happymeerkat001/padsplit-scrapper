import contextlib,io,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from padsplit_scraper.collect_private_inbox import main
from padsplit_scraper.test_inbox_collection import response

class CollectorCommandTests(unittest.TestCase):
    def test_explicit_enable_and_collection_only_are_required_before_login(self):
        calls=[]
        with patch.dict(os.environ,{},clear=True),contextlib.redirect_stderr(io.StringIO()):
            result=main(['--checkpoint','/tmp/unused','--candidate','/tmp/unused2'],session_factory=lambda:calls.append('session'))
        self.assertNotEqual(result,0);self.assertFalse(calls)

    def test_collects_read_only_pages_into_encrypted_candidate(self):
        class Reply:
            status_code=200
            def __init__(self,value):self.value=value
            def json(self):return self.value
        class Session:
            def __init__(self):self.calls=[]
            def post(self,url,**kwargs):
                self.calls.append((url,kwargs))
                if 'chatId' not in kwargs['json']['variables']:return Reply(response([{'id':'private-chat','title':'Private person'}]))
                return Reply(response([{'id':'private-message','text':'secret message','deleted':False,'attachments':[{'deleted':False,'filename':'photo.jpg','location':'https://private.invalid/token'}]}],message=True))
            def close(self):pass
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);checkpoint=root/'state.enc';candidate=root/'candidate.enc';session=Session();output=io.StringIO()
            environment={'PADSPLIT_COLLECTION_ONLY':'1','PADSPLIT_EMAIL':'test@example.invalid','PADSPLIT_PASSWORD':'fictional-secret','PADSPLIT_MIGRATION_KEY':'a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s='}
            with patch.dict(os.environ,environment,clear=True),contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):
                result=main(['--enable-source-read','--checkpoint',str(checkpoint),'--candidate',str(candidate),'--max-pages','2'],session_factory=lambda:session,login_func=lambda *_:None)
            self.assertEqual(result,0);self.assertEqual(len(session.calls),2);self.assertTrue(candidate.is_file())
            self.assertNotIn(b'secret message',candidate.read_bytes());self.assertNotIn('private.invalid',output.getvalue());self.assertNotIn('fictional-secret',output.getvalue())
            self.assertTrue(all(call[1]['allow_redirects'] is False for call in session.calls))

if __name__=='__main__':unittest.main()
