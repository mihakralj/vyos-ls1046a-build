"""F-314 (2026-11-13): table-driven ehash CRC-64, computed outside fe_stats_lock.

Stage S3 of plans/ASK2-FLOW-SCALING-PLAN.md. A 30 s perf profile of a 65,536-flow
install on .185 (image 2026.11.13-0317) put 5.7 % of all CPU samples in the self
time of fman_pcd_fe_flow_get_stats_checked() and another 4.4 % in
queued_spin_lock_slowpath under it: the stats read computed the bucket CRC-64
bit-serially (8 shift/xor steps per key byte, 400 for the 50-byte routed key)
while holding pcd->fe_stats_lock, so every nf_flowtable stats poll held the lock
for the whole CRC and the other CPUs spun on it.

1. fman_pcd_crc64() becomes the byte-wise table form of the same reflected
   CRC-64/ECMA-182 (poly 0xC96C5795D7870F42, seed ~0, no final xor). That is the
   form of NXP's genuine crc64.h, which the bit-serial transcription was proven
   identical to; the 256-entry table is generated here from the poly, and
   bin/test-ehash-crc.py checks the generated C against the bit-serial
   reference, the RM self-test crc64("123456789") = 0x66A2364420E6C605 and the
   silicon bucket datum (F-307: four keys in bucket 0x507f). Inserts, deletes
   and the stats read all get the faster CRC.
2. fman_pcd_ehash_bucket_from_crc() takes the shift/mask step on its own;
   fman_pcd_ehash_bucket_index() keeps its signature and calls it.
3. get_stats_checked() computes the CRC before taking fe_stats_lock and only
   applies the table's shift and mask inside it.

No record, key, bucket or table layout change. Needs F-312.
Idempotent (marker); every anchor must match exactly once.
"""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
MARK = "F-314"
POLY = 0xC96C5795D7870F42


def table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ POLY if c & 1 else c >> 1
        t.append(c)
    return t


def table_c():
    rows = []
    t = table()
    for i in range(0, 256, 4):
        rows.append("\t" + " ".join(f"0x{v:016x}ULL," for v in t[i:i + 4]))
    return "\n".join(rows)


CRC_OLD = """\
static u64 fman_pcd_crc64(const u8 *p, size_t len)
{
\tu64 crc = ~0ULL;
\tsize_t i;
\tint b;

\tfor (i = 0; i < len; i++) {
\t\tcrc ^= (u64)p[i];
\t\tfor (b = 0; b < 8; b++)
\t\t\tcrc = (crc & 1ULL) ?
\t\t\t      (crc >> 1) ^ FMAN_EHASH_CRC64_POLY :
\t\t\t      (crc >> 1);
\t}
\treturn crc;
}
"""
CRC_NEW = """\
/* F-314: byte-wise table form (NXP crc64.h), generated from FMAN_EHASH_CRC64_POLY. */
static const u64 fman_pcd_crc64_tab[256] = {
%s
};

static u64 fman_pcd_crc64(const u8 *p, size_t len)
{
\tu64 crc = ~0ULL;
\tsize_t i;

\tfor (i = 0; i < len; i++)
\t\tcrc = fman_pcd_crc64_tab[(crc ^ p[i]) & 0xff] ^ (crc >> 8);
\treturn crc;
}
""" % table_c()

IDX_OLD = """\
static u16 fman_pcd_ehash_bucket_index(const u8 *key, u8 key_size,
\t\t\t\t       u8 hash_shift, u32 mask)
{
\tu64 crc = fman_pcd_crc64(key, key_size);

\tcrc >>= ((6 - (hash_shift & 0x7)) << 3);
\treturn (u16)crc & (u16)mask;
}
"""
IDX_NEW = """\
static u16 fman_pcd_ehash_bucket_from_crc(u64 crc, u8 hash_shift, u32 mask)
{
\tcrc >>= ((6 - (hash_shift & 0x7)) << 3);
\treturn (u16)crc & (u16)mask;
}

static u16 fman_pcd_ehash_bucket_index(const u8 *key, u8 key_size,
\t\t\t\t       u8 hash_shift, u32 mask)
{
\treturn fman_pcd_ehash_bucket_from_crc(fman_pcd_crc64(key, key_size),
\t\t\t\t\t      hash_shift, mask);
}
"""

GETTER = "static int fman_pcd_fe_flow_get_stats_checked(struct fman *fm, u8 hw_port_id,\n"
G_DECL_OLD = "\tint rc = -ENOENT;\n\n\tif (!pcd || !key || key_size == 0)\n\t\treturn -EINVAL;\n\n\tspin_lock(&pcd->fe_stats_lock);\t/* F-312 */\n"
G_DECL_NEW = ("\tint rc = -ENOENT;\n\tu64 crc;\n\n\tif (!pcd || !key || key_size == 0)\n\t\treturn -EINVAL;\n\n"
              "\t/* F-314: the CRC needs no lock; only the table's shift/mask does. */\n"
              "\tcrc = fman_pcd_crc64(key, key_size);\n\tspin_lock(&pcd->fe_stats_lock);\t/* F-312 */\n")
G_IDX_OLD = ("\t\thlist_for_each_entry(f, &t->bidx[fman_pcd_ehash_bucket_index(key,\n"
             "\t\t\t\t\tkey_size, t->hash_shift, t->hash_mask)], bnode) {\n")
G_IDX_NEW = ("\t\thlist_for_each_entry(f, &t->bidx[fman_pcd_ehash_bucket_from_crc(crc,\n"
             "\t\t\t\t\tt->hash_shift, t->hash_mask)], bnode) {\n")


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise ValueError(f"{label}: anchor found {n} times (expected 1)")
    return text.replace(old, new)


def main():
    try:
        src = open(PCD).read()
    except FileNotFoundError as exc:
        print(f"### F-314: FATAL: {exc}")
        return 1
    if MARK in src:
        print("### F-314: already applied")
        return 0
    try:
        if "F-312" not in src:
            raise ValueError("needs F-312 (fe_stats_lock)")
        if f"FMAN_EHASH_CRC64_POLY\t\t0x{POLY:X}ULL" not in src:
            raise ValueError("FMAN_EHASH_CRC64_POLY is not the expected reflected ECMA-182 poly")
        src = replace_once(src, CRC_OLD, CRC_NEW, "crc64")
        src = replace_once(src, IDX_OLD, IDX_NEW, "bucket_index")
        start = src.index(GETTER)
        end = src.index("\n}\n", start) + 3
        func = replace_once(src[start:end], G_DECL_OLD, G_DECL_NEW, "getter crc before lock")
        func = replace_once(func, G_IDX_OLD, G_IDX_NEW, "getter bucket from crc")
        src = src[:start] + func + src[end:]
    except ValueError as exc:
        print(f"### F-314: FATAL: {exc}")
        return 1
    with open(PCD, "w") as stream:
        stream.write(src)
    print("### F-314: table-driven ehash CRC-64; stats read hashes before taking fe_stats_lock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
