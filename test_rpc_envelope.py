"""Socket-free fail-closed JSON-RPC envelope tests; inert credentials only."""
import json
import unittest
from unittest.mock import Mock
from local_rpc import LocalRPC, RemoteRPCError


class EnvelopeTests(unittest.TestCase):
    def client(self, value, status=200):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = status
        response.read.return_value = json.dumps(value).encode()
        factory = Mock(return_value=connection)
        client = LocalRPC(19543, credential_provider=lambda: '__cookie__:inert',
                          connection_factory=factory)
        self.addCleanup(client.close)
        return client, connection

    def test_valid_null_result_is_accepted_once(self):
        client, conn = self.client({'id': 1, 'result': None, 'error': None})
        self.assertIsNone(client.call('submitblock', '00'))
        conn.request.assert_called_once()

    def test_valid_jsonrpc2_result_without_error(self):
        client, _ = self.client({'jsonrpc': '2.0', 'id': 1, 'result': 'tip'})
        self.assertEqual(client.call('getbestblockhash'), 'tip')

    def test_declared_unread_response_bytes_prevent_null_acceptance(self):
        for remaining in (1,70):
            with self.subTest(remaining=remaining):
                client,conn=self.client({'id':1,'result':None,'error':None})
                conn.getresponse.return_value.length=remaining
                with self.assertRaises(RuntimeError):client.call('submitblock','00')
                conn.request.assert_called_once()
                self.assertIsNone(client.authorization)

    def test_id_must_be_exact_integer(self):
        for identifier in (True, 1.0, '1', None, 2):
            with self.subTest(identifier=identifier):
                client, conn = self.client({'id': identifier, 'result': None, 'error': None})
                with self.assertRaises(RuntimeError): client.call('submitblock', '00')
                conn.request.assert_called_once()
                self.assertIsNone(client.authorization)

    def test_malformed_nonnull_error_never_accepts_submission(self):
        for error in (False, 0, '', [], {}, {'code': True}, {'code': -28.0}, {'code': 'oops'}):
            with self.subTest(error=error):
                client, conn = self.client({'id': 1, 'result': None, 'error': error})
                with self.assertRaises(RuntimeError): client.call('submitblock', '00')
                conn.request.assert_called_once()
                self.assertIsNone(client.authorization)

    def test_real_error_code_is_preserved_not_private_message(self):
        client, conn = self.client({'id': 1, 'error': {'code': -28, 'message': 'secret marker'}}, 500)
        with self.assertRaises(RemoteRPCError) as error: client.call('getblockchaininfo')
        self.assertEqual(error.exception.code, -28)
        self.assertNotIn('secret marker', str(error.exception))
        conn.request.assert_called_once()

    def test_wrong_status_or_missing_result_never_accepts_submission(self):
        for value, status in (({'id': 1, 'result': None}, 500), ({'id': 1}, 200), ([], 200)):
            client, conn = self.client(value, status)
            with self.assertRaises(RuntimeError): client.call('submitblock', '00')
            conn.request.assert_called_once()

if __name__ == '__main__': unittest.main(verbosity=2)
