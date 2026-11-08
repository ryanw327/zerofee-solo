"""Host dispatch and thermal/cancel regressions; no GPU, Core or process execution."""
import ctypes as C
from pathlib import Path
from queue import Queue
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from bitcoin import MAX_TARGET
from gpu_hasher import GPUHasher
from journal import SubmissionJournal
from miner import Miner
from run_limits import SESSION_BATCH_SIZE
from test_live import MockLive
from test_mining_sessions import Clock,Stop
from test_vectors import ADDRESS
from test_tuning_benchmark import Clock as BenchClock,Stop as BenchStop,FakeGPU
from advanced_settings import TuningSettings
from tuning_benchmark import run_sweep

class DispatchSafetyTests(unittest.TestCase):
    def fake_gpu(self,threads=128):
        gpu=object.__new__(GPUHasher);gpu.closed=False;gpu.threads_per_block=threads;gpu.max_threads_per_block=256
        gpu.buffers=[C.c_uint64(i) for i in range(8)];gpu.use_midstate=False
        gpu.cached_header=bytes(80);gpu.cached_target=None;gpu.launches=gpu.hashes=0
        gpu.function=1;gpu.flag_buffer=C.create_string_buffer(32);gpu.htod=Mock(return_value=0)
        gpu.launch=Mock(return_value=0);gpu.sync=Mock(return_value=0);gpu.dtoh=Mock(return_value=0);gpu.check=lambda *a:None
        return gpu
    def test_target_is_uploaded_as_little_endian_uint256_and_cached(self):
        gpu=self.fake_gpu();target=int.from_bytes(bytes(range(32)),'little');uploads=[]
        gpu.htod.side_effect=lambda device,pointer,size:uploads.append(C.string_at(pointer,size)) or 0
        GPUHasher._execute(gpu,bytes(80),0,1,target)
        GPUHasher._execute(gpu,bytes(80),1,1,target)
        self.assertEqual(uploads,[bytes(range(32))]);self.assertEqual(gpu.launch.call_count,2)
    def test_mutated_launch_shape_is_rechecked_before_allocation_or_dispatch(self):
        for value in (0,129,True,512):
            gpu=self.fake_gpu();gpu.threads_per_block=value
            with self.assertRaises(ValueError):GPUHasher._execute(gpu,bytes(80),0,1,MAX_TARGET)
            gpu.launch.assert_not_called();gpu.htod.assert_not_called()
    def test_input_rejections_never_reach_dispatch(self):
        cases=[(bytes(79),0,1,0),(bytes(80),-1,1,0),(bytes(80),1<<32,1,0),
               (bytes(80),0,0,0),(bytes(80),0,262145,0),(bytes(80),0,1,-1),(bytes(80),0,1,1<<256)]
        for args in cases:
            gpu=self.fake_gpu()
            with self.assertRaises(ValueError):GPUHasher._execute(gpu,*args)
            gpu.launch.assert_not_called()
    def test_failure_loading_first_cuda_dll_closes_added_dll_directory(self):
        directory=Mock()
        with patch.dict('gpu_hasher.os.environ',{'PATH':'test-only'}),patch('gpu_hasher.os.add_dll_directory',return_value=directory),patch('gpu_hasher.C.WinDLL',side_effect=OSError('synthetic load failure')):
            with self.assertRaisesRegex(OSError,'synthetic'):GPUHasher()
        directory.close.assert_called_once()
    def test_full_nonce_space_refreshes_job_before_reusing_nonce_zero(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();ranges=[]
        class GPU:
            def search_batch(self,header,start,count,target):
                ranges.append((header,start,count))
                if len(ranges)==16385:stop.set()
                return None
        with tempfile.TemporaryDirectory() as folder,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(folder)/'journal.json') as journal:
                result=Miner(rpc,ADDRESS,GPU(),journal=journal,stop_event=stop).run(10,None,
                    batch_size=SESSION_BATCH_SIZE,run_profile='session')
        self.assertEqual(len(ranges),16385);self.assertEqual(ranges[-2][1],(1<<32)-SESSION_BATCH_SIZE)
        self.assertEqual(ranges[-1][1],0);self.assertNotEqual(ranges[-1][0],ranges[0][0])
        self.assertTrue(all(start+count<=1<<32 for _,start,count in ranges));self.assertEqual(rpc.submissions,0)
        self.assertEqual(result['hashes'],(1<<32)+SESSION_BATCH_SIZE)

class BenchmarkCancelTests(unittest.TestCase):
    def sweep(self,clock,stop,gpu,monitor):
        with patch('gpu_identity.verify_identity'):
            return run_sweep(TuningSettings(),stop,Queue(),gpu_factory=lambda **_:gpu,
                monitor_factory=lambda *a,**k:monitor,read=lambda:dict(temperature_c=50),clock=clock,idle_check=lambda:None)
    def test_cancel_during_gpu_setup_never_dispatches_a_proof_batch(self):
        clock=BenchClock();stop=BenchStop(clock);gpu=FakeGPU(clock,stop);monitor=Mock(reason=None)
        def enter():stop.set();return gpu
        gpu.__class__=type('CancelOnEnter',(FakeGPU,),{'__enter__':lambda self:enter()})
        result=self.sweep(clock,stop,gpu,monitor)
        self.assertFalse(gpu.proofs);self.assertFalse(gpu.ranges);self.assertTrue(gpu.closed)
        self.assertEqual(result['status'],'STOPPED');monitor.close.assert_called_once()
    def test_cancel_during_proof_never_dispatches_a_measured_batch(self):
        clock=BenchClock();stop=BenchStop(clock);gpu=FakeGPU(clock,stop);monitor=Mock(reason=None)
        original=gpu.batch
        def proof(*args):value=original(*args);stop.set();return value
        gpu.batch=proof;result=self.sweep(clock,stop,gpu,monitor)
        self.assertEqual(gpu.proofs,[128]);self.assertFalse(gpu.ranges);self.assertTrue(gpu.closed)
        self.assertEqual(result['status'],'STOPPED');self.assertIsNone(result['best_starting_point'])
    def test_actual_thermal_monitor_missing_sensor_path_stops_benchmark(self):
        from gpu_monitor import GPUMonitor
        from test_thermal_safety import WaitClock,Clock as ThermalClock
        clock=BenchClock();stop=BenchStop(clock);gpu=FakeGPU(clock,stop);thermal_clock=ThermalClock()
        def monitor_factory(events,worker_stop,policy):
            monitor=GPUMonitor(events,worker_stop,policy=policy,read=lambda:None,clock=thermal_clock.now)
            monitor.stop_event=WaitClock(thermal_clock)
            monitor.start=monitor.work  # Deterministic real guard loop; no background or hardware.
            return monitor
        result=run_sweep(TuningSettings(),stop,Queue(),gpu_factory=lambda **_:gpu,monitor_factory=monitor_factory,
            read=lambda:dict(temperature_c=50),clock=clock,idle_check=lambda:None)
        self.assertTrue(stop.is_set());self.assertFalse(gpu.proofs);self.assertFalse(gpu.ranges)
        self.assertEqual(result['status'],'CANCELLED')

if __name__=='__main__':unittest.main()
