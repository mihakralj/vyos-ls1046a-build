#!/usr/bin/env python3
"""test-ehash-crc.py <kernel-source-dir>: the generated fman_pcd.c ehash CRC-64 and bucket index match the silicon.

Extracts fman_pcd_crc64() (and its F-314 table, if present) plus the bucket helpers from the generated fman_pcd.c,
compiles them on the host and compares with an independent bit-serial reflected CRC-64/ECMA-182 (poly
0xC96C5795D7870F42, seed ~0, no final xor) over random keys of the live key sizes (16, 38, 50), the RM self-test
crc64("123456789") = 0x66A2364420E6C605, and the silicon datum (F-307, .185 2026-11-08: four routed keys in bucket
0x507f with the per-port table's shift 0 / mask 0x7fff). Also checks that the stats read hashes outside fe_stats_lock
once F-314 is applied. Works on trees with or without F-314.
"""
import os, random, re, subprocess, sys, tempfile

POLY = 0xC96C5795D7870F42


def ref(b):
    c = 0xFFFFFFFFFFFFFFFF
    for x in b:
        c ^= x
        for _ in range(8):
            c = (c >> 1) ^ POLY if c & 1 else c >> 1
    return c


def extract(src):
    parts = ["#include <stdint.h>\n#include <stddef.h>\n#include <stdio.h>\n#include <string.h>\n",
             "typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint64_t u64;\n",
             "#define FMAN_EHASH_CRC64_POLY 0xC96C5795D7870F42ULL\n"]
    if "fman_pcd_crc64_tab[256]" in src:
        i = src.index("static const u64 fman_pcd_crc64_tab[256]")
        parts.append(src[i:src.index("};\n", i) + 3])
    for head in ("static u64 fman_pcd_crc64(const u8 *p, size_t len)",
                 "static u16 fman_pcd_ehash_bucket_from_crc(", "static u16 fman_pcd_ehash_bucket_index("):
        if head in src:
            i = src.index(head)
            parts.append(src[i:src.index("\n}\n", i) + 3])
    parts.append("""
int main(void) {
    char line[512]; u8 k[256];
    while (fgets(line, sizeof line, stdin)) {
        size_t n = strlen(line) / 2, i;
        for (i = 0; i < n; i++) { unsigned v; sscanf(line + 2 * i, "%2x", &v); k[i] = (u8)v; }
        printf("%016llx %04x\\n", (unsigned long long)fman_pcd_crc64(k, n),
               (unsigned)fman_pcd_ehash_bucket_index(k, (u8)n, 0, 0x7fff));
    }
    return 0;
}
""")
    return "".join(parts)


def routed_key(sip, dip, sport, dport):
    k = bytearray(50); k[0] = 0x40; k[1:5] = bytes(sip); k[5:9] = bytes(dip); k[41] = 17
    k[42:44] = sport.to_bytes(2, "big"); k[44:46] = dport.to_bytes(2, "big")
    return bytes(k)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src = open(os.path.join(sys.argv[1], "drivers/net/ethernet/freescale/fman/fman_pcd.c")).read()
    errors = []
    rnd = random.Random(314)
    keys = [b"123456789"] + [routed_key((10, 99, 1, 112), (10, 99, 1, 185), sp, 51999) for sp in (40001, 44516, 52296, 65005)]
    keys += [bytes(rnd.getrandbits(8) for _ in range(n)) for n in (16, 38, 50) for _ in range(20000 // 3)]
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "crc.c"); exe = os.path.join(d, "crc")
        open(c, "w").write(extract(src))
        subprocess.run(["cc", "-O2", "-Wall", "-Werror", "-Wno-unused-function", "-o", exe, c], check=True)
        out = subprocess.run([exe], input="".join(k.hex() + "\n" for k in keys), capture_output=True, text=True,
                             check=True).stdout.split("\n")
    for k, line in zip(keys, out):
        got_crc, got_idx = (int(x, 16) for x in line.split())
        want = ref(k)
        if got_crc != want or got_idx != (want >> 48) & 0x7FFF:
            errors.append(f"key {k.hex()[:24]}..({len(k)} B): crc {got_crc:016x} bucket {got_idx:#x}, want {want:016x}")
            if len(errors) > 5:
                break
    if ref(b"123456789") != 0x66A2364420E6C605:
        errors.append("reference CRC self-test failed")
    if any(int(l.split()[1], 16) != 0x507F for l in out[1:5]):
        errors.append("silicon datum: the four F-307 keys do not land in bucket 0x507f")
    if "F-314" in src:
        g = src[src.index("static int fman_pcd_fe_flow_get_stats_checked("):]
        g = g[:g.index("\n}\n")]
        locked = g[g.index("spin_lock(&pcd->fe_stats_lock)"):]
        if re.search(r"fman_pcd_crc64\(|fman_pcd_ehash_bucket_index\(", locked):
            errors.append("get_stats_checked still hashes under fe_stats_lock")
    for e in errors:
        print(f"### test-ehash-crc: FAIL: {e}")
    if errors:
        return 1
    print(f"### test-ehash-crc: OK ({len(keys)} keys match the bit-serial reference, RM self-test, silicon bucket 0x507f"
          f"{'; stats read hashes outside fe_stats_lock' if 'F-314' in src else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
