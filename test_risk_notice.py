"""First-launch acknowledgment tests with temporary files and hidden Tk."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from gui_runner import Runner
from miner_gui import App
from risk_notice import is_acknowledged, record_acknowledgment, RISK_TEXT
from test_gui_window import FakeTray

class RiskTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=tk.Tk();self.root.withdraw()
        self.path=Path(self.folder.name)/'ack.json';self.runner=Runner(None)
        self.app=App(self.root,self.runner,FakeTray,settings_path=Path(self.folder.name)/'settings.json',acknowledgment_path=self.path)
    def tearDown(self):
        self.app.finish_exit()
    def test_first_launch_is_unchecked_and_blocks_start_until_acknowledged(self):
        dialog=self.app.risk_dialog;self.assertIsNotNone(dialog);self.assertFalse(dialog.checked.get())
        dialog.accept();self.assertFalse(self.path.exists())
        self.assertEqual(str(self.app.start_button['state']),'disabled')
        with patch.object(self.runner,'start') as start:
            self.app.start();self.app.connect_run();start.assert_not_called()
        self.app.open_tuning();self.assertIsNone(self.app.advanced_dialog)
        dialog.checked.set(True);dialog.refresh();dialog.accept()
        self.assertTrue(is_acknowledged(self.path));self.assertTrue(self.app.risk_ready)
        self.assertIsNone(self.app.risk_dialog);self.assertIsNone(self.runner.thread)
        self.assertEqual(str(self.app.start_button['state']),'normal')
    def test_acknowledgment_reopens_without_notice_and_never_starts_work(self):
        record_acknowledgment(self.path);self.app.finish_exit()
        self.root=tk.Tk();self.root.withdraw()
        self.app=App(self.root,self.runner,FakeTray,settings_path=Path(self.folder.name)/'settings.json',acknowledgment_path=self.path)
        self.assertTrue(self.app.risk_ready);self.assertIsNone(self.app.risk_dialog);self.assertIsNone(self.runner.thread)
    def test_bad_or_old_notice_requires_acknowledgment(self):
        for raw in ('{}','null','{"notice_version":0,"acknowledged":true}','{"notice_version":true,"acknowledged":true}','{"notice_version":1,"acknowledged":false}'):
            self.path.write_text(raw);self.assertFalse(is_acknowledged(self.path))
    def test_failed_ack_write_keeps_start_blocked(self):
        dialog=self.app.risk_dialog;dialog.checked.set(True)
        with patch('risk_notice.record_acknowledgment',side_effect=OSError('synthetic failure')):dialog.accept()
        self.assertFalse(self.app.risk_ready);self.assertIn('synthetic failure',dialog.error.get())
    def test_notice_has_accurate_stress_thermal_warranty_and_reward_caveats(self):
        for fragment in ('Not all pacing presets','cannot guarantee','as-is','without warranty','extremely low odds','electricity','does not eliminate liability'):
            self.assertIn(fragment,RISK_TEXT)

if __name__=='__main__':unittest.main()
