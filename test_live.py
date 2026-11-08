"""Mocked mainnet only; published genesis proof tests routing, never a live submission."""
from dataclasses import replace
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
import io
from unittest.mock import patch
from collections import Counter
from types import SimpleNamespace
from address import payout_script
from bitcoin import sha256d,known_headers,target_from_compact,with_nonce
from block_builder import varbytes,parse_transaction,merkle_root,build_candidate
from journal import SubmissionJournal
from miner import Miner,NodeRPC,NodeNotReady,InvalidSolution,InvalidCandidate,SubmissionUncertain,StaleJob
from network_policy import network_profile
from test_builder import fixture

ADDRESS='bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4'
def main_template():
    t=fixture();t.update(height=974813,bits='1701e5ac',target=f'{target_from_compact(0x1701e5ac):064x}',
        coinbasevalue=312500020,capabilities=['proposal'],vbavailable={},vbrequired=0,
        noncerange='00000000ffffffff',weightlimit=4000000,sizelimit=4000000)
    for entry in t['transactions']:
        tx=parse_transaction(bytes.fromhex(entry['data']));entry.update(weight=len(tx.stripped)*3+len(tx.raw),sigops=0)
    return t

class MockLive(NodeRPC):
    """In-memory mainnet interface: no executable, socket, credential or GPU access."""
    def __init__(self):
        self.network='mainnet';self.calls=Counter();self.deadline=None;self.identity_checked=False
        self.template=main_template();self.tip=self.template['previousblockhash'];self.submissions=0;self.verifications=0
        self.info=dict(chain='main',blocks=974812,headers=974812,bestblockhash=self.tip,initialblockdownload=False,warnings=[])
        self.net=dict(version=320100,networkactive=True,connections=2,warnings=[])
        self.failure=None;self.rejection=None;self.confirmations=2;self.proposal_rejection=None
    def verify_session(self):self.verifications+=1
    def call(self,method,*args):
        self.calls[method]+=1
        if method=='getblockchaininfo':return dict(self.info,bestblockhash=self.tip)
        if method=='getnetworkinfo':return self.net
        if method=='getblockhash':return network_profile('mainnet').genesis
        if method=='getbestblockhash':return self.tip
        if method=='getblocktemplate':return self.proposal_rejection if args[0].get('mode')=='proposal' else self.template
        if method=='submitblock':
            self.submissions+=1
            if self.failure:raise self.failure
            self.submitted_hash=sha256d(bytes.fromhex(args[0])[:80])[::-1].hex()
            return self.rejection
        if method=='getblockheader':return {'hash':args[0],'confirmations':self.confirmations}
        if method=='validateaddress':return {'isvalid':True,'scriptPubKey':payout_script(ADDRESS,'mainnet').hex()}
        raise AssertionError('Unexpected mock method: '+method)

class HistoricalGenesis:
    """Published complete historical block; fake Core accepts it for routing tests only.
    Real Core would reject this as duplicate. Modern BIP34/SegWit candidates are
    validated independently on regtest and in the prior live proposal checks.
    """
    network='mainnet';height=0;previous='00'*32;target=target_from_compact(0x1d00ffff);job_id='published-genesis-fixture'
    def __init__(self):
        self.header=known_headers()[0][1];self.created=time.monotonic()
        text=b'The Times 03/Jan/2009 Chancellor on brink of second bailout for banks'
        script=bytes.fromhex('04ffff001d0104')+bytes([len(text)])+text
        key=bytes.fromhex('04678afdb0fe5548271967f1a67130b7105cd6a828e03909a67962e0ea1f61deb649f6bc3f4cef38c4f35504e51ec112de5c384df7ba0b8d578a4c702b6bf11d5f')
        raw=struct.pack('<I',1)+b'\1'+bytes(32)+b'\xff'*4+varbytes(script)+b'\xff'*4+b'\1'+struct.pack('<Q',5000000000)+varbytes(b'\x41'+key+b'\xac')+bytes(4)
        self.coinbase=parse_transaction(raw)
    def raw(self,nonce):return with_nonce(self.header,nonce)+b'\1'+self.coinbase.raw
    def validate(self,nonce,require_pow=False):
        if self.header[36:68]!=self.coinbase.txid:raise ValueError('Published merkle root mismatch')
        digest=sha256d(self.raw(nonce)[:80])
        if require_pow and int.from_bytes(digest,'little')>self.target:raise ValueError('Invalid published proof')
        return digest

class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'journal.json'
    def tearDown(self):self.temp.cleanup()
    def published(self):
        rpc=MockLive();rpc.tip='00'*32;job=HistoricalGenesis();nonce=2083236893
        return rpc,job,nonce,job.validate(nonce,True)
    def test_mainnet_template_construction_and_hash_budget(self):
        rpc=MockLive()
        class NoSolutionGPU:
            def batch(self,h,n,count,t):return [bytes(32)]*count,[0]*count
        with SubmissionJournal(self.path) as journal:
            runner=Miner(rpc,ADDRESS,NoSolutionGPU(),journal=journal)
            job=runner.new_job();self.assertEqual(job.network,'mainnet');job.validate()
            result=runner.run(max_seconds=1,max_hashes=5,batch_size=2)
        self.assertEqual(result['hashes'],5);self.assertEqual(result['chain'],'mainnet');self.assertEqual(rpc.submissions,0)
    def test_known_mainnet_proof_through_mock_submission_and_journal(self):
        rpc,job,nonce,digest=self.published()
        self.assertEqual(digest[::-1].hex(),network_profile('mainnet').genesis)
        with SubmissionJournal(self.path) as journal:
            result=Miner(rpc,ADDRESS,None,journal=journal).submit_solution(job,nonce,digest)
        self.assertTrue(result['accepted']);self.assertTrue(result['active_chain'])
        self.assertEqual(rpc.submissions,1);self.assertEqual(rpc.verifications,1)
        self.assertEqual(json.loads(self.path.read_text())['status'],'CONFIRMED')
    def test_accepted_side_chain_is_reported_without_false_stale_error(self):
        rpc,job,nonce,digest=self.published();rpc.confirmations=-1
        with SubmissionJournal(self.path) as journal:
            result=Miner(rpc,ADDRESS,None,journal=journal).submit_solution(job,nonce,digest)
        self.assertTrue(result['accepted']);self.assertFalse(result['active_chain'])
    def test_uncertain_transport_leaves_pending_and_refuses_next_run(self):
        for error in [RuntimeError('connection lost'),OSError('I/O'),subprocess.TimeoutExpired('mock',1),KeyboardInterrupt()]:
            path=self.path.with_name(type(error).__name__+'.json');rpc,job,nonce,digest=self.published();rpc.failure=error
            with SubmissionJournal(path) as journal:
                with self.assertRaises(SubmissionUncertain):Miner(rpc,ADDRESS,None,journal=journal).submit_solution(job,nonce,digest)
            self.assertEqual(json.loads(path.read_text())['status'],'PENDING');self.assertEqual(rpc.submissions,1)
            with self.assertRaises(RuntimeError):
                with SubmissionJournal(path):pass
    def test_journal_failure_prevents_any_submission(self):
        rpc,job,nonce,digest=self.published()
        with SubmissionJournal(self.path) as journal,patch.object(journal,'begin',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):Miner(rpc,ADDRESS,None,journal=journal).submit_solution(job,nonce,digest)
        self.assertEqual(rpc.submissions,0)
    def test_rejection_is_recorded_and_does_not_count_as_accepted(self):
        rpc,job,nonce,digest=self.published();rpc.rejection='high-hash'
        with SubmissionJournal(self.path) as journal:
            result=Miner(rpc,ADDRESS,None,journal=journal).submit_solution(job,nonce,digest)
        self.assertFalse(result['accepted']);self.assertEqual(json.loads(self.path.read_text())['status'],'REJECTED')
    def test_bad_proof_digest_network_and_stale_jobs_never_submit(self):
        rpc,job,nonce,digest=self.published()
        with SubmissionJournal(self.path) as journal:
            runner=Miner(rpc,ADDRESS,None,journal=journal)
            for n,h in [(nonce,bytes(32)),(0,sha256d(job.raw(0)[:80]))]:
                with self.assertRaises(InvalidSolution):runner.submit_solution(job,n,h)
            job.network='regtest'
            with self.assertRaises(InvalidCandidate):runner.submit_solution(job,nonce,digest)
            job.network='mainnet';job.created-=20
            with self.assertRaises(StaleJob):runner.submit_solution(job,nonce,digest)
        self.assertEqual(rpc.submissions,0)
    def test_reverify_session_failure_never_submits(self):
        rpc,job,nonce,digest=self.published()
        with SubmissionJournal(self.path) as journal,patch.object(rpc,'verify_session',side_effect=RuntimeError('changed owner')):
            with self.assertRaises(RuntimeError):Miner(rpc,ADDRESS,None,journal=journal).submit_solution(job,nonce,digest)
        self.assertEqual(rpc.submissions,0)
    def test_lost_confirmation_preserves_known_acceptance(self):
        rpc,job,nonce,digest=self.published();original=rpc.call
        def call(method,*args):
            if method=='getblockheader':raise OSError('lost response')
            return original(method,*args)
        rpc.call=call
        with SubmissionJournal(self.path) as journal:
            with self.assertRaises(SubmissionUncertain):Miner(rpc,ADDRESS,None,journal=journal).submit_solution(job,nonce,digest)
        self.assertEqual(json.loads(self.path.read_text())['status'],'ACCEPTED')
    def test_node_readiness_network_genesis_and_version(self):
        rpc=MockLive();rpc.guard()
        for field,value in [('initialblockdownload',True),('headers',974813),('warnings',['warning'])]:
            original=rpc.info[field];rpc.info[field]=value
            with self.assertRaises(NodeNotReady):rpc.guard()
            rpc.info[field]=original
        for field,value in [('networkactive',False),('connections',0)]:
            original=rpc.net[field];rpc.net[field]=value
            with self.assertRaises(NodeNotReady):rpc.guard()
            rpc.net[field]=original
        rpc.net['version']=310000
        with self.assertRaises(RuntimeError):rpc.guard()
        rpc.info['chain']='regtest'
        with self.assertRaises(RuntimeError):rpc.guard()
        rpc=MockLive();original=rpc.call
        rpc.call=lambda m,*a:'00'*32 if m=='getblockhash' else original(m,*a)
        with self.assertRaises(RuntimeError):rpc.guard()
    def test_live_rpc_policy_before_network_and_raw_errors_withheld(self):
        with patch.object(NodeRPC,'verify_session'):
            rpc=NodeRPC(self.temp.name,19732,network='mainnet',cookie=str(self.path),session_pid=1,opt_in=True,submit=True)
        with patch('miner.subprocess.run',side_effect=AssertionError('No network allowed')):
            for method in ['stop','sendrawtransaction','walletpassphrase','loadwallet','submitheader','generatetoaddress']:
                with self.assertRaises(ValueError):rpc.call(method)
        with patch('miner.subprocess.run',return_value=SimpleNamespace(returncode=1,stderr='SECRET')):
            with self.assertRaises(RuntimeError) as exc:rpc.call('getblockchaininfo')
        self.assertNotIn('SECRET',str(exc.exception))
    def test_no_live_rpc_without_both_opt_ins_or_session(self):
        with patch('miner.subprocess.run',side_effect=AssertionError('No process allowed')):
            for kwargs in [{},{'opt_in':True},{'submit':True},{'opt_in':True,'submit':True}]:
                with self.assertRaises((RuntimeError,ValueError)):NodeRPC(self.temp.name,network='mainnet',**kwargs)
    def test_journal_single_writer_and_crash_lock(self):
        with SubmissionJournal(self.path):
            with self.assertRaises(FileExistsError):
                with SubmissionJournal(self.path):pass
        lock=Path(str(self.path)+'.lock');lock.write_text('simulated crashed process')
        with self.assertRaises(FileExistsError):
            with SubmissionJournal(self.path):pass
        self.assertTrue(lock.exists())
    def test_unlocked_journal_cannot_be_used(self):
        journal=SubmissionJournal(self.path)
        with self.assertRaises(RuntimeError):journal.begin('00'*32,0,'00'*32)
        with self.assertRaises(ValueError):Miner(MockLive(),ADDRESS,None,journal=journal)
    def test_corrupt_or_unknown_journal_fails_closed(self):
        for entry in [[],{}, {'status':'UNKNOWN'}, {'status':'ACCEPTED','block_hash':'invalid'}]:
            self.path.write_text(json.dumps(entry))
            with self.assertRaises(RuntimeError):
                with SubmissionJournal(self.path):pass
            self.assertFalse(Path(str(self.path)+'.lock').exists())
    def test_status_only_marks_validated_search_and_does_not_break_mining(self):
        from test_builder import DummyRPC,ADDRESS as TEST_ADDRESS
        class RPC(DummyRPC):
            def call(self,method,*args):
                if method=='getblocktemplate' and 'mode' not in args[0]:return fixture()
                return super().call(method,*args)
        class GPU:
            def batch(self,header,nonce,count,target):
                self.assertion='Mining' in output.getvalue()
                return [bytes(32)]*count,[0]*count
        output=io.StringIO();gpu=GPU()
        with patch('miner.sys.stderr',output):result=Miner(RPC(),TEST_ADDRESS,gpu).run(max_seconds=1,max_hashes=3,batch_size=1)
        self.assertTrue(gpu.assertion);self.assertEqual(result['hashes'],3)
        self.assertEqual(output.getvalue(),'Mining\nStopped\n')
        class Reject(RPC):
            def call(self,method,*args):
                if method=='getblocktemplate' and 'mode' in args[0]:return 'rejected'
                return super().call(method,*args)
        output=io.StringIO()
        with patch('miner.sys.stderr',output),self.assertRaises(InvalidCandidate):Miner(Reject(),TEST_ADDRESS,gpu).run(max_seconds=1,max_hashes=3)
        self.assertNotIn('Mining',output.getvalue())
        class Closed:
            def write(self,text):raise OSError('closed output')
            def flush(self):pass
        with patch('miner.sys.stderr',Closed()):result=Miner(RPC(),TEST_ADDRESS,gpu).run(max_seconds=1,max_hashes=3)
        self.assertEqual(result['hashes'],3)
if __name__=='__main__':unittest.main(verbosity=2)
