# Experimental source preview: status and remaining validation

This directory is the sanitized experimental source preview, published at the
maintainer's request. It is not an installer or a production-ready release. Do not upload the working miner folder, a parent directory, previous Git
history, runtime files, logs or node/wallet data.

Remaining validation before claiming a supported production release:

1. The maintainer selected MIT publication of original contributions. Retain the
   third-party notices and review attribution for subsequent changes.
   ZeroFee Solo is a working name; name/trademark availability is unverified.
2. Complete a clean Windows machine setup using the documented Python/Tk, default Core
   paths and CUDA 13.4 DLL layout. Validate missing config, unsupported custom folders,
   first launch, recovery, writable state paths and tray behavior. These release defaults
   differ from the private development installation and have only mock/CPU validation.
   Mining, benchmarking and the diagnostic now fail closed unless PCI identities for
   the CUDA context device and telemetry GPU 0 match before kernel dispatch. Validate
   this binding on hardware; systems with multiple GPUs still have no coordinated
   selection, partitioning or monitoring support.
3. With explicit hardware-test authorization and no concurrent miner, verify each supported
   thread count against independent CPU digests, target boundaries, nonce boundaries and
   compact-output overflow. Then verify an actual GPU-found block on a disconnected
   private regtest chain, including full block assembly, proposal and submission results.
   Never use the operator's live mainnet node for this check.
   Start with the provided `verify_gpu_vectors.py` diagnostic: it checks PCI identity,
   thermal/cancel gates and at most 1,280 logical synthetic hashes within a 30-second
   cooperative budget. Passing it is the first hardware step, not completion of this gate.
4. Exercise benchmark cancellation, hot/missing sensor stops and resource cleanup on real
   hardware. Run a separately approved sustained trial to assess thermal soak. A 60-second
   sweep is insufficient for a sustained-performance claim. Do not advertise unmeasured MH/s.
5. Re-run offline tests and the publication audit. Use only `publication-allowlist.json`
   and verify the final release manifest hashes. Audit the prospective Git index/history,
   including any later changes; `.gitignore` alone cannot protect already tracked data.
6. Public repository: https://github.com/ryanw327/zerofee-solo. The initial README-only
   commits are followed by this allowlisted source snapshot. No binaries, installer,
   private development history or runtime artifacts are part of this publication.
   Experimental source availability does not mean the outstanding tests passed.

Actual mainnet winning-block acceptance, network propagation and reward maturity have
not been demonstrated by this candidate. Documentation and UI must keep node acceptance
separate from confirmations, permanence and spendability. These limitations must remain
visible in this explicitly experimental preview.

Post-submit snapshot hardening is CPU-tested: after a successful `submitblock`, a missing,
non-integer or out-of-range confirmation count leaves the journal at `ACCEPTED` and the
outcome uncertain, rather than claiming confirmation or automatically submitting again.
Integer zero and -1 remain inactive snapshots; positive integers indicate active-chain
status at that observation. This does not establish permanence, reward maturity, actual
GPU execution or real-node winning-block integration. Current guarded test evidence is
included in `validation/offline-tests.json` and the release manifest.

Malformed `submitblock` result values are also CPU-tested: null means acceptance,
while definite nonempty rejection strings settle a rejection. BIP22 `inconclusive` and
`duplicate-inconclusive` replies retain durable `PENDING`, as do malformed values.
Unresolved submissions skip confirmation lookup and block journal reopening until the
operator resolves the outcome. This adds no automatic retry or real-node validation.

Physical-device identity checks have mocked coverage for normalized matches, mismatches,
missing/malformed PCI identities, failed queries, cancellation and resource cleanup.
The telemetry query has a two-second timeout. These CPU checks establish control flow,
not real driver identity accuracy, GPU execution or thermal protection guarantees.

HTTP response framing now fails closed when declared Content-Length bytes remain after
read completion. Guarded mock-envelope and real synthetic-loopback checks cover early
EOF, malformed chunks, valid chunked null results, deadlines, cancellation, connection
reuse and no-retry behavior. They never use Core or real block submission.

## Isolated release-preparation update

The GUI now additionally displays “Solo mining • No pool fees”; the electricity and
low-odds warning remains visible. This change was recreated in a separate source copy,
not applied to the active private installation. It needs Windows visual verification.
No mining behavior, configuration or live node state was changed.

Development reports for a related private build record 52 GPU diagnostic cases and
1,280 CPU comparisons, 14 ms cancellation cleanup, and a 60.04-second synthetic sweep
peaking at 49°C. These results have not been independently reproduced on this exact
sanitized revision. They partially address hardware feasibility, not clean-machine
compatibility, sustained thermal soak, hot/missing sensor fault injection, or a
GPU-found regtest block's complete assembly/proposal/submission. None of those remaining
gates is waived by normal live mining or a displayed hash rate.

The existing validation/offline-tests.json is historical evidence retained from the
base candidate. New preparation-run reports are separate and must not be confused with
that original full-suite result.

Preparation validation in the isolated Linux workspace: ten portable audit/license/UI-
label/tip-policy tests passed; the 90-file allowlist/pattern/syntax audit had no findings.
The full offline suite could not start because Windows ctypes.WinDLL is unavailable on
Linux. No claim of a new full-suite pass or visual UI verification is made. Re-run the
unchanged full Windows harness and visually check the added label before integrating.

The optional block-win tip disclosure uses the requested “buying Ryan a coffee”
wording. The maintainer supplied a new public Bitcoin receiving address, now configured
only as the developer-tip recipient, never a miner payout default. Its mainnet Bech32
checksum and uppercase/lowercase script equivalence were checked. A 205x205 PNG was
generated using qrcode 8.2 and independently decoded using zxing-cpp 3.1.1; the decoded
bitcoin: payload exactly matched the canonical lowercase recipient. The PNG digest is
pinned in ApprovedTip. No QR-generation dependencies are needed at runtime.

Ten portable tests passed, including exact-recipient copy behavior using a fake clipboard,
win eligibility and the retained no-deduction text. This is not a real Windows clipboard
or visible-widget check; full Windows UI tests remain pending. The audit permits this
specific public recipient only in tip_support.py and its explicit audit rule, not mining
configuration, coinbase code, or arbitrary files. No software was installed on the
operator's PC and no funds were sent. Publication changes only the public repository.
