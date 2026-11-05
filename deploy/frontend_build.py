#!/usr/bin/env python3
"""Fail-closed Vercel static output builder; never deploys or modifies a project."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import ipaddress
from urllib.parse import urlsplit
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from padsplit_scraper.publication_guard import check_publication, _TOKENS, _snapshot, _occupancy, _thermostat

# Existing pages use relative paths. No documentation, private folders, caches,
# source files, or arbitrary generated artifacts are copied into the build.
TOP_LEVEL = {'.html', '.css', '.js', '.svg'}
SNAPSHOTS = ('data/latest.json', 'data/occupancy.json', 'thermostat/latest.json')
RELEASE_SOURCES = {'vendors.html': Path('deploy/frontend-api/vendors.html'),
                   'codes.html': Path('deploy/frontend-api/codes.html'),
                   'templates.html': Path('deploy/frontend-api/templates.html'),
                   'integrations.html': Path('deploy/frontend-api/integrations.html'),
                   'kpi-history.html': Path('deploy/frontend-api/kpi-history.html'),
                   'stats.html': Path('deploy/frontend-api/stats.html')}
DIRECT_STORAGE = re.compile(r'firebase-firestore\.js|firestore\.googleapis\.com|\bgetFirestore\s*\(', re.I)
PROJECT_ID = re.compile(r'\bprojectId\s*:\s*["\']([^"\']+)["\']')
LITERAL_FIREBASE_KEY = re.compile(r'\bapiKey\s*:\s*["\'][^"\']+["\']')


class AssetLinks(HTMLParser):
    def __init__(self): super().__init__(); self.paths = []
    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {'src', 'href'} and value:
                parsed = urlsplit(value)
                if not parsed.scheme and not parsed.netloc:
                    path = parsed.path.removeprefix('./').removeprefix('/')
                    if path and Path(path).suffix in TOP_LEVEL: self.paths.append(path)


def secure_api_origin(value: str) -> str:
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port
        if (url.scheme != 'https' or not host or url.username or url.password or
                url.path not in ('', '/') or url.query or url.fragment or
                host.lower() == 'localhost' or host.lower().endswith('.localhost')):
            raise ValueError
        try: address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            labels = host.lower().split('.')
            if (len(labels) < 2 or labels[-1] in {'local', 'internal', 'localhost'} or
                    any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
                raise ValueError
        if address is not None and not address.is_global: raise ValueError
        authority = f'[{host.lower()}]' if address is not None and address.version == 6 else host.lower()
        return f'https://{authority}' + (f':{port}' if port and port != 443 else '')
    except ValueError:
        raise ValueError('API origin must be a public HTTPS origin without path or credentials.') from None


def source_files(root: Path) -> list[tuple[Path, Path]]:
    docs = root / 'docs'
    names = json.loads((root / 'deploy/frontend-assets.json').read_text(encoding='utf-8'))
    if (not isinstance(names, list) or len(names) != len(set(names)) or
            any(not isinstance(name, str) or Path(name).name != name or name.startswith('.') or name == 'runtime-config.js'
                or Path(name).suffix not in TOP_LEVEL for name in names)):
        raise ValueError('Invalid frontend asset manifest')
    for target, source in RELEASE_SOURCES.items():
        if (not isinstance(target, str) or Path(target).name != target or
                not isinstance(source, Path) or source.is_absolute() or '..' in source.parts or
                source.parts[:2] != ('deploy', 'frontend-api') or len(source.parts) != 3):
            raise ValueError('Invalid release source mapping')
    files = [(Path(name), root / RELEASE_SOURCES[name] if name in RELEASE_SOURCES else docs / name) for name in names]
    return files + [(Path(name), docs / name) for name in SNAPSHOTS]


def check_release_sources(root: Path, firebase_project: str, output_root: Path | None = None) -> list[str]:
    errors = []
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', firebase_project):
        return ['A valid explicitly selected Firebase project ID is required.']
    paths = source_files(root)
    allowed = {str(destination) for destination, _ in paths} | {'runtime-config.js'}
    for destination, path in paths:
        if output_root is not None: path = output_root / destination
        anchor = output_root if output_root is not None else root
        rel = path.relative_to(anchor)
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != anchor and anchor in parent.parents) or not path.is_file():
            errors.append(f'{rel}: missing, non-file, or symbolic link'); continue
        try: text = path.read_text(encoding='utf-8')
        except (UnicodeError, OSError): errors.append(f'{rel}: unreadable'); continue
        if path.suffix in {'.html', '.js'}:
            if DIRECT_STORAGE.search(text): errors.append(f'{rel}: direct Firestore access is not migrated to the backend API')
            if any(project != firebase_project for project in PROJECT_ID.findall(text)):
                errors.append(f'{rel}: Firebase project does not match this environment')
            if LITERAL_FIREBASE_KEY.search(text):
                errors.append(f'{rel}: hardcoded Firebase web configuration must be environment-specific')
            if path.suffix == '.html' and ('runtime-config.js' not in text or 'PADSPLIT_RUNTIME_CONFIG' not in text or 'padsplit-data-mode' not in text):
                errors.append(f'{rel}: deployment runtime configuration is not consumed')
            if path.suffix == '.html' and len(re.findall(r'<head\b[^>]*>', text, flags=re.I)) != 1:
                errors.append(f'{rel}: expected exactly one head element for the API mode marker')
            if path.suffix == '.html':
                links = AssetLinks(); links.feed(text)
                if any(target not in allowed for target in links.paths):
                    errors.append(f'{rel}: a linked local asset is missing from the allowlist')
    return errors


def valid_web_config(config: object, project: str) -> bool:
    return (isinstance(config, dict) and set(config) == {'apiKey', 'authDomain', 'projectId'}
            and config.get('projectId') == project
            and config.get('authDomain') == project + '.firebaseapp.com'
            and isinstance(config.get('apiKey'), str)
            and bool(re.fullmatch(r'AIza[A-Za-z0-9_-]{35}', config['apiKey'])))


def build(root: Path, output: Path, firebase_project: str, values: list[str], *, api_origin: str,
          environment: str, firebase_web_config: dict, production_api_origin: str | None = None,
          production_firebase_project: str | None = None,
          production_firebase_web_config: dict | None = None, team_enabled: bool = False) -> list[str]:
    errors = check_publication(root, values) + check_release_sources(root, firebase_project)
    if type(team_enabled) is not bool:
        errors.append('Team feature flag must be a boolean.')
    if not valid_web_config(firebase_web_config, firebase_project):
        errors.append('Firebase browser configuration must contain only the selected project, its default auth domain and a valid public web API key.')
    try:
        api_origin = secure_api_origin(api_origin)
        if environment not in ('preview', 'production'): raise ValueError('Invalid environment.')
        if environment == 'preview':
            if not production_api_origin or not production_firebase_project or not valid_web_config(production_firebase_web_config, production_firebase_project):
                raise ValueError('Preview requires known production targets and browser configuration to verify isolation.')
            if api_origin == secure_api_origin(production_api_origin) or firebase_project == production_firebase_project:
                raise ValueError('Preview API and Firebase project must differ from production.')
            if valid_web_config(firebase_web_config, firebase_project) and firebase_web_config['apiKey'] == production_firebase_web_config['apiKey']:
                raise ValueError('Preview and production Firebase web keys must differ.')
    except ValueError as exc: errors.append(str(exc))
    try: resolved = output.resolve()
    except OSError: return errors + ['Output location cannot be resolved.']
    if resolved == root or root in resolved.parents:
        errors.append('Build output must be outside the repository.')
    if output.exists() or output.is_symlink(): errors.append('Build output already exists.')
    if errors: return errors
    output.mkdir(parents=True, mode=0o700)
    try:
        for destination, path in source_files(root):
            dest = output / destination
            dest.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == '.html':
                source = path.read_text(encoding='utf-8')
                marked = re.sub(r'(<head\b[^>]*>)', r'\1\n<meta name="padsplit-data-mode" content="api">', source, count=1, flags=re.I)
                dest.write_text(marked, encoding='utf-8')
            else:
                shutil.copyfile(path, dest, follow_symlinks=False)
        config = {'dataMode':'api', 'apiOrigin':api_origin, 'firebase':firebase_web_config, 'environment':environment}
        if team_enabled: config['teamEnabled'] = True
        (output / 'runtime-config.js').write_text('window.PADSPLIT_RUNTIME_CONFIG = Object.freeze(' + json.dumps(config, separators=(',', ':')) + ');\n', encoding='utf-8')
        vercel_config = root / 'deploy/vercel.json'
        if vercel_config.is_symlink() or not vercel_config.is_file():
            raise ValueError('Vercel configuration is missing or unsafe')
        if json.loads(vercel_config.read_text(encoding='utf-8')).get('version') != 2:
            raise ValueError('Invalid Vercel configuration')
        shutil.copyfile(vercel_config, output / 'vercel.json', follow_symlinks=False)
        # Re-scan exactly what was written. Source files may change between the
        # first validation and copying; no incomplete or unsafe artifact survives.
        emitted_errors = check_release_sources(root, firebase_project, output_root=output)
        private_values = tuple(v for v in values if isinstance(v, str) and len(v) >= 8)
        for emitted in output.rglob('*'):
            if not emitted.is_file(): continue
            contents = emitted.read_text(encoding='utf-8')
            if any(pattern.search(contents) for pattern in _TOKENS) or any(v in contents for v in private_values):
                emitted_errors.append(f'{emitted.relative_to(output)}: private content detected')
        for name, validator in (('data/latest.json', _snapshot), ('data/occupancy.json', _occupancy), ('thermostat/latest.json', _thermostat)):
            try:
                if not validator(json.loads((output / name).read_text(encoding='utf-8'))):
                    emitted_errors.append(f'{name}: unsafe or unexpected public JSON')
            except (OSError, ValueError, TypeError):
                emitted_errors.append(f'{name}: unreadable or invalid public JSON')
        if emitted_errors:
            shutil.rmtree(output)
            return emitted_errors
    except Exception:
        shutil.rmtree(output)
        raise
    return []


def main():
    parser = argparse.ArgumentParser(description='Build release assets only after privacy and migration gates pass')
    parser.add_argument('--output', type=Path, required=True, help='New path outside checkout')
    parser.add_argument('--firebase-project', required=True, help='Firebase project ID for this environment')
    parser.add_argument('--firebase-web-config', type=Path, required=True, help='Local JSON containing only public Firebase web config')
    parser.add_argument('--environment', choices=('preview', 'production'), required=True)
    parser.add_argument('--api-origin', required=True, help='Public HTTPS API origin; no credentials or URL path')
    parser.add_argument('--production-api-origin', help='Required for preview isolation checks')
    parser.add_argument('--production-firebase-project', help='Required for preview isolation checks')
    parser.add_argument('--production-firebase-web-config', type=Path, help='Public production web config; required for preview isolation checks')
    parser.add_argument('--enable-team', action='store_true', help='Explicitly expose the team UI after the separately gated API and roles are configured')
    parser.add_argument('--private-blocklist', type=Path, required=True, help='Private list of known sensitive values, outside checkout')
    args = parser.parse_args()
    blocklist = args.private_blocklist.resolve()
    if ROOT == blocklist or ROOT in blocklist.parents or not blocklist.is_file():
        parser.error('Private blocklist must be an existing file outside the repository.')
    values = blocklist.read_text(encoding='utf-8').splitlines()
    try: web_config = json.loads(args.firebase_web_config.read_text(encoding='utf-8'))
    except (OSError, ValueError): parser.error('Firebase web configuration could not be read.')
    try: production_web_config = json.loads(args.production_firebase_web_config.read_text(encoding='utf-8')) if args.production_firebase_web_config else None
    except (OSError, ValueError): parser.error('Production Firebase web configuration could not be read.')
    errors = build(ROOT, args.output, args.firebase_project, values, api_origin=args.api_origin,
                   environment=args.environment, firebase_web_config=web_config, production_api_origin=args.production_api_origin,
                   production_firebase_project=args.production_firebase_project, production_firebase_web_config=production_web_config,
                   team_enabled=args.enable_team)
    if errors:
        for error in errors: print(error, file=sys.stderr)
        return 1
    print('Release assets prepared; no deployment performed.')
    return 0

if __name__ == '__main__': sys.exit(main())
