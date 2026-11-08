"""Scheduling, nonce partitioning and safety of large-session dispatch; fake GPU/Core."""
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bitcoin import sha256d,with_nonce
from block_builder import build_candidate
from test_vectors import ADDRESS
from journal import SubmissionJournal
from miner import Miner,InvalidSolution,NodeNotReady
from run_limits import SESSION_BATCH_SIZE,validate_limits
from test_live import MockLive
from test_mining_sessions import Clock,Stop
from test_builder import ADDRESS as REG_ADDRESS,fixture

class SearchGPU:
    def __init__(self,clock,stop,stop_at=100,callback=None):
        self.clock,self.stop,self.stop_at,self.callback=clock,stop,stop_at,callback
        self.ranges=[]
    def batch(self,*args):raise AssertionError('Large session must use candidate-only transfer')
    def search_batch(self,header,first,count,target):
        self.ranges.append((header,first,count));self.clock.advance(.005)
        if self.callback:self.callback(len(self.ranges))
        if len(self.ranges)>=self.stop_at:self.stop.set()
        return None

class DispatchTests(unittest.TestCase):
    def test_old_watcher_packet_cannot_retire_newer_work(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=SearchGPU(clock,stop,stop_at=10)
        class Watch:
            def snapshot(self):return dict(generation=1,sample_started=-1,parent='ff'*32)
            def stats(self):return {}
        rpc.watcher=Watch()
        result=self.run_fake(gpu,clock,stop,rpc)
        self.assertEqual(result['stale_jobs'],0);self.assertEqual(result['jobs'],1)
    def test_fresh_watcher_packet_retires_old_parent_before_poll_cadence(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();observation=dict(generation=0,sample_started=0,parent=rpc.tip)
        class Watch:
            def snapshot(self):return dict(observation)
            def stats(self):return {}
        rpc.watcher=Watch()
        def callback(count):
            if count==2:
                rpc.tip='ef'*32;rpc.template['previousblockhash']=rpc.tip
                observation.update(generation=1,sample_started=clock.now(),parent=rpc.tip)
        gpu=SearchGPU(clock,stop,stop_at=10,callback=callback)
        result=self.run_fake(gpu,clock,stop,rpc)
        self.assertEqual(result['tip_changes'],1)
        self.assertLess(result['work_history'][1]['start_elapsed_seconds'],.1)
    def run_fake(self,gpu,clock,stop,rpc,seconds=None):
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                return Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(seconds,None,
                    batch_size=SESSION_BATCH_SIZE,run_profile='session')
    def test_large_batch_requires_session_and_old_test_cap_remains(self):
        validate_limits(20,None,profile='session',can_stop=True,batch_size=SESSION_BATCH_SIZE)
        with self.assertRaises(ValueError):validate_limits(20,100,batch_size=4097)
        with self.assertRaises(ValueError):validate_limits(20,None,profile='session',can_stop=True,batch_size=SESSION_BATCH_SIZE+1)
    def test_single_dispatcher_uses_unique_contiguous_nonce_ranges_and_fewer_rpc_calls(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=SearchGPU(clock,stop)
        result=self.run_fake(gpu,clock,stop,rpc)
        self.assertEqual(result['hashes'],100*SESSION_BATCH_SIZE)
        for previous,current in zip(gpu.ranges,gpu.ranges[1:]):
            self.assertEqual(previous[0],current[0])
            self.assertEqual(current[1],previous[1]+previous[2])
        self.assertLessEqual(rpc.calls['getbestblockhash'],5)
        self.assertLessEqual(rpc.calls['getnetworkinfo'],3)
        self.assertEqual((result['status'],rpc.submissions),('STOPPED',0))
    def test_same_height_tip_change_is_detected_by_time_without_waiting_for_job_refresh(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();changed=[False]
        def callback(count):
            if count==1:
                changed[0]=True;rpc.tip='ab'*32;rpc.template['previousblockhash']=rpc.tip
        gpu=SearchGPU(clock,stop,stop_at=50,callback=callback)
        result=self.run_fake(gpu,clock,stop,rpc)
        self.assertTrue(changed[0]);self.assertEqual(result['tip_changes'],1)
        self.assertEqual(result['work_history'][0]['height'],result['work_history'][1]['height'])
        self.assertLessEqual(result['work_history'][1]['start_elapsed_seconds'],.11)
        self.assertGreater(result['work_history'][1]['hashes'],0)
    def test_stop_during_batch_prevents_any_second_batch_or_submission(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=SearchGPU(clock,stop,stop_at=1)
        result=self.run_fake(gpu,clock,stop,rpc)
        self.assertEqual((len(gpu.ranges),result['hashes'],rpc.submissions),(1,SESSION_BATCH_SIZE,0))
    def test_bad_offset_or_digest_never_reaches_submission(self):
        for candidate in [(-1,bytes(32)),(SESSION_BATCH_SIZE,bytes(32)),(0,bytes(32))]:
            clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=SearchGPU(clock,stop)
            gpu.search_batch=lambda *args:candidate
            with self.assertRaises(InvalidSolution):self.run_fake(gpu,clock,stop,rpc,seconds=1)
            self.assertEqual(rpc.submissions,0)
    def test_guard_and_age_are_still_enforced_on_large_sessions(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=SearchGPU(clock,stop,stop_at=300)
        def callback(count):
            if count==1:rpc.info['warnings']=['synthetic warning']
        gpu.callback=callback
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                result=Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(1.3,None,
                    batch_size=SESSION_BATCH_SIZE,run_profile='session')
        self.assertGreater(result['stale_jobs'],0)
        self.assertLess(len(gpu.ranges),210)
        self.assertEqual(rpc.submissions,0)
    def test_sparse_candidate_keeps_cpu_proof_and_core_confirmation(self):
        # Easy fake regtest work exercises the same production submission path.
        clock=Clock();stop=Stop(clock);job=build_candidate(fixture(),REG_ADDRESS,100)
        class RPC:
            network='regtest';deadline=None
            def __init__(self):self.calls=Counter();self.submissions=0;self.solved=None
            def guard(self):return {'chain':'regtest'}
            def call(self,method,*args):
                self.calls[method]+=1
                if method=='getbestblockhash':return job.previous
                if method=='getblocktemplate':return None
                if method=='submitblock':
                    self.submissions+=1;self.solved=sha256d(bytes.fromhex(args[0])[:80])[::-1].hex();return None
                if method=='getblockheader':return {'hash':self.solved,'confirmations':1}
                raise AssertionError(method)
        rpc=RPC();gpu=SearchGPU(clock,stop)
        def search(header,first,count,target):
            return next((n,sha256d(with_nonce(header,n))) for n in range(64)
                        if int.from_bytes(sha256d(with_nonce(header,n)),'little')<=target)
        gpu.search_batch=search
        with patch('miner.time.monotonic',side_effect=clock.now):
            # Capture created under the same virtual clock used for freshness.
            job=build_candidate(fixture(),REG_ADDRESS,100)
            miner=Miner(rpc,REG_ADDRESS,gpu,stop_event=stop)
            miner.new_job=lambda:job
            result=miner.run(1,None,batch_size=SESSION_BATCH_SIZE,run_profile='session')
        self.assertEqual((len(result['blocks']),rpc.submissions),(1,1))
        self.assertTrue(result['blocks'][0]['active_chain'])

if __name__=='__main__':unittest.main()
