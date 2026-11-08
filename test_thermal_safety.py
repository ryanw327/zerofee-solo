"""Mocked monotonic telemetry only: no GPU, real temperature changes or RPC."""
from queue import Queue
import threading,unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from gpu_monitor import ThermalPolicy,ThermalGuard,GPUMonitor,read_gpu,reading_text
from gui_runner import RunConfig

class Clock:
    def __init__(self):self.value=0
    def now(self):return self.value
class WaitClock:
    def __init__(self,clock):self.clock=clock;self.stopped=False
    def is_set(self):return self.stopped
    def set(self):self.stopped=True
    def wait(self,seconds):self.clock.value+=seconds;return self.stopped

class ThermalTests(unittest.TestCase):
    def test_default_80_degree_core_limit_trips_at_equality_and_latches(self):
        guard=ThermalGuard(started=0)
        self.assertIsNone(guard.observe({'temperature_c':79.9},0))
        reason=guard.observe({'temperature_c':80},2)
        self.assertIn('GPU core',reason);self.assertIn('80',reason)
        self.assertEqual(guard.observe({'temperature_c':40},4),reason)
    def test_configurable_threshold_is_finite_and_bounded(self):
        guard=ThermalGuard(ThermalPolicy(limit_c=75),started=0)
        self.assertIsNone(guard.observe({'temperature_c':74},0))
        self.assertIsNotNone(guard.observe({'temperature_c':75},1))
        for limit in (59,91,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):RunConfig(temperature_limit_c=limit).validate()
    def test_missing_initial_readings_stop_after_defined_grace(self):
        guard=ThermalGuard(started=0)
        self.assertIsNone(guard.observe(None,14.99))
        self.assertIn('15 seconds',guard.observe(None,15))
    def test_valid_reading_resets_missing_streak(self):
        guard=ThermalGuard(started=0)
        self.assertIsNone(guard.observe(None,10))
        self.assertIsNone(guard.observe({'temperature_c':50},14))
        self.assertIsNone(guard.observe(None,20))
        self.assertIsNone(guard.observe(None,28.9))
        self.assertIsNotNone(guard.observe(None,29))
    def test_invalid_readings_count_as_missing_and_hotspot_is_not_substituted(self):
        for value in ({'temperature_c':float('nan')},{'temperature_c':True},{'temperature_c':-1},{'hotspot_c':90}):
            guard=ThermalGuard(started=0)
            self.assertIsNotNone(guard.observe(value,15))
    def test_monitor_requests_cooperative_stop_and_emits_one_clear_reason(self):
        clock=Clock();stop=threading.Event();events=Queue();values=iter([{'temperature_c':70},{'temperature_c':81}])
        monitor=GPUMonitor(events,stop,read=lambda:next(values),clock=clock.now)
        monitor.stop_event=WaitClock(clock);monitor.work()
        self.assertTrue(stop.is_set());self.assertIn('81',monitor.reason)
        safety=[v for k,v in events.queue if k=='safety_stop']
        self.assertEqual(safety,[monitor.reason]);monitor.close();self.assertTrue(stop.is_set())
    def test_failed_reads_stop_without_using_a_gpu_or_sleeping(self):
        clock=Clock();stop=threading.Event();reader=Mock(side_effect=RuntimeError('unavailable'))
        monitor=GPUMonitor(Queue(),stop,read=reader,clock=clock.now);monitor.stop_event=WaitClock(clock)
        monitor.work()
        self.assertTrue(stop.is_set());self.assertEqual(clock.value,16)
        self.assertIn('readings unavailable',monitor.reason)
    def test_unexpected_monitor_failure_still_sets_worker_stop(self):
        clock=Clock();stop=threading.Event();events=Mock();events.put.side_effect=RuntimeError('queue failed')
        monitor=GPUMonitor(events,stop,read=lambda:{'temperature_c':50},clock=clock.now)
        monitor.work();self.assertTrue(stop.is_set());self.assertIn('monitor failed',monitor.reason)
    def test_monitor_requires_actual_stop_control(self):
        with self.assertRaises(ValueError):GPUMonitor(Queue(),None)
    def test_optional_power_or_utilization_failure_does_not_hide_valid_core_sensor(self):
        response=SimpleNamespace(returncode=0,stdout='55, N/A, N/A\n')
        with patch('gpu_monitor.Path.is_file',return_value=True),patch('gpu_monitor.subprocess.run',return_value=response):
            value=read_gpu()
        self.assertEqual(value['temperature_c'],55);self.assertIsNone(value['board_watts'])
        self.assertIsNone(ThermalGuard(started=0).observe(value,16))
        self.assertIn('unavailable',reading_text(value))
    def test_display_handles_missing_invalid_sensor_fields_without_crashing_poll_loop(self):
        for value in (None,{},dict(temperature_c=float('nan')),dict(temperature_c=50,board_watts=None)):
            self.assertIn('hotspot',reading_text(value).lower())

if __name__=='__main__':unittest.main()
