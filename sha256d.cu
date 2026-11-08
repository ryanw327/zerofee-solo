// New local proof of concept, authored from FIPS 180-4 and Bitcoin's header format.
// Fixed 80-byte SHA-256d. No networking, wallet access, or unbounded search.
typedef unsigned int u32;
typedef unsigned char u8;
__device__ __constant__ u32 K[64] = {
  0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
  0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
  0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
  0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
  0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
  0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
  0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
  0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
};
__device__ __forceinline__ u32 rr(u32 x, unsigned n) { return (x >> n) | (x << (32-n)); }
__device__ void initial(u32* s) {
  s[0]=0x6a09e667u; s[1]=0xbb67ae85u; s[2]=0x3c6ef372u; s[3]=0xa54ff53au;
  s[4]=0x510e527fu; s[5]=0x9b05688cu; s[6]=0x1f83d9abu; s[7]=0x5be0cd19u;
}
__device__ void compress_words(u32* state, const u32* input) {
#if ROLLING_SCHEDULE
  u32 w[16];
#else
  u32 w[64];
#endif
#if SHA_UNROLL
#pragma unroll SHA_UNROLL
#endif
  for(unsigned i=0;i<16;i++) w[i]=input[i];
#if !ROLLING_SCHEDULE
#if SHA_UNROLL
#pragma unroll SHA_UNROLL
#endif
  for(unsigned i=16;i<64;i++) {
    u32 x=w[i-15], y=w[i-2];
    w[i]=w[i-16]+(rr(x,7)^rr(x,18)^(x>>3))+w[i-7]+(rr(y,17)^rr(y,19)^(y>>10));
  }
#endif
  u32 a=state[0],b=state[1],c=state[2],d=state[3],e=state[4],f=state[5],g=state[6],h=state[7];
#if SHA_UNROLL
#pragma unroll SHA_UNROLL
#endif
  for(unsigned i=0;i<64;i++) {
#if ROLLING_SCHEDULE
    if(i>=16) {
      u32 x=w[(i-15)&15],y=w[(i-2)&15];
      w[i&15]+=(rr(x,7)^rr(x,18)^(x>>3))+w[(i-7)&15]+(rr(y,17)^rr(y,19)^(y>>10));
    }
    u32 word=w[i&15];
#else
    u32 word=w[i];
#endif
    u32 t1=h+(rr(e,6)^rr(e,11)^rr(e,25))+((e&f)^((~e)&g))+K[i]+word;
    u32 t2=(rr(a,2)^rr(a,13)^rr(a,22))+((a&b)^(a&c)^(b&c));
    h=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
  }
  state[0]+=a;state[1]+=b;state[2]+=c;state[3]+=d;state[4]+=e;state[5]+=f;state[6]+=g;state[7]+=h;
}
__device__ __forceinline__ u32 read_be32(const u8* p) {
  return ((u32)p[0]<<24)|((u32)p[1]<<16)|((u32)p[2]<<8)|p[3];
}
__device__ void compress(u32* state, const u8* block) {
  u32 input[16];
  for(unsigned i=0;i<16;i++) input[i]=read_be32(block+4*i);
  compress_words(state,input);
}
extern "C" __global__ void prepare_midstate(const u8* header, u32* midstate) {
  if(blockIdx.x || threadIdx.x) return;
  u32 state[8];
  initial(state);
  compress(state,header); // Includes feed-forward into the chaining state.
  for(unsigned i=0;i<8;i++) midstate[i]=state[i];
}
__device__ void sha256d80(const u8* header, u32 nonce, u8* digest, const u32* midstate) {
  u32 state[8];
#if USE_MIDSTATE
  for(unsigned i=0;i<8;i++) state[i]=midstate[i];
#else
  initial(state);
  compress(state,header);
#endif
#if WORD_INPUT
  // Explicit byte loads and shifts avoid alignment and strict-aliasing hazards.
  u32 words[16]={0};
  words[0]=read_be32(header+64);words[1]=read_be32(header+68);words[2]=read_be32(header+72);
#if BYTE_PERMUTATION
  words[3]=__byte_perm(nonce,0,0x0123);
#else
  words[3]=(nonce>>24)|((nonce>>8)&0x0000ff00u)|((nonce<<8)&0x00ff0000u)|(nonce<<24);
#endif
  words[4]=0x80000000u;words[15]=640u;
  compress_words(state,words);
  for(unsigned i=0;i<8;i++) words[i]=state[i];
  words[8]=0x80000000u;
  for(unsigned i=9;i<15;i++) words[i]=0;
  words[15]=256u;
  initial(state);
  compress_words(state,words);
#else
  u8 block[64];
  for(unsigned i=0;i<64;i++) block[i]=0;
  for(unsigned i=0;i<16;i++) block[i]=header[64+i];
  for(unsigned i=0;i<4;i++) block[12+i]=(u8)(nonce>>(8*i)); // header bytes 76..79, little endian
  block[16]=0x80; block[62]=0x02; block[63]=0x80; // 80*8 bits
  compress(state,block);
  for(unsigned i=0;i<64;i++) block[i]=0;
  for(unsigned i=0;i<8;i++) {
    block[4*i]=(u8)(state[i]>>24);block[4*i+1]=(u8)(state[i]>>16);
    block[4*i+2]=(u8)(state[i]>>8);block[4*i+3]=(u8)state[i];
  }
  block[32]=0x80;block[62]=0x01; // 32*8 bits
  initial(state);
  compress(state,block);
#endif
  for(unsigned i=0;i<8;i++) {
    digest[4*i]=(u8)(state[i]>>24);digest[4*i+1]=(u8)(state[i]>>16);
    digest[4*i+2]=(u8)(state[i]>>8);digest[4*i+3]=(u8)state[i];
  }
}
__device__ bool at_most_target(const u8* raw_digest, const u8* target_le) {
  // Bitcoin's uint256 value treats the raw SHA digest as little endian.
  for(int i=31;i>=0;i--) {
    if(raw_digest[i]<target_le[i]) return true;
    if(raw_digest[i]>target_le[i]) return false;
  }
  return true; // equality is accepted
}
extern "C" __global__ void hash_nonce_batch(const u8* header, u32 first_nonce, u32 count,
                                            const u8* target_le, u8* hashes, u8* flags, const u32* midstate) {
  u32 index=blockIdx.x*blockDim.x+threadIdx.x;
  if(index>=count) return;
  u8 digest[32];
  sha256d80(header,first_nonce+index,digest,midstate); // uint32 wrap is intentional and tested
  for(unsigned i=0;i<32;i++) hashes[32*index+i]=digest[i];
  flags[index]=at_most_target(digest,target_le)?1:0;
}
extern "C" __global__ void compact_candidates(const u8* header,u32 first,u32 count,const u8* target,
    u32* total,u32* offsets,u8* digests,u32 capacity,const u32* midstate) {
  u32 index=blockIdx.x*blockDim.x+threadIdx.x;
  if(index>=count) return;
  u8 digest[32];sha256d80(header,first+index,digest,midstate);
  if(at_most_target(digest,target)) {
    u32 slot=atomicAdd(total,1u);
    if(slot<capacity) {
      offsets[slot]=index;
      for(u32 j=0;j<32;j++) digests[32*slot+j]=digest[j];
    }
  }
}
