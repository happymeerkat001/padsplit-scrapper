"""Explicit create-only provisioning of the owner-only codes presentation schema."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import sys


def validate_schema(data):
    if not isinstance(data, dict) or set(data) != {'properties'} or not isinstance(data.get('properties'), list) or not 1 <= len(data['properties']) <= 100:
        raise ValueError('invalid schema')
    slugs = set()
    for prop in data['properties']:
        if not isinstance(prop, dict) or set(prop) - {'slug', 'address', 'sections', 'history_enabled'}:
            raise ValueError('invalid property')
        slug, address, sections = prop.get('slug'), prop.get('address'), prop.get('sections')
        if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9_-]{1,100}', slug) or slug in slugs:
            raise ValueError('invalid identifier')
        slugs.add(slug)
        if not isinstance(address, str) or not address.strip() or len(address) > 200 or not isinstance(sections, list) or not 1 <= len(sections) <= 30:
            raise ValueError('invalid property')
        if 'history_enabled' in prop and type(prop['history_enabled']) is not bool:
            raise ValueError('invalid history flag')
        keys = set()
        for section in sections:
            if not isinstance(section, dict) or set(section) - {'label', 'fields'} or not isinstance(section.get('label'), str) or not section['label'].strip() or len(section['label']) > 200 or not isinstance(section.get('fields'), list) or len(section['fields']) > 100:
                raise ValueError('invalid section')
            for field in section['fields']:
                if not isinstance(field, dict) or set(field) - {'key', 'label', 'value', 'placeholder', 'type'}:
                    raise ValueError('invalid field')
                key, label = field.get('key'), field.get('label')
                if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9_]{1,100}', key) or key in keys or not isinstance(label, str) or len(label) > 200 or field.get('value', '') != '':
                    raise ValueError('invalid field')
                keys.add(key)
                if 'placeholder' in field and (not isinstance(field['placeholder'], str) or len(field['placeholder']) > 200):
                    raise ValueError('invalid placeholder')
                if 'type' in field and field['type'] not in ('text', 'date', 'textarea'):
                    raise ValueError('invalid type')
    return data


def run_publisher(loader, client_factory, *, environ=None):
    from padsplit_scraper import runtime
    env = os.environ if environ is None else environ
    if (runtime.flag_value(env.get('PADSPLIT_PRIVATE_SCHEMA_ENABLE')) is not True
            or runtime.flag_value(env.get('PADSPLIT_COLLECTION_ONLY')) is not False
            or runtime.running_in_ci(env)):
        return False
    payload = validate_schema(loader())
    client = client_factory()
    if client is None or getattr(client, 'project', None) != 'padsplit-scrapper':
        raise ValueError('invalid Firebase project')
    # create is atomic and fails if a configuration already exists.
    client.collection('private_configuration').document('codes_schema').create(payload)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description='Create a private codes presentation schema')
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args(argv)
    from padsplit_scraper.persist import _firestore_client_or_none
    def load():
        if args.source.stat().st_size > 500_000:
            raise ValueError('input too large')
        return json.loads(args.source.read_text(encoding='utf-8'))
    try:
        published = run_publisher(load, _firestore_client_or_none)
    except Exception as exc:
        print(f'Private schema provisioning failed ({type(exc).__name__}).', file=sys.stderr)
        return 1
    if not published:
        print('Private schema provisioning disabled by runtime gates.', file=sys.stderr)
        return 1
    print('Private schema created.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
