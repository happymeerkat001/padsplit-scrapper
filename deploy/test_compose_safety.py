import unittest
from pathlib import Path

class ComposeSafetyTests(unittest.TestCase):
    def test_worker_uses_locked_collection_only_entry_point(self):
        text=(Path(__file__).parent/'systemd/padsplit-collector.service.template').read_text()
        self.assertIn('-m padsplit_scraper.locked_collector',text)
        for setting in ('PADSPLIT_COLLECTION_ONLY=1','PADSPLIT_ENABLE_ACTION_HOOKS=0','PADSPLIT_DISABLE_FIRESTORE_UPLOAD=1','PADSPLIT_GIT_PUBLISH=0','PADSPLIT_LOCK_DIR='):
            self.assertIn(setting,text)
    def test_private_database_and_explicit_memory_limits(self):
        text=(Path(__file__).parent/'compose.yaml').read_text()
        self.assertIn('mem_limit: ${PADSPLIT_DB_MEMORY_LIMIT:?',text)
        self.assertIn('mem_limit: ${PADSPLIT_API_MEMORY_LIMIT:?',text)
        self.assertIn('database_private:\n    internal: true',text)
        self.assertNotRegex(text,r'(?m)^\s*ports\s*:')
    def test_api_healthcheck_uses_schema_readiness(self):
        text=(Path(__file__).parent/'compose.yaml').read_text()
        self.assertIn('/health/ready',text)

if __name__=='__main__':unittest.main()
