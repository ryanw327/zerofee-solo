// CPU-only test adapter for the ACTUAL sha256d.cu source. No CUDA headers/runtime.
// Sequential atomicAdd emulation checks output logic, not GPU atomic concurrency.
#define __device__
#define __constant__
#define __global__
#define __forceinline__ inline
struct CpuIndex { unsigned x; };
static CpuIndex blockIdx, blockDim, threadIdx;
static unsigned atomicAdd(unsigned* target, unsigned value) {
    unsigned prior=*target; *target+=value; return prior;
}
static unsigned __byte_perm(unsigned a,unsigned b,unsigned selectors) {
    unsigned result=0;
    for(unsigned n=0;n<4;n++) {
        unsigned selector=(selectors>>(4*n))&15;
        unsigned byte=selector&7;
        unsigned value=((byte<4?a:b)>>(8*(byte&3)))&255;
        if(selector&8) value=(value&128)?255:0;
        result|=value<<(8*n);
    }
    return result;
}
#include "sha256d.cu"
#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C" __attribute__((visibility("default")))
#endif

EXPORT int cpu_batch(const unsigned char* header,unsigned first,unsigned count,
    const unsigned char* target,unsigned threads,int compact,unsigned capacity,
    unsigned char* hashes,unsigned char* flags,unsigned* total,unsigned* offsets,
    unsigned char* sparse_digests) {
    if(!count || count>262144 || (threads!=64 && threads!=128 && threads!=256 && threads!=512) || capacity>64) return -1;
    unsigned midstate[8];
    blockIdx.x=threadIdx.x=0;blockDim.x=threads;
    prepare_midstate(header,midstate);
    *total=0;
    for(blockIdx.x=0;blockIdx.x<(count+threads-1)/threads;blockIdx.x++) {
        for(threadIdx.x=0;threadIdx.x<threads;threadIdx.x++) {
            if(compact) compact_candidates(header,first,count,target,total,offsets,sparse_digests,capacity,midstate);
            else hash_nonce_batch(header,first,count,target,hashes,flags,midstate);
        }
    }
    return 0;
}
EXPORT int cpu_at_most(const unsigned char* digest,const unsigned char* target) {
    return at_most_target(digest,target)?1:0;
}
