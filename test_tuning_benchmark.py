"""Synthetic benchmark flow with a fake clock, CPU oracle and fake telemetry."""
from dataclasses import replace
from pathlib import Path
from queue import Queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from advanced_settings import TuningSettings
from bitcoin import sha256d, with_nonce
from gui_runner import Runner
from tuning_benchmark import run_sweep, BenchmarkWorker

class Clock:
    def __init__(self):self.now=0
    def __call__(self):return self.now

class Stop:
    def __init__(self,clock):self.clock=clock;self.stopped=False;self.waits=[]
    def is_set(self):return self.stopped
    def set(self):self.stopped=True
    def wait(self,seconds):self.waits.append(seconds);self.clock.now+=seconds;return self.stopped

class FakeGPU:
    max_threads_per_block=512
    def __init__(self,clock,stop):self.clock=clock;self.stop=stop;self.closed=False;self.ranges=[];self.proofs=[];self.bad=False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def batch(self,header,start,count,target):
        self.proofs.append(self.threads_per_block)
        return ([bytes(32)]*count if self.bad else [sha256d(with_nonce(header,start+i)) for i in range(count)]),[1]*count
    def search_batch(self,header,start,count,target):
        if target:
            for i in range(count):
                digest=sha256d(with_nonce(header,start+i))
                if int.from_bytes(digest,'little')<=target:return (i,digest)
            return None
        self.ranges.append((self.threads_per_block,start,count,target));self.clock.now+=.5
        if hasattr(self,'callback'):self.callback()
        return None

class SweepTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.stop=Stop(self.clock);self.gpu=FakeGPU(self.clock,self.stop)
        self.monitor=Mock(reason=None);self.events=Queue();self.factory=Mock(return_value=self.gpu)
        self.monitor_factory=Mock(return_value=self.monitor);self.idle=Mock()
        self.settings=replace(TuningSettings(),pacing='Full - no batch pause')

    def run_fake(self,read=None):
        with patch('gpu_identity.verify_identity'),patch('miner.NodeRPC',side_effect=AssertionError('No Core access')):
            return run_sweep(self.settings,self.stop,self.events,gpu_factory=self.factory,monitor_factory=self.monitor_factory,
                             read=read or (lambda:dict(temperature_c=50)),clock=self.clock,idle_check=self.idle)

    def test_sixty_second_sweep_is_bounded_and_checks_all_three_shapes_against_cpu(self):
        result=self.run_fake()
        self.assertEqual(result['seconds'],60);self.assertEqual(result['status'],'COMPLETE')
        self.assertEqual(self.gpu.proofs,[128,256,512]);self.assertEqual(len(result['rows']),3)
        self.assertTrue(all(row['complete'] and row['mh_s']>0 for row in result['rows']))
        self.assertTrue(self.gpu.closed);self.monitor.start.assert_called_once();self.monitor.close.assert_called_once()
        self.assertEqual(set(row[3] for row in self.gpu.ranges),{0});self.assertIn('thermally',result['note'])

    def test_kernel_limit_skips_unsupported_shape_and_rebalances_sixty_seconds(self):
        self.gpu.max_threads_per_block=256;result=self.run_fake()
        self.assertEqual(self.gpu.proofs,[128,256]);self.assertEqual(result['skipped'][0]['threads_per_block'],512)
        self.assertEqual(result['seconds'],60)

    def test_cancel_after_one_batch_closes_gpu_and_does_not_launch_again(self):
        self.gpu.callback=self.stop.set;result=self.run_fake()
        self.assertEqual(len(self.gpu.ranges),1);self.assertEqual(result['status'],'STOPPED')
        self.assertFalse(result['rows'][0]['complete']);self.assertTrue(self.gpu.closed)

    def test_pause_is_interruptible_and_included_in_wall_rate(self):
        self.settings=replace(self.settings,pacing='Custom pause',custom_pause_ms=5000)
        result=self.run_fake();self.assertTrue(self.stop.waits);self.assertLessEqual(result['seconds'],60.5)
        self.assertEqual(result['pause_seconds'],5)
        self.assertLess(result['rows'][0]['mh_s'],65536/.5/1e6)

    def test_hot_or_invalid_initial_readings_cannot_initialize_gpu(self):
        for value in [None,{},dict(temperature_c=80),dict(temperature_c=float('nan')),dict(temperature_c=-1)]:
            with self.subTest(value=value),self.assertRaises(RuntimeError):self.run_fake(read=lambda:value)
        self.factory.assert_not_called();self.monitor.start.assert_not_called()

    def test_external_miner_blocks_before_sensor_or_gpu_access(self):
        self.idle.side_effect=RuntimeError('another miner');read=Mock()
        with self.assertRaisesRegex(RuntimeError,'another miner'):self.run_fake(read)
        read.assert_not_called();self.factory.assert_not_called()

    def test_bad_digest_fails_closed_and_closes_context(self):
        self.gpu.bad=True
        with self.assertRaisesRegex(RuntimeError,'SHA-256d'):self.run_fake()
        self.assertTrue(self.gpu.closed);self.assertFalse(self.gpu.ranges);self.monitor.close.assert_called_once()

    def test_thermal_monitor_stop_prevents_second_dispatch(self):
        def trip():self.monitor.reason='GPU safety';self.stop.set()
        self.gpu.callback=trip;result=self.run_fake()
        self.assertEqual(result['stop_reason'],'GPU safety');self.assertEqual(len(self.gpu.ranges),1)

    def test_setup_consumes_budget_and_cancellation_during_setup_launches_no_batch(self):
        def factory(**args):self.clock.now=61;return self.gpu
        self.factory.side_effect=factory
        result=self.run_fake();self.assertEqual(result['rows'],[]);self.assertFalse(self.gpu.ranges)

    def test_runner_benchmark_lockout_and_separate_result_path(self):
        runner=Runner(None);runner.busy=True
        with self.assertRaises(RuntimeError):runner.start_benchmark(TuningSettings())
        runner.busy=False
        with tempfile.TemporaryDirectory() as folder,patch('tuning_benchmark.assert_no_other_miner'),patch('tuning_benchmark.run_sweep',return_value=dict(kind='SYNTHETIC_GPU_BENCHMARK',rows=[])):
            path=Path(folder)/'gpu-benchmark.json';runner.start_benchmark(TuningSettings(),result_path=path)
            runner.thread.join(2)
            self.assertFalse(runner.busy);self.assertTrue(path.exists())
            event=list(runner.events.queue)[-1];self.assertEqual(event[0],'finished');self.assertEqual(event[1]['mode'],'benchmark')
            self.assertIsNone(event[1]['result']);self.assertFalse((Path(folder)/'gui-last-result.json').exists())

    def test_worker_error_always_clears_busy_and_delivers_finished(self):
        runner=Runner(None);runner.busy=True
        with patch('tuning_benchmark.run_sweep',side_effect=RuntimeError('synthetic failure')):
            BenchmarkWorker.work(runner,TuningSettings(),None)
        self.assertFalse(runner.busy);self.assertIn('synthetic failure',runner.events.get()[1]['error'])

if __name__=='__main__':unittest.main()
