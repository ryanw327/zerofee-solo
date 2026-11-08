# Third-party notices

Bech32/Bech32m checksum and address-validation algorithms are based on BIP173/BIP350 and
closely related to Pieter Wuille's reference implementation. Preserve
`LICENSES/bech32-MIT.txt` and `LICENSES/BIPs-BSD-2-Clause.txt` with source distributions.
The specifications and the separately licensed reference implementation have different
license declarations; both are retained rather than assigning one license to everything.

Bitcoin protocol/genesis fixtures and compact-target handling reference public Bitcoin
Core behavior. `LICENSES/Bitcoin-Core-MIT.txt` is retained conservatively. No Bitcoin Core
executable, wallet, chain data or copied source tree is bundled.

CPython/Tk, Windows, Bitcoin Core and NVIDIA's driver/toolkit utilities are external
dependencies. This project does not grant rights to redistribute them. The NVIDIA SDK
EULA governs toolkit components independently of the project MIT license.
