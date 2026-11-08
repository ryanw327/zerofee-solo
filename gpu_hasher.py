"""Bounded NVRTC/Driver API wrapper for the new local SHA-256d proof of concept."""
import ctypes as C
import os
from pathlib import Path
from bitcoin import MAX_TARGET

MAX_BATCH = 262144
CUDA_BIN = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4\bin\x64")

class GPUHasher:
    def __init__(self, use_midstate=True, unroll=4, rolling_schedule=True, word_input=True, byte_permutation=False, threads_per_block=128):
        if any(type(value) is not bool for value in (use_midstate,rolling_schedule,word_input,byte_permutation)) or type(unroll) is not int or unroll not in (0,1,4,64):
            raise ValueError('Choose a boolean midstate mode and unroll 0/1/4/64')
        from advanced_settings import validate_threads
        validate_threads(threads_per_block)
        self.threads_per_block = threads_per_block
        self.use_midstate,self.unroll=use_midstate,unroll
        self.rolling_schedule,self.word_input=rolling_schedule,word_input
        self.byte_permutation=byte_permutation
        self.context = C.c_void_p()
        self.module = C.c_void_p()
        self.dll_dir = None
        self.launches = 0
        self.hashes = 0
        self.closed = False
        self.buffers = []
        self.cached_header = self.cached_target = None
        self.cached_prefix = None
        self.midstate_builds = 0
        self.candidate_capacity=64;self.candidate_overflows=0;self.fallback_rehashed=0
        try:
            os.environ["PATH"] = str(CUDA_BIN) + os.pathsep + os.environ.get("PATH", "")
            self.dll_dir = os.add_dll_directory(str(CUDA_BIN))
            self.builtins = C.WinDLL(str(CUDA_BIN / "nvrtc-builtins64_134.dll"))
            self.nvrtc = C.WinDLL(str(CUDA_BIN / "nvrtc64_130_0.dll"))
            self.driver = C.WinDLL(r"C:\Windows\System32\nvcuda.dll")
            p, u, z, d = C.c_void_p, C.c_uint, C.c_size_t, C.c_uint64
            pp = C.POINTER(p)
            def bind(lib, name, args):
                fn = getattr(lib, name)
                fn.argtypes, fn.restype = args, C.c_int
                return fn
            self.error_name = bind(self.driver, "cuGetErrorName", [C.c_int, C.POINTER(C.c_char_p)])
            self.init = bind(self.driver, "cuInit", [u])
            self.device_get = bind(self.driver, "cuDeviceGet", [C.POINTER(C.c_int), C.c_int])
            self.device_name = bind(self.driver, "cuDeviceGetName", [p, C.c_int, C.c_int])
            self.capability = bind(self.driver, "cuDeviceComputeCapability", [C.POINTER(C.c_int),C.POINTER(C.c_int),C.c_int])
            self.device_attribute = bind(self.driver, 'cuDeviceGetAttribute', [C.POINTER(C.c_int), C.c_int, C.c_int])
            self.ctx_create = bind(self.driver, "cuCtxCreate_v2", [pp,u,C.c_int])
            self.ctx_destroy = bind(self.driver, "cuCtxDestroy_v2", [p])
            self.module_load = bind(self.driver, "cuModuleLoadData", [pp,p])
            self.module_unload = bind(self.driver, "cuModuleUnload", [p])
            self.function_get = bind(self.driver, "cuModuleGetFunction", [pp,p,C.c_char_p])
            self.alloc = bind(self.driver, "cuMemAlloc_v2", [C.POINTER(d),z])
            self.free = bind(self.driver, "cuMemFree_v2", [d])
            self.htod = bind(self.driver, "cuMemcpyHtoD_v2", [d,p,z])
            self.dtoh = bind(self.driver, "cuMemcpyDtoH_v2", [p,d,z])
            self.launch = bind(self.driver, "cuLaunchKernel", [p,u,u,u,u,u,u,u,p,C.POINTER(p),p])
            self.sync = bind(self.driver, "cuCtxSynchronize", [])
            self.create_program = bind(self.nvrtc,"nvrtcCreateProgram",[pp,C.c_char_p,C.c_char_p,C.c_int,p,p])
            self.compile_program = bind(self.nvrtc,"nvrtcCompileProgram",[p,C.c_int,C.POINTER(C.c_char_p)])
            self.log_size = bind(self.nvrtc,"nvrtcGetProgramLogSize",[p,C.POINTER(z)])
            self.get_log = bind(self.nvrtc,"nvrtcGetProgramLog",[p,p])
            self.cubin_size = bind(self.nvrtc,"nvrtcGetCUBINSize",[p,C.POINTER(z)])
            self.get_cubin = bind(self.nvrtc,"nvrtcGetCUBIN",[p,p])
            self.destroy_program = bind(self.nvrtc,"nvrtcDestroyProgram",[pp])
            self.check(self.init(0), "cuInit")
            device = C.c_int()
            self.check(self.device_get(C.byref(device),0), "cuDeviceGet")
            self.device = device.value
            name = C.create_string_buffer(128)
            self.check(self.device_name(name,128,device.value), "cuDeviceGetName")
            major,minor = C.c_int(),C.c_int()
            self.check(self.capability(C.byref(major),C.byref(minor),device.value), "cuDeviceComputeCapability")
            self.name = name.value.decode()
            self.arch = f"sm_{major.value}{minor.value}"
            cubin = self._compile(self.arch)
            self.check(self.ctx_create(C.byref(self.context),0,device.value), "cuCtxCreate")
            self.check(self.module_load(C.byref(self.module),cubin), "cuModuleLoadData")
            self.function = p()
            self.check(self.function_get(C.byref(self.function),self.module,b"hash_nonce_batch"), "cuModuleGetFunction")
            self.prepare_function = p()
            self.check(self.function_get(C.byref(self.prepare_function),self.module,b"prepare_midstate"), "cuModuleGetFunction")
            self.compact_function=p()
            self.check(self.function_get(C.byref(self.compact_function),self.module,b"compact_candidates"),"compact function")
            self.reset=bind(self.driver,'cuMemsetD32_v2',[d,u,z])
            attribute=bind(self.driver,'cuFuncGetAttribute',[C.POINTER(C.c_int),C.c_int,p])
            # Respect compiled kernel limits as well as the bounded UI choices.
            device_maximum = C.c_int()
            self.check(self.device_attribute(C.byref(device_maximum), 1, device.value), 'maximum device threads')
            self.max_threads_per_block = device_maximum.value
            for function in (self.function, self.compact_function):
                maximum=C.c_int()
                self.check(attribute(C.byref(maximum),0,function),'maximum kernel threads')
                self.max_threads_per_block = min(self.max_threads_per_block, maximum.value)
                if threads_per_block > maximum.value:
                    raise ValueError(f'This kernel supports at most {maximum.value} threads per block.')
            if threads_per_block > self.max_threads_per_block:
                raise ValueError(f'This device/kernel supports at most {self.max_threads_per_block} threads per block.')
            self.kernel_resources={}
            for label,key in [('registers_per_thread',4),('local_bytes_per_thread',3),('static_shared_bytes_per_block',1)]:
                value=C.c_int();self.check(attribute(C.byref(value),key,self.function),'kernel resource query')
                self.kernel_resources[label]=value.value
        except BaseException:
            self.close()
            raise

    def check(self, code, step):
        if code:
            name = C.c_char_p()
            self.error_name(code,C.byref(name))
            label = name.value.decode() if name.value else str(code)
            raise RuntimeError(f"{step}: {label}")

    def _compile(self, arch):
        program = C.c_void_p()
        src = Path(__file__).with_name("sha256d.cu").read_bytes()
        code = self.create_program(C.byref(program),src,b"sha256d.cu",0,None,None)
        if code:
            raise RuntimeError(f"nvrtcCreateProgram: {code}")
        try:
            opts=(C.c_char_p*8)(f"--gpu-architecture={arch}".encode(),b"--std=c++17",
                f'-DUSE_MIDSTATE={int(self.use_midstate)}'.encode(),f'-DSHA_UNROLL={self.unroll}'.encode(),
                f'-DROLLING_SCHEDULE={int(self.rolling_schedule)}'.encode(),f'-DWORD_INPUT={int(self.word_input)}'.encode(),
                b'--ptxas-options=-v',f'-DBYTE_PERMUTATION={int(self.byte_permutation)}'.encode())
            code=self.compile_program(program,8,opts)
            size=C.c_size_t()
            if self.log_size(program,C.byref(size)):
                raise RuntimeError("Could not obtain NVRTC compilation log size")
            log=C.create_string_buffer(max(size.value,1))
            self.get_log(program,log)
            self.compile_log=log.value.decode(errors='replace')
            Path(__file__).with_name("nvrtc-build.log").write_text(self.compile_log)
            if code:
                raise RuntimeError(f"NVRTC compile failed ({code}): {log.value.decode(errors='replace')}")
            if self.cubin_size(program,C.byref(size)) or not size.value:
                raise RuntimeError("No native cubin produced")
            cubin=C.create_string_buffer(size.value)
            if self.get_cubin(program,cubin):
                raise RuntimeError("Could not retrieve cubin")
            return cubin
        finally:
            self.destroy_program(C.byref(program))

    def _execute(self, header, first_nonce, count, target, compact=False):
        if self.closed:
            raise RuntimeError("GPU context is closed")
        from advanced_settings import validate_threads
        validate_threads(self.threads_per_block)
        if self.threads_per_block > self.max_threads_per_block:
            raise ValueError('Threads per block exceeds the queried device/kernel limit.')
        if not isinstance(header,bytes) or len(header)!=80:
            raise ValueError("Expected exactly 80 header bytes")
        if type(first_nonce) is not int or not 0<=first_nonce<=0xffffffff:
            raise ValueError("first_nonce must be uint32")
        if type(count) is not int or not 1<=count<=MAX_BATCH:
            raise ValueError(f"count must be in 1..{MAX_BATCH}")
        if type(target) is not int or not 0<=target<=MAX_TARGET:
            raise ValueError("target must be uint256")
        if not self.buffers:
            try:
                for size in (80,32,32*MAX_BATCH,MAX_BATCH,32,4,4*self.candidate_capacity,32*self.candidate_capacity):
                    pointer=C.c_uint64()
                    self.check(self.alloc(C.byref(pointer),size),"cuMemAlloc")
                    self.buffers.append(pointer)
                self.hash_buffer=C.create_string_buffer(32*MAX_BATCH)
                self.flag_buffer=C.create_string_buffer(MAX_BATCH)
            except BaseException:
                self.close()
                raise
        h,t,out,flags,midstate=self.buffers[:5]
        if self.cached_header != header:
            self.check(self.htod(h,C.create_string_buffer(header),80),"header upload")
            self.cached_header=header
        if self.use_midstate and self.cached_prefix!=header[:64]:
            arguments=(C.c_void_p*2)(C.cast(C.byref(h),C.c_void_p),C.cast(C.byref(midstate),C.c_void_p))
            self.check(self.launch(self.prepare_function,1,1,1,1,1,1,0,None,arguments,None),"prepare midstate")
            self.cached_prefix=header[:64]
            self.midstate_builds+=1
        if self.cached_target != target:
            self.check(self.htod(t,C.create_string_buffer(target.to_bytes(32,"little")),32),"target upload")
            self.cached_target=target
        start,n=C.c_uint(first_nonce),C.c_uint(count)
        if compact:
            total,offsets,digests=self.buffers[5:];capacity=C.c_uint(self.candidate_capacity)
            self.check(self.reset(total,0,1),'reset candidate count')
            arguments=(C.c_void_p*9)(*[C.cast(C.byref(x),C.c_void_p) for x in [h,start,n,t,total,offsets,digests,capacity,midstate]])
            function=self.compact_function
        else:
            arguments=(C.c_void_p*7)(*[C.cast(C.byref(x),C.c_void_p) for x in [h,start,n,t,out,flags,midstate]])
            function=self.function
        self.check(self.launch(function,(count+self.threads_per_block-1)//self.threads_per_block,1,1,self.threads_per_block,1,1,0,None,arguments,None),"cuLaunchKernel")
        self.check(self.sync(),"cuCtxSynchronize")
        self.launches+=1
        self.hashes+=count
        if compact:
            found=C.c_uint();self.check(self.dtoh(C.byref(found),total,4),'candidate count download')
            if found.value>count:raise RuntimeError('GPU candidate count exceeds dispatched interval')
            return found.value
        self.check(self.dtoh(self.flag_buffer,flags,count),"flag download")
        return out,C.string_at(self.flag_buffer,count)

    def batch(self, header, first_nonce, count, target):
        """Full digest interface retained for independent correctness checks."""
        out,flags=self._execute(header,first_nonce,count,target)
        self.check(self.dtoh(self.hash_buffer,out,32*count),"hash download")
        raw=C.string_at(self.hash_buffer,32*count)
        return [raw[i*32:(i+1)*32] for i in range(count)],list(flags)

    def search_batch(self, header, first_nonce, count, target):
        """First nonce in a fully accounted batch; CPU proof checks remain mandatory."""
        result=self.search_candidates(header,first_nonce,count,target)
        return result['candidates'][0] if result['candidates'] else None

    def search_candidates(self,header,first_nonce,count,target):
        """Count every hit; overflow reruns the interval through the full-digest path.

        The caller's logical interval is unchanged. hashes/fallback_rehashed track
        the extra physical GPU work. No candidate is silently lost on easy targets.
        """
        found=self._execute(header,first_nonce,count,target,compact=True)
        overflow=found>self.candidate_capacity
        if overflow:
            self.candidate_overflows+=1
            digests,flags=self.batch(header,first_nonce,count,target)
            self.fallback_rehashed+=count
            items=[(i,digests[i]) for i,flag in enumerate(flags) if flag]
            if len(items)!=found:raise RuntimeError('Candidate overflow fallback count mismatch')
        elif found:
            offsets=(C.c_uint*found)();digests=C.create_string_buffer(32*found)
            self.check(self.dtoh(offsets,self.buffers[6],4*found),'candidate offsets download')
            self.check(self.dtoh(digests,self.buffers[7],32*found),'candidate digests download')
            items=sorted((offsets[i],digests.raw[32*i:32*(i+1)]) for i in range(found))
            if len({i for i,_ in items})!=found or any(i>=count for i,_ in items):
                raise RuntimeError('Invalid or duplicate GPU candidate offset')
        else:items=[]
        return dict(total_candidates=found,overflow=overflow,candidates=items)

    def close(self):
        if not self.closed:
            for pointer in reversed(self.buffers):
                self.free(pointer)
            self.buffers=[]
            if self.module.value:
                self.module_unload(self.module)
                self.module=C.c_void_p()
            if self.context.value:
                self.ctx_destroy(self.context)
                self.context=C.c_void_p()
            if self.dll_dir:
                self.dll_dir.close()
            self.closed=True

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()
