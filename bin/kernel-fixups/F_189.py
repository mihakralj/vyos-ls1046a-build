"""F-189: EHASH-DUAL-FIX Phase 1 — stats-enabled flow insert + bucket pad readback.

CONTEXT (2026-08-14, plans/EHASH-DUAL-FIX-VERIFICATION-PLAN.md Phase 1.1):

Phase 0 (2026-08-07) settled the ehash DDR format as bit-exact correct
(Verdict A) — no bucket/record rewrite needed. The remaining investigative
instrument is the en_ehash_entry second-union-view hardware-writeback stats
(packet_count@+256, packet_bytes@+264, timestamp@+272), gated by
SET_STATS_ENABLE (bit 12, 0x1000) / SET_TIMESTAMP_ENABLE (bit 13, 0x2000)
on the entry flags word. F-182 deliberately cleared those bits because the
then-256B record had no stats area; F-176 has since bumped the allocation
to 320B (FMAN_EHASH_FLOW_REC_SIZE = MAX_EN_EHASH_EXT_ENTRY_SIZE) and
fe_ehash_stats already reads back +256/+264/+272. What is still missing is
a way to REQUEST the stats flags at insert time via fe_flow.

This fixup adds the `add stats` fe_flow syntax, threads a stats flag through
fman_pcd_ehash_add_key(), and extends fe_ehash_stats with the live bucket
pad word (en_exthash_bucket.u64 pad) for Phase 3.2's raw-bucket byte-level
readback. The counters give the first dispatch/FQID-independent "did
hardware even compare this entry" discriminator — one board session can now
answer whether the remaining problem is the compare stage or dispatch/ENQ.

F-189 (6 blocks, fman_pcd.c). Anchored on the exact post-F-188 derived
state. Idempotent ("F-189:" markers). CI-only build.
"""

import sys

changes = 0


def edit(path, blocks):
    """blocks: list of (name, marker, old, new). The marker string MUST
    appear in new -- it is the per-block idempotency token."""
    global changes
    with open(path) as f:
        src = f.read()
    file_changes = 0
    for name, marker, old, new in blocks:
        if marker not in new:
            print(f"### F-189: FATAL: block '{name}' marker {marker} not "
                  "embedded in its replacement text -- fixup bug.")
            sys.exit(1)
        if marker in src:
            print(f"### F-189: {name} already applied")
            continue
        if old not in src:
            print(f"### F-189: FATAL: '{name}' text not found verbatim in "
                  f"{path} -- source drifted. Refusing to guess.")
            sys.exit(1)
        src = src.replace(old, new, 1)
        file_changes += 1
        changes += 1
        print(f"### {path}: F-189 {name} applied")
    if file_changes:
        with open(path, "w") as f:
            f.write(src)


pcd_blocks = [
    ('add_key signature stats param',
     'F-189(add-key-stats-param)',
     "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
     "\t\t\t\t  const u8 *key, u8 key_size,\n"
     "\t\t\t\t  u32 enq_off, u32 fqid)\n",
     "/* F-189(add-key-stats-param): stats flag requests the 320B extended\n"
     " * entry view (packet_count/packet_bytes/timestamp writeback at +256).\n"
     " * Production callers pass false -- stats are an investigative-only\n"
     " * instrument (EHASH-DUAL-FIX Phase 1.1), never on the genl path.\n"
     " */\n"
     "static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n"
     "\t\t\t\t  const u8 *key, u8 key_size,\n"
     "\t\t\t\t  u32 enq_off, u32 fqid, bool stats)\n"),
    ('flags stats bits',
     'F-189(stats-flags)',
     "\t\tu16 flags = 0;\t/* F-182: no STATS_EN (256B record) */\n",
     "\t\t/* F-189(stats-flags): SET_STATS_ENABLE (bit 12, 0x1000) +\n"
     "\t\t * SET_TIMESTAMP_ENABLE (bit 13, 0x2000) per en_ehash_entry's\n"
     "\t\t * second union view (plan Phase 1.1). F-182 cleared these for\n"
     "\t\t * the 256B record; the alloc is 320B since F-176, so the\n"
     "\t\t * hardware-writeback stats area exists and the fe_ehash_stats\n"
     "\t\t * readback (+256/+264/+272) is valid when requested here.\n"
     "\t\t */\n"
     "\t\tu16 flags = stats ? 0x3000 : 0;\n"),
    ('fe_flow stats parse',
     'F-189(fe-flow-stats-parse)',
     "\tnf = sscanf(buf, \"add %u %113s %lx\", &tbl_idx, keytok, &fqid);\n"
     "\tif (nf < 2) {\n",
     "\t/* F-189(fe-flow-stats-parse): optional 'add stats' form arms the\n"
     "\t * hardware-writeback counters on the inserted entry.\n"
     "\t */\n"
     "\tif (!strncmp(buf, \"add stats \", 10)) {\n"
     "\t\tstats = true;\n"
     "\t\tnf = sscanf(buf + 10, \"%u %113s %lx\", &tbl_idx, keytok, &fqid);\n"
     "\t} else {\n"
     "\t\tnf = sscanf(buf, \"add %u %113s %lx\", &tbl_idx, keytok, &fqid);\n"
     "\t}\n"
     "\tif (nf < 2) {\n"),
    ('fe_flow declarations',
     'F-189(stats-decl)',
     "\tu8 key_size = 0;\n"
     "\tint nf, err;\n",
     "\tu8 key_size = 0;\n"
     "\tbool stats = false;\t/* F-189(stats-decl) */\n"
     "\tint nf, err;\n"),
    ('fe_flow add_key call',
     'F-189(fe-flow-add-stats-pass)',
     "\terr = fman_pcd_ehash_add_key(t, key, key_size,\n"
     "\t\t\t\t     (u32)enq_obj->muram_off, (u32)fqid);\n",
     "\terr = fman_pcd_ehash_add_key(t, key, key_size,\n"
     "\t\t\t\t     (u32)enq_obj->muram_off, (u32)fqid,\n"
     "\t\t\t\t     stats);\t/* F-189(fe-flow-add-stats-pass) */\n"),
    ('genl add_key call',
     'F-189(genl-stats-false)',
     "\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n"
     "\t\t\t\t\t\t (u32)enq_obj->muram_off,\n"
     "\t\t\t\t\t\t fman_pcd_resolve_miss_fqid(pcd, hw_port_id));\n",
     "\t\tint err = fman_pcd_ehash_add_key(t, action->key, action->key_size,\n"
     "\t\t\t\t\t\t (u32)enq_obj->muram_off,\n"
     "\t\t\t\t\t\t fman_pcd_resolve_miss_fqid(pcd, hw_port_id),\n"
     "\t\t\t\t\t\t false /* F-189(genl-stats-false) */);\n"),
    ('stats readback pad word',
     'F-189(bucket-pad-readback)',
     "\t\t\tu64 head_now = *flow->bucket_h;\n",
     "\t\t\tu64 head_now = *flow->bucket_h;\n"
     "\t\t\t/* F-189(bucket-pad-readback): en_exthash_bucket.u64 pad -- raw\n"
     "\t\t\t * bucket byte-level readback for Phase 3.2 (overwrite check).\n"
     "\t\t\t */\n"
     "\t\t\tu64 bucket_pad = *(const u64 *)((const u8 *)flow->bucket_h + 8);\n"),
    ('stats printf pad',
     'F-189(stats-printf-pad)',
     "\t\t\tseq_printf(s,\n"
     "\t\t\t\t   \"tbl[%u] idx=%u keysz=%u record_dma=0x%llx bucket_head_now=0x%016llx pkt_count=%llu pkt_bytes=%llu timestamp=0x%08x\\n\",\n"
     "\t\t\t\t   ti, flow->index, flow->key_size,\n"
     "\t\t\t\t   (unsigned long long)flow->record_dma,\n"
     "\t\t\t\t   (unsigned long long)head_now,\n"
     "\t\t\t\t   (unsigned long long)pkt_count,\n"
     "\t\t\t\t   (unsigned long long)pkt_bytes,\n"
     "\t\t\t\t   ts);\n",
     "\t\t\tseq_printf(s,\n"
     "\t\t\t\t   \"tbl[%u] idx=%u keysz=%u record_dma=0x%llx bucket_head_now=0x%016llx bucket_pad_now=0x%016llx pkt_count=%llu pkt_bytes=%llu timestamp=0x%08x\\n\",\n"
     "\t\t\t\t   ti, flow->index, flow->key_size,\n"
     "\t\t\t\t   (unsigned long long)flow->record_dma,\n"
     "\t\t\t\t   (unsigned long long)head_now,\n"
     "\t\t\t\t   (unsigned long long)bucket_pad, /* F-189(stats-printf-pad) */\n"
     "\t\t\t\t   (unsigned long long)pkt_count,\n"
     "\t\t\t\t   (unsigned long long)pkt_bytes,\n"
     "\t\t\t\t   ts);\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)

if changes:
    print(f"### F-189 complete ({changes} blocks)")
else:
    print("### F-189 no changes applied")
    sys.exit(1)
