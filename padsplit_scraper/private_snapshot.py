"""Explicit, opt-in Admin SDK publisher for an owner-only dashboard snapshot.

No connection is made on import. This module is intentionally not invoked by the
scraper or scheduled jobs. Callers must supply an initialized Admin client and arrange retention of
obsolete generations. The standalone command locks only this clone on this host. The pointer is only
replaced after every bounded chunk has been written successfully.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
import re
import uuid
from datetime import datetime
from typing import Any

DEFAULT_CHUNK_BYTES = 400_000
MAX_CHUNKS = 100


def _write_private_snapshot(db: Any, payload: dict[str, Any], *, enabled: bool = False,
                             chunk_bytes: int = DEFAULT_CHUNK_BYTES,
                             run_id: str | None = None) -> bool:
    """Publish messages/tasks and optional occupancy via versioned chunks. Returns False when disabled.

    Existing generations are not deleted. Never log payloads or credentials.
    """
    if not enabled:
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list) or not isinstance(payload.get('tasks'), dict):
        raise ValueError('messages and tasks must be present with expected types')
    if not all(isinstance(key, str) and isinstance(value, list) for key, value in payload['tasks'].items()):
        raise ValueError('task buckets must be arrays')
    if not all(isinstance(message, dict) for message in payload['messages']):
        raise ValueError('messages must contain objects')
    occupancy = payload.get('occupancy')
    if occupancy is not None:
        if (not isinstance(occupancy, dict) or not isinstance(occupancy.get('rooms'), list)
                or not all(isinstance(room, dict) for room in occupancy['rooms'])):
            raise ValueError('occupancy must contain an array of room objects')
        try:
            observed = datetime.fromisoformat(occupancy['scraped_at'].replace('Z', '+00:00'))
        except (KeyError, AttributeError, TypeError, ValueError):
            raise ValueError('occupancy scraped_at must be a timezone-aware ISO timestamp') from None
        if observed.tzinfo is None:
            raise ValueError('occupancy scraped_at must be a timezone-aware ISO timestamp')
    stamp = payload.get('scraped_at')
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00')) if isinstance(stamp, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError('scraped_at must be a timezone-aware ISO timestamp')
    if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or not 1 <= chunk_bytes <= DEFAULT_CHUNK_BYTES:
        raise ValueError('invalid chunk size')
    generation = run_id or uuid.uuid4().hex
    if not re.fullmatch(r'[0-9a-f]{32}', generation):
        raise ValueError('invalid snapshot identifier')
    # ensure_ascii makes character slicing identical to byte slicing, avoiding
    # partial UTF-8 sequences and keeping each Firestore string safely bounded.
    content = {'scraped_at': stamp, 'messages': payload['messages'], 'tasks': payload['tasks']}
    if occupancy is not None:
        content['occupancy'] = occupancy
    text = json.dumps(content, ensure_ascii=True, separators=(',', ':'))
    pieces = [text[i:i + chunk_bytes] for i in range(0, len(text), chunk_bytes)]
    if len(pieces) > MAX_CHUNKS:
        raise ValueError('private snapshot exceeds the maximum size')
    chunks = db.collection('private_dashboard_runs').document(generation).collection('chunks')
    for index, part in enumerate(pieces):
        chunks.document(str(index)).set({'index': index, 'data': part})
    db.collection('private_dashboard').document('current').set({
        'schema_version': 1, 'run_id': generation, 'chunk_count': len(pieces), 'scraped_at': stamp,
    })
    return True


def run_publisher(loader, client_factory, *, environ=None, policy=None, lock_directory=None) -> bool:
    """Publish only with explicit opt-in and a lock for this clone on this host.

    Collection-only, CI, an explicit immutable collection policy, and an unset
    enable flag fail closed before reading the source or initializing Firebase.
    """
    from padsplit_scraper import process_lock, runtime
    env = os.environ if environ is None else environ
    if (runtime.flag_value(env.get('PADSPLIT_PRIVATE_SNAPSHOT_ENABLE')) is not True
            or runtime.flag_value(env.get('PADSPLIT_COLLECTION_ONLY')) is not False
            or runtime.running_in_ci(env)
            or (policy is not None and getattr(policy, 'collection_only', True))):
        return False
    lock_path = (Path(lock_directory) if lock_directory is not None else
                 Path(__file__).resolve().parent.parent / '.runtime-state' / 'private-snapshot-lock')
    with process_lock.acquire_lock('private-snapshot', directory=lock_path, timeout=0):
        payload = loader()
        client = client_factory()
        if client is None:
            raise RuntimeError('Firebase Admin credentials are not configured')
        if getattr(client, 'project', None) != 'padsplit-scrapper':
            raise ValueError('Firebase project does not match the expected project')
        return _write_private_snapshot(client, payload, enabled=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Explicitly publish a private dashboard snapshot')
    parser.add_argument('--source', required=True, type=Path, help='Path to private scraper JSON')
    parser.add_argument('--occupancy-source', type=Path, help='Optional path to private occupancy JSON')
    args = parser.parse_args(argv)
    from padsplit_scraper.persist import _firestore_client_or_none
    def load_source():
        if args.source.stat().st_size > MAX_CHUNKS * DEFAULT_CHUNK_BYTES:
            raise ValueError('input is too large')
        with args.source.open(encoding='utf-8') as source:
            payload = json.load(source)
        if args.occupancy_source is not None:
            if args.occupancy_source.stat().st_size > MAX_CHUNKS * DEFAULT_CHUNK_BYTES:
                raise ValueError('occupancy input is too large')
            with args.occupancy_source.open(encoding='utf-8') as source:
                payload['occupancy'] = json.load(source)
        return payload
    try:
        published = run_publisher(load_source, _firestore_client_or_none)
    except Exception as exc:
        # Do not include exception text: provider errors can contain private data.
        print(f'Private snapshot publication failed ({type(exc).__name__}).', file=sys.stderr)
        return 1
    if not published:
        print('Private snapshot publication disabled by runtime gates.', file=sys.stderr)
        return 1
    print('Private snapshot published.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
