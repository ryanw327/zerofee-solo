"""Lifecycle with a fake Core process adapter; no node, RPC or GPU starts."""
import json
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from journal import SubmissionJournal
from managed_core import ManagedCore, Cancelled, CloseNotSent, EXE, NORMAL_ARGS


class FakeCore:
    def __init__(self):
        self.rows, self.launches, self.closes = [], [], []
        self.pid = 100
        self.cookie = None
        self.extra_port = False
        self.minimize = False
        self.launch_error = None
    def processes(self):
        return self.rows.copy()
    def ports(self):
        return [('127.0.0.1', self.pid, 2)] if self.extra_port or self.cookie else []
    def secure_new(self, path):
        path.mkdir()
    def private(self, path):
        if path.is_symlink():
            raise ValueError('Reparse point')
    def verify(self, pid, cookie):
        assert pid == self.pid and cookie == self.cookie
    def ready(self, pid, cookie):
        self.verify(pid, cookie)
        return True
    def close_window(self, pid):
        return pid * 10
    def launch(self, args):
        self.launches.append(args)
        self.pid += 1
        self.rows = [dict(pid=self.pid, exe=EXE, args=args, owned=True)]
        for arg in args:
            if arg.startswith('-rpccookiefile='):
                self.cookie = Path(arg.partition('=')[2])
                self.cookie.write_text('inert test marker; not a credential')
        if self.launch_error:
            raise self.launch_error
        return self.pid
    def request_close(self, pid, window=None):
        self.closes.append(pid)
        if not self.minimize:
            self.rows = []
            if self.cookie:
                self.cookie.unlink()
                self.cookie = None


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        (self.data / 'bitcoin.conf').write_text('blocksdir=E:\\ExampleBlocks\nwalletdir=E:\\ExampleBlocks\n')
        self.backend = FakeCore()
        self.journal = SubmissionJournal(self.root / 'mainnet-submission.json')
        self.journal.__enter__()
        self.addCleanup(self.release)
        self.manager = self.make_manager()
    def release(self):
        if self.journal.active:
            self.journal.__exit__(None, None, None)
    def make_manager(self):
        return ManagedCore(self.root, self.backend, self.root / 'sessions', self.data, timeout=0,
                           journal=self.journal)
    def prepare(self):
        return self.manager.prepare(approved=True)
    def normal(self):
        self.backend.rows = [dict(pid=99, exe=EXE, args=NORMAL_ARGS, owned=True)]
    @contextmanager
    def timed_wait(self):
        clock = [0.0]
        self.manager.timeout = 1
        with patch('managed_core.time.monotonic', side_effect=lambda: clock[0]), \
             patch('managed_core.time.sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            yield
    def test_fresh_explicit_approval_required_before_every_mutation(self):
        for approval in (False, None, 1, 'yes'):
            with self.assertRaises(ValueError):
                self.manager.prepare(approved=approval)
        self.assertFalse(self.manager.pending())
        self.assertEqual(self.backend.launches, [])
        self.assertEqual(self.backend.closes, [])
    def test_closed_core_complete_round_trip_and_no_credentials_in_records(self):
        session = self.prepare()
        self.assertTrue(self.manager.cookie.exists())
        self.assertEqual(session['core_pid'], self.backend.pid)
        with self.assertRaises(ValueError): self.manager.restore()
        report = self.manager.restore(approved=True)
        self.assertTrue(report['temporary_access_removed'])
        self.assertEqual(self.backend.launches[-1], NORMAL_ARGS)
        self.assertFalse(self.manager.pending())
        self.assertFalse(self.manager.folder.exists())
        self.assertNotIn('inert test marker', (self.root / 'core-last-restoration.json').read_text())
    def test_open_normal_core_is_closed_once_then_restored(self):
        self.normal()
        self.prepare()
        self.assertEqual(self.backend.closes, [99])
        self.manager.restore(approved=True)
        self.assertEqual(self.backend.closes, [99, 101])
        self.assertEqual(len(self.backend.launches), 2)
    def test_foreign_executable_owner_arguments_multiple_processes_and_ports_fail_closed(self):
        cases = [dict(exe='other.exe'), dict(owned=False), dict(args=['-server=1'])]
        for change in cases:
            self.normal()
            self.backend.rows[0].update(change)
            with self.assertRaises(RuntimeError): self.prepare()
            self.assertFalse(self.manager.pending())
        self.normal(); self.backend.rows *= 2
        with self.assertRaises(RuntimeError): self.prepare()
        self.backend.rows = []; self.backend.extra_port = True
        with self.assertRaises(RuntimeError): self.prepare()
        self.assertEqual(self.backend.closes, [])
        self.assertEqual(self.backend.launches, [])
    def test_config_with_security_setting_requires_review(self):
        (self.data / 'bitcoin.conf').write_text('server=1\n')
        with self.assertRaisesRegex(RuntimeError, 'configuration'): self.prepare()
        self.assertFalse(self.manager.pending())
    def test_shared_journal_lock_is_required(self):
        self.manager.journal = None
        with self.assertRaisesRegex(RuntimeError, 'lock'): self.prepare()
        self.assertFalse(self.manager.pending())
    def test_pending_session_cannot_be_reused_to_start_second_run(self):
        self.prepare()
        with self.assertRaisesRegex(RuntimeError, 'previous'): self.prepare()
        self.assertEqual(len(self.backend.launches), 1)
    def test_stop_before_core_changes_restores_normal_without_mining(self):
        event = threading.Event(); event.set()
        with self.assertRaises(Cancelled): self.manager.prepare(approved=True, stop=event)
        self.manager.restore(approved=True)
        self.assertEqual(self.backend.launches, [NORMAL_ARGS])
    def test_uncertain_result_keeps_node_and_record_for_review(self):
        self.prepare()
        self.journal.begin('12' * 32, 1, '34' * 32, network='mainnet')
        with self.assertRaisesRegex(RuntimeError, 'outcome'): self.manager.restore(approved=True)
        self.assertTrue(self.manager.pending())
        self.assertEqual(self.backend.closes, [])
        self.assertEqual(len(self.backend.launches), 1)
    def test_changed_config_after_setup_keeps_pending_recovery(self):
        self.prepare()
        (self.data / 'bitcoin.conf').write_text('blocksdir=other\n')
        with self.assertRaisesRegex(RuntimeError, 'configuration'): self.manager.restore(approved=True)
        self.assertTrue(self.manager.pending())
        self.assertEqual(self.backend.closes, [])
    def test_recovery_adopts_exact_temporary_process_without_repeating_launch(self):
        self.prepare()
        recovered = self.make_manager()
        recovered.restore(approved=True)
        self.assertEqual(len(self.backend.launches), 2)
        self.assertFalse(recovered.pending())
    def test_manual_restoration_is_verified_without_closing_or_launching_again(self):
        self.prepare()
        self.backend.request_close(101)
        self.normal()
        recovered = self.make_manager()
        recovered.restore(approved=True)
        self.assertEqual(self.backend.closes, [101])
        self.assertEqual(len(self.backend.launches), 1)
        self.assertFalse(recovered.pending())
    def test_close_request_is_not_repeated_after_timeout(self):
        self.prepare()
        self.backend.minimize = True
        with self.assertRaises(TimeoutError): self.manager.restore(approved=True)
        with self.assertRaises(TimeoutError): self.make_manager().restore(approved=True)
        self.assertEqual(self.backend.closes, [101])
        self.backend.minimize = False
        self.backend.request_close(101)  # operator File > Exit in this synthetic case
        self.make_manager().restore(approved=True)
        self.assertFalse(self.manager.pending())
    def test_normal_close_timeout_does_not_report_normal_as_restored(self):
        self.normal(); self.backend.minimize = True
        with self.assertRaises(TimeoutError): self.prepare()
        with self.assertRaises(TimeoutError): self.manager.restore(approved=True)
        self.assertTrue(self.manager.pending())
        self.assertEqual(self.backend.closes, [99])
        self.assertEqual(self.backend.launches, [])
    def test_uncertain_temporary_launch_is_inspected_and_restored_without_retry(self):
        self.backend.launch_error = OSError('synthetic loss after process creation')
        with self.assertRaises(OSError): self.prepare()
        self.backend.launch_error = None
        self.make_manager().restore(approved=True)
        self.assertEqual(len(self.backend.launches), 2)
        self.assertEqual(self.backend.launches[-1], NORMAL_ARGS)
    def test_uncertain_normal_launch_is_detected_on_recovery_without_duplicate(self):
        self.prepare()
        self.backend.launch_error = OSError('synthetic loss after process creation')
        with self.assertRaises(OSError): self.manager.restore(approved=True)
        self.backend.launch_error = None
        self.make_manager().restore(approved=True)
        self.assertEqual(len(self.backend.launches), 2)
    def test_missing_normal_after_requested_launch_requires_manual_check(self):
        self.prepare()
        self.backend.request_close(101)
        self.manager.save('normal_start_requested')
        with self.assertRaisesRegex(RuntimeError, 'already requested'): self.make_manager().restore(approved=True)
        self.assertEqual(len(self.backend.launches), 1)
        self.assertTrue(self.manager.pending())
    def test_corrupt_or_traversal_or_interrupted_record_does_not_change_processes(self):
        self.prepare()
        state = dict(self.manager.state)
        for bad in ('../escape', 'x' * 32):
            self.manager.state = dict(state, session=bad)
            self.manager.save()
            with self.assertRaises(RuntimeError): self.make_manager().restore(approved=True)
        self.manager.state = state; self.manager.save()
        self.manager.temporary.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'record write'): self.make_manager().restore(approved=True)
        self.assertEqual(self.backend.closes, [])
        self.assertEqual(len(self.backend.launches), 1)
    def test_nonempty_session_directory_is_not_deleted(self):
        self.prepare()
        (self.manager.folder / 'keep.txt').write_text('evidence')
        with self.assertRaises(OSError): self.manager.restore(approved=True)
        self.assertTrue((self.manager.folder / 'keep.txt').exists())
        self.assertTrue(self.manager.pending())

    def test_listener_and_cookie_do_not_bypass_warmup_wait(self):
        phases = []
        def ready(pid, cookie):
            self.backend.verify(pid, cookie)
            phases.append(self.manager.state['phase'])
            return len(phases) == 3
        self.backend.ready = ready
        with self.timed_wait(): self.prepare()
        self.assertEqual(phases, ['temporary_start_requested'] * 3)
        self.assertEqual(self.manager.state['phase'], 'temporary_running')
        self.assertEqual(len(self.backend.launches), 1)

    def test_permanent_warmup_times_out_and_restores_without_a_run(self):
        self.backend.ready = lambda *args: False
        with self.timed_wait(), self.assertRaises(TimeoutError): self.prepare()
        self.assertEqual(self.manager.state['phase'], 'temporary_start_requested')
        self.manager.restore(approved=True)
        self.assertFalse(self.manager.pending())
        self.assertEqual(self.backend.closes, [101])

    def test_stop_during_warmup_restores_and_never_marks_session_ready(self):
        stop = threading.Event()
        self.backend.ready = lambda *args: stop.set() or False
        with self.timed_wait(), self.assertRaises(Cancelled):
            self.manager.prepare(approved=True, stop=stop)
        self.assertEqual(self.manager.state['phase'], 'temporary_start_requested')
        self.manager.restore(approved=True)
        self.assertEqual(self.backend.closes, [101])
        self.assertFalse(self.manager.pending())

    def test_identity_is_rechecked_between_warmup_polls(self):
        def ready(*args):
            self.backend.rows = [dict(self.backend.rows[0], owned=False)]
            return False
        self.backend.ready = ready
        with self.timed_wait(), self.assertRaisesRegex(RuntimeError, 'owner'): self.prepare()
        self.assertEqual(self.backend.closes, [])
        self.assertEqual(len(self.backend.launches), 1)

    def test_restoration_waits_for_main_window_without_consuming_close_request(self):
        self.prepare()
        observed = []
        def window(pid):
            observed.append(self.manager.state['phase'])
            return pid * 10 if len(observed) == 3 else None
        self.backend.close_window = window
        with self.timed_wait(): self.manager.restore(approved=True)
        self.assertEqual(observed, ['temporary_running'] * 3)
        self.assertEqual(self.backend.closes, [101])
        self.assertEqual(len(self.backend.launches), 2)

    def test_missing_window_timeout_remains_recoverable_without_duplicate_close(self):
        self.prepare()
        self.backend.close_window = lambda pid: None
        with self.timed_wait(), self.assertRaises(TimeoutError): self.manager.restore(approved=True)
        self.assertEqual(self.manager.state['phase'], 'temporary_running')
        self.assertEqual(self.backend.closes, [])
        self.backend.close_window = lambda pid: pid * 10
        self.make_manager().restore(approved=True)
        self.assertEqual(self.backend.closes, [101])
        self.assertFalse(self.manager.pending())

    def test_ambiguous_windows_send_nothing_and_leave_prior_phase(self):
        self.prepare()
        with patch.object(self.backend, 'close_window', side_effect=RuntimeError('Multiple windows')):
            with self.assertRaisesRegex(RuntimeError, 'Multiple'): self.manager.restore(approved=True)
        self.assertEqual(self.manager.state['phase'], 'temporary_running')
        self.assertEqual(self.backend.closes, [])

    def test_exit_during_window_wait_does_not_send_an_extra_close(self):
        self.prepare()
        def window(pid):
            self.backend.rows = []
            self.backend.cookie.unlink(); self.backend.cookie = None
            return None
        self.backend.close_window = window
        with self.timed_wait(): self.manager.restore(approved=True)
        self.assertEqual(self.backend.closes, [])
        self.assertEqual(len(self.backend.launches), 2)

    def test_proven_unsent_close_can_wait_again_but_only_one_message_is_queued(self):
        self.prepare()
        original = self.backend.request_close
        attempts = []
        def close(pid, window):
            attempts.append((pid, window))
            if len(attempts) == 1:
                raise CloseNotSent('Window changed before queueing')
            original(pid, window)
        self.backend.request_close = close
        with self.timed_wait(): self.manager.restore(approved=True)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.backend.closes, [101])

    def test_uncertain_close_is_never_repeated_on_recovery(self):
        self.prepare()
        original = self.backend.request_close
        def close(pid, window):
            original(pid, window)
            raise RuntimeError('Synthetic failure after dispatch')
        self.backend.request_close = close
        with self.assertRaisesRegex(RuntimeError, 'after dispatch'): self.manager.restore(approved=True)
        self.assertEqual(self.manager.state['phase'], 'temporary_close_requested')
        self.backend.request_close = original
        self.make_manager().restore(approved=True)
        self.assertEqual(self.backend.closes, [101])

    def test_cancel_before_normal_window_exists_does_not_close_normal_core(self):
        self.normal()
        stop = threading.Event()
        self.backend.close_window = lambda pid: stop.set() or None
        with self.timed_wait(), self.assertRaises(Cancelled):
            self.manager.prepare(approved=True, stop=stop)
        self.manager.restore(approved=True)
        self.assertEqual(self.backend.closes, [])
        self.assertEqual(self.backend.launches, [])

    def test_identity_change_after_window_selection_sends_nothing(self):
        self.prepare()
        def window(pid):
            self.backend.rows = [dict(self.backend.rows[0], owned=False)]
            return pid * 10
        self.backend.close_window = window
        with self.assertRaisesRegex(RuntimeError, 'owner'): self.manager.restore(approved=True)
        self.assertEqual(self.backend.closes, [])
        self.assertEqual(self.manager.state['phase'], 'temporary_running')


if __name__ == '__main__':
    unittest.main()
