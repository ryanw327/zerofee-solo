"""Independent Bitcoin candidate construction from getblocktemplate, BIP34 and BIP141."""
from dataclasses import dataclass
import heapq
import struct
import time
from bitcoin import sha256d,target_from_compact,with_nonce
from address import payout_script
from network_policy import network_profile,subsidy

MAX_MONEY=21_000_000*100_000_000
COMMIT_PREFIX=bytes.fromhex("6a24aa21a9ed")
ZERO32=bytes(32)

def compact_size(n):
    if type(n) is not int or not 0<=n<=0xffffffffffffffff: raise ValueError("Invalid CompactSize")
    if n<253:return bytes([n])
    if n<=0xffff:return b"\xfd"+struct.pack("<H",n)
    if n<=0xffffffff:return b"\xfe"+struct.pack("<I",n)
    return b"\xff"+struct.pack("<Q",n)

def varbytes(data):return compact_size(len(data))+data

class Reader:
    def __init__(self,data):self.data=data;self.pos=0
    def take(self,n):
        if n<0 or self.pos+n>len(self.data):raise ValueError("Truncated transaction")
        out=self.data[self.pos:self.pos+n];self.pos+=n;return out
    def number(self):
        lead=self.take(1)[0]
        if lead<253:return lead
        count={253:2,254:4,255:8}[lead]
        value=int.from_bytes(self.take(count),"little")
        if value<{253:253,254:65536,255:1<<32}[lead]:raise ValueError("Noncanonical CompactSize")
        return value
    def blob(self):return self.take(self.number())

@dataclass(frozen=True)
class Transaction:
    raw:bytes
    stripped:bytes
    txid:bytes
    wtxid:bytes
    inputs:tuple
    outputs:tuple
    witness:tuple
    scripts:tuple
    @property
    def coinbase(self):
        return len(self.inputs)==1 and self.inputs[0]==(ZERO32,0xffffffff)

def parse_transaction(raw):
    if not isinstance(raw,bytes) or not 10<=len(raw)<=4_000_000:raise ValueError("Invalid transaction length")
    r=Reader(raw);version=r.take(4)
    witness_flag=raw[4:6]==b"\x00\x01"
    if raw[4]==0 and not witness_flag:raise ValueError("Unsupported witness flags/empty inputs")
    if witness_flag:r.take(2)
    vin_start=r.pos;ni=r.number()
    if not 1<=ni<=100_000:raise ValueError("Invalid input count")
    inputs=[];scripts=[]
    for _ in range(ni):
        prev=r.take(32);index=int.from_bytes(r.take(4),"little");scripts.append(r.blob());r.take(4)
        inputs.append((prev,index))
    vin=raw[vin_start:r.pos];vout_start=r.pos;no=r.number()
    if not 1<=no<=100_000:raise ValueError("Invalid output count")
    outputs=[]
    for _ in range(no):
        value=int.from_bytes(r.take(8),"little")
        if value>MAX_MONEY:raise ValueError("Invalid output amount")
        outputs.append((value,r.blob()))
    if sum(v for v,_ in outputs)>MAX_MONEY:raise ValueError("Output sum exceeds money range")
    vout=raw[vout_start:r.pos];witness=[]
    if witness_flag:
        for _ in range(ni):
            count=r.number()
            if count>100_000:raise ValueError("Witness stack too large")
            witness.append(tuple(r.blob() for _ in range(count)))
        if not any(witness):raise ValueError("Superfluous witness encoding")
    lock=r.take(4)
    if r.pos!=len(raw):raise ValueError("Trailing transaction bytes")
    stripped=version+vin+vout+lock
    return Transaction(raw,stripped,sha256d(stripped),sha256d(raw),tuple(inputs),tuple(outputs),tuple(witness),tuple(scripts))

def merkle_root(leaves):
    if not leaves or any(len(h)!=32 for h in leaves):raise ValueError("Invalid merkle leaves")
    layer=list(leaves)
    while len(layer)>1:
        if len(layer)&1:layer.append(layer[-1])
        layer=[sha256d(layer[i]+layer[i+1]) for i in range(0,len(layer),2)]
    return layer[0]

def push(data):
    if len(data)<=75:return bytes([len(data)])+data
    if len(data)<=255:return b"\x4c"+bytes([len(data)])+data
    raise ValueError("Coinbase push too large")

def height_push(height):
    if type(height) is not int or not 0<height<1<<31:raise ValueError("Invalid height")
    if height<=16:return bytes([0x50+height])
    data=height.to_bytes((height.bit_length()+7)//8,"little")
    if data[-1]&0x80:data+=b"\0"
    return push(data)

def coinbase(height,extra,payout,reward,commitment,reserved=ZERO32,aux=()):
    if type(extra) is not int or not 0<=extra<1<<64:raise ValueError("Extranonce must be uint64")
    if type(reward) is not int or not 0<=reward<=MAX_MONEY:raise ValueError("Invalid reward")
    if len(reserved)!=32 or len(commitment)!=38 or not commitment.startswith(COMMIT_PREFIX):
        raise ValueError("Invalid witness commitment")
    script=height_push(height)+push(struct.pack("<Q",extra))+push(b"/NVRTC-local/")
    for value in aux:script+=push(value)
    if not 2<=len(script)<=100:raise ValueError("Coinbase scriptSig outside consensus limits")
    vin=b"\x01"+ZERO32+b"\xff"*4+varbytes(script)+b"\xff"*4
    outputs=b"\x02"+struct.pack("<Q",reward)+varbytes(payout)+bytes(8)+varbytes(commitment)
    witness=b"\x01\x20"+reserved
    return struct.pack("<I",2)+b"\x00\x01"+vin+outputs+witness+bytes(4)

def ordered_transactions(entries):
    txs=[parse_transaction(bytes.fromhex(e["data"])) for e in entries]
    ids={t.txid:i for i,t in enumerate(txs)}
    if len(ids)!=len(txs) or any(t.coinbase for t in txs):raise ValueError("Duplicate transaction or template coinbase")
    for e,t in zip(entries,txs):
        for field,digest in (("txid",t.txid),("hash",t.wtxid)):
            if field in e and e[field]!=digest[::-1].hex():raise ValueError("Template transaction hash mismatch")
    parents=[]
    for i,(e,t) in enumerate(zip(entries,txs)):
        deps={ids[h] for h,_ in t.inputs if h in ids}
        for index in e.get("depends",[]):
            if type(index) is not int or not 1<=index<=len(txs):raise ValueError("Invalid dependency index")
            deps.add(index-1)
        if i in deps:raise ValueError("Self dependency")
        parents.append(deps)
    children=[[] for _ in txs]
    degree=[len(p) for p in parents]
    for i,deps in enumerate(parents):
        for parent in deps:children[parent].append(i)
    ready=[i for i,d in enumerate(degree) if not d];heapq.heapify(ready);order=[]
    while ready:
        i=heapq.heappop(ready);order.append(i)
        for child in children[i]:
            degree[child]-=1
            if degree[child]==0:heapq.heappush(ready,child)
    if len(order)!=len(txs):raise ValueError("Cyclic transaction dependencies")
    return tuple(txs[i] for i in order)

@dataclass(frozen=True)
class Candidate:
    header:bytes
    transactions:tuple
    height:int
    previous:str
    target:int
    reward:int
    payout:bytes
    extra:int
    created:float
    weight_limit:int
    size_limit:int
    network:str="regtest"
    @property
    def job_id(self):return sha256d(self.header[:76]).hex()
    def raw(self,nonce=0):
        return with_nonce(self.header,nonce)+compact_size(len(self.transactions))+b"".join(t.raw for t in self.transactions)
    def validate(self,nonce=0,require_pow=False):
        if len(self.header)!=80 or not self.transactions or not self.transactions[0].coinbase:
            raise ValueError("Invalid candidate structure")
        if self.target>network_profile(self.network).pow_limit:raise ValueError("Target exceeds network proof limit")
        cb=self.transactions[0]
        if any(t.coinbase for t in self.transactions[1:]):raise ValueError("Extra coinbase")
        if len({t.txid for t in self.transactions})!=len(self.transactions):raise ValueError("Duplicate block transaction")
        seen=set()
        ids={t.txid for t in self.transactions[1:]}
        spent=set()
        by_id={t.txid:t for t in self.transactions}
        for t in self.transactions[1:]:
            if any(h in ids and h not in seen for h,_ in t.inputs):raise ValueError("Non-topological block")
            for outpoint in t.inputs:
                h,index=outpoint
                if outpoint==(ZERO32,0xffffffff):raise ValueError("Null outpoint in non-coinbase transaction")
                if outpoint in spent:raise ValueError("Duplicate spent outpoint in candidate")
                spent.add(outpoint)
                if h==cb.txid:raise ValueError("Immature in-block coinbase spend")
                if h in by_id and index>=len(by_id[h].outputs):raise ValueError("Missing in-block output")
            seen.add(t.txid)
        if not cb.scripts[0].startswith(height_push(self.height)):raise ValueError("Wrong BIP34 height")
        if not 2<=len(cb.scripts[0])<=100:raise ValueError("Invalid coinbase script length")
        if cb.witness!=((ZERO32,),):raise ValueError("Wrong coinbase witness reserved value")
        commit=COMMIT_PREFIX+sha256d(merkle_root([ZERO32]+[t.wtxid for t in self.transactions[1:]])+ZERO32)
        if cb.outputs!=((self.reward,self.payout),(0,commit)):raise ValueError("Coinbase reward/payout/commitment mismatch")
        if self.header[4:36]!=bytes.fromhex(self.previous)[::-1]:raise ValueError("Wrong previous hash")
        if self.header[36:68]!=merkle_root([t.txid for t in self.transactions]):raise ValueError("Wrong merkle root")
        if target_from_compact(int.from_bytes(self.header[72:76],"little"))!=self.target:raise ValueError("Wrong target")
        raw=self.raw(nonce)
        base_size=80+len(compact_size(len(self.transactions)))+sum(len(t.stripped) for t in self.transactions)
        if base_size*3+len(raw)>min(self.weight_limit,4_000_000) or len(raw)>min(self.size_limit,4_000_000):raise ValueError("Block exceeds limits")
        digest=sha256d(raw[:80])
        if require_pow and int.from_bytes(digest,"little")>self.target:raise ValueError("CPU proof of work failed")
        return digest

def build_candidate(template,address,extra,network="regtest"):
    profile=network_profile(network)
    payout=payout_script(address,network)
    if "coinbasevalue" not in template:raise ValueError("coinbasevalue template capability required")
    txs=ordered_transactions(template.get("transactions",[]))
    height=template["height"];reward=template["coinbasevalue"]
    if type(height) is not int or type(reward) is not int:raise ValueError("Invalid template numeric types")
    # Profile-aware accounting; a mainnet profile does not authorize execution.
    fees=[e.get("fee") for e in template.get("transactions",[])]
    if all(type(v) is int for v in fees):
        if reward!=subsidy(height,network)+sum(fees):raise ValueError("Network subsidy/fee accounting mismatch")
    witness_root=merkle_root([ZERO32]+[t.wtxid for t in txs])
    commitment=COMMIT_PREFIX+sha256d(witness_root+ZERO32)
    if template.get("default_witness_commitment") not in (None,commitment.hex()):
        raise ValueError("Template witness commitment mismatch")
    cb=parse_transaction(coinbase(height,extra,payout,reward,commitment,aux=tuple(bytes.fromhex(v) for v in template.get("coinbaseaux",{}).values())))
    all_txs=(cb,)+txs
    bits=int(template["bits"],16);target=target_from_compact(bits)
    if target!=int(template["target"],16):raise ValueError("Compact and expanded targets differ")
    if target>profile.pow_limit:raise ValueError("Target exceeds network proof limit")
    stamp=max(template["curtime"],template.get("mintime",0))
    header=struct.pack("<I",template["version"]&0xffffffff)+bytes.fromhex(template["previousblockhash"])[::-1]+merkle_root([t.txid for t in all_txs])+struct.pack("<III",stamp,bits,0)
    candidate=Candidate(header,all_txs,height,template["previousblockhash"],target,reward,payout,extra,time.monotonic(),
                        template.get("weightlimit",4_000_000),template.get("sizelimit",4_000_000),network)
    candidate.validate()
    sigops=[e.get("sigops") for e in template.get("transactions",[])]
    if all(type(v) is int for v in sigops) and sum(sigops)>template.get("sigoplimit",80_000):
        raise ValueError("Sigop limit exceeded")
    return candidate
