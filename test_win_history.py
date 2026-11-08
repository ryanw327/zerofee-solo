"""Saved receipt replay tests. Temporary files and hidden Tk, no node/GPU/network."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
from gui_runner import Runner
from test_vectors import ADDRESS as MAINNET_PAYOUT
from miner_gui import App
from test_builder import ADDRESS
from test_gui_window import FakeTray
from test_optional_tip import labels
from win_history import MAX_RECEIPT_BYTES, SavedWins, load_saved_wins, validate_saved_receipt
from win_moment import WinMoment, chain_text


def receipt(network='regtest', digest='ab'*32, height=123, recorded='2026-11-07T01:00:00+00:00'):
    return dict(network=network,block_hash=digest,height=height,recorded_utc=recorded,
                payout_address=ADDRESS if network=='regtest' else MAINNET_PAYOUT,
                accepted_by_node=True,active_chain_at_check=True,node_confirmations_at_check=1)


def write_receipt(root, value):
    folder=Path(root)/'wins';folder.mkdir(exist_ok=True)
    path=folder/(value['network']+'-'+value['block_hash']+'.json')
    path.write_text(json.dumps(value),encoding='utf-8')
    return path


class SavedReceiptTests(unittest.TestCase):
    def test_empty_history_does_not_create_a_directory_or_fake_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(load_saved_wins(root),([],[]))
            self.assertFalse((Path(root)/'wins').exists())

    def test_multiple_network_receipts_are_newest_first_without_rewriting(self):
        with tempfile.TemporaryDirectory() as root:
            values=[receipt(recorded='2026-11-07T01:00:00+00:00'),
                    receipt('mainnet','cd'*32,124,'2026-11-07T02:00:00+00:00'),
                    receipt('regtest','ef'*32,125,'2026-11-07T03:00:00+00:00')]
            before={write_receipt(root,value):json.dumps(value).encode() for value in values}
            loaded,errors=load_saved_wins(root)
            self.assertFalse(errors)
            self.assertEqual([item['block_hash'] for item in loaded],['ef'*32,'cd'*32,'ab'*32])
            for path,raw in before.items():self.assertEqual(path.read_bytes(),raw)
            self.assertEqual(loaded[1]['node_confirmations_at_check'],1)

    def test_rejected_malformed_or_mismatched_records_never_become_wins(self):
        original=receipt();name='regtest-'+original['block_hash']+'.json'
        changes=[('accepted_by_node',False),('block_hash','cd'*32),('network','mainnet'),('height',True),
                 ('height',-1),('payout_address','not-an-address'),('recorded_utc','not a date'),
                 ('recorded_utc','2026-11-07T01:00:00'),('active_chain_at_check','true'),
                 ('node_confirmations_at_check',True),('node_confirmations_at_check',-1)]
        for key,value in changes:
            changed=dict(original);changed[key]=value
            with self.subTest(key=key,value=value),self.assertRaises((ValueError,TypeError)):
                validate_saved_receipt(changed,name)

    def test_one_corrupt_file_does_not_hide_another_valid_win(self):
        with tempfile.TemporaryDirectory() as root:
            valid=write_receipt(root,receipt())
            broken=Path(root)/'wins'/('mainnet-'+'cd'*32+'.json');broken.write_text('{ broken',encoding='utf-8')
            before=broken.read_bytes();loaded,errors=load_saved_wins(root)
            self.assertEqual(len(loaded),1);self.assertEqual(len(errors),1)
            self.assertEqual(broken.read_bytes(),before);self.assertTrue(valid.exists())

    def test_oversized_receipt_is_skipped_without_rewriting(self):
        with tempfile.TemporaryDirectory() as root:
            path=write_receipt(root,receipt());path.write_bytes(b'x'*(MAX_RECEIPT_BYTES+1))
            loaded,errors=load_saved_wins(root)
            self.assertFalse(loaded);self.assertIn('too large',errors[0])
            self.assertEqual(path.stat().st_size,MAX_RECEIPT_BYTES+1)

    def test_temporary_files_and_unrecognized_names_are_not_loaded(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'wins';folder.mkdir()
            (folder/'pending.tmp').write_text(json.dumps(receipt()),encoding='utf-8')
            (folder/'random.json').write_text(json.dumps(receipt()),encoding='utf-8')
            self.assertEqual(load_saved_wins(root),([],[]))

    def test_linked_receipt_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            write_receipt(root,receipt())
            with patch('win_history.Path.is_symlink',return_value=True):
                loaded,errors=load_saved_wins(root)
            self.assertFalse(loaded);self.assertIn('regular local directory',errors[0])

    def test_sidechain_snapshot_is_preserved_not_upgraded(self):
        value=receipt();value.update(active_chain_at_check=False,node_confirmations_at_check=-1)
        with tempfile.TemporaryDirectory() as root:
            write_receipt(root,value);loaded,errors=load_saved_wins(root)
        self.assertFalse(errors);self.assertFalse(loaded[0]['active_chain_at_check'])
        text=chain_text(loaded[0],historical=True)
        self.assertIn('Was not',text);self.assertIn('-1',text);self.assertIn('not checked',text)


class SavedWinsWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
    def tearDown(self):
        try:self.root.destroy()
        except tk.TclError:pass
    def viewer(self):return SavedWins(self.root,self.directory.name,visible=False)
    def hidden_receipt(self,*args,**kwargs):return WinMoment(*args,**kwargs,visible=False)

    def test_empty_list_is_honest_and_open_is_disabled(self):
        viewer=self.viewer()
        self.assertEqual(viewer.tree.get_children(),())
        self.assertIn('No saved win receipts yet',viewer.status.get())
        self.assertEqual(str(viewer.open_button['state']),'disabled')
        self.assertIsNone(viewer.open_selected())
        self.assertFalse((Path(self.directory.name)/'wins').exists())

    def test_reopened_mainnet_record_is_historical_with_no_sound_or_continue(self):
        value=receipt('mainnet');path=write_receipt(self.directory.name,value);before=path.read_bytes()
        viewer=self.viewer()
        with patch('win_history.WinMoment',side_effect=self.hidden_receipt),patch('winsound.MessageBeep') as beep:
            dialog=viewer.open_selected()
        self.assertTrue(dialog.historical);self.assertIn('saved block receipt',dialog.window.title())
        self.assertIsNone(dialog.on_continue)
        self.assertEqual(str(dialog.continue_button['state']),'disabled')
        self.assertEqual(dialog.continue_button.winfo_manager(),'')
        self.assertIn('SAVED BLOCK RECEIPT',' '.join(labels(dialog.window)))
        self.assertIn(value['recorded_utc'],' '.join(labels(dialog.window)))
        self.assertIn('not checked',' '.join(labels(dialog.window)))
        self.assertNotIn('Normal Core has been restored',' '.join(labels(dialog.window)))
        self.assertIsNotNone(dialog.tip_section);self.assertIsNone(dialog.tip_section.copy_button)
        beep.assert_not_called();dialog.continue_mining();self.assertFalse(dialog.closed)
        self.assertEqual(path.read_bytes(),before)

    def test_historical_mode_overrides_a_supplied_continue_callback(self):
        callback=Mock()
        dialog=WinMoment(self.root,receipt('mainnet'),on_continue=callback,allow_continue=True,historical=True,sound=True,visible=False)
        dialog.continue_mining();callback.assert_not_called()
        self.assertEqual(dialog.finish_button.cget('text'),'Close receipt')

    def test_regtest_replay_stays_clearly_valueless_and_has_no_tip(self):
        write_receipt(self.directory.name,receipt())
        viewer=self.viewer();values=viewer.tree.item(viewer.tree.get_children()[0],'values')
        self.assertIn('no BTC',values[0])
        with patch('win_history.WinMoment',side_effect=self.hidden_receipt):dialog=viewer.open_selected()
        self.assertIn('no Bitcoin won',dialog.window.title())
        self.assertIsNone(dialog.tip_section)

    def test_repeated_open_reuses_window_then_reopens_after_close(self):
        write_receipt(self.directory.name,receipt())
        viewer=self.viewer()
        with patch('win_history.WinMoment',side_effect=self.hidden_receipt) as constructor:
            first=viewer.open_selected()
            with patch.object(first.window,'deiconify'),patch.object(first.window,'lift'):
                self.assertIs(viewer.open_selected(),first)
            self.assertEqual(constructor.call_count,1)
            first.close();second=viewer.open_selected()
            self.assertIsNot(first,second);self.assertEqual(constructor.call_count,2)

    def test_refresh_adds_multiple_receipts_and_preserves_selection(self):
        first=write_receipt(self.directory.name,receipt())
        viewer=self.viewer();selected=viewer.tree.selection()
        write_receipt(self.directory.name,receipt('mainnet','cd'*32,124))
        viewer.refresh()
        self.assertEqual(len(viewer.tree.get_children()),2);self.assertEqual(viewer.tree.selection(),selected)
        first.write_text('broken',encoding='utf-8');viewer.refresh()
        self.assertEqual(len(viewer.tree.get_children()),1)
        self.assertIn('read/validation issue',viewer.status.get())

    def test_app_button_is_available_while_busy_and_does_not_change_run_state(self):
        runner=Runner(result_path=None);app=App(self.root,runner,FakeTray,require_acknowledgment=False)
        runner.busy=True;app.awaiting_result=True;app.seen_wins.add(('mainnet','ab'*32))
        before=(app.status.get(),app.metrics.get(),app.address.get(),set(app.seen_wins))
        try:
            app.refresh_controls();self.assertEqual(str(app.wins_button['state']),'normal')
            with patch('miner_gui.SavedWins') as factory,patch.object(runner,'start') as start,patch.object(runner,'stop') as stop:
                instance=factory.return_value;instance.closed=False
                self.assertIs(app.view_wins(),instance);self.assertIs(app.view_wins(),instance)
                factory.assert_called_once();instance.refresh.assert_called_once();instance.show.assert_called_once()
                start.assert_not_called();stop.assert_not_called()
            self.assertEqual(before,(app.status.get(),app.metrics.get(),app.address.get(),set(app.seen_wins)))
        finally:
            runner.busy=False;app.awaiting_result=False;app.finish_exit()


if __name__=='__main__':unittest.main(verbosity=2)
