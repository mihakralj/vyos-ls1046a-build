"""F-267 (control-plane memory, 2026-10-16): ehash flow records come from a
per-table dma_pool, with the FE context inside the same block.

Each flow cost two dma_alloc_coherent() calls: the 320-byte record (0130) and
the 16-byte F-175 context. Both round up to a page, and the 2026-10-13 B2
capacity test on .185 measured ~9 KiB of MemAvailable per installed record.
A dma_pool of 512-byte, 512-aligned blocks holds both: record at +0 (live
content ends at +192 worst case, NAT66 + VLAN translate), context at +320,
outside the 320-byte record the walker fetches. Blocks are coherent and
zeroed (dma_pool_zalloc), as F-142 requires, and stay 256-byte aligned
(vendor EN_EHASH_ENTRY_ALIGN). Eight records share a page.

The context no longer has its own failure path, and del_key()/flow_drain()
free one block after the SYNC instead of two allocations. F-256's
keep-on-timeout path still frees nothing, and dma_pool_destroy() leaves a busy
pool's pages allocated, so a kept record stays valid after table free. Same
lock, same F-173 barrier, same record bytes; only the record and context bus
addresses change. Also demotes the remaining per-insert log lines to debug:
F-219 duplicate eviction, F-197 FQID fallback resolution and the F-251
drop-ctx trace. Idempotent; every anchor must match its expected count before
anything is written. After F-266.
"""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
DONE = "struct dma_pool *rec_pool;"

EDITS = [
    ("#include <linux/dma-mapping.h>\n",
     "#include <linux/dma-mapping.h>\n#include <linux/dmapool.h>\n", 1),

    ("\tstruct hlist_head *bidx;\t/* F-264: flows by bucket index, mask + 1 heads */\n};\n",
     "\tstruct hlist_head *bidx;\t/* F-264: flows by bucket index, mask + 1 heads */\n"
     "\tstruct dma_pool *rec_pool;\t/* record + FE context blocks */\n};\n", 1),

    ("\tt->dev = fman_get_dev(pcd->fman);\t/* F-142 */\n",
     "\tt->dev = fman_get_dev(pcd->fman);\t/* F-142 */\n"
     "\t/* One block per flow: record at +0, FE context at\n"
     "\t * +FMAN_EHASH_FLOW_REC_SIZE. Self-aligned 512-byte blocks keep\n"
     "\t * records 256-byte aligned and pack eight per page.\n"
     "\t */\n"
     "\tt->rec_pool = dma_pool_create(\"fman_ehash_rec\", t->dev,\n"
     "\t\t\t\t      FMAN_EHASH_FLOW_BLK_SIZE,\n"
     "\t\t\t\t      FMAN_EHASH_FLOW_BLK_SIZE, 0);\n"
     "\tif (!t->rec_pool) {\n"
     "\t\tdma_free_coherent(t->dev, tablesize, t->table_base,\n"
     "\t\t\t\t  t->table_dma);\n"
     "\t\terr = -ENOMEM;\n"
     "\t\tgoto err_put_buf;\n"
     "\t}\n", 1),

    ("\tfman_pcd_ehash_flow_drain(t);\n"
     "\tdma_free_coherent(t->dev, t->table_size, t->table_base, t->table_dma);\n",
     "\tfman_pcd_ehash_flow_drain(t);\n"
     "\t/* Leaves the pages of records F-256 kept allocated. */\n"
     "\tdma_pool_destroy(t->rec_pool);\n"
     "\tdma_free_coherent(t->dev, t->table_size, t->table_base, t->table_dma);\n", 1),

    ("#define FMAN_FE_CTX_SIZE\t\t16\n",
     "#define FMAN_FE_CTX_SIZE\t\t16\n"
     "#define FMAN_EHASH_FLOW_BLK_SIZE\t512\t/* pool block: record + FE context */\n", 1),

    ("\tvoid *record;\t\t\t/* DDR 256-B flow record (dma_alloc_coherent) */\n",
     "\tvoid *record;\t\t\t/* DDR flow record (t->rec_pool block) */\n", 1),
    ("\tvoid *ctx;\t\t\t/* F-175: separate 16B FE context buffer */\n"
     "\tdma_addr_t ctx_dma;\t\t/* F-175: bus address of ctx (stored in record) */\n",
     "\tvoid *ctx;\t\t\t/* F-175: 16B FE context, record + REC_SIZE */\n"
     "\tdma_addr_t ctx_dma;\t\t/* F-175: bus address of ctx (stored in record) */\n", 1),

    ("\t/* dma_alloc_coherent returns page-aligned, zeroed coherent DDR. */\n"
     "\tr = dma_alloc_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE, &rdma,\n"
     "\t\t\t       GFP_KERNEL);\n",
     "\t/* Zeroed coherent DDR block: record, then the FE context. */\n"
     "\tBUILD_BUG_ON(FMAN_EHASH_FLOW_REC_SIZE + FMAN_FE_CTX_SIZE >\n"
     "\t\t     FMAN_EHASH_FLOW_BLK_SIZE);\n"
     "\tr = dma_pool_zalloc(t->rec_pool, GFP_KERNEL, &rdma);\n", 1),

    ("\t\tdma_addr_t ctx_dma;\n"
     "\t\tu8 *ctx = dma_alloc_coherent(t->dev, FMAN_FE_CTX_SIZE, &ctx_dma,\n"
     "\t\t\t\t\t     GFP_KERNEL);\n"
     "\n"
     "\t\tif (!ctx) {\n"
     "\t\t\tdma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE, r, rdma);\n"
     "\t\t\tkfree(flow);\n"
     "\t\t\treturn -ENOMEM;\n"
     "\t\t}\n",
     "\t\tdma_addr_t ctx_dma = rdma + FMAN_EHASH_FLOW_REC_SIZE;\n"
     "\t\tu8 *ctx = (u8 *)r + FMAN_EHASH_FLOW_REC_SIZE;\n"
     "\n", 1),

    ("\t\thlist_del(&flow->bnode);\t/* F-264 */\n"
     "\t\tdma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,\n"
     "\t\t\t\t  flow->record, flow->record_dma);\n"
     "\t\tif (flow->ctx)\t/* F-175 */\n"
     "\t\t\tdma_free_coherent(t->dev, FMAN_FE_CTX_SIZE, flow->ctx,\n"
     "\t\t\t\t\t  flow->ctx_dma);\n",
     "\t\thlist_del(&flow->bnode);\t/* F-264 */\n"
     "\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);\n", 1),

    ("\t/* record is dma_alloc_coherent (0130) \u2014 free it the same way\n"
     "\t * fman_pcd_ehash_flow_drain() does, NOT kfree (which trips\n"
     "\t * free_large_kmalloc and corrupts the coherent allocator). */\n"
     "\tdma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE,\n"
     "\t\t\t  x->record, x->record_dma);\n"
     "\t/* F-257: the F-175 context goes with the record, after the SYNC.\n"
     "\t * (The F-256 timeout path returned above and keeps both.) */\n"
     "\tif (x->ctx)\n"
     "\t\tdma_free_coherent(t->dev, FMAN_FE_CTX_SIZE, x->ctx,\n"
     "\t\t\t\t  x->ctx_dma);\n",
     "\t/* The block holds the record and its FE context; it is freed only\n"
     "\t * after the SYNC (the F-256 timeout path returned above and keeps\n"
     "\t * it). */\n"
     "\tdma_pool_free(t->rec_pool, x->record, x->record_dma);\n", 1),

    ("\t\t\tpr_info_ratelimited(\"fman_pcd: F-219 ehash evicted stale duplicate record",
     "\t\t\tpr_debug(\"fman_pcd: F-219 ehash evicted stale duplicate record", 1),
    ("\t\t\t\t\t    key_size);\n\t}\n\n\tindex = fman_pcd_ehash_bucket_index(",
     "\t\t\t key_size);\n\t}\n\n\tindex = fman_pcd_ehash_bucket_index(", 1),

    ("\tdev_info_ratelimited(fman_get_dev(fman),\n"
     "\t\t\t     \"fe_flow: F-197 resolved hw_port=0x%02x params_fqid=0x0 scheme_fqid=0x%x\\n\",\n"
     "\t\t\t     hw_port_id, scheme_fqid);\n",
     "\tdev_dbg(fman_get_dev(fman),\n"
     "\t\t\"fe_flow: F-197 resolved hw_port=0x%02x params_fqid=0x0 scheme_fqid=0x%x\\n\",\n"
     "\t\thw_port_id, scheme_fqid);\n", 1),

    ("\t\t\tpr_info(\"fman_pcd: F-251 drop-ctx port=%u fe_exit_off=%#lx enq_muram=%#x ",
     "\t\t\tpr_debug(\"fman_pcd: F-251 drop-ctx port=%u fe_exit_off=%#lx enq_muram=%#lx ", 1),
]


def main():
    try:
        with open(PCD) as f:
            src = f.read()
    except FileNotFoundError:
        print(f"### F-267: FATAL: {PCD} not found")
        sys.exit(1)
    if DONE in src:
        print("### F-267: already applied")
        return
    for i, (old, _new, want) in enumerate(EDITS):
        n = src.count(old)
        if n != want:
            print(f"### F-267: FATAL: anchor {i} in {PCD} found {n} times (expected {want})")
            sys.exit(1)
    for old, new, _want in EDITS:
        src = src.replace(old, new)
    for gone in ("dma_alloc_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE",
                 "dma_alloc_coherent(t->dev, FMAN_FE_CTX_SIZE",
                 "dma_free_coherent(t->dev, FMAN_EHASH_FLOW_REC_SIZE",
                 "dma_free_coherent(t->dev, FMAN_FE_CTX_SIZE"):
        if gone in src:
            print(f"### F-267: FATAL: per-record coherent call survived: {gone}")
            sys.exit(1)
    with open(PCD, "w") as f:
        f.write(src)
    print("### F-267: ehash records + FE contexts from per-table dma_pool; per-insert logs demoted")


if __name__ == "__main__":
    main()
