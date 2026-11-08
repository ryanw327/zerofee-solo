# Bech32/Bech32m checksum follows BIP173/BIP350 and Pieter Wuille's reference algorithm.
# See LICENSES/bech32-MIT.txt and LICENSES/BIPs-BSD-2-Clause.txt.
"""SegWit payout decoding from BIP173/BIP350 with explicit network HRP."""
from network_policy import network_profile
CHARSET="qpzry9x8gf2tvdw0s3jn54khce6mua7l"
GEN=(0x3b6a57b2,0x26508e6d,0x1ea119fa,0x3d4233dd,0x2a1462b3)
def payout_script(address,network="regtest"):
    profile=network_profile(network)
    if not isinstance(address,str) or len(address)>90 or any(not 33<=ord(c)<=126 for c in address):
        raise ValueError("Invalid SegWit address")
    if address!=address.lower() and address!=address.upper():
        raise ValueError("Mixed-case address")
    a=address.lower(); pos=a.rfind("1")
    if pos<1 or pos+7>len(a) or a[:pos]!=profile.hrp:
        raise ValueError("Address does not match the selected network")
    try: data=[CHARSET.index(c) for c in a[pos+1:]]
    except ValueError: raise ValueError("Invalid address alphabet")
    chk=1; hrp=a[:pos]
    for v in [ord(c)>>5 for c in hrp]+[0]+[ord(c)&31 for c in hrp]+data:
        top=chk>>25;chk=((chk&0x1ffffff)<<5)^v
        for i,g in enumerate(GEN):
            if (top>>i)&1: chk^=g
    payload=data[:-6]
    if not payload or payload[0]>16: raise ValueError("Invalid witness version")
    version=payload[0];acc=bits=0;program=bytearray()
    for v in payload[1:]:
        acc=(acc<<5)|v;bits+=5
        while bits>=8:
            bits-=8;program.append((acc>>bits)&255)
    if bits>=5 or ((acc<<(8-bits))&255) or not 2<=len(program)<=40:
        raise ValueError("Invalid witness program/padding")
    if version==0 and len(program) not in (20,32): raise ValueError("Invalid v0 length")
    if chk!=(1 if version==0 else 0x2bc830a3): raise ValueError("Invalid checksum variant")
    return bytes([0 if version==0 else 0x50+version,len(program)])+bytes(program)
