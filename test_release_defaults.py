"""Public release defaults: no recipient, developer profile or private fixture dependency."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from app_paths import DEFAULT_DATADIR
from gui_runner import ADDRESS, RunConfig
from managed_core import ManagedCore
from startup import make_plan
from test_vectors import ADDRESS as PUBLIC_VECTOR
from tip_support import configured_tip

class ReleaseDefaultsTests(unittest.TestCase):
    def test_receiving_address_is_blank_and_tip_is_separate(self):
        self.assertEqual(ADDRESS,'');self.assertEqual(RunConfig().address,'')
        self.assertIsNotNone(configured_tip())
        with self.assertRaises(ValueError):RunConfig(mode='mainnet',managed=True).validate()
    def test_data_folder_uses_current_environment_default(self):
        self.assertEqual(Path(DEFAULT_DATADIR).name,'Bitcoin')
        self.assertEqual(RunConfig().datadir,DEFAULT_DATADIR)
    def test_absent_config_is_valid_but_unreviewed_settings_still_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            core=ManagedCore(root=folder,datadir=folder)
            self.assertEqual(core.config_hash(),hashlib.sha256(b'').hexdigest())
            (Path(folder)/'bitcoin.conf').write_text('rpcbind=0.0.0.0\n')
            with self.assertRaises(RuntimeError):core.config_hash()
    def test_plan_uses_explicitly_labelled_scenario_without_measurement_files(self):
        value=make_plan('mainnet',PUBLIC_VECTOR,0x1d00ffff)
        self.assertIn('not measured',value['rate_source'])
        self.assertFalse(value['gpu_started']);self.assertFalse(value['rpc_called'])

if __name__=='__main__':unittest.main()
