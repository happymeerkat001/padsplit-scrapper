import unittest
from padsplit_scraper.inbox_collection import chat_page,message_page,graphql_transport,advance_checkpoint,read_checkpoint,export_candidate,SourceError

def response(nodes,more=False,cursor=None,*,message=False):
    value={'edges':[{'node':x} for x in nodes],'pageInfo':{'hasNextPage':more,'endCursor':cursor}}
    return {'data':{'messenger':({'chat':{'messages':value}} if message else {'chats':value})}}

class InboxCollectionTests(unittest.TestCase):
    def test_display_preserves_only_allowlisted_observed_fields(self):
        node={'id':'fictional','occupancy':{'user':{'firstName':'Example','lastName':None,'picture':'https://private.invalid/secret'},'room':{'roomNumber':'2','pk':'private-id'}},'property':{'address':{'street1':'1 Fictional St','zip':'private-zip'}}}
        item=chat_page(lambda *_:response([node]))['items'][0]
        self.assertEqual(item['source_display'],{'first_name':'Example','last_name':None,'room_number':'2','street':'1 Fictional St'})
        self.assertNotIn('private',repr(item))
        node['occupancy']['user']['firstName']='https://private.invalid/token'
        with self.assertRaises(ValueError):chat_page(lambda *_:response([node]))
    def test_chat_page_preserves_explicit_unread_without_guessing(self):
        from padsplit_scraper.inbox_collection import chat_page
        first=chat_page(lambda *_:response([{'id':'fictional','member':{'isUnread':True}}]))
        self.assertIs(first['items'][0]['unread'],True)
        unknown=chat_page(lambda *_:response([{'id':'fictional'}]))
        self.assertIsNone(unknown['items'][0]['unread'])
        with self.assertRaises(Exception):chat_page(lambda *_:response([{'id':'fictional','member':{'isUnread':'yes'}}]))
    def test_page_cursor_coverage_and_attachment_redaction(self):
        calls=[]
        def post(query,variables):
            calls.append(variables.copy())
            return response([{'id':'msg-1','text':'hello','created':'2026-01-01T00:00:00Z','deleted':False,
                              'attachments':[{'id':'attachment-1','filename':'photo.jpg','mediaType':'image/jpeg','deleted':False,'location':'https://private.invalid/secret'}]}],True,'cursor-1',message=True)
        page=message_page(post,'chat-1',None,50)
        self.assertEqual(page['next_cursor'],'cursor-1');self.assertFalse(page['exhausted'])
        self.assertEqual(calls[0]['chatId'],'chat-1')
        self.assertEqual(page['items'][0]['attachments'][0],{'id':'attachment-1','filename':'photo.jpg','mediaType':'image/jpeg','deleted':False})
        self.assertNotIn('private.invalid',repr(page))
        self.assertIn('filtered',page['coverage'])

    def test_missing_or_repeated_cursor_and_malformed_page_fail_closed(self):
        with self.assertRaises(SourceError):chat_page(lambda *_:response([],True,None),None,50)
        with self.assertRaises(SourceError):chat_page(lambda *_:response([],True,'again'),'again',50)
        with self.assertRaises(SourceError):message_page(lambda *_:{'data':{}},'chat',None,50)
        with self.assertRaises(SourceError):message_page(lambda *_:response([{'id':'x','deleted':'yes'}],message=True),'chat',None,50)

    def test_urls_in_unexpected_attachment_metadata_are_not_returned(self):
        item={'id':'m','attachments':[{'filename':'https://private.invalid/token','mediaType':'image/jpeg','location':'https://private.invalid/token'}]}
        page=message_page(lambda *_:response([item],message=True),'chat')
        self.assertNotIn('private.invalid',repr(page));self.assertNotIn('filename',page['items'][0]['attachments'][0])

    def test_encoded_url_filenames_are_not_returned_but_safe_percent_names_are(self):
        unsafe=['https%3A%2F%2Fprivate.invalid%2Ffile%3Ftoken%3Dfictional',
                'https%253A%252F%252Fprivate.invalid%252Ffile',
                'folder%2Fprivate.jpg','folder\uff0fprivate.jpg','token%3Dfictional-secret']
        for name in unsafe:
            with self.subTest(kind='unsafe'):
                item={'id':'m','attachments':[{'filename':name,'deleted':False}]}
                page=message_page(lambda *_:response([item],message=True),'chat')
                self.assertNotIn('filename',page['items'][0]['attachments'][0])
        for name in ('100% complete.jpg','photo%20one.jpg','photo.jpg'):
            with self.subTest(kind='safe'):
                item={'id':'m','attachments':[{'filename':name,'deleted':False}]}
                page=message_page(lambda *_:response([item],message=True),'chat')
                self.assertEqual(page['items'][0]['attachments'][0]['filename'],name)

    def test_errors_are_generic_and_retry_is_bounded(self):
        calls=[]
        def broken(*_):calls.append(1);raise TimeoutError('PRIVATE TOKEN AND RESPONSE')
        with self.assertRaises(SourceError) as caught:chat_page(broken,None,10,attempts=3)
        self.assertEqual(len(calls),3);self.assertNotIn('PRIVATE',str(caught.exception))
        errors={'errors':[{'message':'secret diagnostic'}]}
        with self.assertRaises(SourceError) as caught:chat_page(lambda *_:errors,None,10)
        self.assertNotIn('secret',str(caught.exception))

    def test_existing_session_transport_is_read_only_and_rejects_unauthorized(self):
        class Result:
            status_code=200
            def json(self):return response([],False,None)
        class Session:
            def __init__(self):self.calls=[];self.result=Result()
            def post(self,url,**kwargs):self.calls.append((url,kwargs));return self.result
        session=Session();transport=graphql_transport(session)
        page=chat_page(transport,None,10);self.assertTrue(page['exhausted'])
        self.assertEqual(len(session.calls),1);self.assertTrue(session.calls[0][0].endswith('/api/graphql/'))
        self.assertEqual(session.calls[0][1]['json']['variables']['first'],10)
        session.result.status_code=401
        with self.assertRaises(SourceError):chat_page(transport,None,10)
        self.assertEqual(len(session.calls),2)

    def test_encrypted_checkpoint_resumes_without_leaking_provider_values(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint.enc';key=b'k'*32
            def post(query,variables):
                if 'chatId' not in variables:
                    return response([{'id':'private-chat','title':'Private tenant'}])
                after=variables['after']
                return response([{'id':'private-message-1' if after is None else 'private-message-2','text':'Private message','created':'2026-01-01T00:00:00Z'}],after is None,'cursor-next' if after is None else None,message=True)
            first=advance_checkpoint(post,path,key,max_pages=1,account_tag='a'*64,observed_at='2026-01-02T00:00:00Z')
            self.assertFalse(first['complete']);self.assertNotIn(b'Private',path.read_bytes())
            second=advance_checkpoint(post,path,key,max_pages=1,account_tag='a'*64);self.assertFalse(second['complete'])
            third=advance_checkpoint(post,path,key,max_pages=1,account_tag='a'*64);self.assertTrue(third['complete'])
            state=read_checkpoint(path,key);self.assertEqual(len(state['chats'][0]['recent_messages']),2)
            self.assertEqual(path.stat().st_mode & 0o077,0)
            candidate=Path(directory)/'candidate.enc';export_candidate(path,candidate,key)
            self.assertNotIn(b'Private',candidate.read_bytes())
            from backend.storage.migrate import read_backup
            records=read_backup(candidate,key)
            self.assertIn('inbox_collection/source',[row['path'] for row in records])
            import json
            chunk=next(row for row in records if '/chunks/' in row['path'])
            self.assertEqual(len(json.loads(chunk['data']['value']['data'])['messages'][0]['recent_messages']),2)
            with self.assertRaises(FileExistsError):export_candidate(path,candidate,key)
            with self.assertRaises(SourceError):read_checkpoint(path,b'x'*32)

    def test_page_failure_does_not_advance_checkpoint(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint.enc';key=b'k'*32
            advance_checkpoint(lambda *_:response([{'id':'a'}]),path,key,max_pages=1,account_tag='a'*64,observed_at='2026-01-02T00:00:00Z')
            before=path.read_bytes()
            with self.assertRaises(SourceError):advance_checkpoint(lambda *_:{'errors':['private']},path,key,max_pages=1,account_tag='a'*64)
            self.assertEqual(path.read_bytes(),before)

    def test_resume_rejects_different_account_without_fetching(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint.enc';key=b'k'*32;calls=[]
            advance_checkpoint(lambda *_:response([{'id':'a'}]),path,key,max_pages=1,observed_at='2026-01-02T00:00:00Z',account_tag='a'*64)
            before=path.read_bytes()
            with self.assertRaises(SourceError):advance_checkpoint(lambda *_:calls.append(1),path,key,max_pages=1,account_tag='b'*64)
            self.assertEqual(path.read_bytes(),before);self.assertFalse(calls)

    def test_failed_durable_write_and_cursor_cycles_do_not_advance(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint.enc';key=b'k'*32
            advance_checkpoint(lambda *_:response([{'id':'a'}]),path,key,max_pages=1,account_tag='a'*64,observed_at='2026-01-02T00:00:00Z')
            before=path.read_bytes()
            with patch('padsplit_scraper.inbox_collection._save_checkpoint',side_effect=OSError('disk error')):
                with self.assertRaises(OSError):advance_checkpoint(lambda *_:response([{'id':'m','text':'private'}],True,'cycle',message=True),path,key,max_pages=1,account_tag='a'*64)
            self.assertEqual(path.read_bytes(),before)
            advance_checkpoint(lambda *_:response([{'id':'m','text':'private'}],True,'cycle',message=True),path,key,max_pages=1,account_tag='a'*64)
            before=path.read_bytes()
            with self.assertRaises(SourceError):advance_checkpoint(lambda *_:response([{'id':'m','text':'private'}],True,'cycle',message=True),path,key,max_pages=1,account_tag='a'*64)
            self.assertEqual(path.read_bytes(),before)

    def test_repeated_message_page_is_idempotent_but_changes_stop(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'checkpoint.enc';key=b'k'*32
            advance_checkpoint(lambda *_:response([{'id':'a'}]),path,key,max_pages=1,account_tag='a'*64,observed_at='2026-01-02T00:00:00Z')
            first={'id':'m','text':'first'}
            advance_checkpoint(lambda *_:response([first],True,'next',message=True),path,key,max_pages=1,account_tag='a'*64)
            before=path.read_bytes()
            with self.assertRaises(SourceError):advance_checkpoint(lambda *_:response([{'id':'m','text':'changed'}],False,None,message=True),path,key,max_pages=1,account_tag='a'*64)
            self.assertEqual(path.read_bytes(),before)
            advance_checkpoint(lambda *_:response([first],False,None,message=True),path,key,max_pages=1,account_tag='a'*64)
            self.assertEqual(len(read_checkpoint(path,key)['chats'][0]['recent_messages']),1)

if __name__=='__main__':unittest.main()
