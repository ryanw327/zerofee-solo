"""CPU-only benchmark compact-path regressions: no GPU, Core or process use."""
import threading
import unittest
from queue import Queue
from unittest.mock import Mock, patch
from advanced_settings import TuningSettings
from bitcoin import sha256d, with_nonce
from tuning_benchmark import run_sweep

class CompactBenchmarkTests(unittest.TestCase):
    def run_fake(self, fault=None):
        now=[0.0]; stop=threading.Event(); monitor=Mock(reason=None)
        class GPU:
            max_threads_per_block=512
            closed=False
            measured=0
            checked=[]
            def __enter__(self):return self
            def __exit__(self,*args):self.closed=True
            def batch(self,header,start,count,target):
                return [sha256d(with_nonce(header,start+i)) for i in range(count)],[1]*count
            def search_batch(self,header,start,count,target):
                if target==0:
                    self.measured+=1; now[0]+=.5; return None
                self.checked.append((self.threads_per_block,start,count,target))
                matches=[(i,sha256d(with_nonce(header,start+i))) for i in range(count)
                         if int.from_bytes(sha256d(with_nonce(header,start+i)),'little')<=target]
                result=matches[0] if matches else None
                if fault=='missing':return None
                if fault=='false-positive' and result is None:return (0,bytes(32))
                if fault=='wrong-offset' and result:return (result[0]+1,result[1])
                if fault=='wrong-digest' and result:return (result[0],bytes(32))
                if fault=='cancel':stop.set()
                if fault=='exclusive' and result:return None
                return result
        gpu=GPU(); gpu.checked=[]
        self.gpu=gpu;self.monitor=monitor
        identity=patch('gpu_identity.verify_identity');identity.start();self.addCleanup(identity.stop)
        result=run_sweep(TuningSettings(pacing='Full - no batch pause'),stop,Queue(),
            gpu_factory=lambda **kw:gpu,monitor_factory=lambda *a,**kw:monitor,
            read=lambda:dict(temperature_c=50),clock=lambda:now[0],idle_check=lambda:None)
        return result

    def assert_rejected(self,fault):
        with self.assertRaisesRegex(RuntimeError,'compact-search'):
            self.run_fake(fault)
        self.assertTrue(self.gpu.closed)
        self.assertEqual(self.gpu.measured,0)
        self.monitor.close.assert_called_once()

    def test_always_empty_compact_kernel_cannot_receive_recommendation(self):self.assert_rejected('missing')
    def test_false_positive_below_boundary_is_rejected(self):self.assert_rejected('false-positive')
    def test_offset_must_match_cpu_oracle(self):self.assert_rejected('wrong-offset')
    def test_compact_digest_must_match_cpu_oracle(self):self.assert_rejected('wrong-digest')
    def test_target_equality_is_inclusive(self):self.assert_rejected('exclusive')
    def test_valid_compact_path_checks_each_launch_shape_at_nonce_boundary(self):
        result=self.run_fake()
        self.assertEqual(result['status'],'COMPLETE')
        self.assertIsNotNone(result['best_starting_point'])
        self.assertEqual([r[0] for r in self.gpu.checked],[128,128,256,256,512,512])
        self.assertTrue(all(start+count==(1<<32) for _,start,count,_ in self.gpu.checked))
        for i in (0,2,4):self.assertEqual(self.gpu.checked[i][3]-1,self.gpu.checked[i+1][3])
    def test_cancel_during_proof_dispatch_does_not_launch_timed_work(self):
        result=self.run_fake('cancel')
        self.assertEqual(result['status'],'STOPPED')
        self.assertEqual(self.gpu.measured,0)
        self.assertEqual(len(self.gpu.checked),1)
        self.assertIsNone(result['best_starting_point'])
        self.assertTrue(self.gpu.closed)

if __name__=='__main__':unittest.main()
