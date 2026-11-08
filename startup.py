"""Read-only startup estimates. This module imports no GPU or RPC client."""
import argparse
import json
import math
from pathlib import Path
from address import payout_script
from bitcoin import target_from_compact,MAX_TARGET
from network_policy import network_profile,MAINNET_VALIDATED,MAINNET_IMPLEMENTED,MAINNET_BLOCKERS

ROOT=Path(__file__).resolve().parent

def probability_at_least_one(hashes,target):
    if type(hashes) is not int or hashes<0 or type(target) is not int or not 0<=target<=MAX_TARGET:
        raise ValueError("Invalid probability inputs")
    p=(target+1)/(1<<256)
    if not hashes:return 0.0
    if p>=1:return 1.0
    return -math.expm1(hashes*math.log1p(-p))

def make_plan(network,address,bits,max_seconds=10,max_hashes=32768,max_blocks=1,
              rate=None,watts=None):
    profile=network_profile(network)
    script=payout_script(address,network)
    if not math.isfinite(max_seconds) or not 0<max_seconds<=60:raise ValueError("Runtime must be in (0,60] seconds")
    if type(max_hashes) is not int or not 1<=max_hashes<=10_000_000:raise ValueError("Hash bound must be in 1..10000000")
    if type(max_blocks) is not int or not 1<=max_blocks<=10:raise ValueError("Block bound must be in 1..10")
    target=target_from_compact(bits)
    if target>profile.pow_limit:raise ValueError("Target exceeds selected network's limit")
    illustrative = rate is None
    rate = 1_000_000 if illustrative else rate  # Explicit scenario, never a developer measurement.
    if not isinstance(rate,(int,float)) or not math.isfinite(rate) or rate<=0:raise ValueError("Invalid rate estimate")
    if watts is not None and (not isinstance(watts,(int,float)) or not math.isfinite(watts) or watts<0):
        raise ValueError("Invalid board-power estimate")
    budget=min(max_hashes,int(rate*max_seconds))
    p=(target+1)/(1<<256)
    chance=probability_at_least_one(budget,target)
    expected_hashes=1/p
    result={
        "mode":"PLAN_ONLY","network":network,"policy_execution_enabled":network=="regtest",
        "mainnet_validation_complete":MAINNET_VALIDATED,
        "mainnet_path_implemented":MAINNET_IMPLEMENTED,
        "address_format_valid":True,"address_ownership_checked":False,"payout_script":script.hex(),
        "bits":f"{bits:08x}","target":f"{target:064x}",
        "bounds":{"seconds":max_seconds,"hashes":max_hashes,"blocks":max_blocks,"max_batch":4096},
        "scenario_hashes_per_second":rate,"rate_source":"illustrative 1 MH/s scenario; not measured" if illustrative else "operator-supplied scenario","optimistic_hash_budget":budget,
        "chance_at_least_one_block":chance,"one_in_per_bounded_run":None if chance==0 else 1/chance,
        "mean_hashes_per_block":expected_hashes,
        "mean_hash_only_seconds_at_unchanged_rate_and_target":expected_hashes/rate,
        "mean_hash_only_years_at_unchanged_rate_and_target":expected_hashes/rate/31557600,
        "gpu_started":False,"rpc_called":False,
        "estimate_limits":"Short offline batch rate is optimistic for a full miner; RPC/build overhead, stale jobs, difficulty changes and device variability are excluded. These are statistical odds, not earnings forecasts."
    }
    if watts is not None:
        result.update(sampled_whole_board_watts=watts,
                      runtime_cap_energy_at_sampled_power_joules=watts*max_seconds,
                      runtime_cap_energy_at_sampled_power_Wh=watts*max_seconds/3600,
                      energy_limits="Scenario at measured whole-GPU-board power; not a guaranteed energy maximum, isolated miner energy, or whole-PC/wall power.")
    if network=="mainnet":result["execution_blockers"]=list(MAINNET_BLOCKERS)
    return result

def snapshot_bits(network):
    if network=="regtest":return 0x207fffff,"regtest reference target (runtime jobs must use fresh template)"
    return 0x1d00ffff,"illustrative difficulty-one target; not the current network target; use --target-bits for an operator scenario"

def main():
    parser=argparse.ArgumentParser(description="Offline startup odds/energy plan; never mines or connects to RPC")
    parser.add_argument("--network",choices=["regtest","mainnet"],default="mainnet")
    parser.add_argument("--address",required=True)
    parser.add_argument("--target-bits")
    parser.add_argument("--max-seconds",type=float,default=10)
    parser.add_argument("--max-hashes",type=int,default=32768)
    parser.add_argument("--max-blocks",type=int,default=1)
    args=parser.parse_args()
    bits,source=snapshot_bits(args.network)
    if args.target_bits is not None:bits=int(args.target_bits,16);source="explicit operator-supplied target snapshot"
    result=make_plan(args.network,args.address,bits,args.max_seconds,args.max_hashes,args.max_blocks)
    result["target_source"]=source
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
