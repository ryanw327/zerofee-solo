"""Diagnostic fixture/stop checks use a CPU oracle, never an actual GPU."""
import contextlib
import ctypes as C
import io
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from bitcoin import sha256d,with_nonce
from verify_gpu_vectors import pci_identity,verify_identity,verify_vectors,main

class CPUOracle:
    max_threads_per_block=512;candidate_capacity=64;candidate_overflows=0
    def batch(self,header,start,count,target):
        actual=[sha256d(with_nonce(header,(start+i)&0xffffffff)) for i in range(count)]
        return actual,[int(int.from_bytes(item,'little')<=target) for item in actual]
    def search_candidates(self,header,start,count,target):
        digests,flags=self.batch(header,start,count,target);self.candidate_overflows+=1
        return dict(overflow=True,total_candidates=sum(flags),candidates=[(i,raw) for i,raw in enumerate(digests) if flags[i]])

class DiagnosticTests(unittest.TestCase):
    def test_pci_identity_normalizes_domain_width_and_rejects_invalid_text(self):
        self.assertEqual(pci_identity('0000:01:00.0'),pci_identity('00000000:01:00.0\n'))
        for value in ('','GPU 0','0000:01:00.0\n0000:02:00.0','0000:01:00.8'):
            with self.assertRaises(ValueError):pci_identity(value)
    def test_vectors_cover_every_shape_and_overflow_with_a_bounded_hash_count(self):
        gpu=CPUOracle();result=verify_vectors(gpu,lambda:None)
        self.assertEqual(result['thread_shapes'],[64,128,256,512])
        self.assertLess(result['cpu_digest_comparisons'],3000);self.assertEqual(gpu.candidate_overflows,4)
    def test_identity_match_and_mismatch_are_checked_before_vector_dispatch(self):
        gpu=Mock();gpu.device=0
        gpu.driver.cuDeviceGetPCIBusId.side_effect=lambda pointer,*_:C.memmove(pointer,b'0000:01:00.0\0',13) and 0
        for bus,valid in [('00000000:01:00.0',True),('00000000:02:00.0',False)]:
            with patch('gpu_monitor.nvidia_smi_path',return_value=Path('synthetic-smi.exe')),patch('gpu_identity.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=bus)):
                if valid:verify_identity(gpu)
                else:
                    with self.assertRaisesRegex(RuntimeError,'differs'):verify_identity(gpu)
            gpu.batch.assert_not_called()
    def test_missing_identity_query_fails_closed_without_dispatch(self):
        gpu=Mock()
        with patch('gpu_monitor.nvidia_smi_path',return_value=Path('synthetic-smi.exe')),patch('gpu_identity.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='')):
            with self.assertRaisesRegex(RuntimeError,'Cannot verify'):verify_identity(gpu)
        gpu.device_get.assert_not_called();gpu.batch.assert_not_called()
    def test_cancel_checkpoint_prevents_any_dispatch(self):
        gpu=CPUOracle()
        with patch.object(gpu,'batch') as batch:
            with self.assertRaisesRegex(RuntimeError,'cancel'):
                verify_vectors(gpu,lambda:(_ for _ in ()).throw(RuntimeError('cancel')))
            batch.assert_not_called()
    def test_missing_explicit_flags_never_load_gpu_or_read_sensor(self):
        with patch.object(sys,'argv',['verify_gpu_vectors.py']),contextlib.redirect_stderr(io.StringIO()):
            with patch('gpu_hasher.GPUHasher') as gpu,patch('gpu_monitor.read_gpu') as read,self.assertRaises(SystemExit):main()
            gpu.assert_not_called();read.assert_not_called()

if __name__=='__main__':unittest.main()
