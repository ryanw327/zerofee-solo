# Dependency and provenance review

Reviewed 2026-11-07. This is a source inspection and attribution review, not proof of
originality or a legal opinion. The maintainer selected MIT publication of original contributions; this review does not establish exhaustive ownership history.

| Component | Source/provenance evidence | Distribution treatment |
| --- | --- | --- |
| Python application and tests | Locally authored source; stdlib-only imports inspected | Project MIT at maintainer direction |
| SHA-256d CUDA kernel | Source header identifies independent implementation from FIPS 180-4 and Bitcoin header format; no vendor includes or sample headers | Local source only; no CUDA toolkit code/binaries included |
| Bech32/Bech32m address checks | Algorithm closely follows BIP173/BIP350 and reference checksum/bit conversion | Retain Pieter Wuille MIT notice and BIP BSD-2-Clause terms conservatively |
| Genesis/protocol test values | Public network constants and serialized published genesis block | Retain Bitcoin Core MIT notice conservatively; no Core binary/source tree bundled |
| Pickaxe icon | Reproducible `make_icon.py` contains local vector shapes and stdlib encoder | Local generated asset; retained under project MIT |
| CPython, stdlib, Tk | External interpreter installation | Not bundled; Python/Tcl/Tk distributions retain their vendor licenses |
| NVIDIA driver, NVRTC, builtins, nvidia-smi | Externally installed NVIDIA dependencies | Not bundled or relicensed; NVIDIA EULA applies separately |
| Bitcoin Core/bitcoin-cli | External default Windows installation | Not bundled; Bitcoin Core MIT license applies separately |
| Windows APIs | ctypes calls to operating-system DLLs | No Windows DLLs/headers bundled |

Primary references checked:

- [BIP173](https://github.com/bitcoin/bips/blob/master/bip-0173.mediawiki): BSD-2-Clause specification and public address example used only in tests.
- [BIP350](https://github.com/bitcoin/bips/blob/master/bip-0350.mediawiki): BSD-2-Clause specification.
- [Bech32 reference source](https://github.com/sipa/bech32/blob/master/ref/python/segwit_addr.py): copyright 2017, 2020 Pieter Wuille, MIT terms.
- [Bitcoin Core COPYING](https://github.com/bitcoin/bitcoin/blob/master/COPYING): MIT terms, retained in `LICENSES/Bitcoin-Core-MIT.txt`.
- [NIST FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/upd1/final): SHA-256 standard; no NIST document is bundled.
- [NVIDIA CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html): SDK remains separately licensed; no redistribution grant is assumed for toolkit files.
- [Python legal statements](https://www.python.org/about/legal/): interpreter and standard library are separately licensed.

No downloaded mining library, bundled third-party Python package, proprietary CUDA
distribution, compiled cubin/PTX or executable is included. The source comment asserting
independent kernel authorship is evidence, not an exhaustive historical code comparison.
MIT is compatible with retaining these permissive-source notices; it does not relicense
external proprietary dependencies. Any later binary release requires a fresh dependency,
redistribution and license audit.

The retained Bech32 MIT notice matches the exact fetched reference-file header after
removing Python comment prefixes. The retained Bitcoin Core notice matches upstream
COPYING, including its Git blob identity. `LICENSES/upstream-verification.json` records
the upstream blob IDs and canonical notice hashes; offline regression tests pin them.
The BIP attribution records the actual Authors/license declarations and does not infer
copyright years that those upstream documents do not state. Its BSD conditions are the
standard BSD-2-Clause text, not a claim that an invented copyright line came upstream.

`kernel_cpu_shim.cpp` and its runner are local test code, using only primitive C++ types
and the local kernel source. No CUDA headers, compiler/runtime binaries or other vendor
source are bundled. An existing C++ compiler is an optional external test dependency.
