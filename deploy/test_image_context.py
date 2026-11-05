"""Verify the API image's small explicitly allowed storage context."""
import ast
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

    def test_encryption_module_has_no_other_internal_dependencies(self):
        tree = ast.parse((ROOT / 'backend/storage/secrets.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(node.level, 'relative imports require an explicit context review')
                self.assertFalse((node.module or '').startswith('backend.'), 'new internal dependency requires context review')
            if isinstance(node, ast.Import):
                self.assertTrue(all(not item.name.startswith('backend.') for item in node.names))

if __name__ == '__main__': unittest.main()
