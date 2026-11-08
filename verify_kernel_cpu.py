"""Compile actual CUDA source as sequential CPU C++; no CUDA/GPU/Core calls.

This verifies algorithm, indexing, byte order and compact-output logic. It cannot
verify CUDA compilation, device execution, races, occupancy or thermal behavior.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from bitcoin import MAX_TARGET, known_headers, sha256d, with_nonce

ROOT=Path(__file__).resolve().parent
MODES=[('optimized',1,1,1,0),('fallback',0,0,0,0),('byte_permutation',1,1,1,1)]

def compile_shim(compiler,folder,mode):
    name,mid,rolling,words,perm=mode
    library=folder/(name+('.dll' if sys.platform=='win32' else '.so'))
    defines=[f'USE_MIDSTATE={mid}',f'ROLLING_SCHEDULE={rolling}',f'WORD_INPUT={words}',f'BYTE_PERMUTATION={perm}','SHA_UNROLL=4']
    if Path(compiler).name.lower()=='cl.exe':
        command=[str(compiler),'/nologo','/O2','/GS-','/Zl','/LD','/GR-',
                 *['/D'+value for value in defines],'/I'+str(ROOT),
                 '/Fo'+str(folder/(name+'.obj')),'/Fe'+str(library),str(ROOT/'kernel_cpu_shim.cpp'),
                 '/link','/NOENTRY','/NODEFAULTLIB','/MACHINE:X64']
    else:
        command=[str(compiler),'-O2','-shared','-fPIC',*['-D'+value for value in defines],
                 '-I'+str(ROOT),str(ROOT/'kernel_cpu_shim.cpp'),'-o',str(library)]
    built=subprocess.run(command,capture_output=True,text=True,timeout=60,cwd=folder)
    if built.returncode:raise RuntimeError('CPU shim compile failed:\n'+built.stdout+built.stderr)
    return library

def verify(library):
    lib=C.CDLL(str(library))
    pointer=C.c_void_p;uint=C.c_uint
    lib.cpu_batch.argtypes=[pointer,uint,uint,pointer,uint,C.c_int,uint,pointer,pointer,pointer,pointer,pointer]
    lib.cpu_batch.restype=C.c_int;lib.cpu_at_most.argtypes=[pointer,pointer];lib.cpu_at_most.restype=C.c_int
    cases=digests=flags=compact_cases=0
    rng=random.Random(20261107)
    def run(header,start,count,target,threads):
        nonlocal cases,digests,flags,compact_cases
        expected=[sha256d(with_nonce(header,(start+i)&0xffffffff)) for i in range(count)]
        wanted=[int(int.from_bytes(value,'little')<=target) for value in expected]
        header_buffer=C.create_string_buffer(header);target_buffer=C.create_string_buffer(target.to_bytes(32,'little'))
        out=C.create_string_buffer(b'\xa5'*(32*count+32));marks=C.create_string_buffer(b'\xa5'*(count+32))
        total=uint();offsets=(uint*65)(*([0xa5a5a5a5]*65));sparse=C.create_string_buffer(b'\xa5'*(32*65))
        def dispatch(compact,capacity=64):
            assert lib.cpu_batch(header_buffer,start,count,target_buffer,threads,compact,capacity,out,marks,C.byref(total),offsets,sparse)==0
        dispatch(0)
        actual=[out.raw[32*i:32*(i+1)] for i in range(count)]
        assert actual==expected,'SHA-256d mismatch'
        assert list(marks.raw[:count])==wanted,'Target byte order/boundary mismatch'
        assert out.raw[32*count:32*count+32]==b'\xa5'*32 and marks.raw[count:count+32]==b'\xa5'*32,'Padded thread wrote beyond count'
        for capacity in (0,2,64):
            for i in range(65):offsets[i]=0xa5a5a5a5
            C.memset(sparse,0xa5,32*65)
            dispatch(1,capacity)
            hits=[i for i,flag in enumerate(wanted) if flag]
            assert total.value==len(hits),'Compact count/overflow mismatch'
            stored=min(len(hits),capacity)
            assert list(offsets)[:stored]==hits[:stored],'Compact offset mismatch'
            assert sparse.raw[:32*stored]==b''.join(expected[i] for i in hits[:stored]),'Compact digest mismatch'
            assert list(offsets)[capacity:]==[0xa5a5a5a5]*(65-capacity),'Compact overflow wrote beyond capacity'
            assert sparse.raw[32*capacity:32*65]==b'\xa5'*(32*(65-capacity)),'Compact digest overflow'
            compact_cases+=1
        cases+=1;digests+=count;flags+=count
    for threads in (64,128,256,512):
        header=rng.randbytes(80)
        for count in (1,31,32,threads-1,threads,threads+1,513):
            for start in (0,0xfffffffe):
                run(header,start,count,rng.randrange(1<<256),threads)
        for _,header,_ in known_headers():
            nonce=int.from_bytes(header[76:80],'little');value=int.from_bytes(sha256d(header),'little')
            for target in (0,MAX_TARGET,value-1,value,value+1):run(header,nonce,1,target,threads)
    endian_cases=0
    for _ in range(256):
        raw=rng.randbytes(32);value=int.from_bytes(raw,'little')
        for target in (0,MAX_TARGET,value-1,value,value+1):
            if not 0<=target<=MAX_TARGET:continue
            assert lib.cpu_at_most(C.create_string_buffer(raw),C.create_string_buffer(target.to_bytes(32,'little')))==int(value<=target)
            endian_cases+=1
    return dict(batch_cases=cases,cpu_digest_comparisons=digests,target_flag_comparisons=flags,
                compact_capacity_cases=compact_cases,independent_target_boundary_cases=endian_cases)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',required=True,type=Path,help='Existing cl.exe, g++ or clang++ executable; nothing is installed')
    parser.add_argument('--work-dir',type=Path,help='Separate temporary build directory; never distribute binaries')
    parser.add_argument('--json-report',type=Path)
    args=parser.parse_args()
    if not args.compiler.is_file():parser.error('Compiler executable does not exist')
    with tempfile.TemporaryDirectory(prefix='zerofee-cpu-') as temporary:
        folder=args.work_dir.resolve() if args.work_dir else Path(temporary)
        folder.mkdir(parents=True,exist_ok=True)
        results={mode[0]:verify(compile_shim(args.compiler,folder,mode)) for mode in MODES}
    report=dict(status='PASS',modes=results,cuda_source_sha256=hashlib.sha256((ROOT/'sha256d.cu').read_bytes()).hexdigest(),
        execution='CPU native shim over actual CUDA source; sequential atomic emulation',gpu_executed=False,
        core_started=False,hardware_validated=False)
    if args.json_report:args.json_report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
