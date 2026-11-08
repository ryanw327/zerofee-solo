"""Offline-only UI tests. Synthetic receipts, nonpayable mocked recipient, no wallet."""
import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import struct
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

from block_builder import build_candidate
from test_builder import ADDRESS, fixture
from tip_support import ApprovedTip, TipSection, configured_tip, tip_eligible
from win_moment import WinMoment, chain_text

# This is intentionally not a Bitcoin address and is never shipped as configured.
NONPAYABLE = 'not-a-bitcoin-address'


def png_fixture():
    # A solid test bitmap, explicitly NOT a QR code. Never shown in a visible UI.
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b'\x89PNG\r\n\x1a\n'
    raw += chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 0, 0, 0, 0))
    raw += chunk(b'IDAT', zlib.compress((b'\x00' + b'\xff' * 64) * 64))
    return raw + chunk(b'IEND', b'')


def fake_tip():
    raw = png_fixture()
    return ApprovedTip(NONPAYABLE, base64.b64encode(raw).decode('ascii'),
                       hashlib.sha256(raw).hexdigest(), 'bitcoin:' + NONPAYABLE)


def mainnet_receipt():
    # This does not enter receipt validation or any payment path.
    return dict(network='mainnet', block_hash='ab' * 32, height=123,
                payout_address='SYNTHETIC WINNER PAYOUT: NO PAYMENT ADDRESS',
                accepted_by_node=True, active_chain_at_check=True,
                node_confirmations_at_check=1)


def labels(widget):
    result=[]
    for child in widget.winfo_children():
        if 'text' in child.keys():
            result.append(str(child.cget('text')))
        result.extend(labels(child))
    return result


class TipPolicyTests(unittest.TestCase):
    def test_absent_configuration_fails_closed(self):
        with patch('tip_support.APPROVED_TIP', None):
            self.assertIsNone(configured_tip())

    def test_nonpayable_fixture_is_rejected_without_a_mock(self):
        with patch('tip_support.APPROVED_TIP', fake_tip()):
            self.assertIsNone(configured_tip())

    def test_regtest_address_is_rejected_for_mainnet_tips(self):
        tip = replace(fake_tip(), address=ADDRESS, verified_qr_payload='bitcoin:' + ADDRESS)
        with patch('tip_support.APPROVED_TIP', tip):
            self.assertIsNone(configured_tip())

    def test_wrong_payload_or_image_hash_is_rejected(self):
        for tip in (replace(fake_tip(), verified_qr_payload='bitcoin:other-invalid-placeholder'),
                    replace(fake_tip(), qr_sha256='0' * 64),
                    replace(fake_tip(), qr_png_base64='not-base64'),
                    replace(fake_tip(), qr_sha256=None)):
            with self.subTest(tip=tip), patch('tip_support.payout_script'), patch('tip_support.APPROVED_TIP', tip):
                self.assertIsNone(configured_tip())

    def test_untyped_configuration_and_invalid_png_fail_closed(self):
        for tip in ({'address': NONPAYABLE}, 'address', replace(fake_tip(), qr_png_base64='', qr_sha256=hashlib.sha256(b'').hexdigest())):
            with patch('tip_support.payout_script'), patch('tip_support.APPROVED_TIP', tip):
                self.assertIsNone(configured_tip())

    def test_recipient_is_frozen_and_uri_has_no_amount(self):
        tip=fake_tip()
        with self.assertRaises(FrozenInstanceError):
            tip.address='different'
        self.assertEqual(tip.uri, tip.verified_qr_payload)
        self.assertNotIn('?', tip.uri)

    def test_known_mainnet_receipt_requires_restoration_and_active_chain(self):
        receipt=mainnet_receipt()
        self.assertTrue(tip_eligible(receipt, True))
        self.assertFalse(tip_eligible(receipt, False))
        self.assertFalse(tip_eligible(receipt, True, 'Recovery needs attention'))
        for key,value in [('network','regtest'),('network','simulation'),('accepted_by_node',False),
                          ('active_chain_at_check',False),('node_confirmations_at_check',None),
                          ('node_confirmations_at_check',-1),('node_confirmations_at_check',0),
                          ('node_confirmations_at_check',True)]:
            changed=dict(receipt);changed[key]=value
            self.assertFalse(tip_eligible(changed, True), (key,value))

    def test_optional_config_does_not_change_full_coinbase_payout(self):
        template=fixture()
        before=build_candidate(template, ADDRESS, 7)
        with patch('tip_support.APPROVED_TIP', fake_tip()):
            after=build_candidate(template, ADDRESS, 7)
        self.assertEqual(before.raw(), after.raw())
        self.assertEqual(after.transactions[0].outputs[0], (template['coinbasevalue'], after.payout))
        self.assertEqual(len(after.transactions[0].outputs), 2)
        self.assertEqual(after.transactions[0].outputs[1][0], 0)


class TipWidgetTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
    def tearDown(self):
        try:self.root.destroy()
        except tk.TclError:pass

    def test_unconfigured_section_never_constructs_qr_or_clipboard_action(self):
        with patch('tip_support.APPROVED_TIP', None), patch('tip_support.tk.PhotoImage') as image:
            section=TipSection(self.root)
        image.assert_not_called()
        self.assertIsNone(section.recipient)
        self.assertIsNone(section.copy_button)
        self.assertIsNone(section.address_widget)
        self.assertFalse(section.copy_address())
        self.assertIn('not configured', section.message.get())
        self.assertNotIn(NONPAYABLE, ' '.join(labels(section)))
        self.assertIsNone(section.toggle_button)
        self.assertEqual(section.body.winfo_manager(), 'pack')
        section.toggle();section.toggle()
        self.assertEqual(section.body.winfo_manager(), 'pack')
        self.assertIn('zero-fee miner', ' '.join(labels(section)))

    def configured_section(self):
        # Deliberate mock-only validation so no real recipient is ever used.
        validation=patch('tip_support.payout_script');validation.start();self.addCleanup(validation.stop)
        with patch('tip_support.APPROVED_TIP', fake_tip()):
            return TipSection(self.root)

    def test_configured_mock_copies_exact_displayed_value_only_on_click(self):
        section=self.configured_section()
        expected=section.address_widget.get('1.0','end-1c')
        with patch.object(section,'clipboard_clear') as clear, patch.object(section,'clipboard_append') as append, \
             patch.object(section,'clipboard_get',return_value=expected) as get:
            clear.assert_not_called();append.assert_not_called();get.assert_not_called()
            self.assertTrue(section.copy_address())
        clear.assert_called_once_with();append.assert_called_once_with(expected)
        self.assertEqual(expected, NONPAYABLE)
        self.assertIn('Verify the full', section.message.get())

    def test_clipboard_error_and_substitution_never_report_success(self):
        section=self.configured_section()
        with patch.object(section,'clipboard_clear',side_effect=tk.TclError('unavailable')):
            self.assertFalse(section.copy_address())
            self.assertIn('Could not copy', section.message.get())
        with patch.object(section,'clipboard_clear'),patch.object(section,'clipboard_append'), \
             patch.object(section,'clipboard_get',return_value='substituted'):
            self.assertFalse(section.copy_address())
            self.assertIn('Clipboard check failed', section.message.get())

    def test_qr_display_failure_disables_copy_and_address(self):
        with patch('tip_support.payout_script'),patch('tip_support.APPROVED_TIP',fake_tip()), \
             patch('tip_support.tk.PhotoImage',side_effect=tk.TclError('bad image')):
            section=TipSection(self.root)
        self.assertIsNone(section.recipient);self.assertIsNone(section.copy_button)
        self.assertIsNone(section.address_widget)

    def test_copy_after_section_destroyed_does_not_touch_clipboard(self):
        section=self.configured_section();section.destroy()
        with patch.object(section,'clipboard_clear') as clear:
            self.assertFalse(section.copy_address())
        clear.assert_not_called()


class TipWinIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
    def tearDown(self):
        try:self.root.destroy()
        except tk.TclError:pass

    def dialog(self,receipt=None,allow_continue=True,notice=''):
        return WinMoment(self.root,receipt or mainnet_receipt(),on_continue=Mock(),
                         allow_continue=allow_continue,notice=notice,sound=False,visible=False)

    def test_tip_does_not_modify_receipt_or_continue_automatically(self):
        receipt=mainnet_receipt();before=deepcopy(receipt)
        dialog=self.dialog(receipt)
        self.assertIsNotNone(dialog.tip_section)
        dialog.tip_section.toggle();dialog.tip_section.toggle()
        self.assertEqual(receipt,before)
        dialog.on_continue.assert_not_called()
        callback=dialog.on_continue
        dialog.close();dialog.close();callback.assert_not_called()

    def test_maturity_text_does_not_claim_live_spendability(self):
        dialog=self.dialog()
        text=dialog.maturity_note.cget('text')
        self.assertIn('100-block',text)
        self.assertIn('does not establish spendability',text)
        self.assertIn('Node acceptance', ' '.join(labels(dialog.window)))
        self.assertIn('confirmations: 1',chain_text(mainnet_receipt()))

    def test_regtest_sidechain_and_blocked_continue_do_not_offer_tip(self):
        for receipt,allowed,notice in [(dict(mainnet_receipt(),network='regtest'),True,''),
                                      (dict(mainnet_receipt(),active_chain_at_check=False),True,''),
                                      (mainnet_receipt(),False,''),(mainnet_receipt(),True,'Receipt error')]:
            dialog=self.dialog(receipt,allowed,notice)
            self.assertIsNone(dialog.tip_section);dialog.close()

    def test_continue_remains_one_explicit_action_after_disclosure(self):
        dialog=self.dialog();callback=dialog.on_continue
        dialog.tip_section.toggle()
        dialog.continue_mining();dialog.continue_mining();callback.assert_called_once_with()

    def test_small_layout_keeps_action_footer_outside_scroll_region(self):
        dialog=self.dialog();dialog.tip_section.toggle()
        dialog.window.geometry('720x500');self.root.update_idletasks()
        self.assertIsNot(dialog.finish_button.master, dialog.tip_section.master)
        self.assertNotIn(dialog.finish_button, dialog.scroll_canvas.winfo_children())
        dialog.resize_content(SimpleNamespace(width=500))
        self.assertLessEqual(int(dialog.maturity_note.cget('wraplength')),476)
        dialog.scroll_content(SimpleNamespace(delta=-120))
        self.assertEqual(str(dialog.continue_button['state']),'normal')


if __name__=='__main__':unittest.main(verbosity=2)
