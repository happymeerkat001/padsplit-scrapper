"""Fail closed on sensitive data in the public Pages publication surface.

This is a focused guard, not a general secret scanner or Git history scrubber.
An optional private blocklist adds exact values without displaying or storing them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

_SNAPSHOTS = ('docs/data/latest.json', 'padsplit_scraper/docs/data/latest.json')
_COUNTS = {'Requests', 'Open', 'Eviction', 'Other'}
_SOURCE_NAMES = {'messages', 'stats', 'occupancy'}
_STATES = {'ok', 'degraded', 'failed'}
_TOKENS = (
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b'),
    re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{20,}\b'),
    re.compile(r'\bsk_live_[A-Za-z0-9]{20,}\b'),
)


def _stamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None
    except ValueError:
        return False


def _status(value: object, *, source: bool = False) -> bool:
    if not isinstance(value, dict):
        return False
    allowed = {'state', 'scraped_at', 'fallback_used'} if source else {
        'state', 'mode', 'run_scraped_at', 'last_complete_success', 'fallback_used', 'sources'}
    if set(value) - allowed:
        return False
    if 'state' in value and value['state'] not in (_STATES | ({'skipped'} if source else set())):
        return False
    if 'mode' in value and value['mode'] not in {'full', 'messages_only'}:
        return False
    if 'fallback_used' in value and type(value['fallback_used']) is not bool:
        return False
    for key in ('scraped_at', 'run_scraped_at', 'last_complete_success'):
        if key in value and not _stamp(value[key]):
            return False
    if 'sources' in value:
        sources = value['sources']
        if not isinstance(sources, dict) or set(sources) - _SOURCE_NAMES:
            return False
        if not all(_status(s, source=True) for s in sources.values()):
            return False
    return True


def _snapshot(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        'scraped_at', 'messages', 'tasks', 'task_counts', 'private_content_omitted', 'run_status'}:
        return False
    counts = value['task_counts']
    return (_stamp(value['scraped_at']) and value['messages'] == [] and value['tasks'] == {}
            and value['private_content_omitted'] is True and isinstance(counts, dict)
            and not set(counts) - _COUNTS and all(type(n) is int and n >= 0 for n in counts.values())
            and _status(value['run_status']))


def _occupancy(value: object) -> bool:
    return (isinstance(value, dict) and set(value) == {'scraped_at', 'derived_from', 'rooms', 'private_content_omitted'}
            and _stamp(value['scraped_at']) and value['derived_from'] == ['messages', 'tasks']
            and value['rooms'] == [] and value['private_content_omitted'] is True)


def _thermostat(value: object) -> bool:
    return (isinstance(value, dict) and set(value) == {'scraped_at', 'locations', 'private_content_omitted'}
            and value['scraped_at'] is None and value['locations'] == [] and value['private_content_omitted'] is True)


def _ledger(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {'date', 'posted', 'source', 'posted_at'}:
        return False
    try:
        date.fromisoformat(value['date'])
    except (TypeError, ValueError):
        return False
    return type(value['posted']) is bool and value['source'] in {'scrape', 'cron'} and _stamp(value['posted_at'])


def _empty_code_defaults(text: str) -> bool:
    declarations = list(re.finditer(r'\bconst\s+DEFAULTS\s*=', text))
    return len(declarations) == 1 and bool(re.match(r'\s*\[\s*\]\s*;', text[declarations[0].end():]))


def _publication_files(root: Path) -> set[Path]:
    """Include tracked and nonignored untracked text, as well as public files.

    Ignored local outputs and credentials are intentionally not inspected; they
    must not be staged. The Git index and history need separate checks.
    """
    paths = set((root / 'docs').rglob('*'))
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '--others',
                             '--exclude-standard', '-z'], check=True, capture_output=True)
    paths.update(root / os.fsdecode(name) for name in result.stdout.split(b'\0') if name)
    return paths


def check_publication(root: Path, known_private_values: Iterable[str] = (), *, check_index: bool = False) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    checks = {**{p: _snapshot for p in _SNAPSHOTS},
              'docs/data/occupancy.json': _occupancy, 'docs/thermostat/latest.json': _thermostat}
    ledger = 'docs/data/task_digest_ledger.json'
    if (root / ledger).exists():
        checks[ledger] = _ledger
    for name, validate in checks.items():
        try:
            value = json.loads((root / name).read_text(encoding='utf-8'))
            if not validate(value):
                errors.append(f'{name}: unsafe or unexpected public JSON')
        except (OSError, ValueError, TypeError):
            errors.append(f'{name}: unreadable or invalid public JSON')
    try:
        if not _empty_code_defaults((root / 'docs/codes.html').read_text(encoding='utf-8')):
            errors.append('docs/codes.html: access defaults must all be literal empty strings')
    except (OSError, UnicodeError):
        errors.append('docs/codes.html: unreadable access defaults')

    private_values = tuple(v for v in known_private_values if isinstance(v, str) and len(v) >= 8)
    public_dir = root / 'docs'
    if not public_dir.is_dir():
        errors.append('docs: missing public directory')
        return errors
    try:
        paths = _publication_files(root)
        tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '-z'],
                                 check=True, capture_output=True).stdout.split(b'\0')
        for name in tracked:
            if not name:
                continue
            parts = Path(os.fsdecode(name)).parts
            if (parts[-1].startswith('.env') or parts[:2] in {('padsplit_scraper', 'output'), ('thermostat', 'output')}
                    or parts[0] in {'logs', '.runtime-state', '.private-config'}):
                errors.append('Git index contains a prohibited private path; publication blocked.')
                break
        if check_index:
            # Worktree scans only prove the staged bytes when there are no
            # unstaged or nonignored untracked files. Fail closed otherwise.
            changed = subprocess.run(['git', '-C', str(root), 'diff', '--name-only', '--no-ext-diff', '-z', '--'],
                                     check=True, capture_output=True).stdout
            untracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--others', '--exclude-standard', '-z'],
                                       check=True, capture_output=True).stdout
            if changed or untracked:
                errors.append('Git index differs from worktree or contains untracked files; stage and review before checking.')
    except (OSError, subprocess.CalledProcessError):
        errors.append('Git file enumeration failed; publication blocked.')
        return errors
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {
            '.html', '.js', '.css', '.json', '.md', '.txt', '.py', '.yml', '.yaml', '.sh', '.mjs', '.cjs', '.plist', '.svg', '.rules'}:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            errors.append(f'{path.relative_to(root)}: unreadable public text')
            continue
        if any(pattern.search(text) for pattern in _TOKENS):
            errors.append(f'{path.relative_to(root)}: sensitive credential pattern')
        if any(value in text for value in private_values):
            errors.append(f'{path.relative_to(root)}: private blocklist match')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description='Check public assets before publishing')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--private-blocklist', type=Path, default=os.getenv('PUBLICATION_BLOCKLIST_PATH'),
                        help='Optional private UTF-8 file containing one sensitive value per line')
    parser.add_argument('--check-index', action='store_true',
                        help='Require the index and worktree to match with no nonignored untracked files')
    args = parser.parse_args()
    values: list[str] = []
    if args.private_blocklist:
        try:
            values = Path(args.private_blocklist).read_text(encoding='utf-8').splitlines()
        except OSError:
            print('Private blocklist could not be read; publication blocked.')
            return 1
    errors = check_publication(args.root, values, check_index=args.check_index)
    if errors:
        for error in errors:
            print(error)
        return 1
    print('Public asset checks passed; Git history and unknown secrets require separate review.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
