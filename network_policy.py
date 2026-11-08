"""Explicit network profiles. Live execution needs fresh scoped authorization."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Network:
    name:str
    hrp:str
    halving_interval:int
    pow_limit:int
    genesis:str

NETWORKS={
 "regtest":Network("regtest","bcrt",150,(1<<255)-1,"0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206"),
 "mainnet":Network("mainnet","bc",210000,int("00000000"+"f"*56,16),"000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f")
}
MAINNET_VALIDATED=False  # This public source candidate still needs hardware and clean-machine validation.
MAINNET_IMPLEMENTED=True
MAINNET_BLOCKERS=(
 "Activation requires separate authorization, explicit mining/submission opt-ins and a verified temporary localhost session.",
 "This source candidate lacks fresh GPU/clean-machine validation. Actual mainnet winning-block acceptance and propagation remain untested."
)
def network_profile(name):
    try:return NETWORKS[name]
    except KeyError:raise ValueError("Unsupported network")
def subsidy(height,network="regtest"):
    if type(height) is not int or height<0:raise ValueError("Invalid height")
    halvings=height//network_profile(network).halving_interval
    return (50*100_000_000)>>halvings if halvings<64 else 0
def authorize_execution(network,opt_in=False,submit=False):
    network_profile(network)
    if network=="mainnet":
        if not opt_in or not submit:raise RuntimeError("Mainnet requires --mainnet-opt-in and --submit-blocks, plus separate activation approval.")
    elif opt_in or submit:raise ValueError("Live activation flags require --network mainnet")
