"""Private operational configuration loads only explicitly available data."""
import json
import tempfile
import unittest
from pathlib import Path

from smarthome import clocks


class PrivateOperationalConfigTests(unittest.TestCase):
    def test_missing_alias_config_has_no_automated_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(clocks.load_aliases(Path(tmp) / 'missing.json'), ())

    def test_private_aliases_are_validated_and_converted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'aliases.json'
            path.write_text(json.dumps([[['sample','lane'], '99 sample lane', None]]))
            self.assertEqual(clocks.load_aliases(path), ((('sample','lane'), '99 sample lane', None),))
            path.write_text(json.dumps([[[], '99 sample lane', None]]))
            with self.assertRaises(ValueError): clocks.load_aliases(path)

    def test_house_profiles_fail_closed_and_validate(self):
        from padsplit_scraper.private_profiles import load_house_profiles
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.json'
            self.assertEqual(load_house_profiles(path), {})
            fixture = {'example_house': {'label': 'Example House', 'aliases': ['example house'],
                       'require_front': True, 'require_back': False, 'sifely_back': False,
                       'front_keys': ['front'], 'back_keys': [], 'expect_room_code': False}}
            path.write_text(json.dumps(fixture))
            self.assertEqual(load_house_profiles(path), fixture)
            fixture['example_house']['aliases'] = ['']
            path.write_text(json.dumps(fixture))
            with self.assertRaises(ValueError):
                load_house_profiles(path)

    def test_missing_house_profiles_do_not_match(self):
        from unittest.mock import patch
        from padsplit_scraper import lockout_reply
        with patch.object(lockout_reply, 'HOUSE_PROFILES', {}):
            self.assertFalse(lockout_reply.match_house('Example House').slug)

    def test_example_configs_load(self):
        from padsplit_scraper.private_profiles import load_house_profiles
        examples = Path(__file__).parent / 'examples/private-config'
        self.assertTrue(load_house_profiles(examples / 'lockout-house-profiles.json'))
        self.assertTrue(clocks.load_aliases(examples / 'smarthome-aliases.json'))

    def test_schedule_paths_default_to_private_directory(self):
        from thermostat import schedule
        self.assertIn('.private-config', str(schedule.SCHEDULES_PATH))
        self.assertIn('.private-config', str(clocks.SCHEDULES_PATH))
        self.assertIn('.private-config', str(clocks.CLOCKS_PATH))


if __name__ == '__main__': unittest.main()
