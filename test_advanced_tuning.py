from test_vectors import ADDRESS as TEST_PAYOUT
"""Hidden real Tk and in-memory device/Core tests. Never invokes a real GPU."""
from dataclasses import replace
import ctypes as C
import json
from pathlib import Path
from queue import Queue
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from advanced_settings import TuningSettings, load_settings, save_settings, THREAD_COUNTS
from gui_runner import RunConfig, Runner
from test_vectors import ADDRESS
from miner_gui import App, MAINNET
from test_gui_window import FakeTray
from bitcoin import MAX_TARGET, sha256d, with_nonce
from gpu_hasher import GPUHasher
from test_live import MockLive
from journal import SubmissionJournal
from miner import execute

class SettingsTests(unittest.TestCase):
    def test_missing_settings_loads_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            self.assertEqual(load_settings(path),(TuningSettings(),''));self.assertFalse(path.exists())

    def test_round_trip_and_unknown_or_invalid_data_has_visible_default_notice(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            value=replace(TuningSettings(),pacing='Custom pause',custom_pause_ms=123,batch_size=65536,threads_per_block=512,temperature_limit_c=75)
            save_settings(path,value)
            self.assertEqual(load_settings(path),(value,''))
            for raw in ('{','[]','{"version":9,"settings":{}}','{"version":1,"settings":{"extra":1}}',
                        '{"version":1,"settings":{"temperature_limit_c":99}}'):
                path.write_text(raw);actual,notice=load_settings(path)
                self.assertEqual(actual,TuningSettings());self.assertTrue(notice);self.assertEqual(path.read_text(),raw)

    def test_validation_rejects_unsafe_types_nonfinite_and_bounds(self):
        fields={'pacing':['bad',None], 'custom_pause_ms':[-1,5001,float('nan'),float('inf'),True,'5'],
                'batch_size':[0,4097,262145,True,65536.0], 'threads_per_block':[0,32,129,1024,True],
                'temperature_limit_c':[59,91,float('nan'),float('inf'),True]}
        for field,values in fields.items():
            for value in values:
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):replace(TuningSettings(),**{field:value}).validate()
        for value in (60,80,90):replace(TuningSettings(),temperature_limit_c=value).validate()
        for value in (0,5000):replace(TuningSettings(),pacing='Custom pause',custom_pause_ms=value).validate()

    def test_failed_atomic_save_keeps_previous_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';save_settings(path,TuningSettings());before=path.read_bytes()
            with patch('advanced_settings.Path.replace',side_effect=OSError('write failure')),self.assertRaises(OSError):
                save_settings(path,replace(TuningSettings(),threads_per_block=256))
            self.assertEqual(path.read_bytes(),before)

    def test_run_config_rejects_invalid_launch_before_worker(self):
        for args in (dict(threads_per_block=1024),dict(batch_size=262145)):
            runner=Runner(None)
            with self.assertRaises(ValueError):runner.start(RunConfig(**args))
            self.assertIsNone(runner.thread)

class WindowTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.path=Path(self.folder.name)/'settings.json'
        self.root=tk.Tk();self.root.withdraw();self.runner=Runner(None)
        self.app=App(self.root,self.runner,FakeTray,settings_path=self.path,require_acknowledgment=False)
        self.app.address.set(TEST_PAYOUT)
        self.app.show=lambda:None

    def tearDown(self):
        self.runner.busy=False;self.app.awaiting_result=False
        self.app.finish_exit()

    def open(self):
        self.app.open_tuning();return self.app.advanced_dialog

    def drain(self):
        if self.app.poll_id:self.root.after_cancel(self.app.poll_id)
        self.app.poll()

    def test_cancel_and_reset_cancel_never_change_saved_values(self):
        value=replace(TuningSettings(),threads_per_block=256);self.app.save_tuning(value)
        before=self.path.read_bytes();dialog=self.open()
        dialog.variables['threads_per_block'].set('512');dialog.close()
        self.assertEqual(self.app.tuning,value);self.assertEqual(self.path.read_bytes(),before)
        dialog=self.open();self.assertEqual(dialog.variables['threads_per_block'].get(),'256')
        dialog.reset();dialog.close();self.assertEqual(self.path.read_bytes(),before)

    def test_save_reopen_and_reset_save_propagate_to_config(self):
        dialog=self.open()
        for name,value in dict(pacing='Custom pause',custom_pause_ms='123',batch_size='65536',threads_per_block='512',temperature_limit_c='75').items():
            dialog.variables[name].set(value)
        dialog.save();self.assertIsNone(self.app.advanced_dialog)
        config=self.app.chosen_config(managed=True)
        self.assertEqual((config.pause_seconds,config.batch_size,config.threads_per_block,config.temperature_limit_c),(.123,65536,512,75))
        self.assertIn('65,536-hash',self.app.scope_text(config))
        self.assertIn('512 threads',self.app.scope_text(config))
        self.assertEqual(load_settings(self.path)[0],self.app.tuning)
        dialog=self.open();self.assertEqual(dialog.variables['custom_pause_ms'].get(),'123.0')
        dialog.reset();dialog.save();self.assertEqual(load_settings(self.path)[0],TuningSettings())

    def test_invalid_draft_cannot_save_or_benchmark(self):
        for field,value in [('custom_pause_ms','nan'),('temperature_limit_c','91'),('batch_size','999999'),('threads_per_block','129')]:
            dialog=self.open();dialog.variables[field].set(value)
            with patch.object(self.runner,'start_benchmark') as start:
                dialog.save();dialog.benchmark();start.assert_not_called()
            self.assertTrue(dialog.message.get());self.assertFalse(self.path.exists());self.assertIsNotNone(self.app.advanced_dialog)
            dialog.close()

    def test_main_start_and_tray_start_blocked_while_editor_open(self):
        self.open();self.app.refresh_controls()
        self.assertEqual(str(self.app.start_button['state']),'disabled')
        with patch.object(self.runner,'start') as start,patch('miner_gui.messagebox.askyesno') as prompt:
            self.app.start();self.app.connect_run();self.app.restore_core();self.app.detect_core()
            start.assert_not_called();prompt.assert_not_called()

    def test_active_and_pending_finished_lockout_preserve_stop(self):
        self.runner.busy=True;self.app.refresh_controls();self.app.open_tuning()
        self.assertIsNone(self.app.advanced_dialog)
        self.assertEqual(str(self.app.advanced_button['state']),'disabled')
        self.assertEqual(str(self.app.stop_button['state']),'normal')
        with self.assertRaises(RuntimeError):self.app.save_tuning(TuningSettings())
        self.runner.busy=False;self.app.awaiting_result=True;self.app.open_tuning()
        self.assertIsNone(self.app.advanced_dialog)

    def test_benchmark_start_cancel_finish_is_separate_from_mining_metrics(self):
        dialog=self.open();old_metrics=self.app.metrics.get()
        with patch.object(self.runner,'start_benchmark',side_effect=lambda _:setattr(self.runner,'busy',True)):
            dialog.benchmark()
        self.assertTrue(self.app.awaiting_result);self.assertEqual(str(dialog.save_button['state']),'disabled')
        self.assertEqual(str(dialog.stop_button['state']),'normal')
        dialog.close();self.assertTrue(self.runner.stop_event.is_set());self.assertIsNotNone(self.app.advanced_dialog)
        self.runner.busy=False
        value=dict(mode='benchmark',result=None,error=None,restoration_error=None,
                   benchmark=dict(status='STOPPED',rows=[dict(batch_size=65536,threads_per_block=128,mh_s=1.23,complete=False)]))
        self.runner.events.put(('finished',value));self.drain()
        self.assertEqual(self.app.metrics.get(),old_metrics);self.assertIn('1.23 MH/s',dialog.benchmark_text.get())
        self.assertFalse(self.path.exists());self.assertFalse(self.app.awaiting_result)
        dialog.close();self.assertIsNone(self.app.advanced_dialog)

    def test_benchmark_error_is_displayed_without_core_restoration_message(self):
        dialog=self.open();self.app.awaiting_result=True
        self.runner.events.put(('finished',dict(mode='benchmark',result=None,error='synthetic device failure',restoration_error=None)))
        self.drain();self.assertIn('device failure',self.app.detail.get());self.assertIn('device failure',dialog.benchmark_text.get())
        self.assertNotIn('Core',self.app.detail.get())

class PropagationTests(unittest.TestCase):
    def test_execute_uses_custom_batch_threads_and_retains_cpu_proof(self):
        from types import SimpleNamespace
        from test_mining_sessions import Clock, Stop
        from test_performance import SearchGPU
        class InertRPC(MockLive):
            def __enter__(self):return self
            def __exit__(self,*args):pass
        clock=Clock();stop=Stop(clock);rpc=InertRPC()
        gpu=SearchGPU(clock,stop,stop_at=2)
        factory=Mock();factory.return_value.__enter__=Mock(return_value=gpu);factory.return_value.__exit__=Mock(return_value=False)
        args=SimpleNamespace(network='mainnet',datadir=r'C:\inert',cookie='',session_pid=1,mainnet_opt_in=True,
                             submit_blocks=True,address=ADDRESS,max_seconds=10,max_hashes=None,max_blocks=1,
                             run_profile='session',pause_seconds=0,batch_size=65536,threads_per_block=512)
        with tempfile.TemporaryDirectory() as folder, patch('gpu_identity.verify_identity'),patch('miner.time.monotonic',side_effect=clock.now),patch('miner.NodeRPC',return_value=rpc),patch('miner.GPUHasher',factory):
            with SubmissionJournal(Path(folder)/'journal.json') as journal:result=execute(args,19732,journal,stop)
        factory.assert_called_once_with(threads_per_block=512)
        self.assertEqual([row[2] for row in gpu.ranges],[65536,65536]);self.assertEqual(result['bounds']['batch_size'],65536)
        self.assertEqual(rpc.submissions,0)

    def test_cuda_grid_rounds_up_for_every_supported_shape_without_device_access(self):
        from unittest.mock import ANY
        for threads in THREAD_COUNTS:
            gpu=object.__new__(GPUHasher);gpu.closed=False;gpu.threads_per_block=threads;gpu.max_threads_per_block=512;gpu.buffers=[C.c_uint64(i) for i in range(8)]
            gpu.cached_header=bytes(80);gpu.cached_target=0;gpu.use_midstate=False;gpu.launches=gpu.hashes=0
            gpu.function=1;gpu.flag_buffer=C.create_string_buffer(600);gpu.launch=Mock(return_value=0);gpu.sync=lambda:0;gpu.dtoh=lambda *args:0;gpu.check=lambda *args:None
            for count in (1,threads-1,threads,threads+1,513):
                GPUHasher._execute(gpu,bytes(80),0,count,0)
                args=gpu.launch.call_args.args
                self.assertEqual((args[1],args[4]),((count+threads-1)//threads,threads))

if __name__=='__main__':unittest.main()
