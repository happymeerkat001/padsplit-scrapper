"""Do not automatically replay non-idempotent provider actions."""
import unittest
from unittest.mock import Mock

from padsplit_scraper.scraper import create_session, login


class ScraperRetrySafetyTests(unittest.TestCase):
    def test_login_never_follows_redirect_with_credentials(self):
        session = Mock()
        session.post.return_value.status_code = 307
        with self.assertRaises(RuntimeError):
            login(session, 'fixture@example.invalid', 'fixture-password')
        self.assertIs(session.post.call_args.kwargs.get('allow_redirects'), False)
    def test_server_errors_retry_get_but_not_mutating_methods(self):
        session = create_session()
        self.addCleanup(session.close)
        retry = session.get_adapter('https://example.invalid/').max_retries
        self.assertTrue(retry.is_retry('GET', 503))
        self.assertFalse(retry.is_retry('POST', 503))
        self.assertFalse(retry.is_retry('PATCH', 503))

    def test_login_error_does_not_echo_provider_response_body(self):
        session = Mock()
        session.post.return_value.status_code = 401
        session.post.return_value.text = 'private-response-sentinel'
        with self.assertRaises(RuntimeError) as result:
            login(session, 'fixture@example.invalid', 'fixture-password')
        self.assertIn('401', str(result.exception))
        self.assertNotIn('private-response-sentinel', str(result.exception))


if __name__ == '__main__':
    unittest.main()
