"""Isolation checks for the fictional demo; never contacts external services."""
import http.client
import subprocess
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from server import Handler

class DemoIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def request(self,path,method='GET',host=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        headers={'Host':host} if host else {}
        conn.request(method,path,headers=headers);response=conn.getresponse()
        result=(response.status,dict(response.getheaders()),response.read());conn.close();return result
    def test_requires_explicit_flag(self):
        script=Path(__file__).resolve().parent/'server.py'
        proc=subprocess.run([sys.executable,str(script)],capture_output=True,text=True,timeout=3)
        self.assertNotEqual(proc.returncode,0)
    def test_only_known_files_and_demo_endpoint_are_served(self):
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/__demo_session')[0],200)
        self.assertEqual(self.request('/operations-tokens.css')[0],200)
        self.assertEqual(self.request('/operations-icons.svg')[0],200)
        self.assertEqual(self.request('/task-filter.js')[0],200)
        self.assertEqual(self.request('/property-summary.js')[0],200)
        self.assertEqual(self.request('/property-summary.css')[0],200)
        self.assertEqual(self.request('/navigation.js')[0],200)
        self.assertEqual(self.request('/overview-insights.js')[0],200)
        self.assertEqual(self.request('/overview-layout.css')[0],200)
        self.assertEqual(self.request('/inbox-search.js')[0],200)
        self.assertEqual(self.request('/integration-status.js')[0],200)
        self.assertEqual(self.request('/display-format.js')[0],200)
        self.assertEqual(self.request('/team-policy.js')[0],200)
        for path in ['/.env','/../firestore.rules','/server.py','/data/latest.json']:
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/',host='example.com')[0],403)
        self.assertEqual(self.request('/__demo_session','POST')[0],501)
    def test_csp_and_browser_code_have_no_production_adapter(self):
        _,headers,_=self.request('/')
        csp=headers['Content-Security-Policy']
        self.assertIn("default-src 'none'",csp)
        self.assertIn("connect-src 'self'",csp)
        self.assertIn("form-action 'none'",csp)
        script=(Path(__file__).resolve().parent/'demo.js').read_text()
        self.assertIn("fetch('/__demo_session'",script)
        self.assertIn("['127.0.0.1','localhost']",script)
        self.assertEqual(script.count('fetch('),1)
        for forbidden in ['firebasejs/', 'firebaseio.com', 'firestore.googleapis.com', 'padsplit.com', 'cloudfunctions.net','XMLHttpRequest','WebSocket','sendBeacon']:
            self.assertNotIn(forbidden.lower(),script.lower())
        self.assertFalse((Path(__file__).resolve().parent.parent/'docs'/'demo.js').exists())

if __name__=='__main__': unittest.main()
