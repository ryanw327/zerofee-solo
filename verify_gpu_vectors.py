"""Explicit bounded synthetic correctness diagnostic; never connects to Core or mines.

Not run by offline verification. Hardware use requires --start --acknowledge-risk.
"""
import argparse
import json
from pathlib import Path
from queue import Queue
import signal
import threading
import time
from bitcoin import MAX_TARGET,known_headers,sha256d,with_nonce
from gpu_identity import pci_identity,verify_identity

def verify_vectors(gpu,checkpoint):
    """At most 1,280 logical digest checks across four shapes, plus tiny overflow reruns."""
    checks=hashes=0;supported=[]
    for threads in (64,128,256,512):
        if threads>gpu.max_threads_per_block:continue
        supported.append(threads);gpu.threads_per_block=threads
        cases=[(header,int.from_bytes(header[76:80],'little'),1,MAX_TARGET) for _,header,_ in known_headers()]
        header=bytes(range(80))
        cases += [(header,0,threads+1,MAX_TARGET),(header,0xfffffffe,4,MAX_TARGET)]
        raw=sha256d(with_nonce(header,7));value=int.from_bytes(raw,'little')
        cases += [(header,7,1,target) for target in (0,value-1,value,value+1,MAX_TARGET)]
        for header,start,count,target in cases:
            checkpoint()
            actual,flags=gpu.batch(header,start,count,target)
            expected=[sha256d(with_nonce(header,(start+i)&0xffffffff)) for i in range(count)]
            wanted=[int(int.from_bytes(item,'little')<=target) for item in expected]
            if actual!=expected or flags!=wanted:raise RuntimeError('GPU digest/target verification failed')
            checks+=1;hashes+=count
            checkpoint()
        # A guaranteed hit count above compact capacity must recover every offset.
        checkpoint();before=gpu.candidate_overflows
        header=bytes(range(80));count=gpu.candidate_capacity+1
        output=gpu.search_candidates(header,0,count,MAX_TARGET)
        expected=[(i,sha256d(with_nonce(header,i))) for i in range(count)]
        if not output['overflow'] or output['total_candidates']!=count or output['candidates']!=expected or gpu.candidate_overflows!=before+1:
            raise RuntimeError('GPU compact-output overflow recovery failed')
        checks+=1;hashes+=count;checkpoint()
    if not supported:raise RuntimeError('No supported diagnostic thread counts')
    return dict(thread_shapes=supported,batch_cases=checks,cpu_digest_comparisons=hashes)

def main():
    from risk_notice import RISK_TEXT
    parser=argparse.ArgumentParser(description=__doc__,epilog=RISK_TEXT)
    parser.add_argument('--start',action='store_true')
    parser.add_argument('--acknowledge-risk',action='store_true')
    parser.add_argument('--json-report',type=Path)
    args=parser.parse_args()
    if not args.start or not args.acknowledge_risk:parser.error('Explicit --start and --acknowledge-risk are required')
    from tuning_benchmark import assert_no_other_miner
    from gpu_monitor import GPUMonitor,ThermalGuard,read_gpu
    from gpu_hasher import GPUHasher
    assert_no_other_miner()
    first=read_gpu();guard=ThermalGuard()
    if guard.observe(first,time.monotonic()) or guard.missing_since is not None:
        raise RuntimeError('A valid GPU core reading below 80°C is required')
    stop=threading.Event();signal.signal(signal.SIGINT,lambda *_:stop.set())
    monitor=GPUMonitor(Queue(),stop);began=time.monotonic();deadline=began+30
    def checkpoint():
        if stop.is_set():raise RuntimeError(monitor.reason or 'Diagnostic cancelled')
        if time.monotonic()>=deadline:raise TimeoutError('Diagnostic 30-second budget reached')
    try:
        monitor.start();checkpoint()
        with GPUHasher() as gpu:
            checkpoint();verify_identity(gpu);checkpoint()
            result=verify_vectors(gpu,checkpoint)
    finally:monitor.close()
    result.update(status='PASS',elapsed_seconds=time.monotonic()-began,synthetic_vectors_only=True,
        core_used=False,block_submission=False,rate_benchmark=False,
        limits='Small correctness check only. Does not validate atomic concurrency fully, long-run stability, thermal soak, Core integration or mainnet propagation.')
    if args.json_report:args.json_report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
