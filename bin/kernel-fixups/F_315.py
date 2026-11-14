"""F-315 (2026-11-14): batched ehash counter scan for ask.ko's stats cache.

nf_flowtable polls every hardware flow with FLOW_CLS_STATS once 10 % of its timeout has passed (every
3 s at the 30 s default; ~44k polls/s at 131,072 flows). Each poll hashed the 50-byte key, took
fe_stats_lock and memcmp'd the bucket's record keys in DMA memory before reading the counters; on
image 2026.11.14-0030 that was 10 % of CPU during a 65,536-flow install and most of the 20 % steady
hold CPU at 131,072 flows (plans/ASK2-FLOW-SCALING-PLAN.md S3/S4, step 8).

fman_pcd_fe_stats_scan() reports (owner token, packets, bytes) for every record of one owner class,
walking t->bidx bucket by bucket under fe_stats_lock: the same lock, the same record lifetime and the
same counter reads (dma_rmb, be64 at record+256/+264) as fman_pcd_fe_flow_get_stats_checked() (F-312
review 2026-11-13), read-only, no record, bucket, register or MURAM write. One call holds the lock for
at most @budget records (checked at bucket boundaries) and ASK_SCAN_MAX_BUCKETS buckets; the caller
resumes with *cursor = (table index << 16) | bucket. The cursor is re-checked against the table it now
names on every resume: tables differ in size, and a table freed between calls shifts the indices.
Independent design review 2026-11-14: safe with the resume bound check and a per-record hold cap.

Needs F-280 (owner), F-286 (owner_token), F-312 (fe_stats_lock). Idempotent (marker); every anchor
must match exactly once. Host check: bin/test-fe-stats-scan.py.
"""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
HDR = "include/linux/fsl/fman_pcd.h"
MARK = "F-315"

PCD_ANCHOR = "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_get_stats_identified);\n"
PCD_ADD = """
/* F-315: batched counter read for the ask.ko stats cache; see fman_pcd.h. */
#define FMAN_PCD_FE_SCAN_MAX_BUCKETS	4096	/* per lock hold, empty buckets included */
int fman_pcd_fe_stats_scan(struct fman *fm, u32 owner, u32 *cursor, u32 budget,
\t\t\t   fman_pcd_fe_stats_fn fn, void *arg)
{
\tstruct fman_pcd *pcd = fman_get_pcd(fm);
\tstruct fman_pcd_ehash_table *t;
\tstruct fman_pcd_ehash_flow *f;
\tu32 tbl, b, seen = 0, visited = 0;
\tint rc = 0;

\tif (!pcd || !cursor || !fn || !budget)
\t\treturn -EINVAL;
\towner &= FMAN_PCD_FE_OWNER_MASK;
\ttbl = *cursor >> 16;
\tb = *cursor & 0xffff;

\tspin_lock(&pcd->fe_stats_lock);\t/* F-312: what get_stats_checked() holds */
\tt = fman_pcd_ehash_table_by_index(pcd, tbl);
\twhile (t && seen < budget && visited < FMAN_PCD_FE_SCAN_MAX_BUCKETS) {
\t\t/* also a cursor left by a larger table that has since gone */
\t\tif (b > t->hash_mask) {
\t\t\tt = fman_pcd_ehash_table_by_index(pcd, ++tbl);
\t\t\tb = 0;
\t\t\tcontinue;
\t\t}
\t\thlist_for_each_entry(f, &t->bidx[b], bnode) {
\t\t\tconst u8 *r = f->record;

\t\t\tif (f->owner != owner || !f->owner_token)
\t\t\t\tcontinue;
\t\t\t/* Order the counter loads after the DMA writer's stores. */
\t\t\tdma_rmb();
\t\t\tfn(arg, f->owner_token,
\t\t\t   be64_to_cpu(*(const __be64 *)(r + 256)),
\t\t\t   be64_to_cpu(*(const __be64 *)(r + 264)));
\t\t\tseen++;
\t\t}
\t\tb++;
\t\tvisited++;
\t}
\tif (!t) {\t\t/* past the last table: the pass is complete */
\t\ttbl = 0;
\t\tb = 0;
\t\trc = 1;
\t}
\tspin_unlock(&pcd->fe_stats_lock);
\t*cursor = (tbl << 16) | b;
\treturn rc;
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_stats_scan);
"""

HDR_ANCHOR = ("int fman_pcd_fe_flow_get_stats_identified(struct fman *fm, u8 hw_port_id,\n"
              "\t\t\t\t\t  const u8 *key, u8 key_size,\n"
              "\t\t\t\t\t  u32 owner, u64 owner_token,\n"
              "\t\t\t\t\t  u64 *packets, u64 *bytes,\n"
              "\t\t\t\t\t  u32 *timestamp);\n")
HDR_ADD = """/* F-315: report (owner token, packets, bytes) of every record owned by @owner in up to @budget
 * records, resuming at *@cursor ((table index << 16) | bucket; start at 0). Returns 1 when the pass
 * wrapped (cursor back at 0), 0 when more remains, -EINVAL on bad arguments. @fn runs under the
 * driver's stats spinlock: it must not sleep and must not call back into the FMan PCD API. */
typedef void (*fman_pcd_fe_stats_fn)(void *arg, u64 owner_token, u64 packets, u64 bytes);
int fman_pcd_fe_stats_scan(struct fman *fm, u32 owner, u32 *cursor, u32 budget,
\t\t\t   fman_pcd_fe_stats_fn fn, void *arg);
"""


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise ValueError(f"{label}: anchor found {n} times (expected 1)")
    return text.replace(old, new)


def main():
    try:
        src = open(PCD).read()
        hdr = open(HDR).read()
    except FileNotFoundError as exc:
        print(f"### F-315: FATAL: {exc}")
        return 1
    if MARK in src and MARK in hdr:
        print("### F-315: already applied")
        return 0
    try:
        for need in ("F-312", "F-314"):
            if need not in src:
                raise ValueError(f"needs {need}")
        if "u64 owner_token;\t\t/* F-286" not in src or "u8 owner;\t\t\t/* F-280" not in src:
            raise ValueError("needs F-280 owner and F-286 owner_token in struct fman_pcd_ehash_flow")
        src = replace_once(src, PCD_ANCHOR, PCD_ANCHOR + PCD_ADD, "fman_pcd.c after get_stats_identified")
        hdr = replace_once(hdr, HDR_ANCHOR, HDR_ANCHOR + HDR_ADD, "fman_pcd.h after get_stats_identified")
    except ValueError as exc:
        print(f"### F-315: FATAL: {exc}")
        return 1
    with open(PCD, "w") as stream:
        stream.write(src)
    with open(HDR, "w") as stream:
        stream.write(hdr)
    print("### F-315: batched ehash counter scan (fman_pcd_fe_stats_scan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
