"""Publication checks exercise synthetic data only."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from padsplit_scraper.publication_guard import check_publication


class PublicationContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.latest = {'scraped_at': '2026-01-01T00:00:00Z', 'messages': [], 'tasks': {},
                       'task_counts': {'Open': 2}, 'private_content_omitted': True,
                       'run_status': {'state': 'ok'}}
        self.write_json('docs/data/latest.json', self.latest)
        self.write_json('padsplit_scraper/docs/data/latest.json', self.latest)
        self.write_json('docs/data/occupancy.json', {'scraped_at': '2026-01-01T00:00:00Z',
            'derived_from': ['messages', 'tasks'], 'rooms': [], 'private_content_omitted': True})
        self.write_json('docs/thermostat/latest.json', {'scraped_at': None, 'locations': [],
                                                        'private_content_omitted': True})
        self.write('docs/codes.html', 'const DEFAULTS = [];')

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding='utf-8')

    def write_json(self, name, data):
        self.write(name, json.dumps(data))

    def test_accepts_withheld_public_files(self):
        self.assertEqual(check_publication(self.root), [])

    def test_rejects_tenant_or_unexpected_nested_fields(self):
        self.latest['messages'] = [{'text': 'fictional private content'}]
        self.latest['run_status'] = {'state': 'ok', 'sources': {'stats': {'error': 'private'}}}
        self.write_json('docs/data/latest.json', self.latest)
        self.assertTrue(check_publication(self.root))

    def test_rejects_room_rows_and_thermostat_records(self):
        self.write_json('docs/data/occupancy.json', {'scraped_at': '2026-01-01T00:00:00Z',
            'derived_from': ['messages', 'tasks'], 'rooms': [{'address': 'Example Lane'}],
            'private_content_omitted': True})
        self.write_json('docs/thermostat/latest.json', {'scraped_at': None,
            'locations': [{'name': 'Example'}], 'private_content_omitted': True})
        errors = check_publication(self.root)
        self.assertTrue(any('occupancy' in x for x in errors))
        self.assertTrue(any('thermostat' in x for x in errors))

    def test_rejects_nonempty_or_dynamic_code_defaults(self):
        for value in ('"2468"', 'readSecret()', '""'):
            self.write('docs/codes.html', f'const DEFAULTS = [ {{ fields: [{{ value: {value} }}] }}\n    ];')
            self.assertTrue(check_publication(self.root))

    def test_reports_secret_patterns_and_private_sentinels_without_echoing_them(self):
        secret = 'ghp_' + 'a' * 36
        sentinel = 'synthetic-private-sentinel'
        self.write('docs/extra.html', f'{secret} {sentinel}')
        errors = check_publication(self.root, [sentinel])
        self.assertTrue(errors)
        self.assertNotIn(secret, '\n'.join(errors))
        self.assertNotIn(sentinel, '\n'.join(errors))

    def test_scrape_workflow_checks_assets_before_staging(self):
        workflow = (Path(__file__).parent / '.github/workflows/scrape.yml').read_text()
        guard = workflow.index('python -m padsplit_scraper.publication_guard')
        self.assertLess(guard, workflow.index('git add docs/data/latest.json'))

    def test_digest_workflow_checks_ledger_before_staging(self):
        workflow = (Path(__file__).parent / '.github/workflows/slack_digest.yml').read_text()
        self.assertLess(workflow.index('padsplit_scraper.publication_guard'), workflow.index('git add "$ledger"'))

    def test_rejects_private_content_in_digest_ledger(self):
        self.write_json('docs/data/task_digest_ledger.json', {'date': '2026-01-01', 'posted': True,
            'source': 'private tenant data', 'posted_at': '2026-01-01T00:00:00Z'})
        self.assertTrue(any('task_digest_ledger' in e for e in check_publication(self.root)))

    def test_local_scripts_check_assets_before_staging(self):
        for name in ('run_morning.sh', 'run_afternoon.sh'):
            script = (Path(__file__).parent / name).read_text()
            self.assertLess(script.index('padsplit_scraper.publication_guard'), script.index(' add -- '))

    def test_scans_publishable_source_and_docs_outside_pages(self):
        secret = 'synthetic-private-tenant-sentinel'
        self.write('README.md', secret)
        errors = check_publication(self.root, [secret])
        self.assertTrue(any('README.md' in error for error in errors))
        self.assertNotIn(secret, '\n'.join(errors))

    def test_scans_deployment_and_database_text(self):
        sentinel = 'synthetic-private-sentinel'
        for name in ('launchd/test.plist', 'docs/test.svg', 'firestore.rules', 'migrations/test.sql', 'deploy/test.service', 'deploy/api.Dockerfile', 'backend/requirements.lock'):
            self.write(name, sentinel)
        errors = check_publication(self.root, [sentinel])
        for name in ('launchd/test.plist', 'docs/test.svg', 'firestore.rules', 'migrations/test.sql', 'deploy/test.service', 'deploy/api.Dockerfile', 'backend/requirements.lock'):
            self.assertTrue(any(name in error for error in errors))

    def test_git_enumeration_failure_blocks_publication(self):
        with patch('padsplit_scraper.publication_guard.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, 'git')):
            errors = check_publication(self.root)
        self.assertTrue(any('enumerat' in error.lower() for error in errors))

    def test_index_check_blocks_unstaged_or_untracked_files(self):
        subprocess.run(['git', '-C', str(self.root), 'add', '-A'], check=True)
        self.assertEqual(check_publication(self.root, check_index=True), [])
        self.write('README.md', 'untracked')
        self.assertTrue(check_publication(self.root, check_index=True))
        subprocess.run(['git', '-C', str(self.root), 'add', 'README.md'], check=True)
        self.write('README.md', 'unstaged change')
        self.assertTrue(check_publication(self.root, check_index=True))

    def test_staging_a_raw_output_or_environment_file_is_blocked(self):
        self.write('.env', 'SYNTHETIC=value')
        self.write('padsplit_scraper/output/stats.json', '{}')
        subprocess.run(['git', '-C', str(self.root), 'add', '-f', '.env', 'padsplit_scraper/output/stats.json'], check=True)
        errors = check_publication(self.root)
        self.assertTrue(any('private path' in error.lower() for error in errors))

    def test_staging_private_configuration_is_blocked(self):
        self.write('.private-config/example.json', '{}')
        subprocess.run(['git', '-C', str(self.root), 'add', '-f', '.private-config/example.json'], check=True)
        self.assertTrue(any('private path' in error.lower() for error in check_publication(self.root)))


if __name__ == '__main__':
    unittest.main()
