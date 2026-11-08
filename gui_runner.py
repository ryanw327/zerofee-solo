from app_paths import DEFAULT_DATADIR
"""One cooperative worker; no node, GPU, timers or mining start on import."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from queue import Queue
import threading
import time
from types import SimpleNamespace
from run_limits import validate_limits

ROOT = Path(__file__).resolve().parent
ADDRESS = ''  # Operator must enter and confirm their own receiving address.

@dataclass(frozen=True)
class RunConfig:
    mode: str = 'simulation'
    seconds: float | None = 10
    hashes: int | None = 32768
    address: str = ADDRESS
    datadir: str = DEFAULT_DATADIR
    cookie: str = ''
    core_pid: int = 0
    managed: bool = False
    profile: str = 'test'
    pause_seconds: float = 0
    batch_size: int = 262144
    threads_per_block: int = 128
    temperature_limit_c: float = 80
    telemetry_grace_seconds: float = 15

    def validate(self):
        if self.mode not in ('simulation', 'mainnet'):
            raise ValueError('Choose simulation or an existing mainnet session.')
        validate_limits(self.seconds,self.hashes,profile=self.profile,pause_seconds=self.pause_seconds,can_stop=True)
        from advanced_settings import BATCH_SIZES, validate_threads
        if type(self.batch_size) is not int or self.batch_size not in BATCH_SIZES:
            raise ValueError("Choose a listed batch size (4096–262144).")
        validate_threads(self.threads_per_block)
        from gpu_monitor import ThermalPolicy
        ThermalPolicy(self.temperature_limit_c,self.telemetry_grace_seconds).validate()
        if self.mode == 'mainnet':
            from address import payout_script
            payout_script(self.address, 'mainnet')
            if self.profile == 'session' and not self.managed:
                raise ValueError('Mining sessions require automatic Core connection and restoration.')
            if self.managed:
                if self.datadir != DEFAULT_DATADIR:
                    raise ValueError('Automatic connection supports the default Bitcoin Core data folder only.')
            elif not Path(self.datadir).is_absolute() or not Path(self.cookie).is_absolute() or self.core_pid <= 0:
                raise ValueError('Supply the existing Core data folder, private cookie path and current Core PID.')

class Runner:
    def __init__(self, result_path=ROOT / 'gui-last-result.json'):
        self.events = Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.thread = None
        self.result_path = result_path

    def start(self, config, approved=False, connection_approved=False):
        if self.busy:
            raise RuntimeError('A run is already active. Wait until it stops.')
        config.validate()
        if config.mode == 'mainnet' and approved is not True:
            raise ValueError('Approve this bounded mainnet run and valid-block submission first.')
        if config.managed and (config.mode != 'mainnet' or connection_approved is not True):
            raise ValueError('Approve temporary access and clean Core restarts for this single run first.')
        self.stop_event.clear()
        self.busy = True
        self.events.put(('status', 'Starting'))
        self.thread = threading.Thread(target=self._work, args=(config,), name='BitcoinMinerWorker')
        try:
            self.thread.start()
        except BaseException:
            self.busy = False
            raise

    def stop(self):
        if self.busy:
            self.stop_event.set()
            self.events.put(('status', 'Stopping'))

    def start_benchmark(self, settings, result_path=ROOT / 'gpu-benchmark.json'):
        if self.busy:
            raise RuntimeError('Wait until the active session has stopped.')
        settings.validate()
        from tuning_benchmark import BenchmarkWorker, assert_no_other_miner
        assert_no_other_miner()
        self.stop_event.clear()
        self.busy = True
        self.thread = threading.Thread(target=BenchmarkWorker.work, args=(self, settings, result_path),
                                       name='SyntheticGPUBenchmark')
        try:
            self.thread.start()
        except BaseException:
            self.busy = False
            raise

    def _work(self, config):
        result, error = None, None
        managed, restoration, restoration_error, monitor = None, None, None, None
        journal = None
        last_public_write=0
        public_counts={}
        public_phase='Starting'
        def publish(phase,counts=None,force=False):
            nonlocal last_public_write,public_counts
            if config.profile!='session' or config.mode!='mainnet':return
            if counts is not None:public_counts=counts
            now=time.monotonic()
            if not force and now-last_public_write<1:return
            last_public_write=now
            path=ROOT/'gui-session-status.json'
            value=dict(updated_utc=datetime.now(timezone.utc).isoformat(),phase=phase,
                requested_seconds=config.seconds,run_profile=config.profile,**public_counts)
            try:
                path.with_suffix('.tmp').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
                path.with_suffix('.tmp').replace(path)
            except OSError:pass
        def status(state):
            nonlocal public_phase
            public_phase=state
            self.events.put(('status',state));publish(state,force=True)
        def progress(counts):
            self.events.put(('progress',counts));publish(public_phase,counts)
        try:
            if config.mode == 'simulation':
                self.events.put(('status', 'Simulating'))
                if config.profile == 'session':
                    began=time.monotonic()
                    while not self.stop_event.is_set():
                        elapsed=time.monotonic()-began
                        remaining=None if config.seconds is None else max(0,config.seconds-elapsed)
                        self.events.put(('progress',dict(hashes=None,blocks=None,elapsed_seconds=elapsed,
                            remaining_seconds=remaining,hashes_per_second=None)))
                        if remaining is not None and remaining<=0:break
                        self.stop_event.wait(.25 if remaining is None else min(.25,remaining))
                    stopped=self.stop_event.is_set()
                else:
                    stopped = self.stop_event.wait(config.seconds)
                result = dict(status='STOPPED' if stopped else 'COMPLETE', mode='simulation',
                              hashes=None, blocks=None, note='Controls test only. No GPU, RPC or mining.')
            else:
                from miner import execute, MiningStopped
                from journal import SubmissionJournal
                journal = SubmissionJournal(ROOT / 'mainnet-submission.json')
                journal.__enter__()
                if config.managed:
                    from managed_core import ManagedCore, Cancelled
                    managed = ManagedCore(journal=journal)
                    try:
                        session = managed.prepare(approved=True, stop=self.stop_event,
                            status=status)
                    except Cancelled:
                        result = dict(status='STOPPED', mode='mainnet', hashes=None, blocks=[],
                                      note='Stopped during connection setup; no mining started.')
                        return  # finally still restores Core and delivers the finished event.
                    config = replace(config, managed=False, **session)
                # The journal is shared with the CLI in this app folder; never clear it automatically.
                args = SimpleNamespace(network='mainnet', datadir=config.datadir, cookie=config.cookie,
                    session_pid=config.core_pid, mainnet_opt_in=True, submit_blocks=True, address=config.address,
                    max_seconds=config.seconds, max_hashes=config.hashes, max_blocks=1)
                args.run_profile, args.pause_seconds = config.profile, config.pause_seconds
                args.batch_size = config.batch_size if config.profile == 'session' else 4096
                args.threads_per_block = config.threads_per_block
                from gpu_monitor import GPUMonitor,ThermalPolicy
                monitor = GPUMonitor(self.events,self.stop_event,
                    policy=ThermalPolicy(config.temperature_limit_c,config.telemetry_grace_seconds))
                monitor.start()  # Mandatory monitoring; failure stops before execute.
                try:
                    if self.stop_event.is_set():
                        raise MiningStopped('Stop requested')
                    callbacks=dict(on_work=lambda work:self.events.put(('work',work))) if config.profile=='session' else {}
                    result = execute(args, 19732, journal, self.stop_event,status,progress,**callbacks)
                except MiningStopped:
                    result = dict(status='STOPPED', mode='mainnet', hashes=None, blocks=[],
                                  note='Stopped during startup; no mining-loop result available.')
        except Exception as exc:
            result=getattr(exc,'mining_result',result)
            error = f'{type(exc).__name__}: {exc}'
        finally:
            if monitor is not None:
                try:
                    monitor.close()
                except Exception:
                    self.events.put(('notice','GPU telemetry could not close cleanly; Core restoration is continuing.'))
            stop_reason=getattr(monitor,'reason',None) if monitor is not None else None
            if type(stop_reason) is not str:stop_reason=None
            if managed is not None and managed.created and managed.pending():
                try:
                    restoration = managed.restore(approved=True,
                        status=status)
                except Exception as exc:
                    restoration_error = str(exc)
            if journal is not None and journal.active:
                try:
                    journal.__exit__(None, None, None)
                except Exception as exc:
                    restoration_error = (restoration_error or '') + ' Journal release needs review: ' + str(exc)
            record = dict(recorded_utc=datetime.now(timezone.utc).isoformat(), mode=config.mode,
                          result=result, error=error, restoration=restoration,
                          restoration_error=restoration_error,run_profile=config.profile,
                          requested_seconds=config.seconds,batch_pause_seconds=config.pause_seconds,payout_address=config.address,
                          tuning=dict(batch_size=config.batch_size if config.profile=='session' else 4096,
                              threads_per_block=config.threads_per_block),
                          stop_reason=stop_reason,gpu_safety=dict(core_limit_c=config.temperature_limit_c,
                          missing_readings_grace_seconds=config.telemetry_grace_seconds,sensor='GPU core (temperature.gpu); hotspot is not read'))
            try:
                from win_moment import save_receipts,receipt_snapshots
                record['win_receipts']=save_receipts(record,ROOT)
            except Exception as exc:
                try:record['win_receipts']=receipt_snapshots(record)
                except Exception:record['win_receipts']=[]
                record['win_receipt_error']=f'{type(exc).__name__}: {exc}'
                self.events.put(('notice','Accepted-block receipt needs attention; retain the final result and submission journal.'))
            publish('Finished',force=True)
            if self.result_path is not None:
                try:
                    temporary = self.result_path.with_suffix('.tmp')
                    temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
                    temporary.replace(self.result_path)
                except OSError:
                    self.events.put(('notice', 'Could not save the result file; the displayed result is still valid.'))
            self.busy = False
            self.events.put(('finished', record))

    def restore_core(self, approved=False):
        if approved is not True:
            raise ValueError('Approve restoration of normal Core first.')
        if self.busy:
            raise RuntimeError('Wait until the current worker stops.')
        self.stop_event.clear()
        self.busy = True
        def work():
            result, error = None, None
            try:
                from managed_core import ManagedCore
                from journal import SubmissionJournal
                with SubmissionJournal(ROOT / 'mainnet-submission.json') as journal:
                    result = ManagedCore(journal=journal).restore(approved=True,
                        status=lambda state: self.events.put(('status', state)))
            except Exception as exc:
                error = str(exc)
            finally:
                self.busy = False
                self.events.put(('finished', dict(mode='recovery', result=result, error=error,
                    restoration=result, restoration_error=error)))
        self.thread = threading.Thread(target=work, name='CoreRestorationWorker')
        try:
            self.thread.start()
        except BaseException:
            self.busy = False
            raise
