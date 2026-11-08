"""Fail closed on malformed submitblock result; all RPC calls are inert fakes."""
import tempfile
import unittest
from pathlib import Path
from bitcoin import sha256d, with_nonce
from block_builder import build_candidate
from journal import SubmissionJournal
from miner import Miner, SubmissionUncertain
from test_builder import ADDRESS, DummyRPC, fixture

class SubmissionResultTests(unittest.TestCase):
    def attempt(self, result, journal):
        class RPC(DummyRPC):
            def call(self, method, *args):
                if method == 'submitblock':
                    self.submissions += 1
                    return result
                if method == 'getblockheader':
                    self.header_reads += 1
                    return {'hash': args[0], 'confirmations': 1}
                return super().call(method, *args)
        rpc = RPC(); rpc.header_reads = 0
        miner = Miner(rpc, ADDRESS, None, journal=journal)
        job = build_candidate(fixture(), ADDRESS, 1)
        nonce = next(n for n in range(1000) if int.from_bytes(sha256d(with_nonce(job.header, n)), 'little') <= job.target)
        return rpc, lambda: miner.submit_solution(job, nonce, sha256d(with_nonce(job.header, nonce)))

    def test_malformed_results_keep_submission_unresolved(self):
        for result in (False, True, 0, 1, 1.0, [], {}, {'error': 'bad'}, ''):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'submission.json'
                with SubmissionJournal(path) as journal:
                    rpc, submit = self.attempt(result, journal)
                    with self.assertRaises(SubmissionUncertain): submit()
                    self.assertIn(journal.entry['status'], ('PENDING', 'UNCERTAIN'))
                    self.assertEqual(rpc.submissions, 1)
                    self.assertEqual(rpc.header_reads, 0)
                with self.assertRaisesRegex(RuntimeError, 'Resolve the previous submission'):
                    with SubmissionJournal(path): pass

    def test_nonempty_string_rejection_stays_rejected(self):
        with tempfile.TemporaryDirectory() as root, SubmissionJournal(Path(root) / 'submission.json') as journal:
            rpc, submit = self.attempt('bad-txnmrklroot', journal)
            outcome = submit()
            self.assertFalse(outcome['accepted'])
            self.assertEqual(journal.entry['status'], 'REJECTED')
            self.assertEqual(rpc.submissions, 1)
            self.assertEqual(rpc.header_reads, 0)

    def test_inconclusive_replies_keep_submission_unresolved(self):
        for result in ('inconclusive', 'duplicate-inconclusive'):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'submission.json'
                with SubmissionJournal(path) as journal:
                    rpc, submit = self.attempt(result, journal)
                    with self.assertRaises(SubmissionUncertain): submit()
                    self.assertEqual(journal.entry['status'], 'PENDING')
                    self.assertEqual(rpc.submissions, 1)
                    self.assertEqual(rpc.header_reads, 0)
                with self.assertRaisesRegex(RuntimeError, 'Resolve the previous submission'):
                    with SubmissionJournal(path): pass

    def test_null_result_is_accepted_and_confirmed(self):
        with tempfile.TemporaryDirectory() as root, SubmissionJournal(Path(root) / 'submission.json') as journal:
            rpc, submit = self.attempt(None, journal)
            self.assertTrue(submit()['accepted'])
            self.assertEqual(journal.entry['status'], 'CONFIRMED')
            self.assertEqual(rpc.submissions, 1)
            self.assertEqual(rpc.header_reads, 1)

    def test_malformed_without_journal_is_still_uncertain(self):
        rpc, submit = self.attempt(False, None)
        with self.assertRaises(SubmissionUncertain): submit()
        self.assertEqual(rpc.submissions, 1)
        self.assertEqual(rpc.header_reads, 0)
