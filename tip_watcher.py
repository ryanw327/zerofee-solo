"""Read-only GBT longpoll notifications; main-loop polling remains a fallback."""
import re,threading,time

class TipWatcher:
    def __init__(self,reader,initial,external_stop=None,clock=None):
        self.reader,self.external_stop=reader,external_stop
        self.clock=clock or time.monotonic;self.stop=threading.Event();self.lock=threading.Lock()
        self.parent=initial.get('previousblockhash');self.token=initial.get('longpollid')
        self.generation=0;self.sample_started=self.observed=self.clock()
        self.healthy=False;self.errors=0;self.notifications=[]
        self.thread=threading.Thread(target=self.work,name='BitcoinTipLongpoll',daemon=True)
    def start(self):self.thread.start()
    def stopping(self):return self.stop.is_set() or (self.external_stop is not None and self.external_stop.is_set())
    def publish(self,parent,started):
        if not isinstance(parent,str) or not re.fullmatch('[0-9a-f]{64}',parent):raise ValueError('Invalid observed parent hash')
        with self.lock:
            if self.stopping():return
            self.healthy=True
            if parent!=self.parent:
                self.parent=parent;self.generation+=1;self.sample_started=started;self.observed=self.clock()
                self.notifications.append(dict(generation=self.generation,parent=parent,request_started=started,observed=self.observed))
                self.notifications=self.notifications[-100:]
    def work(self):
        while not self.stopping():
            try:
                if isinstance(self.token,str) and self.token:
                    template=self.reader.call('getblocktemplate',{'rules':['segwit'],'capabilities':['proposal'],'longpollid':self.token},timeout=65)
                    if self.stopping():break
                    self.token=template.get('longpollid')
                started=self.clock()
                self.publish(self.reader.call('getbestblockhash',timeout=3),started)
                if not self.token and self.stop.wait(.1):break
            except Exception:
                if self.stopping():break
                with self.lock:self.errors+=1;self.healthy=False
                # Watcher failure never weakens the main thread's strict checks.
                self.token=None
                if self.stop.wait(.25):break
    def snapshot(self):
        with self.lock:return dict(parent=self.parent,generation=self.generation,sample_started=self.sample_started,
            observed=self.observed,healthy=self.healthy,errors=self.errors,mode='longpoll' if self.token else 'polling_fallback')
    def close(self):
        self.stop.set();self.reader.close() # Shutdown wakes a blocked longpoll read.
        if self.thread.is_alive():self.thread.join(2)
        if self.thread.is_alive():raise RuntimeError('Read-only tip watcher did not stop within two seconds')
    def stats(self):
        with self.lock:return dict(generation=self.generation,errors=self.errors,notifications=list(self.notifications),
            mode='longpoll' if self.token else 'polling_fallback',reader_transport=self.reader.stats())
