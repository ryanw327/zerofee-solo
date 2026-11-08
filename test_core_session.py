"""Discovery fails closed; test paths are metadata only, never credentials."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core_session import discover_session


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)

    def cookie(self, name):
        path = self.base / name / '.cookie'
        path.parent.mkdir()
        path.touch()
        return path

    def test_no_listener_explains_rpc_without_reading_credentials(self):
        with patch('endpoint.endpoints', return_value=[]), patch('endpoint.verify_endpoint') as verify:
            with self.assertRaisesRegex(ValueError, 'RPC off'):
                discover_session(r'C:\ExampleBitcoin', self.base)
        verify.assert_not_called()

    def test_wildcard_or_multiple_listeners_are_rejected(self):
        for rows in [[('0.0.0.0', 123, 2)], [('127.0.0.1', 123, 2), ('::1', 123, 2)]]:
            with patch('endpoint.endpoints', return_value=rows), self.assertRaisesRegex(ValueError, 'binding'):
                discover_session(r'C:\ExampleBitcoin', self.base)

    def test_missing_or_ambiguous_cookies_are_rejected(self):
        with patch('endpoint.endpoints', return_value=[('127.0.0.1', 123, 2)]):
            with self.assertRaisesRegex(ValueError, 'unique'):
                discover_session(r'C:\ExampleBitcoin', self.base)
            self.cookie('one'); self.cookie('two')
            with self.assertRaisesRegex(ValueError, 'unique'):
                discover_session(r'C:\ExampleBitcoin', self.base)

    def test_verification_is_required_and_cookie_contents_not_read(self):
        cookie = self.cookie('one')
        with patch('endpoint.endpoints', return_value=[('127.0.0.1', 123, 2)]), \
             patch('endpoint.verify_endpoint') as verify, patch.object(Path, 'read_text', side_effect=AssertionError('No credentials')):
            result = discover_session(r'C:\ExampleBitcoin', self.base)
        verify.assert_called_once_with(123, str(cookie), 19732, r'C:\ExampleBitcoin')
        self.assertEqual(result, dict(cookie=str(cookie), core_pid=123))

    def test_failed_endpoint_verification_is_not_used(self):
        self.cookie('one')
        with patch('endpoint.endpoints', return_value=[('127.0.0.1', 123, 2)]), \
             patch('endpoint.verify_endpoint', side_effect=ValueError('Wrong Core executable')):
            with self.assertRaisesRegex(ValueError, 'Wrong Core executable'):
                discover_session(r'C:\ExampleBitcoin', self.base)


if __name__ == '__main__': unittest.main()
