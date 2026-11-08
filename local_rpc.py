"""Per-session loopback JSON-RPC; credentials stay in memory, never in logs."""
import base64,http.client,io,json,select,socket,threading,time
from pathlib import Path

class CancelableReader(io.RawIOBase):
    """Avoid an uninterruptible Windows recv while a longpoll awaits headers."""
    def __init__(self,sock,cancel,timeout):
        self.sock,self.cancel=sock,cancel;self.deadline=time.monotonic()+timeout
        # Preserve socket.makefile lifetime semantics when HTTP says close:
        # HTTPConnection may release its socket before the body is consumed.
        self.file_ref=sock.makefile('rb',buffering=0)
    def readable(self):return True
    def close(self):
        if not self.closed:self.file_ref.close()
        super().close()
    def readinto(self,buffer):
        while True:
            if self.cancel.is_set():raise OSError('Local RPC reader closed')
            remaining=self.deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Local RPC response deadline reached')
            ready,_,_=select.select([self.sock],[],[],min(.1,remaining))
            if ready:
                if self.cancel.is_set():raise OSError('Local RPC reader closed')
                return self.sock.recv_into(buffer)

class ResponseSocket:
    def __init__(self,sock,cancel,timeout):self.sock,self.cancel,self.timeout=sock,cancel,timeout
    def makefile(self,*args,**kwargs):return io.BufferedReader(CancelableReader(self.sock,self.cancel,self.timeout))

class RemoteRPCError(RuntimeError):
    def __init__(self,code):super().__init__(f'Local RPC returned error code {code}; raw message withheld');self.code=code

class LocalRPC:
    METHODS=frozenset('getblockchaininfo getnetworkinfo getbestblockhash getblockhash getblockheader getconnectioncount validateaddress getblocktemplate submitblock'.split())
    def __init__(self,port,cookie=None,verify=None,credential_provider=None,connection_factory=None,allowed_methods=None):
        if type(port) is not int or not 1024<=port<=65535 or port==8332:raise ValueError('Reviewed loopback RPC port required')
        self.port,self.cookie,self.verify=port,cookie,verify
        self.provider=credential_provider;self.factory=connection_factory or http.client.HTTPConnection
        self.allowed=frozenset(allowed_methods) if allowed_methods is not None else self.METHODS
        self.connection=None;self.authorization=None;self.closed=False;self.sequence=0
        self.state_lock=threading.Lock();self.call_lock=threading.Lock();self.cancel=threading.Event()
        self.connections_opened=0;self.requests=0;self.request_seconds=0
    def fork(self,allowed_methods=None):
        allowed=self.allowed if allowed_methods is None else frozenset(allowed_methods)
        if not allowed<=self.allowed:raise ValueError('Reader cannot expand RPC permissions')
        return LocalRPC(self.port,self.cookie,self.verify,self.provider,self.factory,allowed)
    def _connect(self,timeout):
        with self.state_lock:
            if self.closed:raise RuntimeError('Local RPC client is closed')
            if self.connection is not None and self.connection.sock is not None:return self.connection
            if self.connection is not None:self.connection.close();self.connection=None;self.authorization=None
        if self.verify:self.verify() # Validate owner, executable, args, ACL and authenticated loopback binding before reading cookie.
        with self.state_lock:
            if self.closed:raise RuntimeError('Local RPC client closed during identity verification')
        if self.provider:credential=self.provider()
        else:
            raw=Path(self.cookie).read_bytes()
            if len(raw)>4096:raise ValueError('Invalid private cookie')
            credential=raw.decode('ascii').strip()
        if not isinstance(credential,str) or not credential.startswith('__cookie__:') or not credential.partition(':')[2] or any(c in credential for c in '\r\n'):
            raise ValueError('Invalid private cookie format')
        authorization='Basic '+base64.b64encode(credential.encode('ascii')).decode('ascii')
        connection=self.factory('127.0.0.1',self.port,timeout=min(timeout,3))
        with self.state_lock:
            if self.closed:raise RuntimeError('Local RPC client is closed')
            self.connection=connection;self.authorization=authorization
        try:
            connection.connect()
            with self.state_lock:
                if self.closed:raise RuntimeError('Local RPC client closed during connect')
                self.connections_opened+=1
            return connection
        except BaseException:
            self._discard(connection);raise
    def _discard(self,connection):
        with self.state_lock:
            if self.connection is connection:self.connection=None;self.authorization=None
        try:
            if connection.sock:connection.sock.shutdown(socket.SHUT_RDWR)
        except (OSError,AttributeError):pass
        connection.close()
    def call(self,method,*params,timeout=3):
        if method not in self.allowed:raise ValueError('RPC method is outside this client scope')
        if timeout<=0:raise TimeoutError('RPC deadline reached')
        started=time.perf_counter()
        with self.call_lock:
            connection=None
            try:
                connection=self._connect(timeout)
                with self.state_lock:
                    if self.closed:raise RuntimeError('Local RPC client is closed')
                    authorization=self.authorization
                    self.sequence+=1;identifier=self.sequence
                connection.sock.settimeout(timeout)
                connection.response_class=lambda sock,**kw:http.client.HTTPResponse(ResponseSocket(sock,self.cancel,timeout),**kw)
                body=json.dumps(dict(jsonrpc='2.0',id=identifier,method=method,params=list(params)),separators=(',',':'))
                self.requests+=1
                # No proxy, redirects, URL input or automatic retries, including submitblock.
                connection.request('POST','/',body,{'Content-Type':'application/json','Authorization':authorization,'Connection':'keep-alive'})
                with self.state_lock:
                    if self.closed:raise RuntimeError('Local RPC client closed during request')
                response=connection.getresponse();raw=response.read(16_000_001)
                if len(raw)>16_000_000:raise RuntimeError('Local RPC response exceeded limit')
                if isinstance(response.length, int) and response.length != 0:raise RuntimeError('Truncated local RPC response body')
                if response.status in (301,302,303,307,308,401,403):raise RuntimeError(f'Local RPC HTTP {response.status}; no redirect or authentication retry')
                value=json.loads(raw)
                if not isinstance(value,dict) or type(value.get('id')) is not int or value['id']!=identifier:raise RuntimeError('Unexpected local RPC response ID')
                error=value.get('error')
                if error is not None:
                    code=error.get('code') if isinstance(error,dict) else None
                    if type(code) is not int:raise RuntimeError('Invalid local RPC error')
                    raise RemoteRPCError(code)
                if response.status!=200 or 'result' not in value:raise RuntimeError('Unexpected local RPC response')
                return value['result']
            except RemoteRPCError:raise
            except BaseException as exc:
                if connection is not None:self._discard(connection)
                if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
                if isinstance(exc,TimeoutError):raise
                # Never include response bodies, credentials, HTTP headers or raw errors.
                raise RuntimeError(f'Local RPC {method} failed ({type(exc).__name__}); no retry was sent') from None
            finally:self.request_seconds+=time.perf_counter()-started
    def close(self):
        self.cancel.set()
        with self.state_lock:
            self.closed=True;connection=self.connection;self.connection=None;self.authorization=None
        if connection is not None:self._discard(connection)
    def stats(self):return dict(requests=self.requests,connections_opened=self.connections_opened,request_seconds=self.request_seconds)

