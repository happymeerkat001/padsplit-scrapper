import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock
from padsplit_scraper.private_codes_schema import run_publisher, validate_schema

class PrivateCodesSchemaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent / 'tests/fixtures/codes-schema.json').read_text())
        self.env = {'PADSPLIT_PRIVATE_SCHEMA_ENABLE':'1', 'PADSPLIT_COLLECTION_ONLY':'0'}

    def test_gates_prevent_loading_or_connecting(self):
        for env in ({}, {'PADSPLIT_PRIVATE_SCHEMA_ENABLE':'1'}, {**self.env, 'CI':'true'}):
            loader, factory = Mock(), Mock()
            self.assertFalse(run_publisher(loader, factory, environ=env))
            loader.assert_not_called(); factory.assert_not_called()

    def test_create_only_correct_project(self):
        client = Mock(); client.project = 'padsplit-scrapper'
        self.assertTrue(run_publisher(lambda:self.fixture, lambda:client, environ=self.env))
        client.collection.assert_called_once_with('private_configuration')
        client.collection().document.assert_called_once_with('codes_schema')
        client.collection().document().create.assert_called_once_with(self.fixture)
        client.project = 'wrong-project'
        with self.assertRaises(ValueError): run_publisher(lambda:self.fixture, lambda:client, environ=self.env)

    def test_invalid_or_secret_values_rejected(self):
        bad = copy.deepcopy(self.fixture)
        bad['properties'][0]['sections'][0]['fields'][0]['value'] = 'fictional-secret'
        with self.assertRaises(ValueError): validate_schema(bad)
        bad = copy.deepcopy(self.fixture)
        bad['properties'].append(copy.deepcopy(bad['properties'][0]))
        with self.assertRaises(ValueError): validate_schema(bad)
