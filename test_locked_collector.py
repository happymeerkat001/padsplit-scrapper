import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import locked_collector, process_lock

class LockedCollectorTests(unittest.TestCase):
    def test_refuses_overlapping_run_before_scraper_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            env={'PADSPLIT_LOCK_DIR':folder,'PADSPLIT_OUTPUT_DIR':folder,'PADSPLIT_STATE_DIR':folder,'PADSPLIT_COLLECTION_ONLY':'1','PADSPLIT_ENABLE_ACTION_HOOKS':'0','PADSPLIT_DISABLE_FIRESTORE_UPLOAD':'1','PADSPLIT_GIT_PUBLISH':'0'}
            with process_lock.acquire_lock('padsplit-collector',environ=env):
                with patch.object(locked_collector.scraper,'run') as run:
                    self.assertNotEqual(locked_collector.main(environ=env),0)
                    run.assert_not_called()
    def test_requires_explicit_collection_only_gates(self):
        with tempfile.TemporaryDirectory() as folder:
            env={'PADSPLIT_LOCK_DIR':folder,'PADSPLIT_OUTPUT_DIR':folder,'PADSPLIT_STATE_DIR':folder,'PADSPLIT_COLLECTION_ONLY':'1','PADSPLIT_ENABLE_ACTION_HOOKS':'0','PADSPLIT_DISABLE_FIRESTORE_UPLOAD':'1','PADSPLIT_GIT_PUBLISH':'0'}
            for key in ('PADSPLIT_LOCK_DIR','PADSPLIT_OUTPUT_DIR','PADSPLIT_STATE_DIR','PADSPLIT_COLLECTION_ONLY','PADSPLIT_ENABLE_ACTION_HOOKS','PADSPLIT_DISABLE_FIRESTORE_UPLOAD','PADSPLIT_GIT_PUBLISH'):
                invalid=dict(env);invalid.pop(key)
                with self.subTest(key=key),patch.object(locked_collector.scraper,'run') as run:
                    self.assertNotEqual(locked_collector.main(environ=invalid),0)
                    run.assert_not_called()
    def test_runs_isolated_once_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            env={'PADSPLIT_LOCK_DIR':folder,'PADSPLIT_OUTPUT_DIR':folder,'PADSPLIT_STATE_DIR':folder,'PADSPLIT_COLLECTION_ONLY':'1','PADSPLIT_ENABLE_ACTION_HOOKS':'0','PADSPLIT_DISABLE_FIRESTORE_UPLOAD':'1','PADSPLIT_GIT_PUBLISH':'0'}
            with patch.object(locked_collector.scraper,'run') as run:
                run.return_value.process_exit_code=0
                self.assertEqual(locked_collector.main(environ=env),0)
                run.assert_called_once_with(isolate_output=True)
            with process_lock.acquire_lock('padsplit-collector',environ=env): pass
    def test_refuses_checkout_paths_and_redacts_worker_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            env={'PADSPLIT_LOCK_DIR':folder,'PADSPLIT_OUTPUT_DIR':folder,'PADSPLIT_STATE_DIR':folder,'PADSPLIT_COLLECTION_ONLY':'1','PADSPLIT_ENABLE_ACTION_HOOKS':'0','PADSPLIT_DISABLE_FIRESTORE_UPLOAD':'1','PADSPLIT_GIT_PUBLISH':'0'}
            with patch.object(locked_collector.scraper,'run',side_effect=RuntimeError('private-example-sentinel')) as run, patch('sys.stderr') as stderr:
                self.assertEqual(locked_collector.main(environ=env),1)
                self.assertNotIn('private-example-sentinel',''.join(str(call) for call in stderr.write.call_args_list))
                run.assert_called_once()
            env['PADSPLIT_OUTPUT_DIR']=str(Path(__file__).resolve().parent/'unsafe-output')
            with patch.object(locked_collector.scraper,'run') as run:
                self.assertEqual(locked_collector.main(environ=env),2)
                run.assert_not_called()
            shared=Path(folder)/'shared';shared.mkdir(mode=0o755);shared.chmod(0o755)
            env['PADSPLIT_OUTPUT_DIR']=str(shared)
            with patch.object(locked_collector.scraper,'run') as run:
                self.assertEqual(locked_collector.main(environ=env),2)
                run.assert_not_called()
