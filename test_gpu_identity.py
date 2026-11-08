"""Physical-board guard regressions using inert native, telemetry and mining fakes."""
import contextlib
import ctypes as C
import io
from pathlib import Path
from queue import Queue
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from advanced_settings import TuningSettings
from gpu_identity import pci_identity, verify_identity
from journal import SubmissionJournal
from miner import execute, MiningStopped
from test_live import ADDRESS, MockLive
from test_mining_sessions import Clock, Stop, GPU
from test_tuning_benchmark import Clock as BenchClock, Stop as BenchStop, FakeGPU
from tuning_benchmark import run_sweep


def identity_device(gpu, bus=b'0000:0A:00.0', device=7):
    gpu.device = device
    def query(pointer, size, selected):
        C.memmove(pointer, bus + b'\0', len(bus) + 1)
        return 0
    gpu.driver = SimpleNamespace(cuDeviceGetPCIBusId=Mock(side_effect=query))
    def check(code, step):
        if code: raise RuntimeError('Synthetic CUDA query failure')
    gpu.check = check
    return gpu


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.response=SimpleNamespace(returncode=0,stdout='00000000:0a:00.0\n')
        self.path=patch('gpu_monitor.nvidia_smi_path',return_value=Path('synthetic-smi.exe'))
        self.run=patch('gpu_identity.subprocess.run',return_value=self.response)
        self.path.start();self.process=self.run.start()
        self.addCleanup(self.path.stop);self.addCleanup(self.run.stop)

    def test_normalization_and_strict_single_identity(self):
        self.assertEqual(pci_identity('0000:0A:00.0'),pci_identity(self.response.stdout))
        for value in (None,1,True,b'0000:0a:00.0','','N/A','0000:0a:20.0',
                      '0000:0a:00.8','0000:0a:00.0\n0000:0b:00.0'):
            with self.subTest(value=value),self.assertRaises(ValueError):pci_identity(value)

    def test_query_uses_exact_context_device_and_bounded_telemetry_index_zero(self):
        gpu=identity_device(Mock())
        self.assertEqual(verify_identity(gpu),(0,10,0,0))
        self.assertEqual(gpu.driver.cuDeviceGetPCIBusId.call_args.args[2],7)
        self.assertEqual(self.process.call_args.args[0][1],'-i=0')
        self.assertEqual(self.process.call_args.kwargs['timeout'],2)
        gpu.batch.assert_not_called()

    def test_unavailable_or_malformed_telemetry_fails_before_cuda_query(self):
        for value in ('','N/A',None,'0000:0a:00.0, 0000:0b:00.0'):
            gpu=identity_device(Mock());self.response.stdout=value
            with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'Cannot verify'):verify_identity(gpu)
            gpu.driver.cuDeviceGetPCIBusId.assert_not_called();gpu.batch.assert_not_called()

    def test_failed_or_timed_out_telemetry_query_is_fail_closed(self):
        for fault in (OSError('inert unavailable'),subprocess.TimeoutExpired('inert',2)):
            self.process.side_effect=fault;gpu=identity_device(Mock())
            with self.assertRaisesRegex(RuntimeError,'Cannot verify'):verify_identity(gpu)
            gpu.driver.cuDeviceGetPCIBusId.assert_not_called()
        self.process.side_effect=None;self.response.returncode=1
        with self.assertRaisesRegex(RuntimeError,'Cannot verify'):verify_identity(identity_device(Mock()))

    def test_invalid_context_handle_or_cuda_bus_fails_closed(self):
        for value in (True,None,-1,'0'):
            gpu=identity_device(Mock(),device=value)
            with self.subTest(value=value),self.assertRaisesRegex(RuntimeError,'Cannot verify'):verify_identity(gpu)
            gpu.driver.cuDeviceGetPCIBusId.assert_not_called()
        for bus in (b'',b'N/A',b'0000:0a:00.8',b'\xff'):
            with self.subTest(bus=bus),self.assertRaisesRegex(RuntimeError,'Cannot verify'):
                verify_identity(identity_device(Mock(),bus=bus))

    def test_failed_cuda_query_never_dispatches(self):
        gpu=identity_device(Mock());gpu.driver.cuDeviceGetPCIBusId.side_effect=None
        gpu.driver.cuDeviceGetPCIBusId.return_value=1
        with self.assertRaisesRegex(RuntimeError,'Cannot verify'):verify_identity(gpu)
        gpu.batch.assert_not_called()

    def mining(self, bus=None, cancel=False, cancel_on_enter=False):
        clock=Clock();stop=Stop(clock);rpc=MockLive()
        gpu=identity_device(GPU(clock,stop,seconds=.1,stop_at=1))
        self.gpu=gpu;self.rpc=rpc;self.stop=stop
        if cancel_on_enter:
            gpu.__class__=type('CancelOnEnter',(GPU,),{'__enter__':lambda self:stop.set() or self})
        if bus is not None:self.response.stdout=bus
        if cancel:self.process.side_effect=lambda *a,**kw:stop.set() or self.response
        args=SimpleNamespace(network='mainnet',datadir='inert',cookie='',session_pid=1,
            mainnet_opt_in=True,submit_blocks=True,address=ADDRESS,max_seconds=1,
            max_hashes=None,max_blocks=1,run_profile='session',pause_seconds=0)
        with patch('miner.NodeRPC',return_value=rpc),patch('miner.GPUHasher',return_value=gpu),\
             patch('miner.time.monotonic',side_effect=clock.now),contextlib.redirect_stdout(io.StringIO()):
            with tempfile.TemporaryDirectory() as directory,SubmissionJournal(Path(directory)/'submission.json') as journal:
                return execute(args,19732,journal,stop)

    def benchmark(self, bus=None, cancel=False, cancel_on_enter=False):
        clock=BenchClock();stop=BenchStop(clock);gpu=identity_device(FakeGPU(clock,stop))
        monitor=Mock(reason=None);self.gpu=gpu;self.monitor=monitor
        if cancel_on_enter:
            gpu.__class__=type('BenchCancelOnEnter',(FakeGPU,),{'__enter__':lambda self:stop.set() or self})
        if bus is not None:self.response.stdout=bus
        if cancel:self.process.side_effect=lambda *a,**kw:stop.set() or self.response
        return run_sweep(TuningSettings(pacing='Full - no batch pause'),stop,Queue(),
            gpu_factory=lambda **kw:gpu,monitor_factory=lambda *a,**kw:monitor,
            read=lambda:dict(temperature_c=50),clock=clock,idle_check=lambda:None)

    def test_mining_mismatch_and_malformed_identity_prevent_every_dispatch(self):
        for bus in ('0000:0b:00.0','','N/A'):
            with self.subTest(bus=bus),self.assertRaises(RuntimeError):self.mining(bus)
            self.assertTrue(self.gpu.closed);self.assertEqual(self.gpu.batches,0)
            self.assertEqual(self.rpc.submissions,0)

    def test_mining_unavailable_identity_closes_context_without_dispatch(self):
        self.process.side_effect=OSError('synthetic absent')
        with self.assertRaisesRegex(RuntimeError,'Cannot verify'):self.mining()
        self.assertTrue(self.gpu.closed);self.assertEqual(self.gpu.batches,0)

    def test_matching_normalized_identity_allows_mining_fake_dispatch(self):
        result=self.mining()
        self.assertEqual(result['status'],'STOPPED');self.assertEqual(self.gpu.batches,1)
        self.assertTrue(self.gpu.closed);self.assertEqual(self.rpc.submissions,0)

    def test_mining_cancel_during_identity_or_setup_prevents_dispatch(self):
        with self.assertRaises(MiningStopped):self.mining(cancel=True)
        self.assertTrue(self.gpu.closed);self.assertEqual(self.gpu.batches,0)
        self.process.reset_mock(side_effect=True)
        with self.assertRaises(MiningStopped):self.mining(cancel_on_enter=True)
        self.process.assert_not_called();self.assertTrue(self.gpu.closed)

    def test_benchmark_mismatch_and_malformed_identity_prevent_proof_and_timed_work(self):
        for bus in ('0000:0b:00.0','','N/A'):
            with self.subTest(bus=bus),self.assertRaises(RuntimeError):self.benchmark(bus)
            self.assertTrue(self.gpu.closed);self.assertFalse(self.gpu.proofs);self.assertFalse(self.gpu.ranges)
            self.monitor.close.assert_called_once()

    def test_benchmark_unavailable_identity_closes_monitor_and_context(self):
        self.process.side_effect=OSError('synthetic absent')
        with self.assertRaisesRegex(RuntimeError,'Cannot verify'):self.benchmark()
        self.assertTrue(self.gpu.closed);self.assertFalse(self.gpu.proofs)
        self.monitor.close.assert_called_once()

    def test_matching_normalized_identity_allows_all_benchmark_fake_shapes(self):
        result=self.benchmark()
        self.assertEqual(result['status'],'COMPLETE');self.assertEqual(self.gpu.proofs,[128,256,512])
        self.assertTrue(self.gpu.ranges);self.assertTrue(self.gpu.closed)
        self.monitor.close.assert_called_once()

    def test_benchmark_cancel_during_identity_or_setup_launches_no_kernels(self):
        result=self.benchmark(cancel=True)
        self.assertEqual(result['status'],'STOPPED');self.assertFalse(self.gpu.proofs)
        self.assertFalse(self.gpu.ranges);self.assertTrue(self.gpu.closed)
        self.monitor.close.assert_called_once()
        self.process.reset_mock(side_effect=True)
        result=self.benchmark(cancel_on_enter=True)
        self.assertEqual(result['status'],'STOPPED');self.process.assert_not_called()
        self.assertFalse(self.gpu.proofs);self.assertTrue(self.gpu.closed)


if __name__=='__main__':unittest.main()
