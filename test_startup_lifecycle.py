from app_paths import DEFAULT_DATADIR
"""Actual adapter logic against mocked RPC/native APIs only; no host mutations."""
import ctypes as C
from collections import Counter
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_vectors import ADDRESS
from journal import SubmissionJournal
from managed_core import WindowsCore, CloseNotSent
from miner import NodeRPC, NodeNotReady, RPCFailure, Miner, SubmissionUncertain
from test_live import MockLive, HistoricalGenesis


class ReadinessAdapterTests(unittest.TestCase):
    def probe(self, failure=None):
        rpc = Mock()
        rpc.guard.side_effect = failure
        with patch('miner.NodeRPC', return_value=rpc) as constructor:
            result = WindowsCore().ready(123, Path(r'C:\private\.cookie'))
        constructor.assert_called_once_with(DEFAULT_DATADIR, 19732, network='mainnet',
            cookie=r'C:\private\.cookie', session_pid=123, opt_in=True, submit=True)
        rpc.guard.assert_called_once_with()
        return result
    def test_rpc_warmup_and_sync_are_retryable_readiness_only(self):
        self.assertFalse(self.probe(RPCFailure('sanitized warmup', 28)))
        self.assertFalse(self.probe(NodeNotReady('syncing')))
        self.assertTrue(self.probe())
    def test_authentication_and_other_rpc_failures_are_not_retried(self):
        for code in (1, 401, 32601):
            with self.assertRaises(RPCFailure): self.probe(RPCFailure('sanitized failure', code))
        with self.assertRaisesRegex(ValueError, 'owner'): self.probe(ValueError('Wrong owner'))
    def test_rpc_failure_preserves_only_sanitized_exit_metadata(self):
        rpc = object.__new__(NodeRPC)
        rpc.network, rpc.calls, rpc.base = 'mainnet', Counter(), ['inert-test-cli']
        rpc.timeout, rpc.deadline = 3, None
        failed = SimpleNamespace(returncode=28, stderr='SENSITIVE RAW ERROR', stdout='')
        with patch('miner.subprocess.run', return_value=failed), self.assertRaises(RPCFailure) as raised:
            rpc.call('getblockchaininfo')
        self.assertEqual(raised.exception.exit_code, 28)
        self.assertNotIn('SENSITIVE', str(raised.exception))
        self.assertIsInstance(raised.exception, RuntimeError)
    def test_warmup_during_submit_is_uncertain_and_never_retried(self):
        rpc, job, nonce = MockLive(), HistoricalGenesis(), 2083236893
        rpc.tip = '00' * 32
        rpc.failure = RPCFailure('sanitized warmup', 28)
        with tempfile.TemporaryDirectory() as directory:
            with SubmissionJournal(Path(directory) / 'journal.json') as journal:
                miner = Miner(rpc, ADDRESS, None, journal=journal)
                with self.assertRaises(SubmissionUncertain):
                    miner.submit_solution(job, nonce, job.validate(nonce, True))
                self.assertEqual(journal.entry['status'], 'PENDING')
        self.assertEqual(rpc.submissions, 1)


class NativeCloseAdapterTests(unittest.TestCase):
    REQUIRED_STYLE = 0x00C00000 | 0x00080000 | 0x00010000
    def api(self, windows):
        # The callback and Win32 adapter are real; every native API call is mocked.
        def enum(callback, unused):
            for handle in windows: callback(handle, 0)
            return 1
        def pid(handle, pointer): pointer._obj.value = windows[handle]['pid']; return 1
        def title(handle, buffer, size): buffer.value = windows[handle]['title']; return len(buffer.value)
        return SimpleNamespace(EnumWindows=Mock(side_effect=enum),
            GetWindowThreadProcessId=Mock(side_effect=pid), GetWindowTextW=Mock(side_effect=title),
            GetWindowLongW=Mock(side_effect=lambda handle, index: windows[handle]['style']),
            PostMessageW=Mock(return_value=1))
    def window(self, pid=123, title='Bitcoin Core', style=None):
        return dict(pid=pid, title=title, style=self.REQUIRED_STYLE if style is None else style)
    def test_no_window_and_splash_only_wait_without_dispatch(self):
        for windows in ({}, {10: self.window(style=0x00C80000)},
                        {10: self.window(title='Bitcoin Core ')}):
            api = self.api(windows)
            with patch('managed_core.C.WinDLL', return_value=api):
                self.assertIsNone(WindowsCore().close_window(123))
            api.PostMessageW.assert_not_called()
    def test_splash_dialog_and_foreign_windows_are_ignored_for_main_window(self):
        api = self.api({10: self.window(style=0x00C80000), 11: self.window(style=0x00C80000, title='Dialog'),
            12: self.window(pid=999), 13: self.window(title='Bitcoin Core - wallet')})
        with patch('managed_core.C.WinDLL', return_value=api):
            self.assertEqual(WindowsCore().close_window(123), 13)
    def test_ambiguous_main_windows_fail_closed(self):
        api = self.api({10: self.window(), 11: self.window()})
        with patch('managed_core.C.WinDLL', return_value=api), self.assertRaisesRegex(RuntimeError, 'Multiple'):
            WindowsCore().close_window(123)
        api.PostMessageW.assert_not_called()
    def test_stale_handle_is_known_unsent(self):
        api = self.api({11: self.window()})
        with patch('managed_core.C.WinDLL', return_value=api), self.assertRaises(CloseNotSent):
            WindowsCore().request_close(123, 10)
        api.PostMessageW.assert_not_called()
    def test_matching_window_receives_one_cooperative_close(self):
        api = self.api({10: self.window()})
        with patch('managed_core.C.WinDLL', return_value=api):
            WindowsCore().request_close(123, 10)
        api.PostMessageW.assert_called_once_with(10, 0x0010, 0, 0)
    def test_failed_post_is_known_unsent(self):
        api = self.api({10: self.window()}); api.PostMessageW.return_value = 0
        with patch('managed_core.C.WinDLL', return_value=api), self.assertRaises(CloseNotSent):
            WindowsCore().request_close(123, 10)


if __name__ == '__main__': unittest.main()
