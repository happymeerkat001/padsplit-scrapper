"""Build the deliberately minimal, public GitHub Pages snapshot.

Raw scraper output includes tenant conversations, codes and financial fields.
None of that data belongs in a public static file. Unknown fields and free text
are never copied. This projection retains aggregate task counts and run health.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

_BUCKETS = ('Requests', 'Open', 'Eviction', 'Other')
_STATES = {'ok', 'degraded', 'failed'}
_MODES = {'full', 'messages_only'}


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError('scraped_at must be an ISO 8601 timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('scraped_at must be an ISO 8601 timestamp') from exc
    if parsed.tzinfo is None:
        raise ValueError('scraped_at must include a timezone')
    return value


def make_public_snapshot(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError('snapshot must be an object')
    scraped_at = _timestamp(raw.get('scraped_at'))
    tasks = raw.get('tasks', {})
    if not isinstance(tasks, dict):
        raise ValueError('tasks must be an object')
    counts = {}
    for bucket in _BUCKETS:
        if bucket in tasks:
            if not isinstance(tasks[bucket], list):
                raise ValueError('task bucket must be a list')
            counts[bucket] = len(tasks[bucket])
    result: dict[str, Any] = {
        'scraped_at': scraped_at,
        'messages': [],
        'tasks': {},
        'task_counts': counts,
        'private_content_omitted': True,
        'run_status': {},
    }
    status = raw.get('run_status')
    if isinstance(status, dict):
        if status.get('state') in _STATES:
            result['run_status']['state'] = status['state']
        if status.get('mode') in _MODES:
            result['run_status']['mode'] = status['mode']
        for key in ('run_scraped_at', 'last_complete_success'):
            if status.get(key) is not None:
                try:
                    result['run_status'][key] = _timestamp(status[key])
                except ValueError:
                    pass
        if isinstance(status.get('fallback_used'), bool):
            result['run_status']['fallback_used'] = status['fallback_used']
        raw_sources = status.get('sources')
        if isinstance(raw_sources, dict):
            sources = {}
            for name in ('messages', 'stats', 'occupancy'):
                raw_source = raw_sources.get(name)
                if not isinstance(raw_source, dict):
                    continue
                source = {}
                if raw_source.get('state') in _STATES | {'skipped'}:
                    source['state'] = raw_source['state']
                if raw_source.get('scraped_at') is not None:
                    try:
                        source['scraped_at'] = _timestamp(raw_source['scraped_at'])
                    except ValueError:
                        pass
                if isinstance(raw_source.get('fallback_used'), bool):
                    source['fallback_used'] = raw_source['fallback_used']
                if source:
                    sources[name] = source
            if sources:
                result['run_status']['sources'] = sources
    return result


def make_public_occupancy(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get('rooms'), list):
        raise ValueError('occupancy must contain a rooms list')
    if any(not isinstance(row, dict) for row in raw['rooms']):
        raise ValueError('occupancy row must be an object')
    # A room address, presence flag, event date or even ticket ID may reveal
    # information about a tenant.  Keep the public compatibility shape while
    # withholding all records; only an authenticated private source may serve them.
    return {'scraped_at': _timestamp(raw.get('scraped_at')),
            'derived_from': ['messages', 'tasks'], 'rooms': [],
            'private_content_omitted': True}


def write_public_snapshot(source: Path, destination: Path, *, occupancy: bool = False) -> None:
    raw = json.loads(source.read_text(encoding='utf-8'))
    payload = make_public_occupancy(raw) if occupancy else make_public_snapshot(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.public-snapshot-', delete=False) as f:
            tmp = Path(f.name)
            json.dump(payload, f, indent=2)
            f.write('\n')
        os.replace(tmp, destination)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description='Project private scraper output for public Pages')
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--occupancy', action='store_true', help='Project an occupancy snapshot')
    args = parser.parse_args()
    write_public_snapshot(args.source, args.destination, occupancy=args.occupancy)


if __name__ == '__main__':
    main()
