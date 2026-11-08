"""Portable synthetic-thread checks: no GPU, node, process launch or real telemetry."""
from queue import Queue
import threading,time,unittest
from unittest.mock import patch
from types import SimpleNamespace
from gpu_monitor import GPUMonitor,ThermalPolicy,reading_text

class WatchdogTests(unittest.TestCase):
    def monitor(self, read=lambda:{'temperature_c':50}, events=None):
        clock=SimpleNamespace(value=0)
        m=GPUMonitor(events or Queue(),threading.Event(),ThermalPolicy(sample_seconds=.1),
                     read=read,clock=lambda:clock.value)
        self.addCleanup(m.close)
        return m,clock
    def test_healthy_start_waits_for_valid_reading_and_close_is_clean(self):
        m,c=self.monitor();m.start()
        self.assertTrue(m.ready.is_set());self.assertFalse(m.worker_stop.is_set())
        m.close();c.value=100;m.check_heartbeat()
        self.assertIsNone(m.reason);self.assertFalse(m.watchdog.is_alive())
    def test_hung_reader_after_good_reading_trips_independently(self):
        gate=threading.Event();entered=threading.Event();calls=[]
        self.addCleanup(gate.set)
        def read():
            if calls:entered.set();gate.wait(2)
            calls.append(1);return {'temperature_c':50}
        m,c=self.monitor(read);m.start();self.assertTrue(entered.wait(1))
        c.value=15
        self.assertTrue(m.worker_stop.wait(1));self.assertIn('heartbeat',m.reason)
        reason=m.reason;gate.set();time.sleep(.05)
        self.assertEqual(m.reason,reason)
    def test_initial_hung_reader_blocks_start_and_stops(self):
        gate=threading.Event();entered=threading.Event();self.addCleanup(gate.set)
        def read():entered.set();gate.wait(2);return {'temperature_c':50}
        m,c=self.monitor(read);errors=[]
        t=threading.Thread(target=lambda:self.capture_start(m,errors));t.start()
        self.assertTrue(entered.wait(1));self.assertFalse(m.ready.is_set())
        c.value=15;self.assertTrue(m.worker_stop.wait(1));t.join(1)
        self.assertFalse(t.is_alive());self.assertTrue(errors);gate.set()
    @staticmethod
    def capture_start(m,errors):
        try:m.start()
        except RuntimeError as e:errors.append(str(e))
    def test_hot_initial_reading_never_ready(self):
        m,c=self.monitor(lambda:{'temperature_c':80})
        with self.assertRaises(RuntimeError):m.start()
        self.assertFalse(m.ready.is_set());self.assertTrue(m.worker_stop.is_set())
    def test_invalid_initial_then_valid_is_not_premature_ready(self):
        gate=threading.Event();entered=threading.Event();self.addCleanup(gate.set)
        calls=[]
        def read():
            if not calls:calls.append(1);return None
            entered.set();gate.wait(1);return {'temperature_c':50}
        m,c=self.monitor(read);errors=[];t=threading.Thread(target=lambda:self.capture_start(m,errors));t.start()
        self.assertTrue(entered.wait(1));self.assertFalse(m.ready.is_set())
        gate.set();t.join(1);self.assertFalse(t.is_alive());self.assertEqual(errors,[])
    def test_stalled_event_delivery_still_stops(self):
        gate=threading.Event();entered=threading.Event();self.addCleanup(gate.set)
        class Events:
            def put(self,item):
                if item[0]=='gpu':entered.set();gate.wait(2)
        m,c=self.monitor(events=Events());errors=[];t=threading.Thread(target=lambda:self.capture_start(m,errors));t.start()
        self.assertTrue(entered.wait(1));c.value=15
        self.assertTrue(m.worker_stop.wait(1));gate.set();t.join(1)
        self.assertTrue(errors)
    def test_concurrent_trips_latch_one_reason(self):
        m,c=self.monitor()
        threads=[threading.Thread(target=m._trip,args=(str(i),)) for i in range(10)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(k=='safety_stop' for k,v in m.events.queue),1)
    def test_closed_monitor_cannot_start(self):
        m,c=self.monitor();m.close()
        with self.assertRaises(RuntimeError):m.start()
        self.assertFalse(m.worker_stop.is_set())
    def test_display_sample_age(self):
        with patch('gpu_monitor.time.monotonic',return_value=20):
            self.assertIn('sample age: 15.0s',reading_text(dict(temperature_c=50,sample_monotonic=5)))


    def test_hot_read_is_stopped_before_blocked_ui_delivery(self):
        class Events:
            def put(self,item):
                if item[0]=='gpu':raise AssertionError('Hot sample delivered before stop')
        m,c=self.monitor(lambda:{'temperature_c':90},Events())
        with self.assertRaises(RuntimeError):m.start()
        self.assertIn('90',m.reason)
    def test_late_result_cannot_refresh_expired_heartbeat(self):
        m,c=self.monitor()
        def late():c.value=15;return {'temperature_c':50}
        m.read=late
        with self.assertRaises(RuntimeError):m.start()
        self.assertFalse(m.ready.is_set());self.assertIn('heartbeat',m.reason)
    def test_second_start_does_not_reset_active_watchdog(self):
        m,c=self.monitor();m.start();c.value=3;before=m._last_heartbeat
        with self.assertRaises(RuntimeError):m.start()
        self.assertEqual(m._last_heartbeat,before);self.assertIsNone(m.reason)
    def test_start_cancel_does_not_invent_safety_failure(self):
        m,c=self.monitor();m.worker_stop.set()
        with self.assertRaises(RuntimeError):m.start()
        self.assertIsNone(m.reason)
    def test_cli_places_execute_inside_monitored_start_and_finally(self):
        import ast
        from pathlib import Path
        tree=ast.parse((Path(__file__).parent/'miner.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        protected=next(n for n in main.body if isinstance(n,ast.Try) and n.finalbody)
        calls=[n for n in ast.walk(protected) if isinstance(n,ast.Call)]
        self.assertTrue(any(isinstance(n.func,ast.Name) and n.func.id=='execute'
                            and any(k.arg=='stop_event' for k in n.keywords) for n in calls))
        self.assertEqual(ast.unparse(protected.body[0]), 'monitor.start()')
        self.assertEqual(ast.unparse(protected.finalbody[0]), 'monitor.close()')


    def test_safety_notification_does_not_use_blocking_put(self):
        class BlockingSink:
            def put(self,item):raise AssertionError('Blocking delivery forbidden for safety stop')
        m,c=self.monitor(events=BlockingSink())
        m._trip('synthetic stop')
        self.assertTrue(m.worker_stop.is_set())
    def test_refreshed_heartbeat_cannot_be_tripped_from_stale_check(self):
        m,c=self.monitor();m._last_heartbeat=1
        m._trip('obsolete timeout',heartbeat_deadline=0)
        self.assertFalse(m.worker_stop.is_set())


    def test_missing_grace_runs_from_last_valid_not_first_failure(self):
        from gpu_monitor import ThermalGuard
        g=ThermalGuard(started=0)
        self.assertIsNone(g.observe({'temperature_c':50},2))
        self.assertIsNone(g.observe(None,10))
        self.assertIsNone(g.observe(None,16.9))
        self.assertIn('unavailable',g.observe(None,17))

if __name__=='__main__':unittest.main()



