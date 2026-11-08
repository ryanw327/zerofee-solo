# ZeroFee Solo

Experimental Windows NVIDIA GPU solo Bitcoin miner for your own local Bitcoin Core node. **No pool fees.**

**Source release preparation is in progress. This repository currently contains documentation only, not downloadable miner source, an installer, or a binary release.** The prerequisites below describe the current candidate, not a promise of broad hardware compatibility.

## Prerequisites and current compatibility

- **Windows x64**, with **CPython 3.13 including Tk** for the GUI. The candidate uses Windows-specific APIs.
- **Bitcoin Core 32.1**, installed locally on the **same computer as the miner**. The preflight currently pins the Core version to `320100`; other versions are not supported yet.
- A **fully synchronized, non-pruned Bitcoin mainnet node**, with networking enabled and at least one peer. Remote nodes, Bitcoin Knots, and pruned-node configurations are not supported yet.
- **One NVIDIA CUDA-capable GPU**, a compatible NVIDIA driver, and **CUDA Toolkit/NVRTC 13.4**. The loader is currently pinned to `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4\bin\x64`, including `nvrtc64_130_0.dll` and `nvrtc-builtins64_134.dll`.
- Working NVIDIA telemetry for the selected GPU. The candidate uses CUDA device 0 and checks that its PCI identity matches telemetry GPU 0 before dispatch. It requires core-temperature readings.
- Your **own valid Bitcoin mainnet SegWit receiving address**. The miner needs no private key or recovery phrase. Keep those private.
- Sufficient storage for a full, non-pruned node, stable power/cooling, and network access for Bitcoin Core.

**GPU minimum:** no independently validated minimum GPU model, compute capability, or VRAM size has been established. The source queries the detected GPU's compute capability and compiles for that architecture; that is not a guarantee that every NVIDIA CUDA GPU works with the pinned toolkit. Do not buy hardware based on an unverified compatibility claim.

There is no AMD/Intel GPU, macOS/Linux, Stratum-pool, external ASIC, or coordinated multi-GPU support in this candidate. The sanitized candidate's automatic Core management currently targets the standard Windows Core executable location and default data directory; custom data/block directory layouts are not yet a supported public setup.

## What “no pool fees” means

The miner works directly with your own node, not a pool. It creates no developer reward output and deducts no developer tip from the miner's payout. Any optional “Buy Ryan a coffee” tip on an eligible block-win screen is a separate, voluntary payment chosen by the winner.

Electricity, hardware, storage, and network use still cost money. Solo mining has extremely low odds of finding a block; there is no promise of a reward.

## Experimental status and safety

Clean-machine Windows setup and complete GPU-found private-regtest block validation remain release gates. A related development build running successfully does not establish compatibility or safety for every system.

GPU work can cause heat, instability, wear, and higher electricity bills. Temperature stops are best-effort safeguards, not guarantees; hotspot temperature is not monitored. Node acceptance is not the same as reward maturity or spendability.

No Python, NVIDIA, Windows, or Bitcoin Core binaries are bundled. Install prerequisites only from their official vendors under their respective licenses.
