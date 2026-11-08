from app_paths import DEFAULT_DATADIR
"""Explicitly approved, temporary Core lifecycle. No action occurs on import.

Credentials stay in Core/bitcoin-cli or the session RPC client's memory. The recovery record contains public
metadata only. A crash never authorizes resuming mining or repeating a launch.
"""
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from endpoint import (METHODS, argv_parse, checked, close, current_process,
                      endpoints, free, open_process, owner_sid, private_path,
                      verify_endpoint)

EXE = r'C:\Program Files\Bitcoin\bitcoin-qt.exe'
DATADIR = DEFAULT_DATADIR
NORMAL_ARGS = ['-datadir=' + DATADIR]
ROOT = Path(__file__).resolve().parent
PHASES = {'prepared', 'normal_close_requested', 'temporary_start_requested',
          'temporary_running', 'temporary_close_requested', 'normal_start_requested', 'restored'}


class Cancelled(Exception):
    pass


class CloseNotSent(RuntimeError):
    """The cooperative close was definitely not queued; selecting again is safe."""


class WindowsCore:
    """Small native adapter, replaceable in lifecycle tests."""
    def processes(self):
        command = ('Get-CimInstance Win32_Process -Filter '
                   '"Name=\'bitcoin-qt.exe\' OR Name=\'bitcoind.exe\'" | '
                   'Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress')
        query = subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
            '-NoProfile', '-Command', command], capture_output=True, text=True,
            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if query.returncode:
            raise RuntimeError('Cannot inspect Core processes. No startup or shutdown is safe.')
        rows = json.loads(query.stdout.strip() or '[]')
        result = []
        for row in ([rows] if isinstance(rows, dict) else rows):
            if not row.get('CommandLine') or not row.get('ExecutablePath'):
                raise RuntimeError('Core process identity is unavailable.')
            count = C.c_int()
            argv = checked(argv_parse(row['CommandLine'], C.byref(count)))
            try:
                args = [argv[n] for n in range(1, count.value)]
            finally:
                free(argv)
            process = checked(open_process(0x1000, False, row['ProcessId']))
            try:
                owned = owner_sid(process) == owner_sid(current_process())
            finally:
                close(process)
            result.append(dict(pid=row['ProcessId'], exe=row['ExecutablePath'], args=args, owned=owned))
        return result

    def ports(self):
        return endpoints(19732) + endpoints(8332)

    def secure_new(self, path):
        path.mkdir()  # Never change the permissions of an existing directory.
        owner = owner_sid(current_process())
        result = subprocess.run([r'C:\Windows\System32\icacls.exe', str(path),
            '/inheritance:r', '/grant:r', '*' + owner + ':(OI)(CI)F',
            '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Could not protect the new session directory.')
        self.private(path)

    def private(self, path):
        private_path(path, owner_sid(current_process()), True)

    def launch(self, args):
        return subprocess.Popen([EXE] + args, creationflags=subprocess.CREATE_NO_WINDOW).pid

    def close_window(self, pid):
        # Core's splash and dialogs have no maximize box. Wait for the actual
        # main window; a listener/cookie can exist long before it is constructed.
        user = C.WinDLL('user32', use_last_error=True)
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        user.EnumWindows.argtypes = [callback_type, W.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        user.GetWindowLongW.argtypes = [W.HWND, C.c_int]
        user.GetWindowLongW.restype = W.LONG
        windows = []
        def visit(hwnd, unused):
            found = W.DWORD()
            user.GetWindowThreadProcessId(hwnd, C.byref(found))
            title = C.create_unicode_buffer(512)
            user.GetWindowTextW(hwnd, title, 512)
            style = user.GetWindowLongW(hwnd, -16)  # GWL_STYLE
            required = 0x00C00000 | 0x00080000 | 0x00010000  # caption/system/maximize
            main_title = title.value == 'Bitcoin Core' or title.value.startswith('Bitcoin Core - ')
            # Mainnet's splash is titled 'Bitcoin Core ' (trailing space).
            if found.value == pid and main_title and style & required == required:
                windows.append(hwnd)
            return True
        checked(user.EnumWindows(callback_type(visit), 0))
        if len(windows) > 1:
            raise RuntimeError('Multiple Core main windows need review; no close request was sent.')
        return windows[0] if windows else None

    def request_close(self, pid, window):
        # Revalidate the handle after the durable intent write. Only definitely
        # unsent requests may be selected again; a successful/uncertain send is
        # never repeated, including after a crash.
        if self.close_window(pid) != window:
            raise CloseNotSent('Core main window changed before close was queued.')
        user = C.WinDLL('user32', use_last_error=True)
        user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        if not user.PostMessageW(window, 0x0010, 0, 0):
            raise CloseNotSent('Core close request could not be queued.')

    def verify(self, pid, cookie):
        verify_endpoint(pid, str(cookie), 19732, DATADIR)

    def ready(self, pid, cookie):
        from miner import NodeRPC, NodeNotReady, RPCFailure
        # This guard uses only read-only methods. Construction repeats the native
        # endpoint checks; no GPU, template search or block submission occurs.
        rpc = NodeRPC(DATADIR, 19732, network='mainnet', cookie=str(cookie),
                      session_pid=pid, opt_in=True, submit=True)
        try:
            rpc.guard()
        except RPCFailure as exc:
            if exc.exit_code == 28:  # Core RPC_IN_WARMUP; do not retry other errors.
                return False
            raise
        except NodeNotReady:
            return False  # Sync/peers/warnings remain guarded by the bounded wait.
        return True


class ManagedCore:
    def __init__(self, root=ROOT, backend=None, base=None, datadir=DATADIR, timeout=120, journal=None):
        self.root, self.backend = Path(root), backend or WindowsCore()
        self.base = Path(base) if base else Path(os.environ['LOCALAPPDATA']) / 'BitcoinPreflight'
        self.datadir, self.timeout = Path(datadir), timeout
        self.path = self.root / 'core-connection.json'
        self.temporary = self.root / 'core-connection.json.tmp'
        self.state = None
        self.created = False
        self.journal = journal

    def pending(self):
        return self.path.exists() or self.temporary.exists()

    def config_hash(self):
        config = self.datadir / 'bitcoin.conf'
        raw = config.read_bytes() if config.exists() else b''
        for line in raw.decode('utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith(('#', ';')) and line.partition('=')[0].strip() not in ('blocksdir', 'walletdir'):
                raise RuntimeError('Core configuration requires review before automatic connection.')
        return hashlib.sha256(raw).hexdigest()

    def journal_clear(self):
        path = self.root / 'mainnet-submission.json'
        if self.journal is None or not self.journal.active or self.journal.path != path:
            raise RuntimeError('Hold the shared submission-journal lock throughout connection and restoration.')
        if Path(str(path) + '.tmp').exists():
            raise RuntimeError('Submission journal/lock needs review; Core access is retained for checking the outcome.')
        if path.exists():
            entry = json.loads(path.read_text())
            if entry.get('status') not in ('ACCEPTED', 'REJECTED', 'CONFIRMED', 'ACCEPTED_SIDECHAIN'):
                raise RuntimeError('Submission outcome needs review. Resolve it on this node before restoring Core; never retry blindly.')
            block_hash = entry.get('block_hash', '')
            if len(block_hash) != 64 or any(c not in '0123456789abcdef' for c in block_hash):
                raise RuntimeError('Submission journal needs review.')

    @property
    def folder(self):
        return self.base / self.state['session']

    @property
    def cookie(self):
        return self.folder / '.cookie'

    def temp_args(self):
        return ['-chain=main', '-datadir=' + DATADIR, '-nosettings', '-disablewallet=1',
            '-server=1', '-rest=0', '-rpcbind=127.0.0.1', '-rpcallowip=127.0.0.1', '-rpcport=19732',
            '-rpccookiefile=' + str(self.cookie), '-rpcwhitelistdefault=1', '-rpcwhitelist=__cookie__:' + METHODS]

    def save(self, phase=None):
        if phase:
            self.state['phase'] = phase
        with self.temporary.open('x', encoding='utf-8') as handle:
            json.dump(self.state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        self.temporary.replace(self.path)

    def load(self):
        if self.temporary.exists():
            raise RuntimeError('Interrupted recovery-record write needs review. No startup or shutdown was attempted.')
        self.state = json.loads(self.path.read_text())
        token = self.state.get('session', '')
        if (len(token) != 32 or any(c not in '0123456789abcdef' for c in token)
                or self.state.get('phase') not in PHASES or self.state.get('version') != 1
                or self.state.get('config_sha256') != self.config_hash()):
            raise RuntimeError('Core recovery record/configuration needs review. No process was changed.')
        if self.folder.resolve().parent != self.base.resolve():
            raise RuntimeError('Session path is outside the private session directory.')

    def row(self):
        rows = self.backend.processes()
        if len(rows) > 1:
            raise RuntimeError('Multiple Core processes need review; no process was changed.')
        if not rows:
            return None
        row = rows[0]
        if not row['owned'] or row['exe'].lower() != EXE.lower():
            raise RuntimeError('Unexpected Core executable or owner.')
        if row['args'] not in (NORMAL_ARGS, self.temp_args()):
            raise RuntimeError('Unexpected Core arguments. No process was changed.')
        return row

    def wait(self, check, stop=None):
        deadline = time.monotonic() + self.timeout
        while True:
            if stop is not None and stop.is_set():
                raise Cancelled('Connection cancelled.')
            result = check()
            if result:
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError('Core did not finish in time. Use File > Exit if needed, then Restore normal Core. No force-kill.')
            time.sleep(.25)

    def no_access(self):
        if self.backend.ports() or any(p.exists() for p in (self.cookie, Path(str(self.cookie) + '.tmp'), self.datadir / '.cookie')):
            raise RuntimeError('An RPC endpoint or cookie remains. Stop for review.')

    def request_close_when_ready(self, row, phase, stop=None):
        original_phase = self.state['phase']
        def request():
            current = self.row()
            if current is None:
                return True  # Process already exited; do not send a message.
            if current != row:
                raise RuntimeError('Core identity changed before shutdown. No close was sent.')
            window = self.backend.close_window(row['pid'])
            if window is None:
                return False
            if stop is not None and stop.is_set():
                raise Cancelled('Connection cancelled before closing Core.')
            self.save(phase)  # Intent precedes the side effect, preserving crash safety.
            try:
                latest = self.row()
            except Exception:
                self.save(original_phase)  # No send has been attempted yet.
                raise
            if latest != row:
                self.save(original_phase)
                if latest is None:
                    return True
                raise RuntimeError('Core identity changed before close dispatch. No close was sent.')
            try:
                self.backend.request_close(row['pid'], window)
            except CloseNotSent:
                self.save(original_phase)  # Proven no dispatch; recovery may wait/select again.
                return False
            return True
        self.wait(request, stop)

    def prepare(self, approved=False, stop=None, status=lambda value: None):
        if approved is not True:
            raise ValueError('Fresh approval for temporary access and clean Core restarts is required.')
        if self.pending():
            raise RuntimeError('Restore/review the previous connection before creating another.')
        self.journal_clear()
        self.state = dict(version=1, session=uuid.uuid4().hex, config_sha256=self.config_hash(), phase='prepared',
                          created_utc=datetime.now(timezone.utc).isoformat())
        # Exact normal identity (or closed Core), no RPC, no default credential.
        row = self.row()
        if row and row['args'] != NORMAL_ARGS:
            raise RuntimeError('An unmanaged temporary connection already exists. Use Detect Core connection.')
        self.no_access()
        # Reserve the record exclusively before any mutation of Core or ACLs.
        with self.path.open('x', encoding='utf-8') as handle:
            json.dump(self.state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        self.created = True
        if not self.base.exists():
            self.backend.secure_new(self.base)
        self.backend.private(self.base)
        self.backend.secure_new(self.folder)
        if stop is not None and stop.is_set():
            raise Cancelled('Connection cancelled.')
        if row:
            status('Closing normal Core safely')
            self.state['closing_normal_pid'] = row['pid']
            self.request_close_when_ready(row, 'normal_close_requested', stop)
            self.wait(lambda: self.row() is None, stop)
        self.no_access()
        if self.state['config_sha256'] != self.config_hash():
            raise RuntimeError('Core configuration changed.')
        if stop is not None and stop.is_set():
            raise Cancelled('Connection cancelled.')
        status('Opening temporary Core connection')
        if self.row() is not None:
            raise RuntimeError('Core appeared before temporary startup. No second instance was launched.')
        self.save('temporary_start_requested')
        self.backend.launch(self.temp_args())
        def ready():
            current = self.row()
            if not current:
                return False
            if current['args'] != self.temp_args():
                raise RuntimeError('Temporary Core identity changed.')
            if not self.cookie.exists() or not self.backend.ports():
                return False
            if self.state['config_sha256'] != self.config_hash():
                raise RuntimeError('Core configuration changed while waiting for readiness.')
            if not self.backend.ready(current['pid'], self.cookie):
                status('Waiting for Core readiness')
                return False
            if stop is not None and stop.is_set():
                raise Cancelled('Connection cancelled during Core readiness checks.')
            return dict(cookie=str(self.cookie), core_pid=current['pid'])
        session = self.wait(ready, stop)
        self.save('temporary_running')
        return session

    def restore(self, approved=False, status=lambda value: None):
        if approved is not True:
            raise ValueError('Approve restoration of normal Core first.')
        if not self.pending():
            return None
        self.load()
        self.journal_clear()  # Retain the intended node for uncertain-result inspection.
        row = self.row()
        phase = self.state['phase']
        if row and row['args'] == self.temp_args():
            status('Closing temporary Core safely')
            if phase != 'temporary_close_requested':
                self.request_close_when_ready(row, 'temporary_close_requested')
            self.wait(lambda: self.row() is None)
        elif row and phase == 'normal_close_requested' and row['pid'] == self.state.get('closing_normal_pid'):
            # A previous close request can still be completing. Do not call it
            # restored or send the request twice just because it is still alive.
            status('Waiting for the requested Core shutdown')
            self.wait(lambda: self.row() is None)
        # An already restored, exact normal process (for example after manual
        # recovery/reboot) can be verified in place without closing or relaunching it.
        self.no_access()
        if not self.row():
            if phase == 'normal_start_requested':
                raise RuntimeError('Normal startup was already requested. Start normal Core manually, then Restore; no duplicate launch was attempted.')
            status('Restoring normal Core')
            self.save('normal_start_requested')
            self.backend.launch(NORMAL_ARGS)
        normal = self.wait(lambda: self.row())
        if normal['args'] != NORMAL_ARGS:
            raise RuntimeError('Normal Core restoration is not verified.')
        self.no_access()
        if self.config_hash() != self.state['config_sha256']:
            raise RuntimeError('Core configuration changed during restoration.')
        if self.folder.exists():
            self.backend.private(self.folder)
            self.folder.rmdir()  # Exact new UUID directory; nonrecursive, empty only.
        self.save('restored')
        report = dict(recorded_utc=datetime.now(timezone.utc).isoformat(),
                      normal_pid=normal['pid'], normal_arguments_verified=True,
                      configuration_unchanged=True, temporary_access_removed=True,
                      wallet_loading_and_peer_sync_verified=False)
        target = self.root / 'core-last-restoration.json'
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        self.path.unlink()
        # The empty protected base is reusable; no credentials or auto-start remain.
        return report
