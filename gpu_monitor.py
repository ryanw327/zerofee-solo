"""Read-only GPU core thermal guard and optional board telemetry; no tuning."""
import math
from pathlib import Path
import subprocess
import threading
import time
from dataclasses import dataclass

@dataclass(frozen=True)
class ThermalPolicy:
    limit_c: float = 80
    missing_grace_seconds: float = 15
    sample_seconds: float = 2
    def validate(self):
        for value in (self.limit_c,self.missing_grace_seconds,self.sample_seconds):
            if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Invalid GPU safety policy')
        if not 60<=self.limit_c<=90:raise ValueError('GPU core stop must be 60–90°C')
        if not 5<=self.missing_grace_seconds<=60 or not .1<=self.sample_seconds<=5:
            raise ValueError('Invalid GPU telemetry grace or sampling interval')

class ThermalGuard:
    """Monotonic, latched safety decision; core sensor only, never hotspot."""
    def __init__(self,policy=None,started=None):
        self.policy=policy or ThermalPolicy();self.policy.validate()
        self.missing_since=time.monotonic() if started is None else started
        self.reason=None
    def observe(self,value,now):
        if self.reason:return self.reason
        temperature=value.get('temperature_c') if isinstance(value,dict) else None
        valid=type(temperature) in (int,float) and math.isfinite(temperature) and temperature>=0
        if valid:
            self.missing_since=None
            if temperature>=self.policy.limit_c:
                self.reason=f'GPU core temperature {temperature:g}°C reached the {self.policy.limit_c:g}°C stop limit.'
        else:
            if self.missing_since is None:self.missing_since=now
            if now-self.missing_since>=self.policy.missing_grace_seconds:
                self.reason=f'GPU core temperature readings unavailable for {self.policy.missing_grace_seconds:g} seconds.'
        return self.reason

def nvidia_smi_path():
    candidates = [Path(r'C:\Windows\System32\nvidia-smi.exe'),
                  Path(r'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe')]
    exe = next((path for path in candidates if path.is_file()), None)
    if exe is None:
        raise RuntimeError('NVIDIA telemetry unavailable')
    return exe

def read_gpu():
    result = subprocess.run([str(nvidia_smi_path()), '-i=0',
        '--query-gpu=temperature.gpu,power.draw,utilization.gpu', '--format=csv,noheader,nounits'],
        capture_output=True, text=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError('NVIDIA telemetry unavailable')
    parts=result.stdout.strip().split(',')
    if len(parts)!=3:
        raise ValueError('NVIDIA telemetry unavailable')
    temperature=float(parts[0].strip())
    if not math.isfinite(temperature) or temperature<0:raise ValueError('NVIDIA core temperature unavailable')
    def optional(part):
        try:value=float(part.strip())
        except ValueError:return None
        return value if math.isfinite(value) and value>=0 else None
    return dict(temperature_c=temperature,board_watts=optional(parts[1]),utilization_percent=optional(parts[2]))

def reading_text(value):
    if not isinstance(value,dict):return 'GPU core readings unavailable; safety grace is running. Hotspot is not monitored.'
    def number(key,suffix):
        item=value.get(key)
        return f'{item:g}{suffix}' if type(item) in (int,float) and math.isfinite(item) and item>=0 else 'unavailable'
    return ('GPU 0 core: '+number('temperature_c','°C')+' · '+number('board_watts',' W board')+
        ' · utilization: '+number('utilization_percent','%')+' · hotspot not monitored')

class GPUMonitor:
    def __init__(self, events, worker_stop, policy=None, read=None, clock=None):
        if not callable(getattr(worker_stop,'set',None)):raise ValueError('GPU safety requires a cooperative worker stop event')
        self.events,self.worker_stop=events,worker_stop
        self.policy=policy or ThermalPolicy();self.policy.validate()
        self.read=read or read_gpu;self.clock=clock or time.monotonic
        self.guard=ThermalGuard(self.policy,self.clock());self.reason=None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.work, name='MinerGPUReadings', daemon=True)
    def start(self):
        self.thread.start()
    def work(self):
        try:self._work_loop()
        except BaseException as exc:self._trip(f'GPU core temperature monitor failed ({type(exc).__name__}).')
    def _trip(self,reason):
        if self.reason:return
        self.reason=reason+' Cooperative stop requested; no automatic restart.'
        self.worker_stop.set()
        try:self.events.put(('safety_stop',self.reason))
        except Exception:pass
    def _work_loop(self):
        while not self.stop_event.is_set():
            try:
                value = self.read()
            except Exception:
                value = None
            self.events.put(('gpu', value))
            reason=self.guard.observe(value,self.clock())
            if reason:
                self._trip(reason)
                break
            if self.stop_event.wait(self.policy.sample_seconds):
                break
    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(3)
