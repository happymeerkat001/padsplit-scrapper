"""Verify the API image's small explicitly allowed storage context."""
import ast
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class ImageContextTests(unittest.TestCase):
    def test_only_required_storage_files_are_included(self):
        dockerfile = (ROOT / 'deploy/api.Dockerfile').read_text()
        ignore = (ROOT / 'deploy/api.Dockerfile.dockerignore').read_text().splitlines()
        self.assertEqual(ignore[0], '**')
        storage = [line for line in ignore if line.startswith('!backend/storage')]
        self.assertEqual(storage, ['!backend/storage/', '!backend/storage/__init__.py', '!backend/storage/secrets.py'])
        self.assertIn('COPY backend/storage/__init__.py backend/storage/secrets.py /app/backend/storage/', dockerfile)
        self.assertNotIn('COPY backend/storage/ /app/', dockerfile)
        self.assertNotIn('!backend/storage/migrations/', ignore)
        self.assertNotIn('!.env', ignore)
        for name in ('__init__.py', 'secrets.py'):
            self.assertTrue((ROOT / 'backend/storage' / name).is_file())

    def test_scraper_modules_are_explicitly_included(self):
        dockerfile = (ROOT / 'deploy/api.Dockerfile').read_text()
        ignore = (ROOT / 'deploy/api.Dockerfile.dockerignore').read_text().splitlines()
        modules = ('weather', 'inbox_display', 'attachment_metadata', 'private_codes_schema')
        self.assertEqual([line for line in ignore if line.startswith('!padsplit_scraper')],
                         ['!padsplit_scraper/'] + [f'!padsplit_scraper/{name}.py' for name in modules])
        self.assertIn('COPY ' + ' '.join(f'padsplit_scraper/{name}.py' for name in modules) + ' /app/padsplit_scraper/', dockerfile)

    def test_internal_imports_have_explicit_image_allowlist(self):
        allowed = {'backend.storage', 'backend.storage.secrets', 'padsplit_scraper.weather',
                   'padsplit_scraper.inbox_display', 'padsplit_scraper.attachment_metadata',
                   'padsplit_scraper.private_codes_schema'}
        for source in (ROOT / 'backend/api').glob('*.py'):
            if source.name.startswith('test_'): continue
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(node.level, f'{source}: relative imports require review')
                    module = node.module or ''
                    if module == 'padsplit_scraper':
                        for alias in node.names:
                            self.assertIn(module + '.' + alias.name, allowed, f'{source}: missing image dependency')
                    elif module.startswith('backend.api.'):
                        self.assertTrue((ROOT / (module.replace('.', '/') + '.py')).is_file(), f'{source}: missing API module')
                    elif module.startswith(('backend.', 'padsplit_scraper.')):
                        self.assertIn(module, allowed, f'{source}: missing image dependency')
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module = alias.name
                        if module.startswith('backend.api.'):
                            self.assertTrue((ROOT / (module.replace('.', '/') + '.py')).is_file(), f'{source}: missing API module')
                        elif module.startswith(('backend.', 'padsplit_scraper.')):
                            self.assertIn(module, allowed, f'{source}: missing image dependency')

    def test_isolated_api_import_and_lazy_codes_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'backend/storage').mkdir(parents=True)
            (root / 'padsplit_scraper').mkdir()
            shutil.copytree(ROOT / 'backend/api', root / 'backend/api', ignore=shutil.ignore_patterns('test_*', '__pycache__'))
            for name in ('__init__.py', 'secrets.py'):
                shutil.copy(ROOT / 'backend/storage' / name, root / 'backend/storage' / name)
            for name in ('weather', 'inbox_display', 'attachment_metadata', 'private_codes_schema'):
                shutil.copy(ROOT / 'padsplit_scraper' / (name + '.py'), root / 'padsplit_scraper' / (name + '.py'))
            script = """
import sys
sys.path.insert(0, sys.argv[1])
from backend.api.app import create_app
from backend.api.repository import Repository
create_app(repository=object(), verify_token=lambda token: token, allowed_origins=['https://example.invalid'])
class Stop(Exception): pass
repo = object.__new__(Repository)
repo.connect = lambda: (_ for _ in ()).throw(Stop())
try: repo.codes_schema('fixture')
except Stop: pass
else: raise AssertionError('codes schema did not reach connection')
import padsplit_scraper.private_codes_schema as schema
assert schema.__file__.startswith(sys.argv[1])
"""
            result = subprocess.run([sys.executable, '-I', '-c', script, str(root)], cwd=root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_encryption_module_has_no_other_internal_dependencies(self):
        tree = ast.parse((ROOT / 'backend/storage/secrets.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(node.level, 'relative imports require an explicit context review')
                self.assertFalse((node.module or '').startswith('backend.'), 'new internal dependency requires context review')
            if isinstance(node, ast.Import):
                self.assertTrue(all(not item.name.startswith('backend.') for item in node.names))

if __name__ == '__main__': unittest.main()
