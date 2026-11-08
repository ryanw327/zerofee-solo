"""Real loopback mock HTTP only; inert test credentials, never Bitcoin/mainnet."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import base64,json,socket,threading,time,unittest
from unittest.mock import Mock
from local_rpc import LocalRPC,RemoteRPCError
from tip_watcher import TipWatcher

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        method=request['method'];self.server.calls.append(method)
        if self.headers.get('Authorization')!=self.server.auth:
            raw=b'{}';self.send_response(401);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if self.server.drop==method:
            self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
        error=None
        if self.server.remote_error==method:error={'code':-28,'message':'SENSITIVE TEST CONTENT'}
        if method=='getblocktemplate' and request['params'][0].get('longpollid'):
            self.server.entered.set();self.server.release.wait(5)
        result=({'previousblockhash':self.server.parent,'longpollid':self.server.parent+'0'} if method=='getblocktemplate' else self.server.parent)
        raw=json.dumps(dict(id=request['id'],result=result,error=error)).encode()
        try:
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            if self.server.close_response:self.send_header('Connection','close');self.close_connection=True
            self.end_headers();self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass

class RPCTransportTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.server.parent='11'*32;self.server.calls=[];self.server.drop=None;self.server.remote_error=None
        self.server.close_response=False
        self.server.entered=threading.Event();self.server.release=threading.Event()
        self.server.auth='Basic '+base64.b64encode(b'__cookie__:inert-test').decode()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.verify=Mock();self.provider=Mock(return_value='__cookie__:inert-test')
        self.client=LocalRPC(self.server.server_port,verify=self.verify,credential_provider=self.provider)
    def tearDown(self):
        self.client.close();self.server.release.set();self.server.shutdown();self.server.server_close();self.thread.join(1)
    def test_reuses_connection_and_does_not_export_credentials(self):
        for _ in range(3):self.assertEqual(self.client.call('getbestblockhash'),'11'*32)
        self.verify.assert_called_once();self.provider.assert_called_once()
        self.assertEqual(self.client.stats()['connections_opened'],1)
        self.assertNotIn('inert-test',json.dumps(self.client.stats()))
        self.client.close();self.assertIsNone(self.client.authorization)
    def test_forbidden_method_is_rejected_before_credentials_or_network(self):
        with self.assertRaises(ValueError):self.client.call('walletpassphrase','not-used')
        self.provider.assert_not_called();self.assertEqual(self.server.calls,[])
        reader=self.client.fork({'getblocktemplate','getbestblockhash'})
        with self.assertRaises(ValueError):reader.call('submitblock','00')
        reader.close()
    def test_remote_warmup_code_preserved_without_raw_message(self):
        self.server.remote_error='getblockchaininfo'
        with self.assertRaises(RemoteRPCError) as raised:self.client.call('getblockchaininfo')
        self.assertEqual(raised.exception.code,-28);self.assertNotIn('SENSITIVE',str(raised.exception))
        self.assertEqual(self.client.call('getbestblockhash'),'11'*32)
    def test_failed_submission_is_never_retried(self):
        self.server.drop='submitblock'
        with self.assertRaises(RuntimeError):self.client.call('submitblock','00')
        self.assertEqual(self.server.calls,['submitblock'])
    def test_authentication_failure_is_not_retried(self):
        self.server.auth='different'
        with self.assertRaises(RuntimeError) as raised:self.client.call('getbestblockhash')
        self.assertNotIn('inert-test',str(raised.exception));self.assertEqual(len(self.server.calls),1)
    def test_close_cancels_blocked_longpoll_and_late_reply_is_ignored(self):
        watcher=TipWatcher(self.client,{'previousblockhash':'11'*32,'longpollid':'old'})
        watcher.start();self.assertTrue(self.server.entered.wait(1))
        started=time.monotonic();watcher.close()
        self.assertLess(time.monotonic()-started,1);self.assertFalse(watcher.thread.is_alive())
        watcher.publish('22'*32,time.monotonic());self.assertEqual(watcher.snapshot()['generation'],0)
    def test_longpoll_detects_same_height_parent_change(self):
        watcher=TipWatcher(self.client,{'previousblockhash':'11'*32,'longpollid':'old'})
        watcher.start();self.assertTrue(self.server.entered.wait(1));self.server.parent='22'*32;self.server.release.set()
        deadline=time.monotonic()+1
        while watcher.snapshot()['parent']!='22'*32 and time.monotonic()<deadline:time.sleep(.005)
        snapshot=watcher.snapshot();watcher.close()
        self.assertEqual(snapshot['parent'],'22'*32);self.assertEqual(snapshot['generation'],1)
        self.assertGreaterEqual(snapshot['observed'],snapshot['sample_started'])
    def test_disallowed_port_is_rejected(self):
        for port in (8332,80,65536):
            with self.assertRaises(ValueError):LocalRPC(port)
    def test_failed_identity_check_never_reads_cookie_or_sends_request(self):
        self.verify.side_effect=ValueError('Identity changed')
        with self.assertRaises(RuntimeError):self.client.call('getbestblockhash')
        self.provider.assert_not_called();self.assertEqual(self.server.calls,[])
    def test_server_close_response_body_is_read_then_next_call_reverifies(self):
        self.server.close_response=True
        for _ in range(2):self.assertEqual(self.client.call('getbestblockhash'),'11'*32)
        self.assertEqual(self.verify.call_count,2);self.assertEqual(self.client.stats()['connections_opened'],2)
    def test_longpoll_failure_falls_back_to_parent_polling(self):
        self.server.drop='getblocktemplate'
        watcher=TipWatcher(self.client,{'previousblockhash':'11'*32,'longpollid':'old'})
        watcher.start();deadline=time.monotonic()+2
        while watcher.snapshot()['errors']==0 and time.monotonic()<deadline:time.sleep(.005)
        self.server.parent='33'*32
        while watcher.snapshot()['parent']!='33'*32 and time.monotonic()<deadline:time.sleep(.005)
        snapshot=watcher.snapshot();watcher.close()
        self.assertEqual(snapshot['parent'],'33'*32)
        self.assertEqual(snapshot['mode'],'polling_fallback');self.assertGreater(snapshot['errors'],0)

if __name__=='__main__':unittest.main()
