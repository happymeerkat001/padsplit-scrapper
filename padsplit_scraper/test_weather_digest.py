import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import slack_task_digest as digest


class WeatherDigestTests(unittest.TestCase):
    def test_disabled_has_no_provider_call(self):
        with patch.object(digest, 'datetime') as clock, patch.object(digest, 'load_weather') as fetch, patch.dict(digest.os.environ, {}, clear=True):
            clock.now.return_value.hour = 6
            self.assertIsNone(digest.fetch_weather())
            fetch.assert_not_called()

    def test_provider_errors_are_sanitized(self):
        with patch.object(digest, 'datetime') as clock, patch.object(digest, 'weather_configured', return_value=object()), patch.object(digest, 'load_weather', side_effect=RuntimeError('private location')), redirect_stdout(io.StringIO()) as output:
            clock.now.return_value.hour = 6
            self.assertIsNone(digest.fetch_weather())
            self.assertNotIn('private location', output.getvalue())


if __name__ == '__main__':
    unittest.main()
