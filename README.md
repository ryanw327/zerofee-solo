# ZeroFee Solo

> **Known safety issue under repair: do not use this preview for mining yet.** Source review found that a stalled telemetry thread can prevent its own missing-reading timeout from running, and startup does not wait for the first valid temperature reading. These are identified control-flow gaps, not a claim of an observed hardware failure. An independent heartbeat watchdog and startup handshake are being developed and tested; no fixed release is available yet.

**Your node. Your GPU. Your whole block reward.**

Experimental Windows solo Bitcoin mining software for one NVIDIA GPU and your own
Bitcoin Core node. Zero developer fee: the application creates no developer reward
output and deducts no tip from your payout. Optional tips are separate, voluntary and
shown only on eligible block-win receipts. Electricity, hardware, node storage and network use still cost
money. There is no pool, cloud service, external reporting or promise of a reward.

**Use at your own risk.** Sustained GPU work can cause heat, instability, wear or damage
and increase electricity bills. Full pacing can heavily load the GPU; other presets may
use less. Pauses are not power caps. Core temperature and missing-reading stops are
best-effort safeguards and cannot guarantee protection. Hotspot temperature is not read.
The software is provided as-is, without warranty. Solo mining has extremely low odds of
finding a block; expect no reward. The first-launch acknowledgment records that you read
the notice; it does not eliminate liability or waive applicable rights.

This is an **experimental source preview**, not a production-ready release or installer.
Clean-machine setup and a disconnected private-chain winning-block test remain
unverified. Related private-build hardware results do not certify this exact source.
Source is available for inspection and development; no executable or installer is provided. The working name and
trademark availability have not been checked. See [RELEASE_STATUS.md](RELEASE_STATUS.md).

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

## Start from source

1. Put this source in a writable folder and install the prerequisites yourself.
2. Back up your node/wallet data through Bitcoin Core's normal procedures. Use your own
   synchronized mainnet node. Automatic management currently supports only Core's default
   `%APPDATA%\Bitcoin` data folder and the standard `C:\Program Files\Bitcoin` executables.
   Custom folders and unrelated Core arguments fail closed and require further work.
3. Open `launch_gui.pyw`, or run `python miner_gui.py`. Read the first-launch notice.
   Opening the app and acknowledging the notice never starts mining.
4. Enter and verify **your own** supported SegWit receiving address. The address starts
   blank; there is no bundled payout recipient. No private key or seed is needed.
5. Choose a duration and review the explicit start prompt. Automatic connection cleanly
   restarts Core into a protected, authenticated loopback-only RPC session, with wallets
   disabled during mining. It restores normal Core after Stop/completion/error when the
   submission outcome is known. Startup is outside the mining duration timer.

The app stops after one node-accepted block, the chosen duration, Stop, a thermal guard,
or an error. An uncertain submission retains the temporary session for review and blocks
another run; never clear the journal or blindly retry. Closing the window hides it to
the tray. Tray Exit requests Stop and waits for cleanup. Nothing mines automatically on
launch, reboot, acknowledgment, a saved setting, or a benchmark result.

## Advanced Tuning

The gear opens a separate modal, preserving the simple main window. Pacing presets are
Full (0 ms), Balanced (50 ms, default), Light (250 ms), and custom pauses from 0–5000 ms.
Batches range over 4096/16384/65536/131072/262144 hashes, with 262144 as default. Thread
choices are 64/128/256/512, checked against device and both compiled kernel limits; 128
is the default. The core cutoff defaults to 80°C, bounded to 60–90°C. Missing valid core
readings for 15 seconds requests Stop. Stop waits for an in-flight GPU call to return.

Save applies only to the next user-started session. Cancel discards the draft; Reset
defaults changes the draft until saved. Tuning locks during active work. Bounded test
mode retains its 4096-hash cap and no pacing. There are no overclock, voltage or fan
controls. Settings are stored locally beside the source.

The explicit synthetic benchmark compares predefined batch/thread pairs for a total
60-second wall budget, using the selected pause/cutoff. Unsupported thread counts are
skipped. Every shape checks GPU digests against CPU SHA-256d before measurement. It uses
no Core work, RPC, block submission or rewards, and never auto-applies a winner. Other
Python/miner processes conservatively block a sweep. Cancel is cooperative; compilation,
the current kernel and cleanup may finish after the budget. A short winning configuration
is only a starting point, not a sustained, thermally soaked result.

Read the risks before explicit headless operation:

```powershell
python tuning_benchmark.py --help
python tuning_benchmark.py --start --acknowledge-risk --pause-ms 50 --temperature-limit-c 80
```

## Results and privacy

Node acceptance is distinct from confirmations, network permanence and spendable reward.
Saved receipts show the snapshot at acceptance. View wins reopens them without a fresh-win
sound, automatic mining or a fresh chain query. Optional tip details use a maintainer-approved static Bitcoin address and QR,
with a copy button. Tips are separate, voluntary payments chosen by the winner. There is no automatic tip, coinbase deduction, wallet call, analytics or
share-your-win transmission.

Runtime files may contain your public payout address, local paths, process metadata and
receipts. Temporary RPC credentials are confined to the protected session and client
memory. Do not upload runtime files or node data. The publication allowlist, ignore rules
and audit exclude logs, settings, cookies, journals, receipts, databases and binaries.

## Development checks

```powershell
python -B verify_offline.py
python -B audit_release.py
python -B verify_transport_wire.py
```

With an existing C++ compiler, `verify_kernel_cpu.py --compiler <compiler-path>`
compiles the actual CUDA source through a CPU-only indexing shim in three modes.
It checks digest/target/compact-output logic against `hashlib`, including padded
thread shapes and nonce wrap. It loads no CUDA libraries and does not validate
GPU compilation, parallel atomics, device execution or thermals. Native build
artifacts stay in a separate temporary directory and must never be published.

The smallest next hardware check is `python verify_gpu_vectors.py --help`, followed
only when idle and explicitly authorized by `--start --acknowledge-risk`. It verifies
CUDA/telemetry PCI identity, uses the 80°C/missing-reading guard, limits work to small
synthetic vectors (at most 1,280 logical hashes), tests all supported thread shapes
and compact overflow, and has a 30-second cooperative budget. No Core, block submission
or rate benchmark is involved. Setup/current calls cannot be interrupted mid-call.
A related private installation passed the diagnostic (52 cases, 1,280 CPU comparisons),
with cancellation cleanup reported at 14 ms, and a 60.04-second synthetic sweep peaking
at 49°C. These are reported development results, not independently reproduced checks
of this exact sanitized revision. They do not establish thermal-soak safety or block acceptance.

The offline harness forbids GPU library loads, process launches and network access.
It uses hidden Tk windows, mocked devices/Core and synthetic/public protocol fixtures.
`verify_transport_wire.py` separately runs the fake loopback transport and wire-fault tests,
  with a guard allowing only ephemeral synthetic IPv4 loopback connections. It is excluded from
that strict offline run. Clean-machine and hardware checks are still pending; passing CPU
tests does not verify GPU execution. The CLI planning target/rate are explicitly labelled
illustrative scenarios, never a live difficulty quote or a measured performance claim.

## License and provenance

Original project contributions are published under MIT at the owner's direction.
The provenance review is an attribution/source inspection, not proof of originality. Third-party portions retain their own
notices in `LICENSES/`. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[PROVENANCE.md](PROVENANCE.md). This project is not endorsed by Bitcoin Core or NVIDIA.
