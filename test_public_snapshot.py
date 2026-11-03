"""Regression checks for public Pages payloads; no network or live data."""
import json
import tempfile
import unittest
from pathlib import Path

from padsplit_scraper.public_snapshot import make_public_snapshot, write_public_snapshot


class PublicSnapshotTests(unittest.TestCase):
    def test_projection_drops_untrusted_payload_and_private_fields(self):
        raw = {
            'scraped_at': '2026-01-01T00:00:00Z',
            'messages': [{'text': 'tenant message', 'phone': 'secret'}],
            'tasks': {'Requests': [{'room_code': 'secret', 'details': 'tenant message', 'id': 123}],
                      'Other': [{'extra_data': {'secret': 'secret'}}]},
            'run_status': {'state': 'ok', 'mode': 'full', 'run_scraped_at': '2026-01-01T00:00:00Z',
                           'error_message': 'secret', 'host': 'private machine'},
            'unknown': {'private': 'secret'},
        }
        result = make_public_snapshot(raw)
        self.assertEqual(result['messages'], [])
        self.assertEqual(result['tasks'], {})
        self.assertEqual(result['task_counts'], {'Requests': 1, 'Other': 1})
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('tenant message', json.dumps(result))
        self.assertEqual(result['run_status']['state'], 'ok')

    def test_input_shape_and_status_fail_closed(self):
        with self.assertRaises(ValueError):
            make_public_snapshot({'scraped_at': 'not a timestamp', 'tasks': {}})
        with self.assertRaises(ValueError):
            make_public_snapshot({'scraped_at': '2026-01-01T00:00:00Z', 'tasks': []})
        self.assertNotIn('state', make_public_snapshot({'scraped_at': '2026-01-01T00:00:00Z',
                                                       'run_status': {'state': 'private'}})['run_status'])

    def test_source_health_is_allowlisted_and_redacted(self):
        raw = {'scraped_at': '2026-01-01T00:00:00Z', 'run_status': {
            'state': 'degraded', 'sources': {
                'occupancy': {'state': 'failed', 'error_message': 'private-sentinel'},
                'stats': {'state': 'ok', 'scraped_at': '2026-01-01T00:00:00Z', 'host': 'private-sentinel'},
                'messages': {'state': 'ok', 'scraped_at': 'not-a-timestamp', 'fallback_used': True},
                'other': {'state': 'ok', 'secret': 'private-sentinel'},
            }}}
        result = make_public_snapshot(raw)
        sources = result['run_status']['sources']
        self.assertEqual(sources['occupancy'], {'state': 'failed'})
        self.assertEqual(sources['stats'], {'state': 'ok', 'scraped_at': '2026-01-01T00:00:00Z'})
        self.assertEqual(sources['messages'], {'state': 'ok', 'fallback_used': True})
        self.assertNotIn('private-sentinel', json.dumps(result))
        self.assertNotIn('other', sources)

    def test_occupancy_projection_never_publishes_room_details(self):
        from padsplit_scraper.public_snapshot import make_public_occupancy
        row = {'property_id': 2, 'address': 'Example Street', 'room_number': 1,
               'occupant_present': True, 'room_code': 'secret', 'user': {'name': 'secret'}}
        out = make_public_occupancy({'scraped_at': '2026-01-01T00:00:00Z',
                                     'derived_from': ['messages', 'tasks'], 'rooms': [row]})
        self.assertNotIn('secret', json.dumps(out))
        self.assertEqual(out['rooms'], [])
        self.assertTrue(out['private_content_omitted'])
        self.assertNotIn('Example Street', json.dumps(out))
        self.assertNotIn('occupant_present', json.dumps(out))

    def test_redacted_digest_never_sends_or_writes_ledger(self):
        from datetime import datetime, timezone
        from unittest.mock import Mock
        from slack_task_digest import run_digest
        with tempfile.TemporaryDirectory() as d:
            send = Mock()
            ledger = Path(d) / 'ledger.json'
            result = run_digest(latest={'private_content_omitted': True, 'tasks': {}},
                                now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                ledger_path=ledger, send=send, weather_fn=Mock())
            self.assertNotEqual(result, 0)
            send.assert_not_called()
            self.assertFalse(ledger.exists())

    def test_failure_does_not_replace_existing_public_file(self):
        with tempfile.TemporaryDirectory() as d:
            source, dest = Path(d)/'in.json', Path(d)/'out.json'
            source.write_text('{invalid')
            dest.write_text('original')
            with self.assertRaises(ValueError):
                write_public_snapshot(source, dest)
            self.assertEqual(dest.read_text(), 'original')

if __name__ == '__main__':
    unittest.main()
