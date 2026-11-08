"""Real loopback wire faults; inert synthetic RPC only, never a Core endpoint."""
import socket
import threading
import time
import unittest
from local_rpc import LocalRPC


class WireFaultTests(unittest.TestCase):
    def serve(self,response,stall=False):
        server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);server.settimeout(3)
        self.addCleanup(server.close)
        entered=threading.Event();release=threading.Event();calls=[]
        def run():
            try:
                conn,_=server.accept()
                with conn:
                    raw=b''
                    while b'\r\n\r\n' not in raw:
                        chunk=conn.recv(4096)
                        if not chunk:return
                        raw+=chunk
                    calls.append(raw);conn.sendall(response);entered.set()
                    if stall:release.wait(2)
            except OSError:pass
        worker=threading.Thread(target=run,daemon=True);worker.start()
        def cleanup():release.set();worker.join(3)
        self.addCleanup(cleanup)
        client=LocalRPC(server.getsockname()[1],credential_provider=lambda:'__cookie__:inert-test')
        self.addCleanup(client.close)
        return client,entered,calls

    def assert_rejected(self,response):
        client,_,calls=self.serve(response)
        with self.assertRaises(RuntimeError) as caught:client.call('submitblock','00',timeout=.5)
        self.assertEqual(len(calls),1)
        self.assertIsNone(client.authorization)
        self.assertNotIn('inert-test',str(caught.exception))
        self.assertNotIn('PRIVATE_RESPONSE',str(caught.exception))

    def test_truncated_content_length(self):
        self.assert_rejected(b'HTTP/1.1 200 OK\r\nContent-Length: 90\r\n\r\n{"id":1,"result":null}')

    def test_invalid_chunk_length(self):
        self.assert_rejected(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nPRIVATE_RESPONSE\r\n')

    def test_valid_chunked_null_submission(self):
        body=b'{"id":1,"result":null}'
        response=b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n'+format(len(body),'x').encode()+b'\r\n'+body+b'\r\n0\r\n\r\n'
        client,_,calls=self.serve(response)
        self.assertIsNone(client.call('submitblock','00',timeout=.5));self.assertEqual(len(calls),1)

    def test_stalled_body_deadline(self):
        client,_,calls=self.serve(b'HTTP/1.1 200 OK\r\nContent-Length: 90\r\n\r\n{',True)
        started=time.monotonic()
        with self.assertRaises(TimeoutError):client.call('submitblock','00',timeout=.2)
        self.assertLess(time.monotonic()-started,1);self.assertEqual(len(calls),1);self.assertIsNone(client.authorization)

    def test_close_interrupts_body_read(self):
        client,entered,calls=self.serve(b'HTTP/1.1 200 OK\r\nContent-Length: 90\r\n\r\n{',True)
        outcomes=[]
        def call():
            try:client.call('submitblock','00',timeout=10);outcomes.append('accepted')
            except RuntimeError:outcomes.append('failed')
        thread=threading.Thread(target=call);thread.start();self.assertTrue(entered.wait(1))
        started=time.monotonic();client.close();thread.join(1)
        self.assertFalse(thread.is_alive());self.assertLess(time.monotonic()-started,1)
        self.assertEqual(outcomes,['failed']);self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main(verbosity=2)
