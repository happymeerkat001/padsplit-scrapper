"""Ensure scheduled publication never stages raw thermostat readings."""
from pathlib import Path
import unittest


class PublicationGuardTests(unittest.TestCase):
    def test_workflows_do_not_publish_raw_thermostat_snapshots(self):
        workflow = Path('.github/workflows/thermostat.yml').read_text()
        self.assertNotIn('git add thermostat/output/', workflow)
        self.assertNotIn('cp thermostat/output/latest.json docs/thermostat/latest.json', workflow)
        for name in ('run_morning.sh', 'run_afternoon.sh'):
            self.assertNotIn('docs/thermostat/latest.json', Path(name).read_text())


if __name__ == '__main__':
    unittest.main()
