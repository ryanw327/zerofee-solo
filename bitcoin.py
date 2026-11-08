"""Independent CPU oracle and header fixtures; never accesses node wallets."""
import hashlib
import struct

MAX_TARGET = (1 << 256) - 1

def sha256d(header):
    return hashlib.sha256(hashlib.sha256(header).digest()).digest()

def with_nonce(header, nonce):
    if len(header) != 80 or type(nonce) is not int or not 0 <= nonce <= 0xffffffff:
        raise ValueError("Expected 80-byte header and uint32 nonce")
    return header[:76] + struct.pack("<I", nonce)

def target_from_compact(bits):
    if type(bits) is not int or not 0 <= bits <= 0xffffffff:
        raise ValueError("nBits must be uint32")
    size, word = bits >> 24, bits & 0x7fffff
    if size <= 3:
        word >>= 8 * (3-size)
        target = word
    else:
        target = word << (8 * (size-3))
    negative = bool(word and bits & 0x800000)
    overflow = bool(word and (size > 34 or (word > 0xff and size > 33) or (word > 0xffff and size > 32)))
    if negative or overflow or not 0 < target <= MAX_TARGET:
        raise ValueError("Negative, zero, or overflowing compact target")
    return target

CLASSIC_MERKLE = "4a5e1e4baab89f3a32518a88c31bc87f618f76673e2cc77ab2127b7afdeda33b"
# Values independently published in Bitcoin Core's network genesis parameters.
GENESIS = [
    ("mainnet",1231006505,2083236893,0x1d00ffff,CLASSIC_MERKLE,"000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f"),
    ("testnet3",1296688602,414098458,0x1d00ffff,CLASSIC_MERKLE,"000000000933ea01ad0ee984209779baaec3ced90fa3f408719526f8d77f4943"),
    ("regtest",1296688602,2,0x207fffff,CLASSIC_MERKLE,"0f9188f13cb7b2c71f2a335e3a4fc328bf5beb436012afca590b1a11466e2206"),
    ("signet",1598918400,52613770,0x1e0377ae,CLASSIC_MERKLE,"00000008819873e925422c1ff0f99f7cc9bbb232af63a077a480a3633bee1ef6"),
    ("testnet4",1714777860,393743547,0x1d00ffff,"7aa0a7ae1e223414cb807e40cd57e667b718e42aaf9306db9102fe28912b7b4e","00000000da84f2bafbbc53dee25a72ae507ff4914b867c565be350b0da8bf043"),
]

def known_headers():
    return [(name, struct.pack("<I",1)+bytes(32)+bytes.fromhex(merkle)[::-1]+struct.pack("<III",timestamp,bits,nonce), expected)
            for name,timestamp,nonce,bits,merkle,expected in GENESIS]
