from app_paths import DEFAULT_DATADIR
"""Bounded template/proposal checks only. No GPU, wallet or submission methods."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import time
from address import payout_script
from block_builder import build_candidate,compact_size
from network_policy import network_profile

ALLOWED={'getblockchaininfo','getnetworkinfo','getbestblockhash','getblockhash','getblockheader',
         'getconnectioncount','validateaddress','getblocktemplate'}
FIELDS={'capabilities','version','rules','vbavailable','vbrequired','previousblockhash','transactions',
        'coinbaseaux','coinbasevalue','longpollid','target','mintime','mutable','noncerange',
        'sigoplimit','sizelimit','weightlimit','curtime','bits','height','default_witness_commitment'}

class PreflightRPC:
    def __init__(self,datadir,cookie,port=19732,network='mainnet'):
        if network not in ('mainnet','regtest') or not 1024<=port<=65535:raise ValueError('Invalid endpoint')
        if not Path(cookie).is_absolute():raise ValueError('Absolute custom cookie path required')
        chain='main' if network=='mainnet' else 'regtest'
        self.base=[r'C:\Program Files\Bitcoin\daemon\bitcoin-cli.exe',f'-chain={chain}',f'-datadir={datadir}',
                   '-rpcconnect=127.0.0.1',f'-rpcport={port}',f'-rpccookiefile={cookie}']
        self.calls=Counter();self.deadline=time.monotonic()+300
    def call(self,method,*args):
        if method not in ALLOWED:raise ValueError('RPC method forbidden by preflight client')
        if method=='getblocktemplate':
            if len(args)!=1 or not isinstance(args[0],dict):raise ValueError('Invalid template request')
            request=args[0]
            keys={'rules','mode','data'} if request.get('mode')=='proposal' else {'rules','capabilities'}
            if set(request)-keys or request.get('rules')!=['segwit']:raise ValueError('Unsupported template request')
            if 'mode' in request and request['mode']!='proposal':raise ValueError('Unsupported mode')
            if request.get('mode')=='proposal':
                data=request.get('data')
                if not isinstance(data,str) or not 162<=len(data)<=8_000_000:raise ValueError('Invalid candidate size')
                bytes.fromhex(data)
        if method=='getblockhash' and args!=(0,):raise ValueError('Only genesis lookup is required')
        timeout=min(15,self.deadline-time.monotonic())
        if timeout<=0:raise TimeoutError('Preflight request budget expired')
        params=[a if isinstance(a,str) else json.dumps(a,separators=(',',':')) for a in args]
        if any('\n' in a or '\r' in a for a in params):raise ValueError('Raw line break in RPC argument')
        self.calls[method]+=1
        result=subprocess.run(self.base+['-stdin',method],input=''.join(p+'\n' for p in params),
            capture_output=True,text=True,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise RuntimeError(f'RPC {method} failed (exit {result.returncode}); raw error withheld')
        value=result.stdout.strip()
        try:return json.loads(value)
        except json.JSONDecodeError:return value or None

def inspect_template(template,info):
    if set(template)-FIELDS:raise ValueError('Unsupported template fields')
    if 'proposal' not in template.get('capabilities',[]):raise ValueError('Proposal capability unavailable')
    if any(r.startswith('!') and r!='!segwit' for r in template['rules']):raise ValueError('Unsupported mandatory rule')
    if any(r.startswith('!') and r!='!segwit' for r in template['vbavailable']):raise ValueError('Unsupported mandatory version deployment')
    version,required=template['version']&0xffffffff,template['vbrequired']
    if type(required) is not int or required<0 or version&required!=required:raise ValueError('Required version bits absent')
    if template.get('noncerange')!='00000000ffffffff':raise ValueError('Unsupported nonce range')
    if not 0<template['weightlimit']<=4_000_000 or not 0<template['sizelimit']<=4_000_000:
        raise ValueError('Unsupported resource limits')
    if not template['mintime']<=template['curtime']<=time.time()+7200:raise ValueError('Invalid timestamp bounds')
    if template['height']!=info['blocks']+1 or template['previousblockhash']!=info['bestblockhash']:
        raise ValueError('Tip changed during template capture')
    if any(type(e.get('fee')) is not int or type(e.get('sigops')) is not int or type(e.get('weight')) is not int
           for e in template['transactions']):raise ValueError('Incomplete transaction accounting')

def run(rpc,address,network='mainnet'):
    profile=network_profile(network);script=payout_script(address,network)
    info=rpc.call('getblockchaininfo');net=rpc.call('getnetworkinfo')
    if info['chain']!=('main' if network=='mainnet' else 'regtest'):raise ValueError('Wrong network')
    if rpc.call('getblockhash',0)!=profile.genesis:raise ValueError('Wrong genesis')
    if network=='mainnet' and (info['initialblockdownload'] or not net['networkactive'] or net['connections']<1):
        raise ValueError('Mainnet node not ready')
    if net['version']!=320100:raise ValueError('Only reviewed Core 32.1 is supported')
    validated=rpc.call('validateaddress',address)
    if not validated.get('isvalid') or validated['scriptPubKey']!=script.hex():raise ValueError('Address mismatch')
    result={'status':'PASS','network':network,'version':net['version'],'height_before':info['blocks'],
            'headers':info['headers'],'initialblockdownload':info['initialblockdownload'],
            'connections':net['connections'],'address_format_valid':True,'address_ownership_checked':False,
            'gpu_started':False,'nonce_search':False,'block_or_transaction_submission':False,'attempts':[]}
    for attempt in range(3):
        began=time.monotonic();info=rpc.call('getblockchaininfo')
        template=rpc.call('getblocktemplate',{'rules':['segwit'],'capabilities':['proposal']})
        inspect_template(template,info)
        job=build_candidate(template,address,attempt+1,network=network)
        tx_by_id={t.txid[::-1].hex():t for t in job.transactions[1:]}
        for entry in template['transactions']:
            tx=tx_by_id[entry['txid']]
            if len(tx.stripped)*3+len(tx.raw)!=entry['weight']:raise ValueError('Transaction weight mismatch')
        if rpc.call('getbestblockhash')!=job.previous:raise ValueError('Tip changed before proposal; stop for fresh capture')
        proposal_start=time.monotonic()
        reply=rpc.call('getblocktemplate',{'mode':'proposal','rules':['segwit'],'data':job.raw().hex()})
        elapsed=time.monotonic()-proposal_start
        if reply is not None:raise ValueError('Core rejected candidate proposal: '+str(reply)[:100])
        if rpc.call('getbestblockhash')!=job.previous:raise ValueError('Tip changed after proposal; validation inconclusive')
        base=80+len(compact_size(len(job.transactions)))+sum(len(t.stripped) for t in job.transactions)
        result['attempts'].append({'height':job.height,'transactions':len(job.transactions),
            'bytes':len(job.raw()),'weight':base*3+len(job.raw()),'coinbase_satoshis':job.reward,
            'bits':template['bits'],'rules':template['rules'],'vbavailable':template['vbavailable'],
            'vbrequired':template['vbrequired'],'proposal_result':reply,
            'proposal_seconds':round(elapsed,4),'attempt_seconds':round(time.monotonic()-began,4)})
    result['height_after']=rpc.call('getblockchaininfo')['blocks'];result['rpc_methods']=dict(rpc.calls)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cookie',required=True);p.add_argument('--output',required=True)
    p.add_argument('--network',choices=['mainnet','regtest'],default='mainnet')
    p.add_argument('--datadir',default=DEFAULT_DATADIR);p.add_argument('--port',type=int,default=19732)
    p.add_argument('--address',required=True);args=p.parse_args()
    report={'status':'FAIL','gpu_started':False,'block_or_transaction_submission':False,
            'started_utc':datetime.now(timezone.utc).isoformat()}
    try:report.update(run(PreflightRPC(args.datadir,args.cookie,args.port,args.network),args.address,args.network))
    except Exception as exc:report['error']=str(exc);raise
    finally:
        report['finished_utc']=datetime.now(timezone.utc).isoformat()
        Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
if __name__=='__main__':main()
