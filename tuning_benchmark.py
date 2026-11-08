"""Explicit, bounded synthetic GPU sweep. No RPC, Core, wallet or submission code."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
from queue import Queue
import threading
import time
from advanced_settings import TuningSettings
from bitcoin import MAX_TARGET, sha256d, with_nonce

SWEEP = ((65536, 128), (131072, 256), (262144, 512))
BENCHMARK_SECONDS = 60

def assert_no_other_miner():
    """Fail closed when process inventory fails; other Python apps are conservative blockers."""
    class Entry(C.Structure):
        _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('th32ProcessID', W.DWORD),
                    ('th32DefaultHeapID', C.c_size_t), ('th32ModuleID', W.DWORD),
                    ('cntThreads', W.DWORD), ('th32ParentProcessID', W.DWORD),
                    ('pcPriClassBase', W.LONG), ('dwFlags', W.DWORD), ('szExeFile', W.WCHAR * 260)]
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [W.HANDLE, C.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == C.c_void_p(-1).value:
        raise RuntimeError('Cannot verify that other mining processes are idle.')
    blockers = []
    try:
        entry = Entry(); entry.dwSize = C.sizeof(entry)
        if not kernel.Process32FirstW(handle, C.byref(entry)):
            raise C.WinError(C.get_last_error())
        while True:
            name = entry.szExeFile.lower()
            if entry.th32ProcessID != os.getpid() and (
                name.startswith(('python', 'cgminer', 'bfgminer', 'cpuminer', 'ccminer', 'xmrig', 'lolminer', 'nbminer', 't-rex'))
                or name in ('miner.exe', 'bitcoin-miner.exe', 'bitcoin miner.exe')):
                blockers.append(f'{name} (PID {entry.th32ProcessID})')
            if not kernel.Process32NextW(handle, C.byref(entry)):
                if C.get_last_error() != 18:
                    raise C.WinError(C.get_last_error())
                break
    finally:
        kernel.CloseHandle(handle)
    if blockers:
        raise RuntimeError('Benchmark requires idle mining. Close other Python/miner processes first: ' + ', '.join(blockers))

def verify_compact_search(gpu, header, stop):
    """Check the timed compact path, not only the separate full-digest kernel.

    A unique minimum exercises inclusive target comparison and nonzero nonce
    offsets without overflowing the compact candidate buffer. The next smaller
    target must yield no candidate. These are synthetic, CPU-oracle checks.
    """
    first = 0xfffffeff
    digests = [sha256d(with_nonce(header, first + i)) for i in range(257)]
    offset = min(range(len(digests)), key=lambda i: int.from_bytes(digests[i], 'little'))
    target = int.from_bytes(digests[offset], 'little')
    for threshold, expected in ((target, (offset, digests[offset])), (target - 1, None)):
        if stop.is_set():
            return False
        if threshold < 0:
            continue  # A zero digest has no representable smaller target.
        actual = gpu.search_batch(header, first, len(digests), threshold)
        if expected is None:
            valid = actual is None
        else:
            valid = (type(actual) is tuple and len(actual) == 2 and type(actual[0]) is int
                     and type(actual[1]) is bytes and actual == expected)
        if not valid:
            raise RuntimeError('Benchmark compact-search CPU/GPU verification failed.')
    return not stop.is_set()


def run_sweep(settings, stop, events, *, gpu_factory=None, monitor_factory=None,
              read=None, clock=None, idle_check=None):
    """60s wall budget includes setup and pauses; cleanup may finish after that boundary."""
    settings.validate()
    (idle_check or assert_no_other_miner)()
    from gpu_monitor import GPUMonitor, ThermalPolicy, ThermalGuard, read_gpu
    from gpu_hasher import GPUHasher
    clock = clock or time.monotonic
    read = read or read_gpu
    policy = ThermalPolicy(settings.temperature_limit_c)
    first = read()  # A valid cool reading is mandatory before GPU initialization.
    guard = ThermalGuard(policy, clock())
    if not isinstance(first, dict) or type(first.get('temperature_c')) not in (int, float):
        raise RuntimeError('Benchmark requires a valid GPU core temperature reading.')
    import math
    if not math.isfinite(first['temperature_c']) or first['temperature_c'] < 0:
        raise RuntimeError('Benchmark requires a valid GPU core temperature reading.')
    reason = guard.observe(first, clock())
    if reason:
        raise RuntimeError(reason)
    monitor = (monitor_factory or GPUMonitor)(events, stop, policy=policy)
    rows = []
    began = clock()
    deadline = began + BENCHMARK_SECONDS
    try:
        monitor.start()
        if stop.is_set():
            return dict(kind='SYNTHETIC_GPU_BENCHMARK', status='CANCELLED', rows=[], seconds=0)
        with (gpu_factory or GPUHasher)(threads_per_block=128) as gpu:
            if not stop.is_set():
                from gpu_identity import verify_identity
                verify_identity(gpu)
            supported = [(batch, threads) for batch, threads in SWEEP if threads <= gpu.max_threads_per_block]
            skipped = [dict(batch_size=batch, threads_per_block=threads, reason='Exceeds queried device/kernel limit')
                       for batch, threads in SWEEP if threads > gpu.max_threads_per_block]
            if not supported:
                raise RuntimeError('No supported benchmark launch sizes.')
            for index, (batch, threads) in enumerate(supported):
                if stop.is_set() or clock() >= deadline:
                    break
                gpu.threads_per_block = threads
                header = bytes(range(80))  # Synthetic header; never obtained from a node.
                digests, flags = gpu.batch(header, 0, 257, MAX_TARGET)
                if len(digests) != 257 or len(flags) != 257 or not all(flags) or any(
                    digest != sha256d(with_nonce(header, nonce)) for nonce, digest in enumerate(digests)):
                    raise RuntimeError('Benchmark CPU/GPU SHA-256d verification failed.')
                if not verify_compact_search(gpu, header, stop):
                    break
                measured = clock(); hashes = 0; cursor = 0; last_progress = -1
                segment_end = min(deadline, began + (index + 1) * BENCHMARK_SECONDS / len(supported))
                while not stop.is_set() and clock() < segment_end:
                    count = min(batch, (1 << 32) - cursor)
                    candidate = gpu.search_batch(header, cursor, count, 0)
                    if candidate is not None:
                        raise RuntimeError('Unexpected benchmark candidate; stopped for correctness review.')
                    hashes += count
                    cursor = (cursor + count) % (1 << 32)
                    if clock() - last_progress >= .2:
                        events.put(('benchmark_progress', dict(variant=index + 1, total_variants=len(supported), batch_size=batch,
                            threads_per_block=threads, elapsed_seconds=clock() - began)))
                        last_progress = clock()
                    if settings.pause_seconds:
                        stop.wait(min(settings.pause_seconds, max(0, segment_end - clock())))
                elapsed = max(0, clock() - measured)
                rows.append(dict(batch_size=batch, threads_per_block=threads, hashes=hashes,
                    measured_seconds=elapsed, mh_s=hashes / elapsed / 1e6 if elapsed else 0,
                    complete=not stop.is_set() and clock() >= segment_end))
    finally:
        monitor.close()
    complete = len(rows) == len(supported) and all(row['complete'] and row['hashes'] for row in rows)
    best = max((row for row in rows if row['complete'] and row['hashes']), key=lambda row: row['mh_s'], default=None)
    return dict(kind='SYNTHETIC_GPU_BENCHMARK', status='STOPPED' if stop.is_set() else 'COMPLETE' if complete else 'INCOMPLETE',
        stop_reason=getattr(monitor, 'reason', None), rows=rows, skipped=skipped, seconds=clock() - began,
        best_starting_point=best if complete else None,
        pacing=settings.pacing, pause_seconds=settings.pause_seconds,
        temperature_limit_c=settings.temperature_limit_c,
        note='Synthetic hashes only; MH/s includes pauses and transfer, excludes per-row proof checks. No Core, blocks or rewards. Settings are never auto-applied. A short sweep is only a starting point, not a thermally soaked sustained result.')

class BenchmarkWorker:
    """Shared busy state belongs to the GUI Runner; this worker only computes a result."""
    @staticmethod
    def work(runner, settings, result_path):
        result = error = None
        try:
            result = run_sweep(settings, runner.stop_event, runner.events)
            if result_path is not None:
                path = Path(result_path); temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
                temporary.replace(path)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        finally:
            runner.busy = False
            runner.events.put(('finished', dict(mode='benchmark', result=None, benchmark=result,
                error=error, restoration_error=None)))

def main():
    from risk_notice import RISK_TEXT
    parser = argparse.ArgumentParser(description='Explicit 60-second synthetic GPU sweep; never mines or submits a block.', epilog=RISK_TEXT)
    parser.add_argument('--start', action='store_true', help='Required explicit start')
    parser.add_argument('--acknowledge-risk', action='store_true', help='Required acknowledgment of GPU stress, electricity costs and as-is operation')
    parser.add_argument('--pause-ms', type=float, default=50)
    parser.add_argument('--temperature-limit-c', type=float, default=80)
    args = parser.parse_args()
    if not args.start:
        parser.error('Pass --start to explicitly begin the 60-second GPU sweep.')
    if not args.acknowledge_risk:
        parser.error('Read --help, then pass --acknowledge-risk to acknowledge GPU stress and as-is operation.')
    settings = TuningSettings(pacing='Custom pause', custom_pause_ms=args.pause_ms,
                              temperature_limit_c=args.temperature_limit_c).validate()
    stop = threading.Event(); events = Queue()
    # Ctrl+C cooperatively cancels at a batch boundary and closes the context.
    import signal
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(json.dumps(run_sweep(settings, stop, events), indent=2))

if __name__ == '__main__':
    main()
