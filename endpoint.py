"""Read-only Windows process/ACL/TCP checks. Never reads a cookie or changes policy."""
import ctypes as C
import http.client
import json
from pathlib import Path
import socket
import struct
import subprocess
import time

P,U=C.c_void_p,C.c_ulong
K=C.WinDLL(r'C:\Windows\System32\kernel32.dll',use_last_error=True)
A=C.WinDLL(r'C:\Windows\System32\advapi32.dll',use_last_error=True)
I=C.WinDLL(r'C:\Windows\System32\iphlpapi.dll',use_last_error=True)
S=C.WinDLL(r'C:\Windows\System32\shell32.dll',use_last_error=True)
def bind(lib,name,args,result=C.c_int):
    f=getattr(lib,name);f.argtypes=args;f.restype=result;return f
free=bind(K,'LocalFree',[P],P)
close=bind(K,'CloseHandle',[P])
open_process=bind(K,'OpenProcess',[U,C.c_int,U],P)
current_process=bind(K,'GetCurrentProcess',[],P)
open_token=bind(A,'OpenProcessToken',[P,U,C.POINTER(P)])
token_info=bind(A,'GetTokenInformation',[P,C.c_int,P,U,C.POINTER(U)])
sid_string=bind(A,'ConvertSidToStringSidW',[P,C.POINTER(P)])
security_info=bind(A,'GetNamedSecurityInfoW',[C.c_wchar_p,C.c_int,U,C.POINTER(P),P,C.POINTER(P),P,C.POINTER(P)],U)
get_ace=bind(A,'GetAce',[P,U,C.POINTER(P)])
control=bind(A,'GetSecurityDescriptorControl',[P,C.POINTER(C.c_ushort),C.POINTER(U)])
attributes=bind(K,'GetFileAttributesW',[C.c_wchar_p],U)
image_name=bind(K,'QueryFullProcessImageNameW',[P,U,C.c_wchar_p,C.POINTER(U)])
tcp_table=bind(I,'GetExtendedTcpTable',[P,C.POINTER(U),C.c_int,U,C.c_int,U],U)
argv_parse=bind(S,'CommandLineToArgvW',[C.c_wchar_p,C.POINTER(C.c_int)],C.POINTER(C.c_wchar_p))

def checked(value):
    if not value:raise C.WinError(C.get_last_error())
    return value
def sid_text(sid):
    out=P();checked(sid_string(sid,C.byref(out)))
    try:return C.wstring_at(out)
    finally:free(out)
def owner_sid(process):
    token=P();checked(open_token(process,8,C.byref(token)))
    try:
        size=U();token_info(token,1,None,0,C.byref(size));data=C.create_string_buffer(size.value)
        checked(token_info(token,1,data,size,C.byref(size)))
        return sid_text(C.cast(data,C.POINTER(P)).contents.value)
    finally:close(token)

class ACL(C.Structure):_fields_=[('revision',C.c_ubyte),('reserved',C.c_ubyte),('size',C.c_ushort),('count',C.c_ushort),('reserved2',C.c_ushort)]
def private_path(path,owner,directory=False):
    flags=attributes(str(path))
    if flags==0xffffffff or flags&0x400:raise ValueError('Missing path or reparse point')
    own,dacl,descriptor=P(),P(),P()
    if security_info(str(path),1,5,C.byref(own),None,C.byref(dacl),None,C.byref(descriptor)):
        raise ValueError('Cannot inspect private-path security')
    try:
        mode=C.c_ushort();revision=U();checked(control(descriptor,C.byref(mode),C.byref(revision)))
        if sid_text(own)!=owner or not dacl or (directory and not mode.value&0x1000):raise ValueError('Unexpected owner or inheritance')
        if C.cast(dacl,C.POINTER(ACL)).contents.count!=3:raise ValueError('Private path needs exactly three ACL entries')
        sids=[]
        for n in range(3):
            ace=P();checked(get_ace(dacl,n,C.byref(ace)));kind,_,size,mask=struct.unpack('<BBHI',C.string_at(ace,8))
            if kind!=0 or size<12 or mask&0x1f01ff!=0x1f01ff:raise ValueError('Unexpected access entry')
            sids.append(sid_text(ace.value+8))
        if set(sids)!={owner,'S-1-5-18','S-1-5-32-544'}:raise ValueError('Cookie access is broader than the approved principals')
    finally:free(descriptor)

class TCP4(C.Structure):_fields_=[(n,U) for n in ['state','address','port','remote','remote_port','pid']]
class TCP6(C.Structure):_fields_=[('address',C.c_ubyte*16),('scope',U),('port',U),('remote',C.c_ubyte*16),('remote_scope',U),('remote_port',U),('state',U),('pid',U)]
def endpoints(port):
    result=[]
    for family,rowtype in [(2,TCP4),(23,TCP6)]:
        for _ in range(3):
            size=U();code=tcp_table(None,C.byref(size),False,family,5,0)
            if code not in (0,122):raise ValueError('TCP table unavailable')
            data=C.create_string_buffer(size.value);code=tcp_table(data,C.byref(size),False,family,5,0)
            if code==0:break
            if code!=122:raise ValueError('TCP table unavailable')
        else:raise ValueError('TCP table changed repeatedly')
        count=C.cast(data,C.POINTER(U)).contents.value
        for n in range(count):
            row=rowtype.from_buffer(data,4+n*C.sizeof(rowtype))
            if socket.ntohs(row.port&65535)==port and row.remote_port==0 and row.pid:
                raw=struct.pack('<I',row.address) if family==2 else bytes(row.address)
                address=socket.inet_ntop(socket.AF_INET if family==2 else socket.AF_INET6,raw)
                result.append((address,row.pid,row.state))
    return result

METHODS='getblockchaininfo,getnetworkinfo,getbestblockhash,getblockhash,getblockheader,getconnectioncount,validateaddress,getblocktemplate,submitblock'
def verify_endpoint(pid,cookie,port,datadir,network='mainnet',timeout=10):
    began=time.monotonic()
    def remaining():
        left=timeout-(time.monotonic()-began)
        if left<=0:raise TimeoutError('Session-verification deadline reached')
        return left
    if type(pid) is not int or pid<=0 or type(port) is not int or not 1024<=port<=65535:raise ValueError('Invalid process/port')
    process=checked(open_process(0x1000,False,pid));owner=owner_sid(current_process())
    try:
        if owner_sid(process)!=owner:raise ValueError('Wrong Core process owner')
        size=U(32768);name=C.create_unicode_buffer(size.value);checked(image_name(process,0,name,C.byref(size)))
        expected=r'C:\Program Files\Bitcoin\bitcoin-qt.exe' if network=='mainnet' else r'C:\Program Files\Bitcoin\daemon\bitcoind.exe'
        if name.value.lower()!=expected.lower():raise ValueError('Wrong Core executable')
    finally:close(process)
    path=Path(cookie);private_path(path.parent,owner,True);private_path(path,owner)
    if Path(str(path)+'.tmp').exists():raise ValueError('Cookie creation incomplete')
    # Restricted PowerShell permits this fixed read-only cmdlet query. No script
    # file, policy override, downloaded code or blocked script contents are used.
    command=f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | Select-Object -ExpandProperty CommandLine"
    query=subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-Command',command],
        capture_output=True,text=True,timeout=remaining(),creationflags=subprocess.CREATE_NO_WINDOW)
    if query.returncode:raise ValueError('Cannot inspect Core command line')
    count=C.c_int();argv=checked(argv_parse(query.stdout.strip(),C.byref(count)))
    try:args=[argv[n] for n in range(1,count.value)]
    finally:free(argv)
    required={'chain':'main' if network=='mainnet' else 'regtest','datadir':str(datadir),'server':'1','disablewallet':'1',
        'nosettings':'','rest':'0','rpcbind':'127.0.0.1','rpcallowip':'127.0.0.1','rpcport':str(port),
        'rpccookiefile':str(cookie),'rpcwhitelistdefault':'1','rpcwhitelist':'__cookie__:'+METHODS}
    if network=='regtest':required.update(listen='0',port='19844',connect='0',dnsseed='0',listenonion='0',discover='0',dbcache='64',par='1',maxmempool='10',printtoconsole='0')
    parsed=[a.removeprefix('-').partition('=')[::2] for a in args]
    if len(parsed)!=len(required) or dict(parsed)!=required:raise ValueError('Unexpected temporary-session arguments')
    for line in (Path(datadir)/'bitcoin.conf').read_text().splitlines():
        line=line.strip()
        if line and not line.startswith(('#',';')) and line.partition('=')[0].strip() not in ('blocksdir','walletdir'):
            raise ValueError('Configuration requires review')
    rows=endpoints(port)
    if len(rows)!=1 or rows[0][:2]!=('127.0.0.1',pid):raise ValueError('RPC binding or ownership mismatch')
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=min(2,remaining()))
    try:
        connection.request('POST','/','{}',{'Content-Type':'application/json'})
        response=connection.getresponse()
        if response.status!=401:raise ValueError('RPC must require authentication')
        response.read()
    finally:connection.close()
    return {'verified':True,'pid':pid,'address':'127.0.0.1','port':port,'raw_state':rows[0][2],'cookie_contents_read':False}
