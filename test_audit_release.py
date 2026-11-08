"""Publication guard checks against synthetic temporary files, never real secrets."""
import json
from pathlib import Path
import tempfile
import unittest
from audit_release import audit

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        (self.root/'publication-allowlist.json').write_text(json.dumps(['publication-allowlist.json','runtime.py']))
        (self.root/'runtime.py').write_text('VALUE = "public"\n')
    def test_clean_allowlist_passes_and_unexpected_log_fails(self):
        self.assertEqual(audit(self.root)['status'],'PASS')
        (self.root/'private.log').write_text('synthetic log')
        self.assertIn(dict(file='private.log',rule='unexpected-file'),audit(self.root)['findings'])
    def test_missing_allowlisted_file_fails(self):
        (self.root/'publication-allowlist.json').write_text(json.dumps(['publication-allowlist.json','missing.py']))
        self.assertIn(dict(file='missing.py',rule='missing-allowlisted-file'),audit(self.root)['findings'])
    def test_profile_path_and_token_shapes_report_only_rule_and_filename(self):
        examples=[('C:'+chr(92)+'Users'+chr(92)+'example','personal-profile-path'),
                  ('gh'+'p_'+'A'*30,'github-token')]
        for value,rule in examples:
            (self.root/'runtime.py').write_text('VALUE = '+repr(value)+'\n')
            result=audit(self.root)
            self.assertIn(dict(file='runtime.py',rule=rule),result['findings'])
            self.assertNotIn(value,json.dumps(result))



class ReleaseBrandingTests(unittest.TestCase):
    def test_no_pool_fee_label_preserves_warning(self):
        source = (Path(__file__).parent / 'miner_gui.py').read_text(encoding='utf-8')
        self.assertIn("text='Solo mining • No pool fees'", source)
        self.assertIn("text='Solo GPU lotto • Budget for electricity; expect no reward.'", source)



class OptionalCoffeeTests(unittest.TestCase):
    def test_coffee_copy_and_no_unapproved_recipient(self):
        from tip_support import APPROVED_TIP, configured_tip
        from audit_release import APPROVED_PUBLIC_TIP
        self.assertEqual(configured_tip().address, APPROVED_PUBLIC_TIP)
        self.assertEqual(APPROVED_TIP.verified_qr_payload, 'bitcoin:' + APPROVED_PUBLIC_TIP)
        source = (Path(__file__).parent / 'tip_support.py').read_text(encoding='utf-8')
        self.assertIn('consider buying Ryan a coffee (optional).', source)
        self.assertIn('No tip is deducted', source)
    def test_copy_uses_exact_approved_address_without_wallet_action(self):
        from tip_support import TipSection, configured_tip
        from types import SimpleNamespace
        from unittest.mock import Mock
        tip = configured_tip()
        clipboard = {}
        fake = SimpleNamespace(recipient=tip, winfo_exists=lambda: True,
            clipboard_clear=lambda: clipboard.clear(),
            clipboard_append=lambda value: clipboard.update(value=value),
            clipboard_get=lambda: clipboard['value'], message=Mock())
        self.assertTrue(TipSection.copy_address(fake))
        self.assertEqual(clipboard['value'], tip.address)
    def test_only_known_mainnet_win_offers_optional_tip(self):
        from tip_support import tip_eligible
        receipt = dict(network='mainnet', accepted_by_node=True,
                       active_chain_at_check=True, node_confirmations_at_check=1)
        self.assertTrue(tip_eligible(receipt, True))
        self.assertFalse(tip_eligible(receipt, False))
        self.assertFalse(tip_eligible(receipt, True, 'Recovery blocked'))
        for key, value in [('network','regtest'), ('accepted_by_node',False),
                           ('active_chain_at_check',False), ('node_confirmations_at_check',0),
                           ('node_confirmations_at_check',True)]:
            self.assertFalse(tip_eligible(dict(receipt, **{key:value}), True))

if __name__=='__main__':unittest.main()
