"""Synthetic result events in the installed real-Tk GUI; NO mining or RPC."""
from pathlib import Path
import sys
import tkinter as tk
import unittest

from gui_runner import Runner
from miner_gui import App
from test_gui_window import FakeTray

class WinningResultDisplayTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.runner=Runner(result_path=None)
        self.app=App(self.root,self.runner,FakeTray,require_acknowledgment=False)
        self.app.awaiting_result=True
    def tearDown(self):self.app.finish_exit()
    def finish(self,result):
        self.runner.events.put(('finished',dict(mode='mainnet',result=result,error=None)))
        self.root.after_cancel(self.app.poll_id);self.app.poll_id=None
        self.app.poll()
    def test_synthetic_accepted_block_shows_one_win_and_worker_stopped(self):
        self.finish(dict(hashes=4096,blocks=[dict(hash='12'*32,accepted=True)],submission_uncertain=None))
        self.assertEqual(self.app.status.get(),'Stopped')
        self.assertEqual(self.app.metrics.get(),'Hashes searched: 4,096    Accepted blocks: 1')
        self.assertFalse(self.app.awaiting_result)
        self.assertIsNone(self.runner.thread)
        self.assertFalse(self.app.tray.active)
    def test_synthetic_uncertain_submission_requests_review_without_false_win(self):
        self.finish(dict(hashes=4096,blocks=[],submission_uncertain='Submission outcome needs checking'))
        self.assertEqual(self.app.status.get(),'Stopped - submission needs review')
        self.assertIn('Do not retry until its outcome is resolved.',self.app.detail.get())
        self.assertNotIn('Accepted blocks: 1',self.app.metrics.get())
        self.assertIsNone(self.runner.thread)
    def test_synthetic_rejected_block_is_not_counted_as_accepted(self):
        self.finish(dict(hashes=4096,blocks=[],rejections=[dict(reason='bad-cb-amount')],submission_uncertain=None))
        self.assertIn('Accepted blocks: 0',self.app.metrics.get())
        self.assertIsNone(self.runner.thread)

if __name__=='__main__':unittest.main(verbosity=2)
