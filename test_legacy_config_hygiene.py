"""Review branch must not embed live Firebase keys or pinned identities."""
import re
import unittest
from pathlib import Path

class LegacyConfigHygieneTests(unittest.TestCase):
    def test_legacy_pages_require_external_firebase_config_and_no_uid_literals(self):
        names=('index.html','private-messages.html','codes.html','templates.html','vendors.html','stats.html','kpi-history.html')
        for name in names:
            with self.subTest(page=name):
                text=(Path('docs')/name).read_text()
                self.assertFalse(bool(re.search(r'AIza[A-Za-z0-9_-]{30,}',text)),'Hardcoded Firebase web key')
                self.assertFalse(bool(re.search(r'const\s+(?:OWNER_UID|STATS_UID|OPERATOR_UID|CODES_UID)\s*=\s*["\'][^"\']+["\']',text)),'Hardcoded user identity')
                self.assertFalse(bool(re.search(r'ownerUid\s*:\s*["\'][^"\']+["\']',text)),'Hardcoded owner identity')
                self.assertTrue('runtime-config.js' in text,'Runtime config script missing')
                self.assertTrue('PADSPLIT_RUNTIME_CONFIG' in text,'Runtime config reference missing')
