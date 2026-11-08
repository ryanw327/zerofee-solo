"""CPU protocol and submission-guard tests for independently constructed candidates."""
import copy
from dataclasses import replace
import struct
import time
import unittest
from address import payout_script
from bitcoin import sha256d,target_from_compact
from block_builder import (compact_size,varbytes,parse_transaction,ordered_transactions,
                           merkle_root,height_push,build_candidate,COMMIT_PREFIX,ZERO32)
from miner import Miner,StaleJob,InvalidSolution

ADDRESS="bcrt1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080"
def transaction(prev,index=0,witness=False):
    vin=b"\x01"+prev+struct.pack("<I",index)+b"\x00"+b"\xff"*4
    vout=b"\x01"+struct.pack("<Q",1000)+varbytes(b"\x51")
    return struct.pack("<I",2)+(b"\x00\x01" if witness else b"")+vin+vout+(b"\x01\x01\x01" if witness else b"")+bytes(4)
def fixture():
    parent=parse_transaction(transaction(b"\x11"*32))
    child=parse_transaction(transaction(parent.txid,witness=True))
    entries=[{"data":parent.raw.hex(),"txid":parent.txid[::-1].hex(),"hash":parent.wtxid[::-1].hex(),"fee":10},
             {"data":child.raw.hex(),"txid":child.txid[::-1].hex(),"hash":child.wtxid[::-1].hex(),"fee":10}]
    return {"height":101,"version":0x20000000,"previousblockhash":"22"*32,"bits":"207fffff",
            "target":f"{target_from_compact(0x207fffff):064x}","curtime":1700000000,"mintime":1699999999,
            "coinbasevalue":5_000_000_020,"transactions":entries,"rules":["!segwit"]}
class DummyRPC:
    def __init__(self,chain="regtest",tip="22"*32):self.chain=chain;self.tip=tip;self.submissions=0
    def guard(self):
        if self.chain!="regtest":raise RuntimeError("not regtest")
    def call(self,method,*args):
        if method=="getbestblockhash":return self.tip
        if method=="submitblock":self.submissions+=1;return None
        return None
class ProtocolTests(unittest.TestCase):
    def test_height_minimal_encoding(self):
        expected={1:"51",16:"60",17:"0111",127:"017f",128:"028000",255:"02ff00",256:"020001"}
        for n,h in expected.items():self.assertEqual(height_push(n).hex(),h)
        with self.assertRaises(ValueError):height_push(0)
    def test_compactsize_boundaries(self):
        for n,h in [(252,"fc"),(253,"fdfd00"),(65535,"fdffff"),(65536,"fe00000100"),(1<<32,"ff0000000001000000")]:
            self.assertEqual(compact_size(n).hex(),h)
    def test_address_network_and_checksum(self):
        self.assertTrue(payout_script(ADDRESS).startswith(b"\0\x14"))
        for invalid in ["bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",ADDRESS[:-1]+"q","B"+ADDRESS[1:]]:
            with self.assertRaises(ValueError):payout_script(invalid)
    def test_transaction_witness_ids(self):
        tx=parse_transaction(transaction(b"\x11"*32,witness=True))
        self.assertNotEqual(tx.txid,tx.wtxid)
        self.assertEqual(tx.txid,sha256d(tx.stripped))
        self.assertEqual(tx.wtxid,sha256d(tx.raw))
        self.assertEqual(tx.witness,((b"\x01",),))
    def test_malformed_serialization(self):
        raw=transaction(b"\x11"*32)
        for invalid in [raw[:-1],raw+b"\0",raw[:4]+b"\xfd\x01\x00"+raw[5:],raw[:4]+b"\x00\x02"+raw[4:]]:
            with self.assertRaises(ValueError):parse_transaction(invalid)
    def test_topological_order_from_outpoints(self):
        template=fixture();entries=template["transactions"]
        result=ordered_transactions(list(reversed(entries)))
        self.assertEqual(result[1].inputs[0][0],result[0].txid)
        with self.assertRaises(ValueError):ordered_transactions(entries+[entries[0]])
        bad=copy.deepcopy(entries);bad[0]["depends"]=[2];bad[1]["depends"]=[1]
        with self.assertRaises(ValueError):ordered_transactions(bad)
        bad=copy.deepcopy(entries);bad[0]["depends"]=[0]
        with self.assertRaises(ValueError):ordered_transactions(bad)
    def test_template_hash_tampering(self):
        template=fixture();template["transactions"][0]["txid"]="00"*32
        with self.assertRaises(ValueError):build_candidate(template,ADDRESS,1)
    def test_coinbase_merkle_and_commitment(self):
        job=build_candidate(fixture(),ADDRESS,1);job.validate()
        cb=job.transactions[0]
        self.assertEqual(cb.outputs[0],(job.reward,payout_script(ADDRESS)))
        root=merkle_root([ZERO32]+[tx.wtxid for tx in job.transactions[1:]])
        self.assertEqual(cb.outputs[1],(0,COMMIT_PREFIX+sha256d(root+ZERO32)))
        self.assertEqual(job.header[36:68],merkle_root([t.txid for t in job.transactions]))
        self.assertEqual(cb.witness,((ZERO32,),))
    def test_extranonce_changes_work(self):
        a=build_candidate(fixture(),ADDRESS,1);b=build_candidate(fixture(),ADDRESS,2)
        self.assertNotEqual(a.job_id,b.job_id)
        self.assertNotEqual(a.transactions[0].txid,b.transactions[0].txid)
    def test_limits_and_reward(self):
        for key,value in [("weightlimit",1),("coinbasevalue",5_000_000_021),("target","00"*32)]:
            template=fixture();template[key]=value
            with self.assertRaises(ValueError):build_candidate(template,ADDRESS,1)
    def test_wrong_merkle_or_payout_rejected(self):
        job=build_candidate(fixture(),ADDRESS,1)
        with self.assertRaises(ValueError):replace(job,header=job.header[:36]+bytes(32)+job.header[68:]).validate()
        with self.assertRaises(ValueError):replace(job,payout=b"\x51").validate()
    def test_solution_and_stale_guards(self):
        job=build_candidate(fixture(),ADDRESS,1);rpc=DummyRPC();miner=Miner(rpc,ADDRESS,None)
        values=[(i,sha256d(job.header[:76]+struct.pack("<I",i))) for i in range(64)]
        good=next((n,h) for n,h in values if int.from_bytes(h,"little")<=job.target)
        bad=next((n,h) for n,h in values if int.from_bytes(h,"little")>job.target)
        self.assertEqual(miner.validate_solution(job,*good),good[1])
        with self.assertRaises(InvalidSolution):miner.validate_solution(job,good[0],bytes(32))
        with self.assertRaises(InvalidSolution):miner.validate_solution(job,*bad)
        with self.assertRaises(StaleJob):miner.submit_solution(replace(job,created=time.monotonic()-10),*good)
        rpc.tip="33"*32
        with self.assertRaises(StaleJob):miner.submit_solution(job,*good)
        self.assertEqual(rpc.submissions,0)
    def test_mainnet_guard(self):
        with self.assertRaises(RuntimeError):Miner(DummyRPC(chain="main"),ADDRESS,None)
    def test_merkle_odd_leaf(self):
        a,b,c=[bytes([i])*32 for i in (1,2,3)]
        self.assertEqual(merkle_root([a]),a)
        self.assertEqual(merkle_root([a,b,c]),sha256d(sha256d(a+b)+sha256d(c+c)))
    def test_hash_and_time_bounds(self):
        class RunRPC(DummyRPC):
            def call(self,method,*args):
                if method=="getblocktemplate" and "mode" not in args[0]:return fixture()
                return super().call(method,*args)
        class NoSolutionGPU:
            def batch(self,header,start,count,target):return [bytes(32)]*count,[0]*count
        result=Miner(RunRPC(),ADDRESS,NoSolutionGPU()).run(max_seconds=1,max_hashes=5,max_blocks=1,batch_size=2)
        self.assertEqual(result["hashes"],5)
        self.assertFalse(result["blocks"])
        class SlowRPC(RunRPC):
            def call(self,method,*args):
                if method=="getblocktemplate" and "mode" not in args[0]:time.sleep(0.01)
                return super().call(method,*args)
        result=Miner(SlowRPC(),ADDRESS,NoSolutionGPU()).run(max_seconds=0.001,max_hashes=5,max_blocks=1,batch_size=2)
        self.assertEqual(result["hashes"],0)
    def test_uncertain_submission_stops_without_retry(self):
        import subprocess
        from miner import SubmissionUncertain
        class TimeoutRPC(DummyRPC):
            def call(self,method,*args):
                if method=="submitblock":
                    self.submissions+=1
                    raise subprocess.TimeoutExpired("local-test-rpc",0.01)
                return super().call(method,*args)
        rpc=TimeoutRPC();miner=Miner(rpc,ADDRESS,None);job=build_candidate(fixture(),ADDRESS,1)
        nonce,digest=next((n,sha256d(job.header[:76]+struct.pack("<I",n))) for n in range(64)
                          if int.from_bytes(sha256d(job.header[:76]+struct.pack("<I",n)),"little")<=job.target)
        with self.assertRaises(SubmissionUncertain):miner.submit_solution(job,nonce,digest)
        self.assertEqual(rpc.submissions,1)
    def test_large_rpc_payload_uses_stdin(self):
        import tempfile
        from unittest.mock import patch
        from types import SimpleNamespace
        from miner import NodeRPC
        with tempfile.TemporaryDirectory() as directory:
            rpc=NodeRPC(directory)
            payload="ab"*50_000
            with patch("miner.subprocess.run",return_value=SimpleNamespace(returncode=0,stdout="null",stderr="")) as run:
                self.assertIsNone(rpc.call("submitblock",payload))
                argv=run.call_args.args[0]
                self.assertNotIn(payload,argv)
                self.assertIn("-stdin",argv)
                self.assertEqual(run.call_args.kwargs["input"],payload+"\n")

    def test_owned_node_cleanup_on_startup_failure(self):
        from test_helpers import stop_owned_test_node
        from unittest.mock import Mock
        process=Mock()
        process.poll.side_effect=[None,None]
        rpc=Mock()
        rpc.call.side_effect=RuntimeError("RPC startup failed")
        self.assertTrue(stop_owned_test_node(process,rpc))
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
    def test_owned_node_clean_stop(self):
        from test_helpers import stop_owned_test_node
        from unittest.mock import Mock
        process=Mock();process.poll.return_value=None;rpc=Mock()
        self.assertFalse(stop_owned_test_node(process,rpc))
        rpc.call.assert_called_once_with("stop")
        process.terminate.assert_not_called()
        process.wait.assert_called_once_with(timeout=20)

if __name__=="__main__":unittest.main(verbosity=2)
