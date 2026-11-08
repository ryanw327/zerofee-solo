"""One writer, durable public-hash journal; uncertain submissions require inspection."""
import json
import os
from pathlib import Path
import time

class SubmissionJournal:
    def __init__(self,path):self.path=Path(path);self.lock=Path(str(path)+'.lock');self.entry={};self.active=False
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.fd=os.open(self.lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        try:
            os.write(self.fd,str(os.getpid()).encode());os.fsync(self.fd)
            if self.path.exists():
                self.entry=json.loads(self.path.read_text())
                if not isinstance(self.entry,dict) or self.entry.get('status') not in ('PENDING','UNCERTAIN','ACCEPTED','REJECTED','CONFIRMED','ACCEPTED_SIDECHAIN'):
                    raise RuntimeError('Unrecognized journal state; inspect before another run')
                block_hash=self.entry.get('block_hash','')
                if not isinstance(block_hash,str) or len(block_hash)!=64 or any(c not in '0123456789abcdef' for c in block_hash):raise RuntimeError('Invalid journal hash')
            if self.entry.get('status') in ('PENDING','UNCERTAIN'):
                raise RuntimeError('Resolve the previous submission in '+str(self.path)+' before another run')
            self.active=True;return self
        except BaseException:self.__exit__(None,None,None);raise
    def __exit__(self,*args):self.active=False;os.close(self.fd);self.lock.unlink()
    def save(self,status,**fields):
        if not self.active:raise RuntimeError('Journal context is not locked')
        self.entry.update(status=status,updated_unix=time.time(),**fields)
        temporary=Path(str(self.path)+'.tmp')
        with temporary.open('w') as f:
            json.dump(self.entry,f,indent=2);f.flush();os.fsync(f.fileno())
        temporary.replace(self.path)
    def begin(self,block_hash,height,previous,network='regtest',core_pid=None):
        if self.entry.get('status') in ('PENDING','UNCERTAIN'):raise RuntimeError('Unresolved submission')
        self.entry={};self.save('PENDING',block_hash=block_hash,height=height,previous=previous,network=network,core_pid=core_pid)
