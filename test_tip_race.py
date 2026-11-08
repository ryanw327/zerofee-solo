"""Regress actual proposal/tip race with in-memory Core and fake GPU only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_vectors import ADDRESS
from journal import SubmissionJournal
from miner import Miner, StaleJob, InvalidCandidate, check_proposal
from test_live import MockLive, HistoricalGenesis
from test_mining_sessions import Clock, Stop, GPU


class ProposalRaceTests(unittest.TestCase):
    def test_only_known_old_parent_proposal_reply_is_retryable(self):
        check_proposal(None)
        with self.assertRaises(StaleJob):check_proposal('inconclusive-not-best-prevblk')
        for value in ['bad-cb-amount','bad-txnmrklroot','bad-prevblk','duplicate-invalid','inconclusive','unexpected']:
            with self.assertRaises(InvalidCandidate):check_proposal(value)
    def test_tip_changes_during_proposal_and_miner_rebuilds_without_stopping(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=GPU(clock,stop,seconds=.2,stop_at=12)
        original=rpc.call;proposals=[0];events=[];old=rpc.tip
        def call(method,*args):
            if method=='getblocktemplate' and args[0].get('mode')=='proposal':
                proposals[0]+=1
                if proposals[0]==2:
                    rpc.tip='ab'*32;rpc.template['previousblockhash']=rpc.tip
                    rpc.info['blocks']+=1;rpc.info['headers']+=1;rpc.template['height']+=1
                    return 'inconclusive-not-best-prevblk'
            return original(method,*args)
        rpc.call=call
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            path=Path(directory)/'journal.json'
            with SubmissionJournal(path) as journal:
                result=Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(None,None,
                    run_profile='session',on_work=events.append)
            self.assertFalse(path.exists())
        self.assertEqual(result['status'],'STOPPED')
        self.assertEqual(result['tip_changes'],1)
        self.assertEqual([event['previous'] for event in events],[old,'ab'*32])
        self.assertGreater(result['work_history'][-1]['hashes'],0)
        self.assertGreaterEqual(result['stale_jobs'],1)
        self.assertEqual(rpc.submissions,0)
    def test_solved_candidate_with_stale_parent_never_begins_submission(self):
        rpc=MockLive();rpc.tip='00'*32;rpc.proposal_rejection='inconclusive-not-best-prevblk'
        job=HistoricalGenesis();nonce=2083236893
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'journal.json'
            with SubmissionJournal(path) as journal:
                miner=Miner(rpc,ADDRESS,None,journal=journal)
                with self.assertRaises(StaleJob):miner.submit_solution(job,nonce,job.validate(nonce,True))
                self.assertEqual(journal.entry,{})
            self.assertFalse(path.exists())
        self.assertEqual(rpc.submissions,0)
    def test_unexpected_error_retains_exact_partial_counts_and_restores_deadline(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=GPU(clock,stop,seconds=.2)
        original=gpu.batch
        def batch(*args):
            if gpu.batches==1:raise InvalidCandidate('synthetic unrecoverable error')
            return original(*args)
        gpu.batch=batch
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                with self.assertRaises(InvalidCandidate) as raised:
                    Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(None,None,run_profile='session')
        partial=raised.exception.mining_result
        self.assertEqual((partial['status'],partial['hashes']),('ERROR',4096))
        self.assertEqual(sum(work['hashes'] for work in partial['work_history']),4096)
        self.assertIsNone(rpc.deadline)
    def test_repeated_stale_proposals_have_a_retry_limit_without_any_submission(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();original=rpc.call;count=[0]
        def call(method,*args):
            if method=='getblocktemplate' and args[0].get('mode')=='proposal':
                count[0]+=1;rpc.tip=f'{count[0]:064x}';rpc.template['previousblockhash']=rpc.tip
                rpc.info['blocks']+=1;rpc.info['headers']+=1;rpc.template['height']+=1
                return 'inconclusive-not-best-prevblk'
            return original(method,*args)
        rpc.call=call
        gpu=GPU(clock,stop)
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                with self.assertRaisesRegex(InvalidCandidate,'8 consecutive') as raised:
                    Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(None,None,run_profile='session')
        self.assertEqual(raised.exception.mining_result['stale_jobs'],8)
        self.assertEqual(gpu.batches,0)
        self.assertEqual(rpc.submissions,0)
    def test_repeated_same_tip_reply_surfaces_instead_of_hiding_parent_bug(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();rpc.proposal_rejection='inconclusive-not-best-prevblk'
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                with self.assertRaisesRegex(InvalidCandidate,'unchanged tip') as raised:
                    Miner(rpc,ADDRESS,GPU(clock,stop),journal=journal,stop_event=stop).run(None,None,run_profile='session')
        self.assertEqual(raised.exception.mining_result['stale_jobs'],2)
        self.assertEqual(rpc.submissions,0)
    def test_rebuilt_header_parent_is_checked_before_proposal(self):
        rpc=MockLive()
        from types import SimpleNamespace
        corrupt=SimpleNamespace(previous=rpc.tip,header=bytes(80))
        with tempfile.TemporaryDirectory() as directory,patch('miner.build_candidate',return_value=corrupt):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                with self.assertRaisesRegex(InvalidCandidate,'header parent'):
                    Miner(rpc,ADDRESS,None,journal=journal).new_job()
    def test_same_height_reorganization_switches_by_hash(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=GPU(clock,stop,seconds=.1,stop_at=5)
        original=gpu.batch
        def batch(*args):
            value=original(*args)
            if gpu.batches==1:rpc.tip='cd'*32;rpc.template['previousblockhash']=rpc.tip
            return value
        gpu.batch=batch
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                result=Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(None,None,run_profile='session')
        self.assertEqual(result['tip_changes'],1)
        self.assertEqual(result['work_history'][0]['height'],result['work_history'][1]['height'])
        self.assertNotEqual(result['work_history'][0]['previous'],result['work_history'][1]['previous'])
        self.assertGreater(result['work_history'][1]['hashes'],0)
        self.assertLess(clock.value,1)
    def test_stop_cancels_stale_proposal_retry_wait(self):
        clock=Clock();stop=Stop(clock,True);rpc=MockLive();rpc.proposal_rejection='inconclusive-not-best-prevblk'
        gpu=GPU(clock,stop)
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                result=Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(None,None,run_profile='session')
        self.assertEqual((result['status'],result['stale_jobs']),('STOPPED',1))
        self.assertEqual(gpu.batches,0)
        self.assertEqual(rpc.submissions,0)


if __name__=='__main__':unittest.main()
