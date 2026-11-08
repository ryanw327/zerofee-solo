from test_vectors import ADDRESS as TEST_PAYOUT
"""Runner integration with fake Core and mocked mining; no RPC/GPU operations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock

from gui_runner import Runner, RunConfig
from journal import SubmissionJournal
from managed_core import ManagedCore
from test_managed_core import FakeCore


class ManagedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'; self.data.mkdir()
        (self.data / 'bitcoin.conf').write_text('blocksdir=E:\\ExampleBlocks\n')
        self.backend = FakeCore()
        self.runner = Runner(result_path=None)
        self.config = RunConfig(mode='mainnet',address=TEST_PAYOUT, managed=True)
        self.patches = [patch('gui_runner.ROOT', self.root),
            patch('gpu_monitor.GPUMonitor',return_value=Mock(reason=None)),
            patch('managed_core.ManagedCore', side_effect=lambda **kw: ManagedCore(
                self.root, self.backend, self.root / 'sessions', self.data, timeout=0, **kw))]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)
    def run_worker(self, execute):
        with patch('miner.execute', side_effect=execute):
            self.runner.start(self.config, approved=True, connection_approved=True)
            self.runner.thread.join(3)
        self.assertFalse(self.runner.busy)
        return [v for k, v in self.runner.events.queue if k == 'finished'][-1]
    def success(self, args, port, journal, stop, status, progress):
        self.assertTrue(journal.active)
        self.assertTrue((self.root / 'mainnet-submission.json.lock').exists())
        self.assertTrue((self.root / 'core-connection.json').exists())
        with self.assertRaises(FileExistsError):
            with SubmissionJournal(self.root / 'mainnet-submission.json'): pass
        return dict(status='COMPLETE', hashes=32768, blocks=[])
    def test_connection_approval_is_separate_from_mining_approval(self):
        with self.assertRaisesRegex(ValueError, 'temporary access'):
            self.runner.start(self.config, approved=True)
        self.assertIsNone(self.runner.thread)
        self.assertEqual(self.backend.launches, [])
    def test_success_restores_core_before_finished_and_releases_shared_lock(self):
        result = self.run_worker(self.success)
        self.assertIsNone(result['error'])
        self.assertTrue(result['restoration']['temporary_access_removed'])
        self.assertIsNone(result['restoration_error'])
        self.assertFalse((self.root / 'mainnet-submission.json.lock').exists())
        self.assertFalse((self.root / 'core-connection.json').exists())
        self.assertEqual(len(self.backend.launches), 2)
    def test_mining_failure_still_restores_and_preserves_error(self):
        def fail(*args): raise RuntimeError('synthetic worker failure')
        result = self.run_worker(fail)
        self.assertIn('synthetic worker failure', result['error'])
        self.assertTrue(result['restoration']['temporary_access_removed'])
    def test_stop_after_connection_skips_mining_and_restores(self):
        self.backend.verify = lambda *args: self.runner.stop_event.set()
        with patch('miner.execute') as execute:
            self.runner.start(self.config, approved=True, connection_approved=True)
            self.runner.thread.join(3)
        execute.assert_not_called()
        result = [v for k, v in self.runner.events.queue if k == 'finished'][-1]
        self.assertTrue(result['restoration']['temporary_access_removed'])
        self.assertEqual(result['result']['hashes'], None)
    def test_uncertain_submission_retains_access_and_blocks_recovery(self):
        def uncertain(args, port, journal, *unused):
            journal.begin('12' * 32, 1, '34' * 32, network='mainnet')
            journal.save('UNCERTAIN')
            return dict(hashes=4096, blocks=[], submission_uncertain='needs review')
        result = self.run_worker(uncertain)
        self.assertIn('outcome', result['restoration_error'])
        self.assertEqual(self.backend.closes, [])
        self.runner.restore_core(approved=True); self.runner.thread.join(3)
        self.assertEqual(self.backend.closes, [])
        self.assertEqual(len(self.backend.launches), 1)
    def test_existing_recovery_record_is_not_automatically_adopted_by_new_run(self):
        (self.root / 'core-connection.json').write_text('{}')
        result = self.run_worker(self.success)
        self.assertIn('previous', result['error'])
        self.assertEqual(self.backend.launches, [])
        self.assertEqual(self.backend.closes, [])
    def test_recovery_itself_requires_action_time_approval(self):
        with self.assertRaises(ValueError): self.runner.restore_core()
        self.assertFalse(self.runner.busy)
        self.assertEqual(self.backend.launches, [])

    def test_execute_is_reached_only_after_warmup_finishes(self):
        polls, clock = [], [0.0]
        def ready(*args):
            polls.append(True)
            return len(polls) == 3
        self.backend.ready = ready
        factory = lambda **kw: ManagedCore(self.root, self.backend,
            self.root / 'sessions', self.data, timeout=1, **kw)
        def execute(*args):
            self.assertEqual(len(polls), 3)
            return self.success(*args)
        with patch('managed_core.ManagedCore', side_effect=factory), \
             patch('managed_core.time.monotonic', side_effect=lambda: clock[0]), \
             patch('managed_core.time.sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            record = self.run_worker(execute)
        self.assertIsNone(record['error'])
        self.assertTrue(record['restoration']['temporary_access_removed'])

    def test_warmup_timeout_waits_for_late_main_window_then_restores_without_execute(self):
        self.backend.ready = lambda *args: False
        windows, clock = [], [0.0]
        def window(pid):
            windows.append(True)
            return pid * 10 if len(windows) == 3 else None
        self.backend.close_window = window
        factory = lambda **kw: ManagedCore(self.root, self.backend,
            self.root / 'sessions', self.data, timeout=1, **kw)
        with patch('managed_core.ManagedCore', side_effect=factory), \
             patch('managed_core.time.monotonic', side_effect=lambda: clock[0]), \
             patch('managed_core.time.sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
             patch('miner.execute') as execute:
            self.runner.start(self.config, approved=True, connection_approved=True)
            self.runner.thread.join(3)
        execute.assert_not_called()
        self.assertFalse(self.runner.busy)
        record = [v for k, v in self.runner.events.queue if k == 'finished'][-1]
        self.assertIn('TimeoutError', record['error'])
        self.assertIsNone(record['restoration_error'])
        self.assertTrue(record['restoration']['temporary_access_removed'])
        self.assertEqual(self.backend.closes, [101])
        self.assertFalse((self.root / 'core-connection.json').exists())


if __name__ == '__main__': unittest.main()
