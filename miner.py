"""Bounded miner; live execution requires two explicit opt-ins and a verified local session."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import time
import secrets
import re
import sys
from address import payout_script
from bitcoin import sha256d,with_nonce
from block_builder import build_candidate
from gpu_hasher import GPUHasher,MAX_BATCH
from network_policy import authorize_execution
from network_policy import network_profile
from preflight import ALLOWED,inspect_template
from journal import SubmissionJournal
from endpoint import verify_endpoint
from startup import make_plan,snapshot_bits
from run_limits import validate_limits,SESSION_BATCH_SIZE

class StaleJob(RuntimeError):pass
class StaleProposal(StaleJob):
    def __init__(self,message,candidate_parent=None,current_tip=None):
        super().__init__(message);self.candidate_parent=candidate_parent;self.current_tip=current_tip
class InvalidSolution(RuntimeError):pass
class InvalidCandidate(RuntimeError):pass
class SubmissionUncertain(RuntimeError):pass
class NodeNotReady(RuntimeError):pass
class MiningStopped(RuntimeError):pass
class RPCFailure(RuntimeError):
    def __init__(self,message,exit_code):
        super().__init__(message);self.exit_code=exit_code

def check_proposal(rejection,solved=False,candidate_parent=None,current_tip=None):
    if rejection=='inconclusive-not-best-prevblk':
        raise StaleProposal('Core tip changed during candidate proposal validation',candidate_parent,current_tip)
    if rejection is not None:
        raise InvalidCandidate(('Solved candidate' if solved else 'Candidate')+f' preflight rejected: {rejection}')

class NodeRPC:
    def __init__(self,datadir,port=19543,timeout=3,network='regtest',cookie=None,session_pid=None,opt_in=False,submit=False,transport='cli'):
        authorize_execution(network,opt_in,submit)
        if not Path(datadir).is_dir():raise ValueError("Existing data directory required")
        if type(port) is not int or not 1024<=port<=65535 or port==8332:raise ValueError("Invalid regtest RPC port")
        self.network=network;self.cookie=cookie;self.session_pid=session_pid;self.datadir=str(Path(datadir).resolve())
        chain='main' if network=='mainnet' else 'regtest'
        self.base=[r"C:\Program Files\Bitcoin\daemon\bitcoin-cli.exe",f"-chain={chain}",f"-datadir={self.datadir}",
                   "-rpcconnect=127.0.0.1",f"-rpcport={port}"]
        self.timeout=timeout;self.calls=Counter();self.timings=Counter();self.deadline=None;self.port=port;self.identity_checked=False
        if transport not in ('cli','http'):raise ValueError('Unsupported local RPC transport')
        self.transport=transport;self.http=None;self.watcher=None
        if network=='mainnet':
            if not cookie or not Path(cookie).is_absolute() or type(session_pid) is not int or session_pid<=0:
                raise ValueError('Live mode requires an absolute private cookie path and --session-pid')
            self.base.append(f'-rpccookiefile={cookie}');self.verify_session()
        if transport=='http':
            from local_rpc import LocalRPC
            private_cookie=cookie if cookie else str(Path(self.datadir)/'regtest'/'.cookie')
            self.http=LocalRPC(port,private_cookie,verify=self.verify_session if network=='mainnet' else None)
    def __enter__(self):return self
    def __exit__(self,exc_type,exc,tb):
        try:self.close()
        except Exception as failure:
            if exc is not None:return False
            if hasattr(self,'mining_result'):failure.mining_result=self.mining_result
            raise
    def start_watcher(self,template,stop_event):
        if self.http is None:return
        from tip_watcher import TipWatcher
        self.watcher=TipWatcher(self.http.fork({'getblocktemplate','getbestblockhash'}),template,stop_event);self.watcher.start()
    def close(self):
        try:
            if getattr(self,'watcher',None) is not None:self.watcher.close()
        finally:
            if getattr(self,'http',None) is not None:self.http.close()
    def verify_session(self):
        limit=10 if self.deadline is None else min(10,self.deadline-time.monotonic())
        if limit<=0:raise TimeoutError('Time limit reached before session verification')
        return verify_endpoint(self.session_pid,self.cookie,self.port,self.datadir,timeout=limit)
    def call(self,method,*args):
        if self.network=='mainnet' and method not in ALLOWED|{'submitblock'}:raise ValueError('RPC method forbidden in live mode')
        self.calls[method]+=1
        if not hasattr(self,'timings'):self.timings=Counter()
        started=time.perf_counter()
        try:return self._request(method,*args)
        finally:self.timings[method]+=time.perf_counter()-started
    def _request(self,method,*args):
        if getattr(self,'http',None) is not None:
            from local_rpc import RemoteRPCError
            timeout=self.timeout if self.deadline is None else min(self.timeout,self.deadline-time.monotonic())
            try:return self.http.call(method,*args,timeout=timeout)
            except RemoteRPCError as exc:raise RPCFailure(f'RPC {method} failed (code {exc.code}); raw error withheld',abs(exc.code)) from None
        params=[json.dumps(a,separators=(",",":")) if not isinstance(a,str) else a for a in args]
        timeout=self.timeout if self.deadline is None else min(self.timeout,self.deadline-time.monotonic())
        if timeout<=0:raise TimeoutError("Mining time limit reached")
        if any("\n" in p or "\r" in p for p in params):raise ValueError("RPC arguments must not contain raw line breaks")
        process=subprocess.run(self.base+["-stdin",method],input="".join(p+"\n" for p in params),capture_output=True,text=True,timeout=timeout,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        if process.returncode:
            status=re.search(r'HTTP (?:error )?(\d{3})',process.stderr)
            raise RPCFailure(f"RPC {method} failed (exit {process.returncode}"+(f", HTTP {status[1]}" if status else '')+"); raw error withheld",process.returncode)
        text=process.stdout.strip()
        if not text:return None
        try:return json.loads(text)
        except json.JSONDecodeError:return text
    def guard(self):
        info=self.call("getblockchaininfo")
        if info.get("chain")!=('main' if self.network=='mainnet' else 'regtest'):raise RuntimeError("Wrong node network")
        if self.network=='mainnet':
            if not self.identity_checked:
                if self.call('getblockhash',0)!=network_profile('mainnet').genesis:raise RuntimeError('Wrong genesis')
                self.identity_checked=True
            net=self.call('getnetworkinfo')
            if net.get('version')!=320100:raise RuntimeError('Only reviewed Core 32.1 is supported')
            if info.get('warnings') or net.get('warnings'):raise NodeNotReady('Core reports warnings')
            if info['initialblockdownload'] or info['headers']!=info['blocks'] or not net['networkactive'] or net['connections']<1:
                raise NodeNotReady('Mainnet node is syncing or disconnected')
        return info

class Miner:
    def __init__(self,rpc,address,gpu,refresh_seconds=1.0,max_job_age=2.0,journal=None,stop_event=None):
        self.stop_event=stop_event
        self.check_stop()
        self.network=getattr(rpc,'network','regtest');self.journal=journal
        payout_script(address,self.network)
        if self.network=='mainnet' and (not isinstance(journal,SubmissionJournal) or not journal.active):raise ValueError('Live mode requires a locked submission journal')
        rpc.guard()
        self.check_stop()
        if not 0.1<=refresh_seconds<=max_job_age<=10:raise ValueError("Invalid freshness limits")
        self.rpc,self.address,self.gpu=rpc,address,gpu
        self.refresh_seconds,self.max_job_age=refresh_seconds,max_job_age
        self.extra=secrets.randbits(64);self.jobs=0;self.stale=0
    def check_stop(self):
        if self.stop_event is not None and self.stop_event.is_set():raise MiningStopped('Stop requested')
    def new_job(self):
        self.check_stop()
        info=self.rpc.guard()
        template=self.rpc.call("getblocktemplate",{"rules":["segwit"],"capabilities":["proposal"]})
        unknown=[r for r in template.get("rules",[]) if r.startswith("!") and r!="!segwit"]
        if unknown:raise InvalidCandidate("Unknown mandatory template rules")
        if self.network=='mainnet':
            if template['previousblockhash']!=info['bestblockhash'] or template['height']!=info['blocks']+1:raise StaleJob('Tip changed during capture')
            inspect_template(template,info)
        self.extra=(self.extra+1)&((1<<64)-1)
        job=build_candidate(template,self.address,self.extra,network=self.network)
        if job.previous!=template['previousblockhash'] or job.header[4:36]!=bytes.fromhex(template['previousblockhash'])[::-1]:
            raise InvalidCandidate('Rebuilt header parent does not match the fresh template; inspect hash byte order')
        if self.network=='mainnet':
            actual={t.txid[::-1].hex():len(t.stripped)*3+len(t.raw) for t in job.transactions[1:]}
            if any(actual[e['txid']]!=e['weight'] for e in template['transactions']):raise InvalidCandidate('Transaction weight mismatch')
        rejection=self.rpc.call("getblocktemplate",{"mode":"proposal","rules":["segwit"],"data":job.raw().hex()})
        self.check_proposal(rejection,job)
        self.jobs+=1
        return job
    def check_proposal(self,rejection,job,solved=False):
        current=self.rpc.call('getbestblockhash') if rejection=='inconclusive-not-best-prevblk' else None
        check_proposal(rejection,solved,job.previous,current)
    def assert_fresh(self,job):
        self.check_stop()
        self.rpc.guard()
        if time.monotonic()-job.created>self.max_job_age:
            raise StaleJob("Job expired")
        if self.rpc.call("getbestblockhash")!=job.previous:
            raise StaleJob("Tip changed")
    def validate_solution(self,job,nonce,gpu_digest):
        if not isinstance(gpu_digest,bytes) or len(gpu_digest)!=32:raise InvalidSolution("Invalid digest shape")
        try:actual=job.validate(nonce,require_pow=True)
        except ValueError as exc:raise InvalidSolution(str(exc))
        if actual!=gpu_digest:raise InvalidSolution("GPU/CPU digest mismatch")
        return actual
    def submit_solution(self,job,nonce,gpu_digest):
        self.check_stop()
        if job.network!=self.network:raise InvalidCandidate("Candidate network does not match the session")
        self.assert_fresh(job)
        digest=self.validate_solution(job,nonce,gpu_digest)
        block=job.raw(nonce).hex()
        rejection=self.rpc.call("getblocktemplate",{"mode":"proposal","rules":["segwit"],"data":block})
        self.check_proposal(rejection,job,solved=True)
        if self.network=='mainnet':self.rpc.verify_session()
        self.assert_fresh(job)
        self.check_stop()
        displayed=digest[::-1].hex()
        if self.journal:self.journal.begin(displayed,job.height,job.previous,self.network,getattr(self.rpc,'session_pid',None))
        try:rejection=self.rpc.call("submitblock",block)
        except (Exception,KeyboardInterrupt) as exc:
            raise SubmissionUncertain("Submission outcome needs checking for "+displayed) from exc
        # A reply is not proof of rejection; retain durable PENDING on malformed results.
        if rejection is not None and (type(rejection) is not str or not rejection):
            raise SubmissionUncertain("Malformed submission result; inspect block "+displayed)
        # BIP22 indeterminate replies are not evidence of block rejection.
        if rejection in ('inconclusive', 'duplicate-inconclusive'):
            raise SubmissionUncertain("Inconclusive submission result; inspect block "+displayed)
        if self.journal:self.journal.save('ACCEPTED' if rejection is None else 'REJECTED',reason=rejection)
        if rejection is not None:return {"accepted":False,"hash":displayed,"reason":rejection}
        try:header=self.rpc.call("getblockheader",displayed)
        except Exception as exc:
            raise SubmissionUncertain("Core accepted block; best-tip confirmation needs checking for "+displayed) from exc
        if not isinstance(header,dict) or header.get('hash')!=displayed:raise SubmissionUncertain('Unexpected block confirmation for '+displayed)
        confirmations=header.get('confirmations')
        if type(confirmations) is not int or confirmations < -1:
            raise SubmissionUncertain('Invalid block confirmation count for '+displayed)
        active=confirmations>0
        if self.journal:self.journal.save('CONFIRMED' if active else 'ACCEPTED_SIDECHAIN',active_chain=active)
        return {"accepted":True,"active_chain":active,"hash":displayed,"height":job.height,"nonce":nonce,"job_id":job.job_id,
            "network":self.network,"payout_address":self.address,"node_confirmations":confirmations}
    def run(self,max_seconds=10,max_hashes=32768,max_blocks=1,batch_size=4096,on_status=None,on_progress=None,
            run_profile='test',pause_seconds=0,on_work=None):
        validate_limits(max_seconds,max_hashes,max_blocks,batch_size,run_profile,pause_seconds,self.stop_event is not None)
        began=time.monotonic();hashes=0;mined=[];rejections=[];job=None;cursor=0;uncertain=None;stopped=False
        old_deadline=getattr(self.rpc,"deadline",None)
        self.rpc.deadline=None if max_seconds is None else began+max_seconds
        state=None
        last_progress=-1.0
        tip_changes=0;work_history=[];work_history_truncated=False
        consecutive_proposal_races=0
        same_tip_races=0;last_same_tip=None
        # Large sessions poll by elapsed time, rather than launching multiple
        # bitcoin-cli processes before every tiny batch. Tests retain strict
        # per-batch checks. Solved candidates always use assert_fresh unchanged.
        scheduled_checks=run_profile=='session' and batch_size>4096
        next_tip_check=next_guard=0
        phase_seconds=Counter();batch_calls=0
        def timed(name,fn,*args):
            started=time.perf_counter()
            try:return fn(*args)
            finally:phase_seconds[name]+=time.perf_counter()-started
        def check_work(current):
            nonlocal next_tip_check,next_guard
            if not scheduled_checks:return self.assert_fresh(current)
            self.check_stop()
            watcher=getattr(self.rpc,'watcher',None)
            if watcher is not None:
                observed=watcher.snapshot()
                # A late response from a request begun before this job must not
                # retire newer work. Main-thread parent polling is the fallback.
                if observed['generation'] and observed['sample_started']>=current.created and observed['parent']!=current.previous:
                    raise StaleJob('Parent changed in longpoll watcher')
            if time.monotonic()-current.created>self.max_job_age:raise StaleJob('Job expired')
            if time.monotonic()>=next_guard:
                self.rpc.guard();self.check_stop();next_guard=time.monotonic()+1
            if time.monotonic()>=next_tip_check:
                tip=self.rpc.call('getbestblockhash');self.check_stop()
                next_tip_check=time.monotonic()+.1
                if tip!=current.previous:raise StaleJob('Tip changed')
        def remaining():
            return None if max_seconds is None else max(0,max_seconds-(time.monotonic()-began))
        def progress(force=False):
            nonlocal last_progress
            elapsed=time.monotonic()-began
            if force or elapsed-last_progress>=.25:
                last_progress=elapsed
                report(on_progress,{'hashes':hashes,'jobs':self.jobs,'blocks':len(mined),
                    'elapsed_seconds':elapsed,'remaining_seconds':remaining(),
                    'hashes_per_second':hashes/elapsed if elapsed>0 else 0,
                    'tip_changes':tip_changes,'work_height':job.height if job else None,
                    'previous':job.previous if job else None})
        def pause(seconds):
            left=remaining()
            seconds=seconds if left is None else min(seconds,left)
            started=time.perf_counter()
            try:
                if self.stop_event is not None:self.stop_event.wait(seconds)
                else:time.sleep(seconds)
            finally:phase_seconds['pacing_wait']+=time.perf_counter()-started
        def report(callback,value):
            if callback is not None:
                try:callback(value)
                except Exception:pass
        def status(value):
            nonlocal state
            if state==value:return
            state=value
            report(on_status,value)
            if sys.stderr is None:return
            try:print(value,file=sys.stderr,flush=True)
            except (OSError,ValueError):pass
        def result_record(result_status):
            return {"status":result_status,"chain":self.network,"blocks":mined,"hashes":hashes,
                "jobs":self.jobs,"stale_jobs":self.stale,"rejections":rejections,"submission_uncertain":uncertain,
                "elapsed_seconds":round(time.monotonic()-began,3),
                "bounds":{"seconds":max_seconds,"hashes":max_hashes,"blocks":max_blocks,"batch_size":batch_size},
                "run_profile":run_profile,"batch_pause_seconds":pause_seconds,
                "tip_changes":tip_changes,"work_history":[dict(item) for item in work_history],
                "work_history_truncated":work_history_truncated,
                "performance":{"gpu_batch_calls":batch_calls,"phase_seconds":dict(phase_seconds),"mining_started_monotonic":began,
                    "rpc_calls_including_startup":dict(self.rpc.calls) if hasattr(self.rpc,'calls') else {},
                    "rpc_seconds_including_startup":dict(getattr(self.rpc,'timings',{})),
                    "tip_poll_interval_seconds":.1 if scheduled_checks else None,
                    "readiness_poll_interval_seconds":1 if scheduled_checks else None,
                    "tip_watcher":self.rpc.watcher.stats() if getattr(self.rpc,'watcher',None) is not None else None,
                    "local_rpc":self.rpc.http.stats() if getattr(self.rpc,'http',None) is not None else None,
                    "note":"Host wall times; job/check aggregates include their RPC times. Watch timestamps are client observations, not exact Core/network arrival delays. CUDA event timings are in the offline profile."}}
        try:
            while (remaining() is None or remaining()>0) and (max_hashes is None or hashes<max_hashes) and len(mined)<max_blocks:
                try:
                    self.check_stop()
                    if job is not None:
                        try:timed('freshness_checks',check_work,job)
                        except StaleJob:self.stale+=1;job=None
                    if job is None or time.monotonic()-job.created>=self.refresh_seconds or cursor>=1<<32:
                        job=timed('job_build_and_proposal',self.new_job);cursor=0
                        if scheduled_checks:
                            next_guard=time.monotonic()+1;next_tip_check=time.monotonic()+.1
                        if not work_history or work_history[-1]['previous']!=job.previous:
                            if work_history:tip_changes+=1
                            work_history.append(dict(height=job.height,previous=job.previous,
                                first_job_id=job.job_id,start_elapsed_seconds=round(time.monotonic()-began,3),
                                hashes_before=hashes,hashes=0))
                            if len(work_history)>100:
                                work_history.pop(0);work_history_truncated=True
                            report(on_work,dict(work_history[-1],tip_changes=tip_changes))
                    if remaining() is not None and remaining()<=0:break
                    count=min(batch_size,(1<<32)-cursor,max_hashes-hashes if max_hashes is not None else batch_size)
                    self.check_stop()
                    status('Mining')
                    search=getattr(self.gpu,'search_batch',None) if scheduled_checks else None
                    if callable(search):
                        candidate=timed('gpu_batch_and_host_transfer',search,job.header,cursor,count,job.target)
                        candidates=[] if candidate is None else [candidate]
                    else:
                        digests,flags=timed('gpu_batch_and_host_transfer',self.gpu.batch,job.header,cursor,count,job.target)
                        candidates=((i,digests[i]) for i,flag in enumerate(flags) if flag)
                    batch_calls+=1
                    hashes+=count
                    work_history[-1].setdefault('first_completed_batch_elapsed_seconds',round(time.monotonic()-began,6))
                    watcher=getattr(self.rpc,'watcher',None)
                    if watcher is not None and 'client_observation_to_first_completed_batch_seconds' not in work_history[-1]:
                        observed=watcher.snapshot()
                        if observed['generation'] and observed['parent']==job.previous and observed.get('observed') is not None:
                            work_history[-1].update(watcher_generation=observed['generation'],
                                client_observation_to_first_completed_batch_seconds=max(0,time.monotonic()-observed['observed']))
                    consecutive_proposal_races=0
                    same_tip_races=0;last_same_tip=None
                    work_history[-1]['hashes']+=count
                    progress()
                    self.check_stop()
                    for i,digest in candidates:
                        if type(i) is not int or not 0<=i<count:raise InvalidSolution('GPU candidate offset is outside its searched batch')
                        try:
                            outcome=self.submit_solution(job,cursor+i,digest)
                            if outcome["accepted"]:mined.append(outcome)
                            else:rejections.append(outcome)
                        except StaleProposal:raise
                        except StaleJob:self.stale+=1
                        job=None
                        break
                    cursor+=count
                    if pause_seconds:pause(pause_seconds)
                except SubmissionUncertain as exc:
                    uncertain=str(exc);break
                except StaleProposal as exc:
                    self.stale+=1;job=None;consecutive_proposal_races+=1
                    if exc.current_tip is not None and exc.current_tip==exc.candidate_parent:
                        same_tip_races=same_tip_races+1 if last_same_tip==exc.current_tip else 1
                        last_same_tip=exc.current_tip
                        if same_tip_races>=2:
                            raise InvalidCandidate('Repeated stale proposals against an unchanged tip; inspect header parent and byte order') from exc
                    else:same_tip_races=0;last_same_tip=None
                    if consecutive_proposal_races>=8:
                        raise InvalidCandidate('Core tip changed during 8 consecutive proposals; stop and review node readiness') from exc
                    status('Waiting');progress();pause(.05)
                except (NodeNotReady,StaleJob):
                    status('Waiting')
                    self.stale+=1;job=None
                    progress();pause(.05)
                except (TimeoutError,subprocess.TimeoutExpired):
                    if self.rpc.deadline is not None and time.monotonic()>=self.rpc.deadline:break
                    raise
        except MiningStopped:stopped=True
        except Exception as exc:
            exc.mining_result=result_record('ERROR')
            raise
        finally:
            progress(force=True)
            self.rpc.deadline=old_deadline;status('Stopped')
        return result_record('SUBMISSION_UNCERTAIN' if uncertain else 'STOPPED' if stopped else 'COMPLETE')

def main():
    parser=argparse.ArgumentParser(description="Bounded NVRTC miner; live activation is explicit and verified")
    parser.add_argument("--network",choices=["regtest","mainnet"],default="regtest")
    parser.add_argument("--mainnet-opt-in",action="store_true",help="Explicitly permit bounded live GPU mining after activation approval")
    parser.add_argument('--submit-blocks',action='store_true',help='Explicitly permit live block submission after activation approval')
    parser.add_argument('--cookie');parser.add_argument('--session-pid',type=int)
    parser.add_argument("--plan-only",action="store_true",help="Display odds/energy without RPC or GPU access")
    parser.add_argument("--target-bits",help="Optional target snapshot for plan-only mode")
    parser.add_argument("--datadir")
    parser.add_argument("--rpc-port",type=int)
    parser.add_argument("--address",required=True,help="Address must match the explicitly selected network")
    parser.add_argument("--max-seconds",type=float,default=10)
    parser.add_argument("--max-hashes",type=int,default=32768)
    parser.add_argument("--max-blocks",type=int,default=1)
    args=parser.parse_args()
    if args.target_bits and not args.plan_only:parser.error('--target-bits is available only with --plan-only')
    payout_script(args.address,args.network)
    bits,source=snapshot_bits(args.network)
    if args.target_bits is not None:bits=int(args.target_bits,16);source="operator-supplied plan target"
    plan=make_plan(args.network,args.address,bits,args.max_seconds,args.max_hashes,args.max_blocks)
    plan["target_source"]=source
    if args.plan_only:
        print(json.dumps(plan,indent=2))
        return
    try:authorize_execution(args.network,args.mainnet_opt_in,args.submit_blocks)
    except (RuntimeError,ValueError) as exc:parser.error(str(exc))
    if not args.datadir:parser.error("--datadir is required for execution")
    if args.network=='mainnet' and (not args.cookie or not args.session_pid):parser.error('Live mode requires --cookie and --session-pid')
    port=args.rpc_port if args.rpc_port is not None else (19732 if args.network=='mainnet' else 19543)
    journal_path=Path(__file__).with_name('mainnet-submission.json') if args.network=='mainnet' else Path(args.datadir)/'miner-submission.json'
    from gpu_monitor import GPUMonitor
    from queue import Queue
    import threading
    stop = threading.Event()
    monitor = GPUMonitor(Queue(), stop)
    try:
        monitor.start()  # Safe-reading handshake precedes node/GPU execution.
        with SubmissionJournal(journal_path) as journal:
            execute(args,port,journal,stop_event=stop)
    finally:
        monitor.close()

def execute(args,port,journal,stop_event=None,on_status=None,on_progress=None,on_work=None):
    profile=getattr(args,'run_profile','test')
    pause_seconds=getattr(args,'pause_seconds',0)
    batch_size=getattr(args,'batch_size',SESSION_BATCH_SIZE if profile=='session' else 4096)
    threads_per_block=getattr(args,'threads_per_block',128)
    from advanced_settings import validate_threads
    validate_threads(threads_per_block)
    validate_limits(getattr(args,'max_seconds',10),getattr(args,'max_hashes',32768),getattr(args,'max_blocks',1),batch_size=batch_size,profile=profile,
                    pause_seconds=pause_seconds,can_stop=stop_event is not None)
    def checkpoint():
        if stop_event is not None and stop_event.is_set():raise MiningStopped('Stop requested')
    checkpoint()
    if on_status:on_status('Checking node')
    with NodeRPC(args.datadir,port,network=args.network,cookie=args.cookie,session_pid=args.session_pid,
                opt_in=args.mainnet_opt_in,submit=args.submit_blocks,transport='http' if profile=='session' else 'cli') as rpc:
        checkpoint()
        info=rpc.guard()
        if args.network=='mainnet':
            address=rpc.call('validateaddress',args.address)
            if not address.get('isvalid') or address.get('scriptPubKey')!=payout_script(args.address,args.network).hex():raise ValueError('Payout address mismatch')
        checkpoint()
        template=rpc.call("getblocktemplate",{"rules":["segwit"]})
        if args.network=='mainnet':inspect_template(template,info)
        plan=(dict(mode='MINING_SESSION',bounds=dict(seconds=args.max_seconds,hashes=None,blocks=1,batch_size=batch_size),
            batch_pause_seconds=pause_seconds,note='Runs until the selected duration, Stop, a win, or an error. Whole-PC energy is not measured.')
            if profile=='session' else make_plan(args.network,args.address,int(template["bits"],16),args.max_seconds,args.max_hashes,args.max_blocks))
        plan["target_source"]="fresh node template"
        plan.update(mode="PRE_EXECUTION_ESTIMATE",rpc_called=True,execution_about_to_start=True,policy_execution_enabled=True)
        print(json.dumps({"startup_plan":plan},indent=2))
        checkpoint()
        if profile=='session' and getattr(rpc,'http',None) is not None:rpc.start_watcher(template,stop_event)
        if on_status:on_status('Preparing GPU')
        with GPUHasher(threads_per_block=threads_per_block) as gpu:
            checkpoint()
            from gpu_identity import verify_identity
            verify_identity(gpu)
            checkpoint()
            result=Miner(rpc,args.address,gpu,journal=journal,stop_event=stop_event,max_job_age=5 if args.network=='mainnet' else 2).run(
                args.max_seconds,args.max_hashes,args.max_blocks,on_status=on_status,on_progress=on_progress,
                run_profile=profile,pause_seconds=pause_seconds,on_work=on_work,batch_size=batch_size)
            result['gpu_execution']={name:getattr(gpu,name,None) for name in ('name','arch','use_midstate','unroll',
                'midstate_builds','rolling_schedule','word_input','byte_permutation','kernel_resources',
                'hashes','candidate_capacity','candidate_overflows','fallback_rehashed','threads_per_block')}
        rpc.mining_result=result
        print(json.dumps(result,indent=2))
        return result

if __name__=="__main__":main()
