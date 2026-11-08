"""Pinned upstream notice comparisons; no network access during tests."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parent

class LicenseNoticeTests(unittest.TestCase):
    def test_retained_notices_match_fetched_upstream_canonical_text(self):
        verified=json.loads((ROOT/'LICENSES/upstream-verification.json').read_text(encoding='utf-8'))
        for key,name in [('core','Bitcoin-Core-MIT.txt'),('bech32','bech32-MIT.txt')]:
            canonical=(ROOT/'LICENSES'/name).read_text(encoding='utf-8').encode('utf-8')
            self.assertEqual(hashlib.sha256(canonical).hexdigest(),verified[key]['canonical_notice_sha256'])
    def test_core_notice_git_blob_identity_matches_upstream(self):
        verified=json.loads((ROOT/'LICENSES/upstream-verification.json').read_text(encoding='utf-8'))
        raw=(ROOT/'LICENSES/Bitcoin-Core-MIT.txt').read_text(encoding='utf-8').encode('utf-8')
        self.assertEqual(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),verified['core']['blob_sha'])
    def test_bip_attribution_does_not_invent_copyright_years(self):
        text=(ROOT/'LICENSES/BIPs-BSD-2-Clause.txt').read_text(encoding='utf-8')
        self.assertNotIn('Copyright (c)',text)
        self.assertIn('upstream Authors field',text)
        self.assertIn('no copyright years or ownership assertions are inferred',text)

if __name__=='__main__':unittest.main()
