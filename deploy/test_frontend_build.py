"""Offline fail-closed tests; all fixtures are fictional."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from frontend_build import build, check_release_sources

class FrontendReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name); self.root = self.base / 'repo'; docs = self.root / 'docs'
        docs.mkdir(parents=True)
        (self.root / 'deploy').mkdir()
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html"]')
        (self.root / 'deploy/vercel.json').write_text('{"version":2}')
        self.web_config = {'projectId':'demo-private', 'authDomain':'demo-private.firebaseapp.com', 'apiKey':'AIza' + 'A'*35}
        self.production_web_config = {'projectId':'live-project', 'authDomain':'live-project.firebaseapp.com', 'apiKey':'AIza' + 'B'*35}
        fixtures = {
            'data/latest.json': {'scraped_at':'2026-01-01T00:00:00Z','messages':[],'tasks':{},'task_counts':{},'private_content_omitted':True,'run_status':{}},
            'data/occupancy.json': {'scraped_at':'2026-01-01T00:00:00Z','derived_from':['messages','tasks'],'rooms':[],'private_content_omitted':True},
            'thermostat/latest.json': {'scraped_at':None,'locations':[],'private_content_omitted':True},
        }
        for name, content in fixtures.items():
            p = docs / name; p.parent.mkdir(exist_ok=True); p.write_text(json.dumps(content))
        (docs / 'index.html').write_text('<html><head><script src="runtime-config.js"></script><script>const config={projectId:"demo-private"}; window.PADSPLIT_RUNTIME_CONFIG; document.querySelector(\'meta[name="padsplit-data-mode"]\')</script></head></html>')
    def test_mismatched_project_and_direct_firestore_are_blocked(self):
        p = self.root / 'docs/index.html'
        p.write_text('const x={projectId:"production-123"}; getFirestore(app);')
        errors = check_release_sources(self.root, 'demo-private')
        self.assertTrue(any('Firestore' in error for error in errors))
        self.assertTrue(any('project' in error for error in errors))
    def test_allowlist_omits_unrelated_and_build_rejects_existing_destination(self):
        (self.root / 'docs/secret.txt').write_text('private')
        with patch('frontend_build.check_publication', return_value=[]):
            out = self.base / 'out'
            opts = dict(api_origin='https://api.staging.example.com', environment='preview', firebase_web_config=self.web_config, production_api_origin='https://api.example.com', production_firebase_project='live-project', production_firebase_web_config=self.production_web_config)
            self.assertEqual(build(self.root, out, 'demo-private', [], **opts), [])
            self.assertFalse((out / 'secret.txt').exists())
            self.assertIn('api.staging.example.com', (out / 'runtime-config.js').read_text())
            self.assertIn('"dataMode":"api"', (out / 'runtime-config.js').read_text())
            self.assertIn('<meta name="padsplit-data-mode" content="api">', (out / 'index.html').read_text())
            self.assertEqual(json.loads((out / 'vercel.json').read_text())['version'], 2)
            self.assertTrue(build(self.root, out, 'demo-private', [], **opts))

    def test_review_pages_are_explicit_preview_only_and_still_scanned(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "private-messages.html"]')
        (self.root / 'docs/index.html').write_text('getFirestore(app);')
        (self.root / 'docs/private-messages.html').write_text('getFirestore(app);')
        review=self.root / 'deploy/frontend-api';review.mkdir()
        template='<html><head><script src="runtime-config.js"></script><script>window.PADSPLIT_RUNTIME_CONFIG; document.querySelector(\'meta[name="padsplit-data-mode"]\')</script></head><body>{}</body></html>'
        (review/'index.html').write_text(template.format('overview-review'))
        (review/'private-messages.html').write_text(template.format('inbox-review'))
        options=dict(api_origin='https://stage.example.com',environment='preview',firebase_web_config=self.web_config,
                     production_api_origin='https://api.example.com',production_firebase_project='live-project',production_firebase_web_config=self.production_web_config)
        with patch('frontend_build.check_publication',return_value=[]):
            self.assertTrue(build(self.root,self.base/'default','demo-private',[],**options))
            output=self.base/'review'
            self.assertEqual(build(self.root,output,'demo-private',[],review_pages=True,**options),[])
            self.assertIn('overview-review',(output/'index.html').read_text())
            self.assertIn('inbox-review',(output/'private-messages.html').read_text())
            self.assertIn('Staging review', (output/'index.html').read_text())
            self.assertIn('Staging review', (output/'private-messages.html').read_text())
            denied=self.base/'production'
            self.assertTrue(build(self.root,denied,'demo-private',[],api_origin='https://api.example.com',environment='production',firebase_web_config=self.web_config,review_pages=True))
            self.assertFalse(denied.exists())
            shared=self.base/'shared-target'
            self.assertTrue(build(self.root,shared,'demo-private',[],review_pages=True,**{**options,'api_origin':'https://api.example.com'}))
            self.assertFalse(shared.exists())
            invalid=self.base/'invalid-flag'
            self.assertTrue(build(self.root,invalid,'demo-private',[],review_pages='true',**options))
            self.assertFalse(invalid.exists())
            (review/'index.html').write_text(template.format('getFirestore(app);'))
            self.assertTrue(build(self.root,self.base/'unsafe','demo-private',[],review_pages=True,**options))
            self.assertFalse((self.base/'unsafe').exists())
            (self.root/'deploy/frontend-assets.json').write_text('["index.html"]')
            with self.assertRaises(ValueError):
                build(self.root,self.base/'missing-review-page','demo-private',[],review_pages=True,**options)
            self.assertFalse((self.base/'missing-review-page').exists())
    def test_symlink_is_rejected_and_no_output_is_created(self):
        (self.root / 'docs/icon.js').symlink_to(self.root / 'docs/index.html')
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "icon.js"]')
        with patch('frontend_build.check_publication', return_value=[]):
            out = self.base / 'out'
            self.assertTrue(build(self.root, out, 'demo-private', [], api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config))
            self.assertFalse(out.exists())

    def test_preview_must_not_share_production_api_or_firebase(self):
        with patch('frontend_build.check_publication', return_value=[]):
            out = self.base / 'out'
            self.assertTrue(build(self.root, out, 'demo-private', [], api_origin='https://api.example.com', environment='preview', firebase_web_config=self.web_config, production_api_origin='https://api.example.com', production_firebase_project='live-project', production_firebase_web_config=self.production_web_config))
            self.assertTrue(build(self.root, out, 'demo-private', [], api_origin='https://stage.example.com', environment='preview', firebase_web_config=self.web_config, production_api_origin='https://api.example.com', production_firebase_project='demo-private', production_firebase_web_config=self.web_config))
            same_key = {**self.production_web_config, 'apiKey':self.web_config['apiKey']}
            self.assertTrue(build(self.root, out, 'demo-private', [], api_origin='https://stage.example.com', environment='preview', firebase_web_config=self.web_config, production_api_origin='https://api.example.com', production_firebase_project='live-project', production_firebase_web_config=same_key))
            self.assertFalse(out.exists())

    def test_http_and_private_addresses_are_rejected(self):
        with patch('frontend_build.check_publication', return_value=[]):
            for origin in ('http://api.example.com', 'https://127.0.0.1', 'https://10.0.0.5', 'https://user:pass@api.example.com'):
                self.assertTrue(build(self.root, self.base / 'out', 'demo-private', [], api_origin=origin, environment='production', firebase_web_config=self.web_config))

    def test_manifest_cannot_escape_docs_directory(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["../secrets.js"]')
        with self.assertRaises(ValueError):
            check_release_sources(self.root, 'demo-private')

    def test_manifest_cannot_override_generated_runtime_config(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "runtime-config.js"]')
        with self.assertRaises(ValueError):
            check_release_sources(self.root, 'demo-private')

    def test_missing_direct_link_blocks_release(self):
        with (self.root / 'docs/index.html').open('a') as stream:
            stream.write('<a href="./missing.html">Missing</a>')
        self.assertTrue(any('missing from the allowlist' in error for error in check_release_sources(self.root, 'demo-private')))

    def test_hardcoded_web_key_blocks_release(self):
        with (self.root / 'docs/index.html').open('a') as stream:
            stream.write('<script>let c={apiKey:"synthetic"};</script>')
        self.assertTrue(any('hardcoded Firebase' in error for error in check_release_sources(self.root, 'demo-private')))

    def test_private_or_mismatched_firebase_config_is_rejected(self):
        with patch('frontend_build.check_publication', return_value=[]):
            for config in ({**self.web_config, 'private_key':'synthetic-secret'}, {**self.web_config, 'projectId':'other-project'}):
                self.assertTrue(build(self.root, self.base / 'out', 'demo-private', [], api_origin='https://api.example.com', environment='production', firebase_web_config=config))
                self.assertFalse((self.base / 'out').exists())

    def test_team_flag_requires_explicit_boolean_and_defaults_off(self):
        with patch('frontend_build.check_publication', return_value=[]):
            opts = dict(api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
            out = self.base / 'off'
            self.assertEqual(build(self.root, out, 'demo-private', [], **opts), [])
            self.assertNotIn('"teamEnabled":true', (out / 'runtime-config.js').read_text())
            enabled = self.base / 'enabled'
            self.assertEqual(build(self.root, enabled, 'demo-private', [], team_enabled=True, **opts), [])
            self.assertIn('"teamEnabled":true', (enabled / 'runtime-config.js').read_text())
            invalid = self.base / 'invalid'
            self.assertTrue(build(self.root, invalid, 'demo-private', [], team_enabled='true', **opts))
            self.assertFalse(invalid.exists())

    def test_reviewed_vendor_release_source_is_scanned_and_copied(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "vendors.html"]')
        (self.root / 'docs/vendors.html').write_text('getFirestore(app); legacy-marker')
        release = self.root / 'deploy/frontend-api/vendors.html'
        release.parent.mkdir()
        release.write_text('<html><head><script src="runtime-config.js"></script><script>window.PADSPLIT_RUNTIME_CONFIG; document.querySelector(\'meta[name="padsplit-data-mode"]\')</script></head><body>api-marker</body></html>')
        with patch('frontend_build.check_publication', return_value=[]):
            opts = dict(api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
            out = self.base / 'out'
            self.assertEqual(build(self.root, out, 'demo-private', [], **opts), [])
            source = (out / 'vendors.html').read_text()
            self.assertIn('api-marker', source)
            self.assertNotIn('legacy-marker', source)
            release.write_text(release.read_text() + 'getFirestore(app);')
            failed = self.base / 'failed'
            self.assertTrue(any('Firestore' in e for e in build(self.root, failed, 'demo-private', [], **opts)))
            self.assertFalse(failed.exists())

    def test_history_release_source_is_scanned_and_copied(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "kpi-history.html"]')
        (self.root / 'docs/kpi-history.html').write_text('getFirestore(app); legacy-marker')
        release = self.root / 'deploy/frontend-api/kpi-history.html'
        release.parent.mkdir()
        release.write_text('<html><head><script src="runtime-config.js"></script><script>window.PADSPLIT_RUNTIME_CONFIG; document.querySelector(\'meta[name="padsplit-data-mode"]\')</script></head><body>api-history-marker</body></html>')
        with patch('frontend_build.check_publication', return_value=[]):
            opts = dict(api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
            out = self.base / 'out'
            self.assertEqual(build(self.root, out, 'demo-private', [], **opts), [])
            self.assertIn('api-history-marker', (out / 'kpi-history.html').read_text())
            self.assertNotIn('legacy-marker', (out / 'kpi-history.html').read_text())
            release.write_text(release.read_text() + 'getFirestore(app);')
            failed = self.base / 'failed'
            self.assertTrue(any('Firestore' in e for e in build(self.root, failed, 'demo-private', [], **opts)))
            self.assertFalse(failed.exists())

    def test_stats_release_source_is_scanned_and_copied(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "stats.html"]')
        (self.root / 'docs/stats.html').write_text('getFirestore(app); legacy-marker')
        release = self.root / 'deploy/frontend-api/stats.html'
        release.parent.mkdir()
        release.write_text('<html><head><script src="runtime-config.js"></script><script>window.PADSPLIT_RUNTIME_CONFIG; document.querySelector(\'meta[name="padsplit-data-mode"]\')</script></head><body>api-stats-marker</body></html>')
        with patch('frontend_build.check_publication', return_value=[]):
            opts = dict(api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
            out = self.base / 'out'
            self.assertEqual(build(self.root, out, 'demo-private', [], **opts), [])
            self.assertIn('api-stats-marker', (out / 'stats.html').read_text())
            self.assertNotIn('legacy-marker', (out / 'stats.html').read_text())
            release.write_text(release.read_text() + 'getFirestore(app);')
            failed = self.base / 'failed'
            self.assertTrue(any('Firestore' in e for e in build(self.root, failed, 'demo-private', [], **opts)))
            self.assertFalse(failed.exists())

    def test_reviewed_codes_release_source_is_scanned_and_copied(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "codes.html"]')
        (self.root / 'docs/codes.html').write_text('getFirestore(app); legacy-marker')
        release = self.root / 'deploy/frontend-api/codes.html'
        release.parent.mkdir()
        release.write_text('<html><head><script src="runtime-config.js"></script><script>window.PADSPLIT_RUNTIME_CONFIG; document.querySelector(\'meta[name="padsplit-data-mode"]\')</script></head><body>api-codes-marker</body></html>')
        with patch('frontend_build.check_publication', return_value=[]):
            opts = dict(api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
            out = self.base / 'out'
            self.assertEqual(build(self.root, out, 'demo-private', [], **opts), [])
            self.assertIn('api-codes-marker', (out / 'codes.html').read_text())
            release.write_text(release.read_text() + 'getFirestore(app);')
            failed = self.base / 'failed'
            self.assertTrue(any('Firestore' in e for e in build(self.root, failed, 'demo-private', [], **opts)))
            self.assertFalse(failed.exists())

    def test_reviewed_templates_release_source_is_scanned_and_copied(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "templates.html"]')
        (self.root / 'docs/templates.html').write_text('getFirestore(app); legacy-marker')
        release = self.root / 'deploy/frontend-api/templates.html'
        release.parent.mkdir()
        release.write_text('<html><head><script src="runtime-config.js"></script><script>window.PADSPLIT_RUNTIME_CONFIG; document.querySelector(\'meta[name="padsplit-data-mode"]\')</script></head><body>api-marker</body></html>')
        with patch('frontend_build.check_publication', return_value=[]):
            opts = dict(api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
            out = self.base / 'out'
            self.assertEqual(build(self.root, out, 'demo-private', [], **opts), [])
            self.assertIn('api-marker', (out / 'templates.html').read_text())
            self.assertNotIn('legacy-marker', (out / 'templates.html').read_text())
            release.write_text(release.read_text() + 'getFirestore(app);')
            failed = self.base / 'failed'
            self.assertTrue(any('Firestore' in e for e in build(self.root, failed, 'demo-private', [], **opts)))
            self.assertFalse(failed.exists())

    def test_reviewed_integrations_release_source_is_scanned_and_copied(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "integrations.html"]')
        (self.root / 'docs/integrations.html').write_text('getFirestore(app); legacy-marker')
        release = self.root / 'deploy/frontend-api/integrations.html'
        release.parent.mkdir()
        release.write_text('<html><head><script src="runtime-config.js"></script><script>window.PADSPLIT_RUNTIME_CONFIG; document.querySelector(\'meta[name="padsplit-data-mode"]\')</script></head><body>api-marker</body></html>')
        with patch('frontend_build.check_publication', return_value=[]):
            opts = dict(api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
            out = self.base / 'out'
            self.assertEqual(build(self.root, out, 'demo-private', [], **opts), [])
            self.assertIn('api-marker', (out / 'integrations.html').read_text())
            self.assertNotIn('legacy-marker', (out / 'integrations.html').read_text())
            release.write_text(release.read_text() + 'getFirestore(app);')
            failed = self.base / 'failed'
            self.assertTrue(any('Firestore' in e for e in build(self.root, failed, 'demo-private', [], **opts)))
            self.assertFalse(failed.exists())

    def test_generated_bytes_are_rescanned_before_artifact_is_accepted(self):
        original_sub = re.sub
        def inject(pattern, replacement, source, *args, **kwargs):
            return original_sub(pattern, replacement, source, *args, **kwargs) + ' getFirestore(app);'
        with patch('frontend_build.check_publication', return_value=[]), patch('frontend_build.re.sub', side_effect=inject):
            out = self.base / 'out'
            errors = build(self.root, out, 'demo-private', [], api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
            self.assertTrue(any('Firestore' in error for error in errors))
            self.assertFalse(out.exists())

    def test_release_source_mapping_cannot_escape_or_collide(self):
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "vendors.html"]')
        with patch('frontend_build.RELEASE_SOURCES', {'vendors.html':Path('../../private.html')}):
            with self.assertRaises(ValueError): check_release_sources(self.root, 'demo-private')
        (self.root / 'deploy/frontend-assets.json').write_text('["index.html", "index.html"]')
        with self.assertRaises(ValueError): check_release_sources(self.root, 'demo-private')

    def test_generated_private_content_and_unsafe_snapshot_are_rejected(self):
        original_copy = shutil.copyfile
        for kind in ('secret', 'snapshot'):
            def inject(src, dst, **kwargs):
                result = original_copy(src, dst, **kwargs)
                if str(dst).endswith('data/latest.json'):
                    Path(dst).write_text('fictional-private-value' if kind == 'secret' else '{}')
                return result
            with patch('frontend_build.check_publication', return_value=[]), patch('frontend_build.shutil.copyfile', side_effect=inject):
                out = self.base / kind
                errors = build(self.root, out, 'demo-private', ['fictional-private-value'], api_origin='https://api.example.com', environment='production', firebase_web_config=self.web_config)
                self.assertTrue(errors)
                self.assertFalse(out.exists())

if __name__ == '__main__': unittest.main()
