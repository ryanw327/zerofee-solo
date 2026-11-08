"""Offline receipt/hidden-Tk tests. No GPU, RPC, real coins or mainnet submission."""
from copy import deepcopy
from pathlib import Path
import json,tempfile,tkinter as tk,unittest
from unittest.mock import Mock,patch
from gui_runner import Runner
from miner_gui import App
from test_gui_window import FakeTray
from test_builder import ADDRESS
from win_moment import save_receipts,receipt_snapshots,continuation_allowed,chain_text,WinMoment

def record():
    return dict(recorded_utc='2026-11-07T00:00:00+00:00',mode='regtest',error=None,restoration_error=None,
        restoration={'temporary_access_removed':True},payout_address=ADDRESS,
        result=dict(chain='regtest',hashes=262144,submission_uncertain=None,blocks=[dict(
            accepted=True,network='regtest',hash='ab'*32,height=101,payout_address=ADDRESS,
            active_chain=True,node_confirmations=1)]))

class ReceiptTests(unittest.TestCase):
    def test_hash_height_payout_and_confirmation_snapshot_are_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            first=save_receipts(record(),directory)[0];path=Path(first['receipt_path']);raw=path.read_bytes()
            second=record();second['recorded_utc']='2026-11-08T00:00:00+00:00'
            save_receipts(second,directory)
            self.assertEqual(path.read_bytes(),raw)
            data=json.loads(raw)
            self.assertEqual((data['block_hash'],data['height'],data['payout_address']),('ab'*32,101,ADDRESS))
            self.assertTrue(data['accepted_by_node']);self.assertEqual(data['node_confirmations_at_check'],1)
            self.assertEqual(len(list((Path(directory)/'wins').iterdir())),1)
    def test_sidechain_acceptance_is_not_presented_as_active_chain_confirmation(self):
        value=record();value['result']['blocks'][0].update(active_chain=False,node_confirmations=-1)
        with tempfile.TemporaryDirectory() as directory:
            receipt=save_receipts(value,directory)[0]
        self.assertIn('Not in your node',chain_text(receipt));self.assertIn('-1',chain_text(receipt))
    def test_uncertain_rejected_and_simulation_outcomes_do_not_create_wins(self):
        values=[]
        for field in ('submission_uncertain',):
            value=record();value['result'][field]='Review pending submission';values.append(value)
        value=record();value['result']['blocks'][0]['accepted']=False;values.append(value)
        value=record();value['mode']='simulation';values.append(value)
        with tempfile.TemporaryDirectory() as directory:
            for value in values:self.assertEqual(save_receipts(value,directory),[])
            self.assertFalse((Path(directory)/'wins').exists())
    def test_bad_hash_address_or_height_does_not_write_a_receipt(self):
        for key,bad in [('hash','../unsafe'),('height',-1),('payout_address','not-an-address')]:
            value=record();value['result']['blocks'][0][key]=bad
            with tempfile.TemporaryDirectory() as directory,self.assertRaises(ValueError):save_receipts(value,directory)
    def test_continuation_requires_explicit_choice_and_successful_managed_restoration(self):
        value=record();value['mode']='mainnet'
        self.assertTrue(continuation_allowed(value))
        for field in ('error','restoration_error'):
            bad=deepcopy(value);bad[field]='Needs review';self.assertFalse(continuation_allowed(bad))
        for field in ('stop_reason','win_receipt_error'):
            bad=deepcopy(value);bad[field]='Needs review';self.assertFalse(continuation_allowed(bad))
        value['result']['submission_uncertain']='Unknown';self.assertFalse(continuation_allowed(value))
        value['result']['submission_uncertain']=None;value['restoration']=None
        self.assertFalse(continuation_allowed(value))
    def test_snapshot_preserves_copyable_details_when_storage_fails(self):
        with tempfile.TemporaryDirectory() as directory,patch('win_moment.Path.write_text',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):save_receipts(record(),directory)
        snapshot=receipt_snapshots(record())[0]
        self.assertEqual((snapshot['block_hash'],snapshot['payout_address']),('ab'*32,ADDRESS))

class DialogTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        with tempfile.TemporaryDirectory() as directory:self.receipt=save_receipts(record(),directory)[0]
    def tearDown(self):
        try:self.root.destroy()
        except tk.TclError:pass
    def test_regtest_label_and_selectable_details_with_no_automatic_continue(self):
        callback=Mock();dialog=WinMoment(self.root,self.receipt,callback,True,sound=False,visible=False)
        self.assertIn('REGTEST',dialog.window.title())
        self.assertIn('ab'*32,dialog.details.get('1.0','end'))
        self.assertIn(ADDRESS,dialog.details.get('1.0','end'))
        callback.assert_not_called();dialog.close();callback.assert_not_called()
    def test_continue_is_one_explicit_click_and_disabled_when_recovery_needed(self):
        callback=Mock();dialog=WinMoment(self.root,self.receipt,callback,True,sound=False,visible=False)
        dialog.continue_mining();dialog.continue_mining();callback.assert_called_once()
        blocked=Mock();dialog=WinMoment(self.root,self.receipt,blocked,False,sound=False,visible=False)
        dialog.continue_mining();blocked.assert_not_called();dialog.close()
    def test_finished_event_shows_win_once_and_continuation_uses_normal_start_flow(self):
        runner=Runner(result_path=None);app=App(self.root,runner,FakeTray,require_acknowledgment=False);app.show=lambda:None
        value=record();value['win_receipts']=[self.receipt]
        value['mode']='mainnet'
        with patch('miner_gui.WinMoment') as popup,patch.object(app,'start') as start:
            for _ in range(2):
                runner.events.put(('finished',value))
                if app.poll_id:self.root.after_cancel(app.poll_id)
                app.poll()
            popup.assert_called_once();start.assert_not_called()
            popup.call_args.kwargs['on_continue']();start.assert_called_once()
        app.finish_exit()

if __name__=='__main__':unittest.main()
