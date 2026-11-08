"""Adversarial startup and network-profile tests; mainnet execution must never start."""
import copy
from decimal import Decimal,getcontext
import io
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from dataclasses import replace
from address import payout_script
from bitcoin import target_from_compact,MAX_TARGET
from block_builder import build_candidate
from network_policy import subsidy,authorize_execution,network_profile
from startup import make_plan,probability_at_least_one
from test_builder import ADDRESS,fixture
import miner

MAIN_ADDRESS="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"

class StartupTests(unittest.TestCase):
    def test_network_addresses_match_core_format_result(self):
        self.assertEqual(payout_script(MAIN_ADDRESS,"mainnet").hex(),"0014751e76e8199196d454941c45d1b3a323f1433bd6")
        for address,network in [(MAIN_ADDRESS,"regtest"),(ADDRESS,"mainnet")]:
            with self.assertRaises(ValueError):payout_script(address,network)
    def test_network_subsidy_boundaries(self):
        for height,network,expected in [(149,"regtest",5_000_000_000),(150,"regtest",2_500_000_000),
                                        (150,"mainnet",5_000_000_000),(209999,"mainnet",5_000_000_000),
                                        (210000,"mainnet",2_500_000_000),(840000,"mainnet",312_500_000)]:
            self.assertEqual(subsidy(height,network),expected)
    def test_mainnet_profile_candidate_is_offline_only(self):
        template=fixture();template.update(height=974803,bits="1701e5ac",
            target=f"{target_from_compact(0x1701e5ac):064x}",coinbasevalue=312_500_020)
        job=build_candidate(template,MAIN_ADDRESS,1,network="mainnet")
        self.assertEqual(job.network,"mainnet");job.validate()
        self.assertEqual(job.transactions[0].outputs[0][0],312_500_020)
        template["coinbasevalue"]=5_000_000_020
        with self.assertRaises(ValueError):build_candidate(template,MAIN_ADDRESS,1,network="mainnet")
    def test_network_pow_limit(self):
        template=fixture();template.update(height=974803,coinbasevalue=312_500_020)
        with self.assertRaises(ValueError):build_candidate(template,MAIN_ADDRESS,1,network="mainnet")
    def test_both_mainnet_opt_ins_required(self):
        for opt in (False,True):
            with self.assertRaises(RuntimeError):authorize_execution("mainnet",opt)
        with self.assertRaises(ValueError):authorize_execution("regtest",True)
        authorize_execution("regtest",False)
        authorize_execution('mainnet',True,True)
    def test_probability_known_cases(self):
        target=(1<<255)-1
        self.assertAlmostEqual(probability_at_least_one(1,target),0.5)
        self.assertAlmostEqual(probability_at_least_one(2,target),0.75)
        self.assertEqual(probability_at_least_one(0,target),0)
        self.assertEqual(probability_at_least_one(1,MAX_TARGET),1)
    def test_tiny_mainnet_probability_against_decimal(self):
        target=target_from_compact(0x1701e5ac);n=32768
        getcontext().prec=70
        p=Decimal(target+1)/Decimal(1<<256)
        oracle=Decimal(1)-(Decimal(1)-p)**n
        self.assertAlmostEqual(probability_at_least_one(n,target)/float(oracle),1,places=12)
    def test_plan_bounds_energy_and_no_side_effects(self):
        with patch("subprocess.run",side_effect=AssertionError("No RPC allowed")):
            plan=make_plan("mainnet",MAIN_ADDRESS,0x1701e5ac,10,32768,1,rate=7_104_734.37,watts=33.494)
        self.assertFalse(plan["gpu_started"]);self.assertFalse(plan["rpc_called"])
        self.assertFalse(plan["policy_execution_enabled"])
        self.assertAlmostEqual(plan["runtime_cap_energy_at_sampled_power_joules"],334.94)
        self.assertEqual(plan["optimistic_hash_budget"],32768)
        self.assertGreater(plan["mean_hash_only_years_at_unchanged_rate_and_target"],1e9)
    def test_bad_plans(self):
        for kwargs in [dict(max_seconds=0),dict(max_seconds=math.inf),dict(max_hashes=0),dict(max_hashes=10_000_001),
                       dict(max_blocks=0),dict(rate=0),dict(watts=-1)]:
            with self.assertRaises(ValueError):make_plan("mainnet",MAIN_ADDRESS,0x1701e5ac,**kwargs)
    def test_mainnet_cli_never_constructs_rpc_or_gpu(self):
        for extra in [[],["--mainnet-opt-in"]]:
            argv=["miner.py","--network","mainnet","--address",MAIN_ADDRESS]+extra
            with patch.object(sys,"argv",argv),patch.object(miner,"NodeRPC",side_effect=AssertionError("RPC forbidden")), \
                 patch.object(miner,"GPUHasher",side_effect=AssertionError("GPU forbidden")), \
                 patch("sys.stderr",new_callable=io.StringIO),self.assertRaises(SystemExit) as exited:
                miner.main()
            self.assertEqual(exited.exception.code,2)
    def test_mainnet_candidate_cannot_reach_regtest_submission(self):
        from test_builder import DummyRPC
        from miner import Miner,InvalidCandidate
        rpc=DummyRPC();runner=Miner(rpc,ADDRESS,None)
        candidate=replace(build_candidate(fixture(),ADDRESS,1),network="mainnet")
        with patch.object(rpc,"call",side_effect=AssertionError("No RPC allowed")),self.assertRaises(InvalidCandidate):
            runner.submit_solution(candidate,0,bytes(32))
    def test_mainnet_cli_plan_only_is_read_only(self):
        argv=["miner.py","--network","mainnet","--address",MAIN_ADDRESS,"--plan-only"]
        with patch.object(sys,"argv",argv),patch.object(miner,"NodeRPC",side_effect=AssertionError("RPC forbidden")), \
             patch.object(miner,"GPUHasher",side_effect=AssertionError("GPU forbidden")),patch("sys.stdout",new_callable=io.StringIO) as out:
            miner.main()
        plan=json.loads(out.getvalue())
        self.assertEqual(plan["mode"],"PLAN_ONLY")
        self.assertFalse(plan["policy_execution_enabled"])
    def test_example_target_is_clearly_not_a_live_snapshot(self):
        from startup import snapshot_bits
        bits, source = snapshot_bits('mainnet')
        self.assertEqual(bits, 0x1d00ffff)
        self.assertIn('illustrative',source)
        self.assertIn('not the current',source)

if __name__=="__main__":unittest.main(verbosity=2)
