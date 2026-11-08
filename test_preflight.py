"""No-node tests for preflight's restricted method and template policy."""
import copy
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from preflight import PreflightRPC,inspect_template,ALLOWED
from test_builder import fixture

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.template=fixture()
        self.template.update(capabilities=['proposal'],vbavailable={},vbrequired=0,
            noncerange='00000000ffffffff',weightlimit=4_000_000,sizelimit=4_000_000)
        for e in self.template['transactions']:e.update(sigops=0,weight=100)
        self.info=dict(blocks=100,bestblockhash=self.template['previousblockhash'])
    def test_supported_template(self):inspect_template(self.template,self.info)
    def test_changed_tip_and_height(self):
        for key,value in [('height',102),('previousblockhash','00'*32)]:
            t=copy.deepcopy(self.template);t[key]=value
            with self.assertRaises(ValueError):inspect_template(t,self.info)
    def test_rules_version_and_limits(self):
        for key,value in [('rules',['!unknown']),('vbavailable',{'!unknown':1}),('vbrequired',1),
                          ('weightlimit',4_000_001),('sizelimit',0),('noncerange','00000001ffffffff')]:
            t=copy.deepcopy(self.template);t[key]=value
            with self.assertRaises(ValueError):inspect_template(t,self.info)
    def test_unknown_fields_and_missing_accounting(self):
        for key in ['coinbasetxn','coinbase_outputs']:
            t=copy.deepcopy(self.template);t[key]={}
            with self.assertRaises(ValueError):inspect_template(t,self.info)
        self.template['transactions'][0].pop('fee')
        with self.assertRaises(ValueError):inspect_template(self.template,self.info)
    def test_client_forbids_mutation_before_process(self):
        rpc=PreflightRPC('unused',str(Path(tempfile.gettempdir())/'test-cookie'))
        with patch('preflight.subprocess.run',side_effect=AssertionError('must not execute')):
            for method in ['submitblock','submitheader','sendrawtransaction','stop','createwallet','loadwallet',
                           'generateblock','generatetoaddress','invalidateblock','setnetworkactive']:
                with self.assertRaises(ValueError):rpc.call(method)
    def test_client_forbids_longpoll_and_unsupported_modes(self):
        rpc=PreflightRPC('unused',str(Path(tempfile.gettempdir())/'test-cookie'))
        for req in [dict(rules=['segwit'],longpollid='x'),dict(rules=['segwit'],mode='template'),
                    dict(rules=['segwit'],mode='proposal',data='00'),dict(rules=['unknown'])]:
            with self.assertRaises(ValueError):rpc.call('getblocktemplate',req)
    def test_client_uses_stdin_and_never_logs_errors(self):
        from types import SimpleNamespace
        rpc=PreflightRPC('unused',str(Path(tempfile.gettempdir())/'test-cookie'))
        with patch('preflight.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='SECRET')):
            with self.assertRaises(RuntimeError) as exc:rpc.call('getblockchaininfo')
            self.assertNotIn('SECRET',str(exc.exception))
        with patch('preflight.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='null')) as process:
            rpc.call('getblocktemplate',{'rules':['segwit']})
            self.assertIn('-stdin',process.call_args.args[0])
            self.assertEqual(process.call_args.kwargs['input'],'{"rules":["segwit"]}\n')
    def test_no_gpu_modules_imported(self):
        import subprocess,sys
        result=subprocess.run([sys.executable,'-c',"import preflight,sys; assert 'gpu_hasher' not in sys.modules and 'miner' not in sys.modules"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
if __name__=='__main__':unittest.main(verbosity=2)
