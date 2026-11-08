from test_vectors import ADDRESS as TEST_PAYOUT
"""Long-session engine/worker/UI tests with virtual time and fake GPU/Core."""
import json
from pathlib import Path
import tempfile
import threading
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from gui_runner import RunConfig, Runner
from test_vectors import ADDRESS
from journal import SubmissionJournal
from managed_core import ManagedCore
from miner import Miner, execute, InvalidCandidate
from miner_gui import App, MINING, LONG_SIMULATION, MAINNET
from run_limits import validate_limits
from test_gui_window import FakeTray
from test_live import MockLive
from test_managed_core import FakeCore


class Clock:
    def __init__(self):self.value=0.0
    def now(self):return self.value
    def advance(self,seconds):self.value+=seconds

class Stop:
    def __init__(self,clock,stop_on_wait=False):self.clock,self.stopped,self.stop_on_wait=clock,False,stop_on_wait
    def is_set(self):return self.stopped
    def set(self):self.stopped=True
    def wait(self,seconds=None):
        if self.stop_on_wait:self.set()
        elif not self.stopped:self.clock.advance(seconds or 0)
        return self.stopped

class GPU:
    def __init__(self,clock,stop,seconds=5,stop_at=None):
        self.clock,self.stop,self.seconds,self.stop_at=clock,stop,seconds,stop_at
        self.batches,self.closed=0,False
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def batch(self,header,nonce,count,target):
        self.batches+=1;self.clock.advance(self.seconds)
        if self.stop_at and self.batches>=self.stop_at:self.stop.set()
        return [bytes(32)]*count,[0]*count
    def search_batch(self,header,nonce,count,target):
        self.batches+=1;self.clock.advance(self.seconds)
        if self.stop_at and self.batches>=self.stop_at:self.stop.set()
        return None


class SessionEngineTests(unittest.TestCase):
    def test_test_caps_are_preserved_and_session_limits_are_explicit(self):
        for seconds,hashes in [(61,1),(10,10_000_001),(None,None)]:
            with self.assertRaises(ValueError):validate_limits(seconds,hashes)
        for seconds in (3600,86400,604800,None):
            validate_limits(seconds,None,profile='session',can_stop=True)
        for options in [dict(seconds=float('nan')),dict(seconds=604801),dict(hashes=32768),dict(can_stop=False)]:
            config=dict(seconds=3600,hashes=None,profile='session',can_stop=True);config.update(options)
            with self.assertRaises(ValueError):validate_limits(**config)
    def test_four_hours_exceeds_old_hash_limit_in_one_context_and_rebuilds_fresh_work(self):
        clock=Clock();stop=Stop(clock);gpu=GPU(clock,stop);rpc=MockLive();progress=[]
        args=SimpleNamespace(network='mainnet',datadir='unused',cookie='unused',session_pid=123,
            mainnet_opt_in=True,submit_blocks=True,address=ADDRESS,max_seconds=14400,max_hashes=None,max_blocks=1,
            run_profile='session',pause_seconds=0)
        with tempfile.TemporaryDirectory() as directory, \
             patch('miner.time.monotonic',side_effect=clock.now),patch('miner.time.sleep',side_effect=clock.advance), \
             patch('gpu_identity.verify_identity'),patch('miner.NodeRPC',return_value=rpc),patch('miner.GPUHasher',return_value=gpu) as constructor:
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                result=execute(args,19732,journal,stop,on_progress=progress.append)
        self.assertEqual(result['elapsed_seconds'],14400)
        self.assertGreater(result['hashes'],10_000_000)
        self.assertEqual(result['bounds']['hashes'],None)
        self.assertGreater(result['jobs'],1000)
        constructor.assert_called_once()
        self.assertTrue(gpu.closed)
        self.assertIsNone(rpc.deadline)
        self.assertEqual(progress[-1]['remaining_seconds'],0)
        self.assertEqual(rpc.submissions,0)
    def test_until_stop_searches_without_a_deadline_then_stops_after_current_batch(self):
        clock=Clock();stop=Stop(clock);gpu=GPU(clock,stop,seconds=.1,stop_at=100);rpc=MockLive()
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                result=Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(None,None,
                    run_profile='session',pause_seconds=0)
        self.assertEqual((result['status'],gpu.batches,result['hashes']),('STOPPED',100,409600))
        self.assertIsNone(result['bounds']['seconds'])
        self.assertIsNone(rpc.deadline)
        self.assertEqual(rpc.submissions,0)
    def test_stop_interrupts_resource_pacing_without_starting_another_batch(self):
        clock=Clock();stop=Stop(clock,True);gpu=GPU(clock,stop,seconds=.1);rpc=MockLive()
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                result=Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(None,None,
                    run_profile='session',pause_seconds=5)
        self.assertEqual((result['status'],gpu.batches),('STOPPED',1))
        self.assertLess(result['elapsed_seconds'],1)
    def test_pacing_is_clamped_to_selected_duration(self):
        clock=Clock();stop=Stop(clock);gpu=GPU(clock,stop,seconds=.2);rpc=MockLive()
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                result=Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(1,None,
                    run_profile='session',pause_seconds=5)
        self.assertEqual((result['elapsed_seconds'],gpu.batches),(1,1))
    def test_long_simulation_start_stop_has_no_gpu_or_rpc(self):
        runner=Runner(result_path=None)
        with patch('miner.NodeRPC',side_effect=AssertionError('No RPC')),patch('miner.GPUHasher',side_effect=AssertionError('No GPU')):
            runner.start(RunConfig(mode='simulation',profile='session',seconds=3600,hashes=None))
            runner.stop();runner.thread.join(2)
        self.assertFalse(runner.busy)
        record=[v for k,v in runner.events.queue if k=='finished'][-1]
        self.assertEqual(record['result']['status'],'STOPPED')
        self.assertIsNone(record['result']['hashes'])

    def test_new_parent_tip_replaces_work_and_counts_hashes_on_each_tip(self):
        clock=Clock();stop=Stop(clock);rpc=MockLive();gpu=GPU(clock,stop,seconds=.1,stop_at=6)
        original=gpu.batch;events=[];old_tip=rpc.tip
        def batch(*args):
            value=original(*args)
            if gpu.batches==1:
                rpc.tip='ab'*32;rpc.template['previousblockhash']=rpc.tip
                rpc.info['blocks']+=1;rpc.info['headers']+=1;rpc.template['height']+=1
            return value
        gpu.batch=batch
        with tempfile.TemporaryDirectory() as directory,patch('miner.time.monotonic',side_effect=clock.now):
            with SubmissionJournal(Path(directory)/'journal.json') as journal:
                result=Miner(rpc,ADDRESS,gpu,journal=journal,stop_event=stop).run(None,None,
                    run_profile='session',on_work=events.append)
        self.assertEqual(result['tip_changes'],1)
        self.assertEqual([e['previous'] for e in events],[old_tip,'ab'*32])
        self.assertEqual([e['hashes'] for e in result['work_history']],[4096,5*4096])
        self.assertEqual(sum(e['hashes'] for e in result['work_history']),result['hashes'])


class SessionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.data.mkdir()
        (self.data/'bitcoin.conf').write_text('blocksdir=E:\\ExampleBlocks\n')
        self.backend=FakeCore();self.runner=Runner(result_path=None)
        self.config=RunConfig(mode='mainnet',address=TEST_PAYOUT,managed=True,profile='session',seconds=14400,hashes=None,pause_seconds=.05)
        self.monitor=Mock()
        patches=[patch('gui_runner.ROOT',self.root),patch('gpu_monitor.GPUMonitor',return_value=self.monitor),
            patch('managed_core.ManagedCore',side_effect=lambda **kw:ManagedCore(
                self.root,self.backend,self.root/'sessions',self.data,timeout=0,**kw))]
        for item in patches:item.start();self.addCleanup(item.stop)
    def work(self,body):
        with patch('miner.execute',side_effect=body):
            self.runner.start(self.config,approved=True,connection_approved=True)
            self.runner.thread.join(3)
        self.assertFalse(self.runner.busy)
        return [v for k,v in self.runner.events.queue if k=='finished'][-1]
    def test_long_scope_reaches_engine_once_and_restores_once_after_stop(self):
        def execute(args,port,journal,stop,*callbacks,**kwargs):
            self.assertEqual((args.run_profile,args.max_seconds,args.max_hashes,args.pause_seconds),('session',14400,None,.05))
            self.assertTrue(journal.active);stop.set()
            return dict(status='STOPPED',hashes=1_000_000,blocks=[])
        record=self.work(execute)
        self.assertTrue(record['restoration']['temporary_access_removed'])
        self.assertEqual(len(self.backend.launches),2)
        self.monitor.close.assert_called_once()
    def test_telemetry_failure_cannot_skip_restoration(self):
        self.monitor.close.side_effect=RuntimeError('optional telemetry failure')
        record=self.work(lambda *args,**kwargs:dict(status='COMPLETE',hashes=4096,blocks=[]))
        self.assertIsNone(record['restoration_error'])
        self.assertTrue(record['restoration']['temporary_access_removed'])
    def test_thermal_reason_survives_cooperative_stop_and_restoration(self):
        self.monitor.reason='GPU core temperature 81°C reached the 80°C stop limit.'
        def stopped(*args,**kwargs):
            self.runner.stop_event.set();return dict(status='STOPPED',hashes=4096,blocks=[])
        record=self.work(stopped)
        self.assertIsNone(record['error'],record)
        self.assertIn('81',record['stop_reason'])
        self.assertTrue(record['restoration']['temporary_access_removed'])
        self.assertIsNone(record['error'])
        self.assertEqual(record['gpu_safety']['core_limit_c'],80)
    def test_monitor_start_failure_prevents_execution_and_still_restores_core(self):
        self.monitor.start.side_effect=RuntimeError('synthetic monitor startup failure')
        called=Mock()
        record=self.work(called)
        called.assert_not_called()
        self.assertIn('monitor startup',record['error'])
        self.assertTrue(record['restoration']['temporary_access_removed'])
    def test_long_run_error_still_restores_core(self):
        def fail(*args,**kwargs):raise RuntimeError('synthetic long-run error')
        record=self.work(fail)
        self.assertIn('synthetic long-run error',record['error'])
        self.assertTrue(record['restoration']['temporary_access_removed'])
    def test_long_uncertain_submission_retains_node_and_prevents_retry(self):
        def uncertain(args,port,journal,*unused,**kwargs):
            journal.begin('12'*32,1,'34'*32,network='mainnet');journal.save('UNCERTAIN')
            return dict(hashes=4096,blocks=[],submission_uncertain='Review result')
        record=self.work(uncertain)
        self.assertIn('outcome',record['restoration_error'])
        self.assertEqual(self.backend.closes,[])
        self.assertEqual(len(self.backend.launches),1)
    def test_long_mainnet_cannot_use_unmanaged_session(self):
        with self.assertRaisesRegex(ValueError,'automatic'):
            RunConfig(mode='mainnet',address=TEST_PAYOUT,profile='session',seconds=None,hashes=None,cookie=r'C:\inert',core_pid=1).validate()
    def test_partial_result_survives_worker_failure_and_core_restoration(self):
        def fail(*args,**kwargs):
            error=InvalidCandidate('synthetic proposal failure')
            error.mining_result=dict(status='ERROR',hashes=8192,blocks=[],tip_changes=1)
            raise error
        record=self.work(fail)
        self.assertEqual(record['result']['hashes'],8192)
        self.assertIn('synthetic proposal failure',record['error'])
        self.assertTrue(record['restoration']['temporary_access_removed'])
    def test_receipt_failure_preserves_result_and_cannot_skip_core_restoration(self):
        with patch('win_moment.save_receipts',side_effect=OSError('synthetic receipt write failure')):
            record=self.work(lambda *args,**kwargs:dict(status='COMPLETE',hashes=4096,blocks=[]))
        self.assertIsNone(record['error'])
        self.assertIn('synthetic receipt',record['win_receipt_error'])
        self.assertTrue(record['restoration']['temporary_access_removed'])


class SessionWindowTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw();self.runner=Runner(result_path=None)
        self.app=App(self.root,self.runner,FakeTray,require_acknowledgment=False);self.app.address.set(TEST_PAYOUT);self.app.show=lambda:None
    def tearDown(self):
        self.runner.stop()
        if self.runner.thread:self.runner.thread.join(2)
        self.app.finish_exit()
    def drain(self):
        if self.app.poll_id:self.root.after_cancel(self.app.poll_id)
        self.app.poll()
    def test_default_is_real_mining_idle_with_twenty_minutes_selected(self):
        self.assertEqual(self.app.mode.get(),MINING)
        self.assertIsNone(self.runner.thread)
        config=self.app.chosen_config(managed=True)
        self.assertEqual((config.seconds,config.hashes,config.profile),(1200,None,'session'))
        self.assertEqual(self.app.session.winfo_manager(),'')
    def test_primary_start_uses_one_connection_prompt_and_selected_duration(self):
        self.app.run_for.set('8 hours')
        with patch('miner_gui.messagebox.askyesno',return_value=True) as prompt,patch.object(self.runner,'start') as start:
            self.app.start()
        prompt.assert_called_once()
        self.assertIn('08:00:00',prompt.call_args.args[1]);self.assertIn('entire selected session',prompt.call_args.args[1])
        config=start.call_args.args[0]
        self.assertEqual((config.seconds,config.hashes),(28800,None))
        self.assertTrue(config.managed)
    def test_cancel_session_confirmation_does_not_start(self):
        with patch('miner_gui.messagebox.askyesno',return_value=False):self.app.start()
        self.assertIsNone(self.runner.thread)
    def test_until_stop_and_custom_minutes_are_explicit(self):
        self.app.run_for.set('Until Stop')
        self.assertIsNone(self.app.chosen_config(managed=True).seconds)
        self.app.run_for.set('Custom minutes');self.app.custom_minutes.set('180')
        self.assertEqual(self.app.chosen_config(managed=True).seconds,10800)
        self.app.custom_minutes.set('nan')
        with patch('miner_gui.messagebox.askyesno') as prompt:self.app.start()
        prompt.assert_not_called();self.assertIsNone(self.runner.thread)
    def test_long_simulation_disables_inputs_then_stop_restores_controls(self):
        self.app.mode.set(LONG_SIMULATION);self.app.mode_changed();self.app.start()
        self.assertEqual(str(self.app.duration_box['state']),'disabled')
        self.runner.events.put(('action','stop'));self.drain();self.runner.thread.join(2);self.drain()
        self.assertEqual(self.app.status.get(),'Stopped - simulation')
        self.assertEqual(str(self.app.duration_box['state']),'readonly')
        self.assertIn('Hashes searched: -',self.app.metrics.get())
    def test_live_resource_and_runtime_events_are_displayed_without_fake_hashes(self):
        self.runner.events.put(('progress',dict(hashes=1_000_000,blocks=0,elapsed_seconds=3600,
            remaining_seconds=3600,hashes_per_second=277.78)))
        self.runner.events.put(('gpu',dict(temperature_c=57,board_watts=42.5,utilization_percent=18)))
        self.drain()
        self.assertIn('01:00:00',self.app.runtime.get());self.assertIn('42.5 W board',self.app.gpu_detail.get())
    def test_bounded_test_mode_retains_original_caps(self):
        self.app.mode.set(MAINNET);self.app.mode_changed()
        config=self.app.chosen_config(managed=True)
        self.assertEqual((config.profile,config.seconds,config.hashes),('test',10,32768))


class TelemetryTests(unittest.TestCase):
    def test_query_is_read_only_and_does_not_expose_raw_errors(self):
        from gpu_monitor import read_gpu
        response=SimpleNamespace(returncode=0,stdout='57, 42.5, 18\n')
        with patch('gpu_monitor.Path.is_file',return_value=True),patch('gpu_monitor.subprocess.run',return_value=response) as run:
            self.assertEqual(read_gpu()['board_watts'],42.5)
        self.assertIn('--query-gpu=',run.call_args.args[0][2]);self.assertEqual(run.call_args.kwargs['timeout'],2)
        response.returncode=1
        with patch('gpu_monitor.Path.is_file',return_value=True),patch('gpu_monitor.subprocess.run',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'unavailable'):read_gpu()


if __name__=='__main__':unittest.main()
