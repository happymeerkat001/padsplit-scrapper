"""Run status must distinguish failed occupancy from fresh statistics."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import persist, runtime, scraper
from test_padsplit_scraper import DummySession, recent_chat, sample_kpis


class OccupancyRunHealthTests(unittest.TestCase):
    def test_occupancy_failure_is_degraded_without_discarding_fresh_stats(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            stack.enter_context(patch.object(persist, 'OUTPUT_DIR', root/'output'))
            stack.enter_context(patch.object(persist, 'DOCS_DATA_DIR', root/'docs'))
            stack.enter_context(patch.object(scraper, 'load_credentials', return_value={'email':'user', 'password':'pw'}))
            stack.enter_context(patch.object(scraper, 'create_session', return_value=DummySession()))
            stack.enter_context(patch.object(scraper, 'login'))
            stack.enter_context(patch.object(scraper, 'fetch_messages', return_value=recent_chat()))
            stack.enter_context(patch.object(scraper, 'fetch_thread_messages', return_value=[]))
            stack.enter_context(patch.object(scraper, 'fetch_tasks', return_value={'Requests': []}))
            stack.enter_context(patch.object(scraper, 'compute_occupancy', side_effect=ValueError('private-sentinel')))
            stack.enter_context(patch.object(scraper, 'fetch_rooms', return_value=[{'id':1}]))
            stack.enter_context(patch.object(scraper, 'fetch_properties_stats', return_value=[]))
            stack.enter_context(patch.object(scraper, 'fetch_earnings', return_value={'results': []}))
            stack.enter_context(patch.object(scraper, 'fetch_performance_history', return_value={}))
            stack.enter_context(patch.object(scraper, 'compute_kpis', return_value=sample_kpis(92)))
            stack.enter_context(patch.object(scraper, 'upload_stats_to_firestore', return_value=False))
            log = io.StringIO()
            with contextlib.redirect_stderr(log):
                outcome = scraper.run(policy=runtime.COLLECTION_ONLY_POLICY)
            self.assertEqual(outcome.action, 'degraded')
            self.assertEqual(outcome.run_status['failed_phase'], 'occupancy')
            self.assertEqual(outcome.run_status['sources']['occupancy']['state'], 'failed')
            self.assertEqual(outcome.run_status['sources']['stats']['state'], 'ok')
            self.assertNotIn('private-sentinel', log.getvalue())
            self.assertTrue((root/'output'/'stats.json').exists())
            self.assertFalse((root/'docs'/'occupancy.json').exists())

if __name__ == '__main__':
    unittest.main()
