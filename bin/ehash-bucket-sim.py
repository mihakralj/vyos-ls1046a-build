#!/usr/bin/env python3
"""ehash-bucket-sim.py <flows>...: bucket occupancy of the TRex ladder flow sets (stl/ask2_udp.py shapes) in ASK2's per-port ehash tables (offline model).
Key: fman_pcd.c ask_fe_build_key_dual (50 B): [0]=0x40, SIP@1, DIP@5, proto@41, sport@42, dport@44 (BE), rest 0.
Hash: fman_pcd_crc64 (reflected ECMA-182 poly 0xC96C5795D7870F42, seed ~0, no final xor); bucket = (crc >> 48) & 0x7FFF
(hash_shift 0, mask 0x7FFF: F-307 prediction, confirmed on silicon 2026-11-08)."""
import collections, sys
POLY = 0xC96C5795D7870F42
T = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ POLY if c & 1 else c >> 1
    T.append(c)
def crc64(b):
    c = 0xFFFFFFFFFFFFFFFF
    for x in b:
        c = T[(c ^ x) & 0xFF] ^ (c >> 8)
    return c
A, B = bytes([10, 99, 1, 112]), bytes([10, 99, 2, 113])
def key(sip, dip, sp, dp):
    k = bytearray(50); k[0] = 0x40; k[1:5] = sip; k[5:9] = dip; k[41] = 17
    k[42:44] = sp.to_bytes(2, "big"); k[44:46] = dp.to_bytes(2, "big"); return bytes(k)
def flows(n):
    if n <= 64511:
        return [(1025 + i, 5000) for i in range(n)]
    hi = n // 256
    return [(1025 + l, 10000 + h) for h in range(hi) for l in range(256)]
def occupancy(n):
    out = {}
    for name, mk in (("eth3 (fwd)", lambda s, d: key(A, B, s, d)), ("eth4 (rev)", lambda s, d: key(B, A, d, s))):
        cnt = collections.Counter((crc64(mk(s, d)) >> 48) & 0x7FFF for s, d in flows(n))
        h = collections.Counter(cnt.values()); used = len(cnt)
        ge4 = sum(v for k, v in h.items() if k >= 4)
        recs_ge4 = sum(k * v for k, v in h.items() if k >= 4)
        out[name] = (used, max(cnt.values()), dict(sorted(h.items())), ge4, recs_ge4)
    return out
def selftest():
    # silicon datum (F-307, 2026-11-08): these four keys share bucket 0x507f in fe_flow on .185
    dip = bytes([10, 99, 1, 185])
    for sp in (40001, 44516, 52296, 65005):
        assert (crc64(key(A, dip, sp, 51999)) >> 48) & 0x7FFF == 0x507F, sp
    print("ok")
if sys.argv[1:] == ["--test"]:
    selftest(); sys.exit()
for n in map(int, sys.argv[1:]):
    for t, (used, mx, h, ge4, r4) in occupancy(n).items():
        print(f"{n:>7} {t}: buckets used {used:>5}/32768  max chain {mx}  hist {h}  buckets>=4 {ge4}  records in them {r4}")
