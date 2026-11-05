"""Inbox search must not block the server event loop on a slow database."""
import asyncio
import threading
import time
import unittest
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from backend.api.app import create_app

class Repo:
    def identity(self, uid): return {'uid':uid,'role':'viewer','properties':[]}
    def ready(self): return True

class InboxAsyncTests(unittest.TestCase):
    def test_oversized_search_is_rejected_without_query_or_echo(self):
        app=create_app(repository=Repo(),verify_token=lambda _: 'fixture',allowed_origins=['http://localhost:9999'])
        payload='x'*3000
        with patch('backend.api.inbox.conversations') as query, TestClient(app) as client:
            response=client.post('/v1/inbox/search',headers={'Authorization':'Bearer fixture'},content=payload)
            self.assertEqual(response.status_code,422)
            self.assertNotIn(payload,response.text)
            query.assert_not_called()

    def test_search_runs_blocking_query_off_event_loop(self):
        entered=threading.Event()
        release=threading.Event()
        def slow_query(*args):
            entered.set()
            if not release.wait(2): raise TimeoutError()
            return []
        async def exercise():
            app=create_app(repository=Repo(),verify_token=lambda _: 'fixture',allowed_origins=['http://localhost:9999'])
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                search=asyncio.create_task(client.post('/v1/inbox/search',headers={'Authorization':'Bearer fixture'},json={'q':'sample','unread':False,'limit':10,'offset':0}))
                timer=threading.Timer(.5,release.set);timer.start()
                try:
                    started=time.monotonic()
                    await asyncio.sleep(.1)
                    self.assertLess(time.monotonic()-started,.35,'event loop was blocked by the search')
                    self.assertTrue(entered.is_set())
                    self.assertFalse(search.done(),'a blocked query must not block the event loop')
                    health=await client.get('/health/live')
                    self.assertEqual(health.status_code,200)
                    release.set()
                    result=await search
                    self.assertEqual(result.status_code,200)
                    self.assertEqual(result.json()['items'],[])
                finally:
                    release.set();timer.cancel()
                    await search
        with patch('backend.api.inbox.conversations',side_effect=slow_query):
            asyncio.run(exercise())
