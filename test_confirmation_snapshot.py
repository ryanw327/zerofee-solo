"""Post-submit snapshot validation; historical proof and in-memory node only."""
import json
from pathlib import Path
import tempfile
import unittest

from journal import SubmissionJournal
from miner import Miner, SubmissionUncertain
from test_live import ADDRESS, HistoricalGenesis, MockLive


class ConfirmationSnapshotTests(unittest.TestCase):
    def submit(self, header):
        rpc = MockLive()
        rpc.tip = '00' * 32
        job = HistoricalGenesis()
        nonce = 2083236893
        digest = job.validate(nonce, True)
        original = rpc.call
        def call(method, *args):
            if method == 'getblockheader':
                return header(digest[::-1].hex())
            return original(method, *args)
        rpc.call = call
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.json'
            with SubmissionJournal(path) as journal:
                try:
                    result = Miner(rpc, ADDRESS, None, journal=journal).submit_solution(job, nonce, digest)
                except SubmissionUncertain as exc:
                    result = exc
            saved = json.loads(path.read_text())
        self.assertEqual(rpc.submissions, 1)
        return result, saved

    def test_malformed_confirmation_counts_never_become_chain_proof(self):
        for count in (True, False, 1.0, '1', None, [], {}, -2):
            with self.subTest(count=count):
                result, saved = self.submit(lambda digest: dict(hash=digest, confirmations=count))
                self.assertIsInstance(result, SubmissionUncertain)
                self.assertEqual(saved['status'], 'ACCEPTED')
                self.assertNotIn('active_chain', saved)

    def test_missing_confirmation_count_is_unknown_not_sidechain(self):
        result, saved = self.submit(lambda digest: dict(hash=digest))
        self.assertIsInstance(result, SubmissionUncertain)
        self.assertEqual(saved['status'], 'ACCEPTED')

    def test_wrong_identity_and_non_object_snapshot_remain_uncertain(self):
        for header in (None, [], {}, {'hash': 'ff' * 32, 'confirmations': 1}):
            with self.subTest(header=header):
                result, saved = self.submit(lambda digest: header)
                self.assertIsInstance(result, SubmissionUncertain)
                self.assertEqual(saved['status'], 'ACCEPTED')

    def test_integer_snapshots_preserve_active_and_inactive_distinction(self):
        for count in (-1, 0, 1, 100):
            with self.subTest(count=count):
                result, saved = self.submit(lambda digest: dict(hash=digest, confirmations=count))
                self.assertTrue(result['accepted'])
                self.assertEqual(result['active_chain'], count > 0)
                self.assertEqual(result['node_confirmations'], count)
                self.assertEqual(saved['status'], 'CONFIRMED' if count > 0 else 'ACCEPTED_SIDECHAIN')


if __name__ == '__main__':
    unittest.main()
