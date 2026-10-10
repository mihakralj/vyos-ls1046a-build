"""Which classifier bucket a flow's key lands in, so a test can crowd one.

The SDK picks an external hash table's bucket from a reflected CRC-64/ECMA
of the key (get_indexed_hash_bucket(), crc64.h): seeded with all ones, no
final xor, the top 16 bits shifted by the table's hashshift and masked. The
CRC is affine, so whether two keys of one length share a bucket depends on
their difference alone: the ports chosen here collide whatever the port id
and addresses around them.

CDX's native IPv4 TCP/UDP key (insert_entry_in_classif_table_encap(),
cdx_ehash.c) is 56 bytes: the logical port id, addresses, ports, protocol,
34 zero bytes for outer tunnel checks and eight for the PPPoE identity.
The protocol tables have 32768 buckets and hashshift 0.
"""
from __future__ import annotations

POLY = 0xC96C5795D7870F42
TABLE = []
for _byte in range(256):
    _crc = _byte
    for _ in range(8):
        _crc = (_crc >> 1) ^ POLY if _crc & 1 else _crc >> 1
    TABLE.append(_crc)

IPV4_MASK = 0x7FFF


def crc64(data: bytes) -> int:
    crc = 0xFFFFFFFFFFFFFFFF
    for byte in data:
        crc = TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc


def bucket(key: bytes, mask: int = IPV4_MASK, shift: int = 0) -> int:
    return (crc64(key) >> ((6 - shift) * 8)) & mask


def ipv4_key(portid: int, source: bytes, destination: bytes, proto: int, sport: int, dport: int) -> bytes:
    return (bytes([portid]) + source + destination
            + sport.to_bytes(2, "big") + dport.to_bytes(2, "big")
            + bytes([proto]) + bytes(42))


def _ports_bucket(sport: int, dport: int) -> int:
    return bucket(ipv4_key(0, bytes(4), bytes(4), 17, sport, dport))


def crowded_ipv4(dports: list[int], low: int = 1024, high: int = 32768) -> list[tuple[int, int]]:
    """One source port per destination port, all of the IPv4 keys sharing a
    bucket in one direction's table, every source port in [low, high): below
    the usual ephemeral range, so binding them collides with nothing."""
    zero = _ports_bucket(0, 0)
    image = {}
    for offset in range(1 << 16):
        image.setdefault(_ports_bucket(offset, 0) ^ zero, []).append(offset)
    # The source port offsets that cancel each destination port's change.
    offsets = [image[_ports_bucket(0, d) ^ _ports_bucket(0, dports[0])] for d in dports]
    for first in range(low, high):
        for choice in range(1 << len(dports)):
            sports = [first ^ options[((choice >> i) & 1) % len(options)]
                      for i, options in enumerate(offsets)]
            if all(low <= s < high for s in sports) and len(set(sports)) == len(sports):
                pairs = list(zip(sports, dports))
                assert len({_ports_bucket(s, d) for s, d in pairs}) == 1, pairs
                return pairs
    raise AssertionError(("no crowded source ports in range", dports, low, high))
