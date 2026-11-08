from test_vectors import ADDRESS as TEST_PAYOUT
"""Real Tk widgets with fake tray and simulated worker; no RPC/GPU access."""
import tkinter as tk
import unittest
from unittest.mock import patch
from gui_runner import Runner
from miner_gui import App, SIMULATION, MAINNET

class FakeTray:
    present = True
    closed = False
    def __init__(self, path, dispatch): self.dispatch = dispatch
    def update(self, status, active): self.status, self.active = status, active
    def close(self): self.closed = True; self.present = False

class WindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.runner = Runner(result_path=None)
        self.discovery = patch('core_session.discover_session', side_effect=ValueError('No existing mining connection'))
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        self.app = App(self.root, self.runner, FakeTray, require_acknowledgment=False)
        self.app.address.set(TEST_PAYOUT)
        self.app.mode.set(SIMULATION); self.app.mode_changed()
        self.app.show = lambda: None
        self.root.update_idletasks()
    def tearDown(self):
        self.runner.stop()
        if self.runner.thread: self.runner.thread.join(2)
        try:
            self.app.finish_exit()
        except tk.TclError:
            pass
    def drain(self):
        if self.app.poll_id:
            self.root.after_cancel(self.app.poll_id)
        self.app.poll()
    def test_idle_does_not_start_worker_and_advanced_fields_hidden(self):
        self.assertFalse(self.runner.busy)
        self.assertIsNone(self.runner.thread)
        self.assertEqual(self.app.status.get(), 'Idle')
        self.assertEqual(self.app.session.winfo_manager(), '')
        self.assertEqual(str(self.app.stop_button['state']), 'disabled')
    def test_gpu_safety_stop_reason_remains_visible_after_restoration_without_restart(self):
        reason='GPU core temperature 81°C reached the 80°C stop limit. No automatic restart.'
        self.runner.events.put(('safety_stop',reason));self.drain()
        self.assertIn('GPU safety',self.app.status.get())
        self.runner.events.put(('finished',dict(mode='mainnet',result=dict(hashes=4096,blocks=[]),
            error=None,restoration_error=None,stop_reason=reason,restoration={'temporary_access_removed':True})))
        self.drain()
        self.assertIn(reason,self.app.detail.get());self.assertIn('GPU safety',self.app.status.get())
        self.assertIsNone(self.runner.thread)
    def test_mode_changes_show_and_hide_advanced_fields(self):
        self.app.status.set('Old connection error')
        self.app.mode.set(MAINNET); self.app.mode_changed()
        self.assertEqual(self.app.status.get(), 'Idle')
        self.assertEqual(self.app.session.winfo_manager(), 'pack')
        self.assertEqual(str(self.app.session_entries[0]['state']), 'normal')
        self.app.mode.set(SIMULATION); self.app.mode_changed()
        self.assertEqual(self.app.session.winfo_manager(), '')
    def test_repeated_start_stop_then_restart(self):
        for _ in range(2):
            self.app.seconds.set('2'); self.app.start()
            first = self.runner.thread
            self.app.start()
            self.assertIs(first, self.runner.thread)
            self.assertEqual(str(self.app.start_button['state']), 'disabled')
            self.app.stop(); self.runner.thread.join(2); self.drain()
            self.assertEqual(self.app.status.get(), 'Stopped - simulation')
            self.assertFalse(self.app.awaiting_result)
            self.assertEqual(str(self.app.start_button['state']), 'normal')
            self.assertIn('Hashes searched: -', self.app.metrics.get())
    def test_finished_event_must_be_consumed_before_restart(self):
        self.app.seconds.set('.01'); self.app.start(); self.runner.thread.join(2)
        first = self.runner.thread
        self.app.start()
        self.assertIs(first, self.runner.thread)
        self.drain()
        self.app.start(); self.runner.thread.join(2); self.drain()
        self.assertIsNot(first, self.runner.thread)
    def test_cancel_mainnet_confirmation_never_starts_worker(self):
        self.app.mode.set(MAINNET); self.app.mode_changed()
        self.app.cookie.set(r'C:\temporary\cookie'); self.app.pid.set('123')
        with patch('miner_gui.messagebox.askyesno', return_value=False) as prompt:
            self.app.start()
        prompt.assert_called_once()
        self.assertIsNone(self.runner.thread)
    def test_invalid_limits_and_missing_session_fail_before_worker(self):
        self.app.seconds.set('nan'); self.app.start()
        self.assertEqual(self.app.status.get(), 'Could not start')
        self.assertIsNone(self.runner.thread)
        self.app.seconds.set('10'); self.app.mode.set(MAINNET); self.app.mode_changed()
        self.app.start()
        self.assertTrue(self.app.detecting)
        self.runner.events.put(('session', (None, 'No existing mining connection')))
        with patch('miner_gui.messagebox.askyesno', return_value=False):
            self.drain()
        self.assertEqual(self.app.status.get(), 'Core connection unavailable')
        self.assertIsNone(self.runner.thread)
    def test_close_hides_and_open_restores_without_start(self):
        self.root.deiconify(); self.root.update()
        self.app.hide(); self.root.update()
        self.assertEqual(self.root.state(), 'withdrawn')
        App.show(self.app); self.root.update()
        self.assertEqual(self.root.state(), 'normal')
        self.assertIsNone(self.runner.thread)
    def test_exit_waits_for_worker_and_closes_tray(self):
        self.app.seconds.set('2'); self.app.start(); self.app.exit()
        self.runner.thread.join(2); self.drain()
        self.assertTrue(self.app.tray.closed)
        self.assertFalse(self.runner.busy)
    def test_tray_failed_recovers_window_and_disables_hide(self):
        called=[]; self.app.show=lambda:called.append(True)
        self.app.tray.present=False
        self.runner.events.put(('action','tray_failed')); self.drain()
        self.assertEqual(called,[True])
        self.assertEqual(str(self.app.hide_button['state']), 'disabled')
    def test_missing_tray_close_can_cancel_then_exit(self):
        self.app.tray.present=False
        with patch('miner_gui.messagebox.askyesno',return_value=False): self.app.hide()
        self.assertFalse(self.app.quitting)
        with patch('miner_gui.messagebox.askyesno',return_value=True): self.app.hide()
        self.assertTrue(self.app.tray.closed)

    def test_queued_tray_exit_consumes_pending_finished_event(self):
        self.runner.events.put(('action', 'exit'))
        self.app.seconds.set('.01'); self.app.start(); self.runner.thread.join(2)
        self.assertFalse(self.runner.busy)
        self.assertTrue(self.app.awaiting_result)
        self.drain()
        self.assertTrue(self.app.closed)
        self.assertFalse(self.app.awaiting_result)
        self.assertTrue(self.app.tray.closed)

    def test_queued_tray_stop_keeps_polling_for_finished_event(self):
        self.runner.events.put(('action', 'stop'))
        self.app.seconds.set('.01'); self.app.start(); self.runner.thread.join(2)
        self.drain()
        self.assertFalse(self.app.awaiting_result)
        self.assertIsNotNone(self.app.poll_id)
        self.assertEqual(self.app.status.get(), 'Stopped - simulation')

    def test_queued_tray_exit_closes_when_idle(self):
        self.runner.events.put(('action', 'exit')); self.drain()
        self.assertTrue(self.app.closed)

    def test_session_found_fills_fields_without_mining(self):
        self.app.detecting = True
        self.runner.events.put(('session', (dict(cookie=r'C:\temporary\.cookie', core_pid=123), None)))
        self.drain()
        self.assertEqual(self.app.pid.get(), '123')
        self.assertEqual(self.app.cookie.get(), r'C:\temporary\.cookie')
        self.assertIsNone(self.runner.thread)

    def test_start_after_discovery_still_needs_run_confirmation(self):
        self.app.mode.set(MAINNET); self.app.mode_changed()
        self.app.detecting = True
        self.app.start_after_detection = True
        self.runner.events.put(('session', (dict(cookie=r'C:\temporary\.cookie', core_pid=123), None)))
        with patch('miner_gui.messagebox.askyesno', return_value=False) as prompt:
            self.drain()
        prompt.assert_called_once()
        self.assertIsNone(self.runner.thread)

    def test_detection_does_not_block_exit(self):
        self.app.detecting = True
        self.app.exit()
        self.assertTrue(self.app.closed)

    def test_connect_cancel_never_starts_any_worker(self):
        with patch('miner_gui.messagebox.askyesno', return_value=False) as prompt:
            self.app.connect_run()
        self.assertEqual(prompt.call_args.kwargs['default'], 'no')
        self.assertIn('32768', self.app.hashes.get())
        self.assertIn('localhost-only', prompt.call_args.args[1])
        self.assertIsNone(self.runner.thread)

    def test_connect_validation_precedes_confirmation(self):
        self.app.hashes.set('0')
        with patch('miner_gui.messagebox.askyesno') as prompt:
            self.app.connect_run()
        prompt.assert_not_called()
        self.assertIsNone(self.runner.thread)

    def test_connect_confirmation_passes_exact_single_run_and_connection_approval(self):
        with patch('miner_gui.messagebox.askyesno', return_value=True), patch.object(self.runner, 'start') as start:
            self.app.connect_run()
        config = start.call_args.args[0]
        self.assertTrue(config.managed)
        self.assertEqual((config.hashes, config.seconds), (32768, 10))
        self.assertEqual(start.call_args.kwargs, dict(approved=True, connection_approved=True))
        self.assertTrue(self.app.awaiting_result)

    def test_pending_connection_blocks_both_start_paths(self):
        self.app.mode.set(MAINNET)
        with patch.object(self.app, 'connection_pending', return_value=True), \
             patch('miner_gui.messagebox.askyesno') as prompt:
            self.app.start(); self.app.connect_run()
        prompt.assert_not_called()
        self.assertIsNone(self.runner.thread)

    def test_cancel_recovery_keeps_worker_idle(self):
        with patch('miner_gui.messagebox.askyesno', return_value=False):
            self.app.restore_core()
        self.assertIsNone(self.runner.thread)

    def test_exit_waits_for_restoration_finished_event(self):
        self.app.awaiting_result = True; self.app.recovering = True
        self.app.exit()
        self.assertFalse(self.app.closed)
        self.runner.events.put(('finished', dict(mode='recovery', result={'normal_pid': 123},
            error=None, restoration={'normal_pid': 123}, restoration_error=None)))
        self.drain()
        self.assertTrue(self.app.closed)

    def test_restoration_failure_cancels_queued_exit_and_keeps_issue_visible(self):
        self.app.awaiting_result = True; self.app.quitting = True
        self.runner.events.put(('finished', dict(mode='mainnet', result={'hashes': 4096, 'blocks': []},
            error=None, restoration=None, restoration_error='Uncertain submission outcome needs review')))
        self.drain()
        self.assertFalse(self.app.closed)
        self.assertFalse(self.app.quitting)
        self.assertIn('Core needs attention', self.app.status.get())
        self.assertIn('No automatic retry', self.app.detail.get())
        self.assertIn('Accepted blocks: 0', self.app.metrics.get())

    def test_restored_result_clears_stale_session_fields(self):
        self.app.cookie.set('old'); self.app.pid.set('123')
        self.runner.events.put(('finished', dict(mode='mainnet', result={'hashes': 32768, 'blocks': []},
            error=None, restoration={'normal_pid': 456}, restoration_error=None)))
        self.drain()
        self.assertEqual((self.app.cookie.get(), self.app.pid.get()), ('', ''))
        self.assertIn('temporary access removed', self.app.detail.get())

if __name__=='__main__': unittest.main()
