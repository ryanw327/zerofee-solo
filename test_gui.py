from test_vectors import ADDRESS as TEST_PAYOUT
"""Safe controls/cancellation tests: in-memory Core and fake GPU only."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
from gui_runner import RunConfig, Runner
from test_vectors import ADDRESS
from journal import SubmissionJournal
from miner import Miner, MiningStopped, NodeNotReady, execute
from test_live import MockLive, HistoricalGenesis

class RunnerTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        root=patch('gui_runner.ROOT',Path(directory.name));root.start();self.addCleanup(root.stop)
        monitor=patch('gpu_monitor.GPUMonitor',return_value=Mock(reason=None))
        monitor.start();self.addCleanup(monitor.stop)
    def test_opens_idle_and_simulation_stop_has_no_mining_stats(self):
        runner = Runner(result_path=None)
        self.assertFalse(runner.busy)
        self.assertIsNone(runner.thread)
        with patch('miner.NodeRPC',side_effect=AssertionError('No RPC')), patch('miner.GPUHasher',side_effect=AssertionError('No GPU')):
            runner.start(RunConfig(seconds=2))
            self.assertTrue(runner.busy)
            with self.assertRaises(RuntimeError):runner.start(RunConfig())
            runner.stop()
            runner.thread.join(2)
        self.assertFalse(runner.busy)
        records = []
        while not runner.events.empty():records.append(runner.events.get_nowait())
        result = [v for k,v in records if k=='finished'][0]['result']
        self.assertEqual(result['status'],'STOPPED')
        self.assertIsNone(result['hashes'])
        self.assertIsNone(result['blocks'])

    def test_simulation_auto_completes_and_can_restart(self):
        runner = Runner(result_path=None)
        for _ in range(2):
            runner.start(RunConfig(seconds=.02))
            runner.thread.join(2)
            self.assertFalse(runner.busy)
        records = list(runner.events.queue)
        self.assertEqual(len([1 for k,v in records if k=='finished' and v['result']['status']=='COMPLETE']),2)

    def test_mainnet_needs_new_approval_and_does_not_touch_node(self):
        config = RunConfig(mode='mainnet',address=TEST_PAYOUT,cookie=r'C:\temporary\cookie',core_pid=123)
        runner = Runner(result_path=None)
        with patch('miner.NodeRPC',side_effect=AssertionError('No RPC')):
            with self.assertRaisesRegex(ValueError,'Approve'):runner.start(config)
        self.assertFalse(runner.busy)
        self.assertIsNone(runner.thread)

    def test_missing_session_and_invalid_limits_fail_closed(self):
        for config in [RunConfig(mode='mainnet',address=TEST_PAYOUT),RunConfig(seconds=float('nan')),RunConfig(seconds=61),
                       RunConfig(hashes=0),RunConfig(hashes=10_000_001),RunConfig(mode='other')]:
            with self.subTest(config=config):
                runner = Runner(result_path=None)
                with self.assertRaises(ValueError):runner.start(config,approved=True)
                self.assertFalse(runner.busy)

    def test_result_file_has_no_session_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'result.json'
            runner = Runner(result_path=path)
            runner.start(RunConfig(seconds=.01,cookie='never-save-me',core_pid=123))
            runner.thread.join(2)
            raw = path.read_text()
            self.assertNotIn('never-save-me',raw)
            self.assertNotIn('core_pid',raw)
            self.assertEqual(json.loads(raw)['mode'],'simulation')

class CancelTests(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'journal.json'
    def tearDown(self):self.temp.cleanup()

    def test_stop_before_first_batch_searches_nothing(self):
        rpc = MockLive()
        with SubmissionJournal(self.path) as journal:
            miner = Miner(rpc,ADDRESS,None,journal=journal,stop_event=self.stop)
            self.stop.set()
            result = miner.run()
        self.assertEqual(result['hashes'],0)
        self.assertEqual(result['status'],'STOPPED')
        self.assertEqual(rpc.submissions,0)
        self.assertIsNone(rpc.deadline)

    def test_stop_during_batch_does_not_submit_or_start_next_batch(self):
        rpc = MockLive()
        stop = self.stop
        class GPU:
            batches = 0
            def batch(self,header,nonce,count,target):
                self.batches+=1
                stop.set()
                return [bytes(32)]*count,[1]*count
        gpu = GPU()
        progress = []
        with SubmissionJournal(self.path) as journal:
            result = Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(max_hashes=8,batch_size=2,on_progress=progress.append)
        self.assertEqual((result['status'],result['hashes'],gpu.batches),('STOPPED',2,1))
        self.assertEqual(progress[0]['hashes'],2)
        self.assertEqual(rpc.submissions,0)
        self.assertFalse(self.path.exists())

    def test_stop_during_waiting_never_searches(self):
        rpc = MockLive()
        with SubmissionJournal(self.path) as journal:
            miner = Miner(rpc,ADDRESS,None,journal=journal,stop_event=self.stop)
            def blocked():
                self.stop.set()
                raise NodeNotReady('mock syncing')
            with patch.object(rpc,'guard',blocked):result = miner.run()
        self.assertEqual(result['status'],'STOPPED')
        self.assertEqual(result['hashes'],0)

    def published(self):
        rpc = MockLive()
        rpc.tip = '00'*32
        job = HistoricalGenesis()
        nonce = 2083236893
        return rpc,job,nonce,job.validate(nonce,True)

    def test_stop_at_final_pre_submit_check_creates_no_pending_record(self):
        rpc,job,nonce,digest = self.published()
        rpc.verify_session = self.stop.set
        with SubmissionJournal(self.path) as journal:
            with self.assertRaises(MiningStopped):
                Miner(rpc,ADDRESS,None,journal=journal,stop_event=self.stop).submit_solution(job,nonce,digest)
        self.assertEqual(rpc.submissions,0)
        self.assertFalse(self.path.exists())

    def test_stop_during_submission_finishes_confirmation_and_journal(self):
        rpc,job,nonce,digest = self.published()
        original = rpc.call
        def call(method,*args):
            if method=='submitblock':self.stop.set()
            return original(method,*args)
        rpc.call = call
        with SubmissionJournal(self.path) as journal:
            result = Miner(rpc,ADDRESS,None,journal=journal,stop_event=self.stop).submit_solution(job,nonce,digest)
        self.assertTrue(result['accepted'])
        self.assertEqual(rpc.submissions,1)
        self.assertEqual(json.loads(self.path.read_text())['status'],'CONFIRMED')
        self.assertFalse(Path(str(self.path)+'.lock').exists())

    def test_stop_during_initial_verification_does_not_create_gpu(self):
        args = SimpleNamespace(network='mainnet',datadir='unused',cookie='unused',session_pid=123,
                               mainnet_opt_in=True,submit_blocks=True,address=ADDRESS)
        rpc = MockLive()
        rpc.guard = lambda: self.stop.set() or rpc.info
        with patch('miner.NodeRPC',return_value=rpc),patch('miner.GPUHasher',side_effect=AssertionError('No GPU')):
            with self.assertRaises(MiningStopped):execute(args,19732,None,self.stop)

    def test_stop_during_gpu_startup_closes_context_without_search(self):
        args = SimpleNamespace(network='mainnet',datadir='unused',cookie='unused',session_pid=123,
            mainnet_opt_in=True,submit_blocks=True,address=ADDRESS,max_seconds=1,max_hashes=2,max_blocks=1)
        rpc = MockLive()
        stop = self.stop
        class GPU:
            closed = False
            def __enter__(self):
                stop.set()
                return self
            def __exit__(self,*args):self.closed = True
        gpu = GPU()
        with patch('miner.NodeRPC',return_value=rpc),patch('miner.GPUHasher',return_value=gpu):
            with self.assertRaises(MiningStopped):execute(args,19732,None,stop)
        self.assertTrue(gpu.closed)
        self.assertEqual(rpc.submissions,0)

if __name__=='__main__':unittest.main()
