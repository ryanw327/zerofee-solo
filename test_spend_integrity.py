"""CPU-only candidate spend-integrity regressions; no node or GPU imports."""
import struct
from dataclasses import replace
import unittest
from bitcoin import target_from_compact
from block_builder import build_candidate, parse_transaction, varbytes, ZERO32

ADDRESS = 'bcrt1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080'
def tx(inputs, value=1000):
    vin = bytes([len(inputs)]) + b''.join(h + struct.pack('<I', n) + b'\0' + b'\xff'*4 for h,n in inputs)
    return struct.pack('<I',2) + vin + b'\x01' + struct.pack('<Q',value) + varbytes(b'\x51') + bytes(4)
def candidate(raws):
    entries = [dict(data=r.hex(), fee=0) for r in raws]
    return build_candidate(dict(height=101, version=0x20000000, previousblockhash='22'*32,
        bits='207fffff', target=f'{target_from_compact(0x207fffff):064x}', curtime=1700000000,
        coinbasevalue=5_000_000_000, transactions=entries), ADDRESS, 1)

class SpendIntegrityTests(unittest.TestCase):
    def test_duplicate_input_within_transaction_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate spent outpoint'):
            candidate([tx([(b'\x11'*32,0)]*2)])
    def test_double_spend_across_distinct_transactions_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate spent outpoint'):
            candidate([tx([(b'\x11'*32,0)],1000),tx([(b'\x11'*32,0)],999)])
    def test_null_outpoint_in_non_coinbase_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Null outpoint'):
            candidate([tx([(ZERO32,0xffffffff),(b'\x11'*32,0)])])
    def test_in_block_parent_output_index_checked(self):
        parent=tx([(b'\x11'*32,0)])
        with self.assertRaisesRegex(ValueError, 'Missing in-block output'):
            candidate([parent,tx([(parse_transaction(parent).txid,1)])])
    def test_same_block_coinbase_spend_rejected(self):
        job=candidate([])
        spend=parse_transaction(tx([(job.transactions[0].txid,0)]))
        with self.assertRaisesRegex(ValueError, 'Immature in-block coinbase'):
            replace(job,transactions=job.transactions+(spend,)).validate()
    def test_valid_parent_child_spend_preserved(self):
        parent=tx([(b'\x11'*32,0)])
        job=candidate([parent,tx([(parse_transaction(parent).txid,0)])])
        self.assertEqual(len(job.transactions),3)
        job.validate()
    def test_distinct_external_outpoints_not_confused(self):
        job=candidate([tx([(b'\x11'*32,0)]),tx([(b'\x11'*32,1)])])
        job.validate()

if __name__ == '__main__': unittest.main()
