"""Exercise snapshot safety with fictional data and no network."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper import persist


class PersistenceSafetyTests(unittest.TestCase):
    def test_failed_replace_preserves_prior_file_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            path.write_text('{"old": true}', encoding='utf-8')
            with patch('padsplit_scraper.persist.os.replace', side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError):
                    persist._write_json(path, {'new': True})
            self.assertEqual(json.loads(path.read_text()), {'old': True})
            self.assertEqual([p.name for p in Path(directory).iterdir()], ['snapshot.json'])

    def test_docs_occupancy_does_not_receive_unapproved_fields(self):
        sample = {'scraped_at': '2026-01-01T00:00:00Z', 'rooms': [
            {'address': 'Example Street', 'room_number': 1, 'occupant_present': True,
             'room_code': 'secret-sentinel', 'extra': {'phone': 'private-sentinel'}}]}
        with tempfile.TemporaryDirectory() as directory:
            snapshot = persist.snapshot_output_dirs()
            self.addCleanup(persist.restore_output_dirs, snapshot)
            root = Path(directory)
            persist.configure_output_dirs(output_dir=root/'private', docs_data_dir=root/'public')
            persist._persist_occupancy_payload(sample)
            private = json.loads((root/'private'/'occupancy.json').read_text())
            public_text = (root/'public'/'occupancy.json').read_text()
            public = json.loads(public_text)
            self.assertEqual(private, sample)
            self.assertNotIn('secret-sentinel', public_text)
            self.assertNotIn('private-sentinel', public_text)
            self.assertEqual(public['rooms'], [])
            self.assertTrue(public['private_content_omitted'])
            self.assertNotIn('Example Street', public_text)

if __name__ == '__main__':
    unittest.main()
