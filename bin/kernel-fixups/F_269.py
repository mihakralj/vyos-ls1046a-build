"""F-269 (ehash collision chain, 2026-10-20): buckets holding two or more
flows point at vendor cumulative nodes instead of a chain of regular records.

Microcode 210.10.1 reads byte 0 of the bucket head target. With bit 7 (0x80)
set it is a cumulative node (vendor en_cumulative_entry): the walker compares
its packed keys, follows the matching record pointer, and follows the next
node link (0x20). Otherwise it is one regular record that matches or misses.
ASK2 head-inserted regular records and wrote the old head into record bytes
2..7, which the walker never follows. On .156 (2026-10-20, routed chain A/B,
3000 flows) heads hit 472/472 and the records behind them missed 472/472;
bridge capacity at N=8192 failed the same way. The vendor ships
cumulative-node builds only; NO_CUMULATIVE_ENTRY is never built (see F-254).

Node layout (256 bytes, 256-aligned, walker DMA-reads 256 B): flags, key
count n, stride (ks, 4, or (ks + 7) & 0x38), pointer offset 13 + n*stride,
big-endian next node at +4, keys from +12, a zero byte, then n big-endian
record pointers. 3 keys per node for the 50-byte routed key.

Copy-on-write: every insert and delete rebuilds the bucket from the F-264
index into fresh nodes from a per-table dma_pool, switches the head with one
64-bit store after wmb(), SYNCs (F-254 INV0), then frees the replaced nodes
(and, on delete, the record). F-256 keeps both on SYNC timeout. A failed
rebuild leaves an insert untouched (-ENOMEM) and makes a delete clear the
bucket head (its other flows stay in software until the next rebuild). Drain
zeroes each head and frees everything; prev_head and record bytes 2..7 are
gone. bin/test-ehash-delete.py runs the real code with a walker model.
Idempotent; every anchor must match its expected count before anything is
written. After F-268.
"""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
DONE = "fman_pcd_ehash_bucket_publish("
SAFETY_DONE = "F-269 timeout ownership quarantine"

EDITS = [
    ('#include <linux/dmapool.h>\n',
     '#include <linux/dmapool.h>\n#include <linux/unaligned.h>\t/* F-269: node pointers */\n', 1),
    ('\nstruct fman_pcd_ehash_table {\n\tstruct list_head node;\n',
     '\n/* F-269: microcode cumulative node (vendor en_cumulative_entry). */\n#define FMAN_EHASH_CU_SIZE\t\t256\t/* walker DMA-reads 256 B, aligned */\n#define FMAN_EHASH_CU_NODE\t\t0x80\n#define FMAN_EHASH_CU_NEXT\t\t0x20\n#define FMAN_EHASH_CU_KEYS_OFF\t\t12\n#define FMAN_EHASH_CU_PTR_SIZE\t\t8\n\n/* F-269: the cumulative nodes a bucket head currently points at. */\nstruct fman_pcd_ehash_cu {\n\tunsigned int n;\n\tstruct {\n\t\tvoid *va;\n\t\tdma_addr_t dma;\n\t} node[];\n};\n\nstruct fman_pcd_ehash_table {\n\tstruct list_head node;\n', 1),
    ('\tstruct dma_pool *rec_pool;\t/* record + FE context blocks */\n};\n',
     '\tstruct dma_pool *rec_pool;\t/* record + FE context blocks */\n\tstruct dma_pool *cu_pool;\t/* F-269: cumulative nodes */\n\tstruct fman_pcd_ehash_cu **cu;\t/* F-269: published nodes per bucket */\n};\n', 1),
    ('\tif (!t->bidx) {\n\t\tkfree(t);\n\t\treturn -ENOMEM;\n\t}\n',
     '\tif (!t->bidx) {\n\t\tkfree(t);\n\t\treturn -ENOMEM;\n\t}\n\tt->cu = kvcalloc((size_t)mask + 1, sizeof(*t->cu), GFP_KERNEL);\t/* F-269 */\n\tif (!t->cu) {\n\t\tkvfree(t->bidx);\n\t\tkfree(t);\n\t\treturn -ENOMEM;\n\t}\n', 1),
    ('\tif (!t->rec_pool) {\n\t\tdma_free_coherent(t->dev, tablesize, t->table_base,\n\t\t\t\t  t->table_dma);\n\t\terr = -ENOMEM;\n\t\tgoto err_put_buf;\n\t}\n',
     '\tif (!t->rec_pool) {\n\t\tdma_free_coherent(t->dev, tablesize, t->table_base,\n\t\t\t\t  t->table_dma);\n\t\terr = -ENOMEM;\n\t\tgoto err_put_buf;\n\t}\n\t/* F-269: 256-byte, 256-aligned cumulative nodes. */\n\tt->cu_pool = dma_pool_create("fman_ehash_cu", t->dev,\n\t\t\t\t     FMAN_EHASH_CU_SIZE, FMAN_EHASH_CU_SIZE, 0);\n\tif (!t->cu_pool) {\n\t\tdma_pool_destroy(t->rec_pool);\n\t\tdma_free_coherent(t->dev, tablesize, t->table_base,\n\t\t\t\t  t->table_dma);\n\t\terr = -ENOMEM;\n\t\tgoto err_put_buf;\n\t}\n', 1),
    ('err_free_t:\n\tkvfree(t->bidx);\t/* F-264 */\n',
     'err_free_t:\n\tkvfree(t->cu);\t/* F-269 */\n\tkvfree(t->bidx);\t/* F-264 */\n', 1),
    ('\tdma_pool_destroy(t->rec_pool);\n\tdma_free_coherent(t->dev, t->table_size, t->table_base, t->table_dma);\n\tkvfree(t->bidx);\t/* F-264 */\n',
     '\tdma_pool_destroy(t->rec_pool);\n\tdma_pool_destroy(t->cu_pool);\t/* F-269 */\n\tdma_free_coherent(t->dev, t->table_size, t->table_base, t->table_dma);\n\tkvfree(t->bidx);\t/* F-264 */\n\tkvfree(t->cu);\t/* F-269 */\n', 1),
    ('\tu64 prev_head;\t\t\t/* old bucket->h, for byte-exact undo */\n',
     '', 1),
    ('\tmutex_unlock(&frag_lock);\n\treturn err;\n}\n\nstatic int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n',
     '\tmutex_unlock(&frag_lock);\n\treturn err;\n}\n\n/*\n * F-269: microcode 210.10.1 follows a collision link only through a\n * cumulative node (byte 0 bit 7, vendor en_cumulative_entry). A regular\n * record either matches or misses, so a record chained behind another one\n * is never reached. A bucket holding one flow points at its record; a bucket\n * holding more points at a chain of cumulative nodes:\n *\n *   +0 flags (0x80 node, 0x40 invalid, 0x20 next)  +1 keys n  +2 stride\n *   +3 13 + n * stride (pointer offset)  +4 next node, big-endian\n *   +12 n keys at stride, one zero byte, n big-endian record pointers\n *\n * Nodes are never edited in place. Every insert and delete rebuilds the\n * bucket from t->bidx, switches the head with one 64-bit store, and frees\n * the old nodes only after a SYNC.\n */\nstatic u8 fman_pcd_ehash_cu_stride(u8 key_size)\n{\n\tif (key_size <= 2)\n\t\treturn key_size;\n\tif (key_size <= 4)\n\t\treturn 4;\n\treturn (key_size + 7) & 0x38;\n}\n\nstatic void fman_pcd_ehash_cu_free(struct fman_pcd_ehash_table *t,\n\t\t\t\t   struct fman_pcd_ehash_cu *cu)\n{\n\tunsigned int i;\n\n\tif (!cu)\n\t\treturn;\n\tfor (i = 0; i < cu->n; i++)\n\t\tdma_pool_free(t->cu_pool, cu->node[i].va, cu->node[i].dma);\n\tkfree(cu);\n}\n\n/*\n * Rebuild bucket @index from t->bidx, leaving out @skip, and switch its\n * head. On success *@old holds the replaced nodes (or NULL); the caller\n * frees them after a SYNC. On -ENOMEM nothing has changed.\n */\nstatic int fman_pcd_ehash_bucket_publish(struct fman_pcd_ehash_table *t,\n\t\t\t\t\t u16 index,\n\t\t\t\t\t const struct fman_pcd_ehash_flow *skip,\n\t\t\t\t\t struct fman_pcd_ehash_cu **old)\n{\n\tu64 *head = (u64 *)((u8 *)t->table_base +\n\t\t\t    (size_t)index * FMAN_EHASH_BUCKET_SIZE);\n\tu8 stride = fman_pcd_ehash_cu_stride(t->key_size);\n\tunsigned int per = (FMAN_EHASH_CU_SIZE - FMAN_EHASH_CU_KEYS_OFF - 1) /\n\t\t\t   (stride + FMAN_EHASH_CU_PTR_SIZE);\n\tstruct fman_pcd_ehash_flow *f, *only = NULL;\n\tstruct fman_pcd_ehash_cu *cu = NULL;\n\tunsigned int n = 0, nodes, i, k;\n\tu64 h = 0;\n\n\thlist_for_each_entry(f, &t->bidx[index], bnode) {\n\t\tif (f == skip)\n\t\t\tcontinue;\n\t\tonly = f;\n\t\tn++;\n\t}\n\n\tif (n == 1) {\n\t\th = swab64((u64)only->record_dma);\n\t} else if (n > 1) {\n\t\tnodes = DIV_ROUND_UP(n, per);\n\t\tcu = kzalloc(struct_size(cu, node, nodes), GFP_KERNEL);\n\t\tif (!cu)\n\t\t\treturn -ENOMEM;\n\t\tfor (i = 0; i < nodes; i++) {\n\t\t\tcu->node[i].va = dma_pool_zalloc(t->cu_pool, GFP_KERNEL,\n\t\t\t\t\t\t\t &cu->node[i].dma);\n\t\t\tif (!cu->node[i].va) {\n\t\t\t\tfman_pcd_ehash_cu_free(t, cu);\n\t\t\t\treturn -ENOMEM;\n\t\t\t}\n\t\t\tcu->n = i + 1;\n\t\t}\n\n\t\ti = 0;\n\t\tk = 0;\n\t\thlist_for_each_entry(f, &t->bidx[index], bnode) {\n\t\t\tunsigned int m;\n\t\t\tu8 *node;\n\n\t\t\tif (f == skip)\n\t\t\t\tcontinue;\n\t\t\tm = min(per, n - i * per);\n\t\t\tnode = cu->node[i].va;\n\t\t\tif (k == 0) {\n\t\t\t\tnode[0] = FMAN_EHASH_CU_NODE;\n\t\t\t\tnode[1] = m;\n\t\t\t\tnode[2] = stride;\n\t\t\t\tnode[3] = FMAN_EHASH_CU_KEYS_OFF + 1 + m * stride;\n\t\t\t\tif (i + 1 < nodes) {\n\t\t\t\t\tnode[0] |= FMAN_EHASH_CU_NEXT;\n\t\t\t\t\tput_unaligned_be64((u64)cu->node[i + 1].dma,\n\t\t\t\t\t\t\t   node + 4);\n\t\t\t\t}\n\t\t\t}\n\t\t\tmemcpy(node + FMAN_EHASH_CU_KEYS_OFF + k * stride,\n\t\t\t       (u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,\n\t\t\t       t->key_size);\n\t\t\tput_unaligned_be64((u64)f->record_dma,\n\t\t\t\t\t   node + node[3] + k * FMAN_EHASH_CU_PTR_SIZE);\n\t\t\tif (++k == m) {\n\t\t\t\tk = 0;\n\t\t\t\ti++;\n\t\t\t}\n\t\t}\n\t\th = swab64((u64)cu->node[0].dma);\n\t}\n\n\t/* F-173: the record and node writes are visible before the head\n\t * that exposes them to the FMan walker. */\n\twmb();\n\tWRITE_ONCE(*head, h);\n\t*old = t->cu[index];\n\tt->cu[index] = cu;\n\treturn 0;\n}\n\n/*\n * F-254 SYNC (RM S5.12.14.1): FMFP_EXTC[INV0] waits out lookups that may\n * still hold a pointer the CPU just unlinked. -ETIMEDOUT (F-256) means a\n * stuck task may still reference it: the caller keeps it.\n */\nstatic int fman_pcd_ehash_sync(struct fman_pcd_ehash_table *t, u32 *extc)\n{\n\tstruct fman *fman = t->pcd ? fman_pcd_get_fman(t->pcd) : NULL;\n\tconst u32 inv0 = 0x80000000U;\n\tunsigned int i;\n\tu32 v = 0;\n\n\t*extc = 0;\n\tdma_wmb();\n\tif (!fman)\n\t\treturn 0;\n\tfman_set_fpm_extc(fman, inv0);\n\tfor (i = 0; i < 100000U; i++) {\n\t\tv = fman_get_fpm_extc(fman);\n\t\tif (!(v & inv0))\n\t\t\tbreak;\n\t\tudelay(1);\n\t}\n\t*extc = v;\n\treturn (v & inv0) ? -ETIMEDOUT : 0;\n}\n\nstatic int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,\n', 1),
    ('\tu64 prev_head, old_native, rec_phys;\n',
     '\tstruct fman_pcd_ehash_cu *old_cu = NULL;\t/* F-269 */\n\tint f269_err;\n', 1),
    ("\tprev_head = *flow->bucket_h;\t\t/* swab'd phys of old head */\n\told_native = swab64(prev_head);\t\t/* unswap to native phys */\n",
     '', 1),
    ('\t\t*(__be16 *)(r + 2) = cpu_to_be16((u16)((old_native >> 32) & 0xffff));\n\t\t*(__be32 *)(r + 4) = cpu_to_be32((u32)(old_native & 0xffffffff));\n',
     '\t\t/* F-269: bytes 2..7 stay zero; the walker never follows a\n\t\t * link out of a regular record. */\n', 1),
    ("\t/* Head-insert: bucket head = swab64(dma_addr(record)). */\n\trec_phys = (u64)rdma;\n\n\t/* F-173: order the record's field writes above (header, key,\n\t * next-FE pointer) before this bucket-head publish becomes\n\t * visible to FMan's independent DMA-capable hash-table walker.\n\t * Without this, a weak-ordered CPU can let the pointer write\n\t * reach visibility before the pointee's field writes do, so\n\t * FMan dereferences a still-stale record -- same bug class and\n\t * fix as vendor's own we-are-mono/ASK commit cb8fc275.\n\t */\n\twmb();\n\t*flow->bucket_h = swab64(rec_phys);\n\n\tflow->record = r;\n\tflow->record_dma = rdma;\n\tflow->prev_head = prev_head;\n\tflow->index = index;\n\tflow->key_size = key_size;\n",
     '\tflow->record = r;\n\tflow->record_dma = rdma;\n\tflow->index = index;\n\tflow->key_size = key_size;\n\n\t/* F-269: rebuild the bucket (the record alone, or cumulative nodes)\n\t * and switch its head; the publish\'s wmb() orders the record writes\n\t * above before the head store (F-173). Replaced nodes are freed after\n\t * a SYNC.\n\t */\n\thlist_add_head(&flow->bnode, &t->bidx[index]);\t/* F-264 */\n\tf269_err = fman_pcd_ehash_bucket_publish(t, index, NULL, &old_cu);\n\tif (f269_err) {\n\t\thlist_del(&flow->bnode);\n\t\tdma_pool_free(t->rec_pool, r, rdma);\n\t\tkfree(flow);\n\t\treturn f269_err;\n\t}\n\tif (old_cu) {\n\t\tu32 extc;\n\n\t\tif (fman_pcd_ehash_sync(t, &extc))\n\t\t\tpr_warn_ratelimited("fman_pcd: F-269 insert SYNC timed out (fmfp_extc=0x%08x): bucket %u old nodes kept\\n",\n\t\t\t\t\t    extc, index);\n\t\telse\n\t\t\tfman_pcd_ehash_cu_free(t, old_cu);\n\t}\n', 1),
    ('\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */\n\thlist_add_head(&flow->bnode, &t->bidx[index]);\t/* F-264 */\n',
     '\tlist_add(&flow->node, &t->flows);\n', 1),
    ('/*\n * Inverse: LIFO restore.  Flows are head-added, so walking the list head-first\n * unwinds the collision chain in reverse insert order, restoring each bucket\n * head to its exact pre-insert value (byte-exact reversibility, section 8.6).\n */\nstatic void fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t)\n{\n\tstruct fman_pcd_ehash_flow *flow, *tmp;\n\n\tlist_for_each_entry_safe(flow, tmp, &t->flows, node) {\n\t\t*flow->bucket_h = flow->prev_head;\n\t\tlist_del(&flow->node);\n\t\thlist_del(&flow->bnode);\t/* F-264 */\n\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);\n\t\tkfree(flow);\n\t}\n}\n',
     '/*\n * Inverse: clear every bucket head, then free the records and nodes\n * (table free and clear-all; no SYNC, as before F-269). Bucket heads start\n * at zero, so this restores the table byte-exact (section 8.6).\n */\nstatic void fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t)\n{\n\tstruct fman_pcd_ehash_flow *flow, *tmp;\n\tu32 i;\n\n\tlist_for_each_entry_safe(flow, tmp, &t->flows, node) {\n\t\tWRITE_ONCE(*flow->bucket_h, 0);\n\t\tlist_del(&flow->node);\n\t\thlist_del(&flow->bnode);\t/* F-264 */\n\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);\n\t\tkfree(flow);\n\t}\n\tfor (i = 0; i <= t->hash_mask; i++) {\n\t\tfman_pcd_ehash_cu_free(t, t->cu[i]);\t/* F-269 */\n\t\tt->cu[i] = NULL;\n\t}\n}\n', 1),
    ('/*\n * F-117: delete ONE flow whose key matches @key, keeping the live silicon\n * collision chain AND the prev_head LIFO-drain invariant intact. O(chain)\n * since F-264 (bucket index).\n * Caller holds pcd->fe_lock (same contract as add_key).\n */\nstatic int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,\n\t\t\t\t  const u8 *key, u8 key_size)\n{\n\tstruct fman_pcd_ehash_flow *x = NULL, *y, *f;\n\tu64 x_phys, x_next;\n\n\tif (!t || !key || key_size == 0 || key_size != t->key_size)\n\t\treturn -EINVAL;\n\n\t/* F-264: only the key\'s own bucket can hold it. */\n\thlist_for_each_entry(f, &t->bidx[fman_pcd_ehash_bucket_index(key, key_size,\n\t\t\t\t\t\tt->hash_shift, t->hash_mask)], bnode) {\n\t\tif (f->key_size == key_size &&\n\t\t    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,\n\t\t\t    key, key_size)) {\n\t\t\tx = f;\n\t\t\tbreak;\n\t\t}\n\t}\n\tif (!x)\n\t\treturn -ENOENT;\n\n\tx_phys = (u64)virt_to_phys(x->record);\n\t/* x\'s own next-pointer: native phys of the record x chains to. */\n\tx_next = ((u64)be16_to_cpu(*(__be16 *)((u8 *)x->record + 2)) << 32) |\n\t\t be32_to_cpu(*(__be32 *)((u8 *)x->record + 4));\n\n\tif (swab64(*x->bucket_h) == x_phys) {\n\t\t/* x is the bucket head: restore head to x\'s saved prev_head. */\n\t\t*x->bucket_h = x->prev_head;\n\t} else {\n\t\t/* mid-chain: find predecessor y (same bucket, next == x). */\n\t\thlist_for_each_entry(y, &t->bidx[x->index], bnode) {\t/* F-264 */\n\t\t\tu64 y_next;\n\n\t\t\tif (y == x || y->index != x->index)\n\t\t\t\tcontinue;\n\t\t\ty_next = ((u64)be16_to_cpu(*(__be16 *)((u8 *)y->record + 2)) << 32) |\n\t\t\t\t be32_to_cpu(*(__be32 *)((u8 *)y->record + 4));\n\t\t\tif (y_next != x_phys)\n\t\t\t\tcontinue;\n\t\t\t/* y now chains to x\'s next; keep the drain invariant.\n\t\t\t * F-254: one 64-bit store (y\'s flags kept, next replaced)\n\t\t\t * so a concurrent FMan walk never sees a torn pointer.\n\t\t\t */\n\t\t\t{\n\t\t\t\tu64 y_word = be64_to_cpu(READ_ONCE(*(__be64 *)y->record));\n\n\t\t\t\ty_word = (y_word & 0xffff000000000000ULL) |\n\t\t\t\t\t (x_next & 0x0000ffffffffffffULL);\n\t\t\t\tWRITE_ONCE(*(__be64 *)y->record, cpu_to_be64(y_word));\n\t\t\t}\n\t\t\ty->prev_head = x->prev_head;\n\t\t\tbreak;\n\t\t}\n\t}\n\n\tlist_del(&x->node);\n\thlist_del(&x->bnode);\t/* F-264 */\n\n\t/* F-254: RM S5.12.14.1 -- after unlinking from a live,\n\t * controller-walked structure, SYNC (FMFP_EXTC[INV0]) before the old\n\t * record may be freed: a lookup that read the old pointer may still be\n\t * in flight. Same primitive as F-177 on insert.\n\t */\n\tdma_wmb();\n\t{\n\t\tstruct fman *f254_fman = t->pcd ? fman_pcd_get_fman(t->pcd) : NULL;\n\n\t\tif (f254_fman) {\n\t\t\tconst u32 f254_inv0 = 0x80000000U;\n\t\t\tconst unsigned int f254_poll_max = 100000U;\n\t\t\tunsigned int f254_i;\n\t\t\tu32 f254_extc = 0;\n\n\t\t\tfman_set_fpm_extc(f254_fman, f254_inv0);\n\t\t\tfor (f254_i = 0; f254_i < f254_poll_max; f254_i++) {\n\t\t\t\tf254_extc = fman_get_fpm_extc(f254_fman);\n\t\t\t\tif (!(f254_extc & f254_inv0))\n\t\t\t\t\tbreak;\n\t\t\t\tudelay(1);\n\t\t\t}\n\t\t\tif (f254_extc & f254_inv0) {\n\t\t\t\tconst u8 *f256_r = x->record;\n\n\t\t\t\t/* F-256: a stuck task may still reference this\n\t\t\t\t * record -- keep it (leak; the FMan is wedged\n\t\t\t\t * and needs a cold boot) and log what it held. */\n\t\t\t\tpr_warn("fman_pcd: F-256 delete SYNC timed out (fmfp_extc=0x%08x): record %pad kept, not freed\\n",\n\t\t\t\t\tf254_extc, &x->record_dma);\n\t\t\t\tpr_warn("fman_pcd: F-256 record %pad hdr+key %64phN\\n",\n\t\t\t\t\t&x->record_dma, f256_r);\n\t\t\t\tpr_warn("fman_pcd: F-256 record %pad stats@256 %24phN\\n",\n\t\t\t\t\t&x->record_dma, f256_r + 256);\n\t\t\t\tkfree(x);\n\t\t\t\treturn 0;\n\t\t\t}\n\t\t}\n\t}\n\n\t/* The block holds the record and its FE context; it is freed only\n\t * after the SYNC (the F-256 timeout path returned above and keeps\n\t * it). */\n\tdma_pool_free(t->rec_pool, x->record, x->record_dma);\n\tkfree(x);\n\treturn 0;\n}\n',
     '/*\n * F-117: delete ONE flow whose key matches @key. O(bucket) since F-264.\n * F-269: rebuild the bucket without the flow and switch its head, then\n * SYNC, then free the record and the replaced nodes.\n * Caller holds pcd->fe_lock (same contract as add_key).\n */\nstatic int fman_pcd_ehash_del_key(struct fman_pcd_ehash_table *t,\n\t\t\t\t  const u8 *key, u8 key_size)\n{\n\tstruct fman_pcd_ehash_flow *x = NULL, *f;\n\tstruct fman_pcd_ehash_cu *old_cu = NULL;\n\tu32 extc;\n\n\tif (!t || !key || key_size == 0 || key_size != t->key_size)\n\t\treturn -EINVAL;\n\n\t/* F-264: only the key\'s own bucket can hold it. */\n\thlist_for_each_entry(f, &t->bidx[fman_pcd_ehash_bucket_index(key, key_size,\n\t\t\t\t\t\tt->hash_shift, t->hash_mask)], bnode) {\n\t\tif (f->key_size == key_size &&\n\t\t    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,\n\t\t\t    key, key_size)) {\n\t\t\tx = f;\n\t\t\tbreak;\n\t\t}\n\t}\n\tif (!x)\n\t\treturn -ENOENT;\n\n\tif (fman_pcd_ehash_bucket_publish(t, x->index, x, &old_cu)) {\n\t\t/* No memory for new nodes: take the bucket out of hardware.\n\t\t * Its other flows stay in software until its next rebuild. */\n\t\tWRITE_ONCE(*x->bucket_h, 0);\n\t\told_cu = t->cu[x->index];\n\t\tt->cu[x->index] = NULL;\n\t\tpr_warn_ratelimited("fman_pcd: F-269 bucket %u rebuild failed: bucket left to software\\n",\n\t\t\t\t    x->index);\n\t}\n\n\tlist_del(&x->node);\n\thlist_del(&x->bnode);\t/* F-264 */\n\n\t/* F-254: a lookup that read the old head may still be in flight. */\n\tif (fman_pcd_ehash_sync(t, &extc)) {\n\t\tconst u8 *f256_r = x->record;\n\n\t\t/* F-256: a stuck task may still reference the record or the\n\t\t * replaced nodes -- keep them (leak; the FMan is wedged and\n\t\t * needs a cold boot) and log what the record held. */\n\t\tpr_warn("fman_pcd: F-256 delete SYNC timed out (fmfp_extc=0x%08x): record %pad kept, not freed\\n",\n\t\t\textc, &x->record_dma);\n\t\tpr_warn("fman_pcd: F-256 record %pad hdr+key %64phN\\n",\n\t\t\t&x->record_dma, f256_r);\n\t\tpr_warn("fman_pcd: F-256 record %pad stats@256 %24phN\\n",\n\t\t\t&x->record_dma, f256_r + 256);\n\t\tkfree(old_cu);\n\t\tkfree(x);\n\t\treturn 0;\n\t}\n\n\t/* The block holds the record and its FE context (F-267). */\n\tfman_pcd_ehash_cu_free(t, old_cu);\n\tdma_pool_free(t->rec_pool, x->record, x->record_dma);\n\tkfree(x);\n\treturn 0;\n}\n', 1),
]

SAFETY_EDITS = [
    ('''struct fman_pcd_ehash_cu {
\tunsigned int n;
\tstruct {
\t\tvoid *va;
\t\tdma_addr_t dma;
\t} node[];
};
''',
     '''struct fman_pcd_ehash_cu {
\tstruct list_head quarantine_node;
\tunsigned int n;
\tstruct {
\t\tvoid *va;
\t\tdma_addr_t dma;
\t} node[];
};
''', 1),
    ('''\tstruct fman_pcd_ehash_cu **cu;\t/* F-269: published nodes per bucket */
};
''',
     '''\tstruct fman_pcd_ehash_cu **cu;\t/* F-269: published nodes per bucket */
\tstruct list_head quarantined_cu;
\tstruct list_head quarantined_flows;
\tbool poisoned;
\tbool draining;
};
''', 1),
    ('''\tINIT_LIST_HEAD(&t->flows);
\tt->dev = fman_get_dev(pcd->fman);\t/* F-142 */
''',
     '''\tINIT_LIST_HEAD(&t->flows);
\tINIT_LIST_HEAD(&t->quarantined_cu);
\tINIT_LIST_HEAD(&t->quarantined_flows);
\tt->dev = fman_get_dev(pcd->fman);\t/* F-142 */
''', 1),
    ('''\t\tcu = kzalloc(struct_size(cu, node, nodes), GFP_KERNEL);
\t\tif (!cu)
\t\t\treturn -ENOMEM;
\t\tfor (i = 0; i < nodes; i++) {
''',
     '''\t\tcu = kzalloc(struct_size(cu, node, nodes), GFP_KERNEL);
\t\tif (!cu)
\t\t\treturn -ENOMEM;
\t\tINIT_LIST_HEAD(&cu->quarantine_node);
\t\tfor (i = 0; i < nodes; i++) {
''', 1),
    ('''static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,
\t\t\t\t  const u8 *key, u8 key_size,
\t\t\t\t  u32 enq_off, u32 fqid, bool stats,
\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,
\t\t\t\t  u16 eth_type,
\t\t\t\t  const struct fman_pcd_nat_params *nat,
\t\t\t\t  const struct fman_pcd_vlan_params *vlan)
{
\tint frag_bpid = 0;\t/* F-262 */
''',
     '''static int fman_pcd_ehash_recover(struct fman_pcd_ehash_table *t)
{
\tstruct fman_pcd_ehash_cu *cu, *cu_tmp;
\tstruct fman_pcd_ehash_flow *flow, *flow_tmp;
\tu32 extc;

\tif (!t->poisoned)
\t\treturn 0;
\tif (fman_pcd_ehash_sync(t, &extc))
\t\treturn -ETIMEDOUT;

\tlist_for_each_entry_safe(cu, cu_tmp, &t->quarantined_cu,
\t\t\t\t quarantine_node) {
\t\tlist_del(&cu->quarantine_node);
\t\tfman_pcd_ehash_cu_free(t, cu);
\t}
\tlist_for_each_entry_safe(flow, flow_tmp, &t->quarantined_flows, node) {
\t\tlist_del(&flow->node);
\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);
\t\tkfree(flow);
\t}
\tt->poisoned = false;
\treturn 0;
}

static int fman_pcd_ehash_add_key(struct fman_pcd_ehash_table *t,
\t\t\t\t  const u8 *key, u8 key_size,
\t\t\t\t  u32 enq_off, u32 fqid, bool stats,
\t\t\t\t  const u8 *l2_dst, const u8 *l2_src,
\t\t\t\t  u16 eth_type,
\t\t\t\t  const struct fman_pcd_nat_params *nat,
\t\t\t\t  const struct fman_pcd_vlan_params *vlan)
{
\tint frag_bpid = 0;\t/* F-262 */


''', 1),
    ('''\t\tif (key_size == 0 || key_size != t->key_size)
\t\treturn -EINVAL;
\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)
''',
     '''\t\tif (key_size == 0 || key_size != t->key_size)
\t\treturn -EINVAL;
\tif (t->draining)
\t\treturn -ESHUTDOWN;
\tif (fman_pcd_ehash_recover(t))
\t\treturn -ETIMEDOUT;
\tif (key_size > FMAN_EHASH_FLOW_KEY_MAX)
''', 1),
    ('''\t\tif (fman_pcd_ehash_sync(t, &extc))
\t\t\tpr_warn_ratelimited("fman_pcd: F-269 insert SYNC timed out (fmfp_extc=0x%08x): bucket %u old nodes kept\\n",
\t\t\t\t\t    extc, index);
\t\telse
\t\t\tfman_pcd_ehash_cu_free(t, old_cu);
''',
     '''\t\tif (fman_pcd_ehash_sync(t, &extc)) {
\t\t\tlist_add_tail(&old_cu->quarantine_node,
\t\t\t\t      &t->quarantined_cu);
\t\t\tt->poisoned = true;
\t\t\tpr_warn_ratelimited("fman_pcd: F-269 insert SYNC timed out (fmfp_extc=0x%08x): bucket %u generation quarantined\\n",
\t\t\t\t\t    extc, index);
\t\t} else {
\t\t\tfman_pcd_ehash_cu_free(t, old_cu);
\t\t}
''', 1),
    ('''\tif (!t || !key || key_size == 0 || key_size != t->key_size)
\t\treturn -EINVAL;

\t/* F-264: only the key's own bucket can hold it. */
''',
     '''\tif (!t || !key || key_size == 0 || key_size != t->key_size)
\t\treturn -EINVAL;
\tif (t->draining)
\t\treturn -ESHUTDOWN;
\tif (fman_pcd_ehash_recover(t))
\t\treturn -ETIMEDOUT;

\t/* F-264: only the key's own bucket can hold it. */
''', 1),
    ('''\t\tkfree(old_cu);
\t\tkfree(x);
\t\treturn 0;
\t}

\t/* The block holds the record and its FE context (F-267). */
''',
     '''\t\tif (old_cu)
\t\t\tlist_add_tail(&old_cu->quarantine_node,
\t\t\t\t      &t->quarantined_cu);
\t\tlist_add_tail(&x->node, &t->quarantined_flows);
\t\tt->poisoned = true;
\t\treturn -ETIMEDOUT;
\t}

\t/* The block holds the record and its FE context (F-267). */
''', 1),
    ('''\tif (!pcd)
\t\treturn;

\tlist_for_each_entry(t, &pcd->fe_ehash_tables, node)
\t\tfman_pcd_ehash_flow_drain(t);
}
''',
     '''\tif (!pcd)
\t\treturn 0;

\tlist_for_each_entry(t, &pcd->fe_ehash_tables, node) {
\t\tint err = fman_pcd_ehash_flow_drain(t);

\t\tif (err)
\t\t\treturn err;
\t}
\treturn 0;
}
''', 1),
    ('''static void fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t);
''',
     '''static int fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t);
''', 1),
    ('''static void fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t)
{
\tstruct fman_pcd_ehash_flow *flow, *tmp;
\tu32 i;

\tlist_for_each_entry_safe(flow, tmp, &t->flows, node) {
\t\tWRITE_ONCE(*flow->bucket_h, 0);
\t\tlist_del(&flow->node);
\t\thlist_del(&flow->bnode);\t/* F-264 */
\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);
\t\tkfree(flow);
\t}
\tfor (i = 0; i <= t->hash_mask; i++) {
\t\tfman_pcd_ehash_cu_free(t, t->cu[i]);\t/* F-269 */
\t\tt->cu[i] = NULL;
\t}
}
''',
     '''static int fman_pcd_ehash_flow_drain(struct fman_pcd_ehash_table *t)
{
\tstruct fman_pcd_ehash_flow *flow, *flow_tmp;
\tstruct fman_pcd_ehash_cu *cu, *cu_tmp;
\tu32 i;
\tu32 extc;

\tt->draining = true;
\tfor (i = 0; i <= t->hash_mask; i++) {
\t\tu64 *head = (u64 *)((u8 *)t->table_base +
\t\t\t\t    (size_t)i * FMAN_EHASH_BUCKET_SIZE);

\t\tWRITE_ONCE(*head, 0);
\t}
\tdma_wmb();
\tif (fman_pcd_ehash_sync(t, &extc))
\t\treturn -ETIMEDOUT;

\tlist_for_each_entry_safe(flow, flow_tmp, &t->flows, node) {
\t\tlist_del(&flow->node);
\t\thlist_del(&flow->bnode);
\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);
\t\tkfree(flow);
\t}
\tlist_for_each_entry_safe(flow, flow_tmp, &t->quarantined_flows, node) {
\t\tlist_del(&flow->node);
\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);
\t\tkfree(flow);
\t}
\tfor (i = 0; i <= t->hash_mask; i++) {
\t\tfman_pcd_ehash_cu_free(t, t->cu[i]);
\t\tt->cu[i] = NULL;
\t}
\tlist_for_each_entry_safe(cu, cu_tmp, &t->quarantined_cu,
\t\t\t\t quarantine_node) {
\t\tlist_del(&cu->quarantine_node);
\t\tfman_pcd_ehash_cu_free(t, cu);
\t}
\tt->poisoned = false;
\tt->draining = false;
\treturn 0;
}
''', 1),
    ('''static void fman_pcd_ehash_table_free(struct fman_pcd *pcd,
\t\t\t\t      struct fman_pcd_ehash_table *t);
''',
     '''static int fman_pcd_ehash_table_free(struct fman_pcd *pcd,
\t\t\t\t     struct fman_pcd_ehash_table *t);
''', 1),
    ('''static void fman_pcd_ehash_table_free(struct fman_pcd *pcd,
\t\t\t\t      struct fman_pcd_ehash_table *t)
{
\tlist_del(&t->node);
\tfman_pcd_ehash_flow_drain(t);
\t/* Leaves the pages of records F-256 kept allocated. */
\tdma_pool_destroy(t->rec_pool);
\tdma_pool_destroy(t->cu_pool);\t/* F-269 */
\tdma_free_coherent(t->dev, t->table_size, t->table_base, t->table_dma);
\tkvfree(t->bidx);\t/* F-264 */
\tkvfree(t->cu);\t/* F-269 */
\tkfree(t);
\tfman_pcd_ehash_int_buf_put(pcd);
}
''',
     '''static int fman_pcd_ehash_table_free(struct fman_pcd *pcd,
\t\t\t\t     struct fman_pcd_ehash_table *t)
{
\tint err;

\terr = fman_pcd_ehash_flow_drain(t);
\tif (err)
\t\treturn err;
\tlist_del(&t->node);
\t/* Leaves the pages of records F-256 kept allocated. */
\tdma_pool_destroy(t->rec_pool);
\tdma_pool_destroy(t->cu_pool);\t/* F-269 */
\tdma_free_coherent(t->dev, t->table_size, t->table_base, t->table_dma);
\tkvfree(t->bidx);\t/* F-264 */
\tkvfree(t->cu);\t/* F-269 */
\tkfree(t);
\tfman_pcd_ehash_int_buf_put(pcd);
\treturn 0;
}
''', 1),
    ('''static void fman_pcd_ehash_drain(struct fman_pcd *pcd)
{
\tstruct fman_pcd_ehash_table *t, *tmp;

\tlist_for_each_entry_safe(t, tmp, &pcd->fe_ehash_tables, node)
\t\tfman_pcd_ehash_table_free(pcd, t);
}
''',
     '''static int fman_pcd_ehash_drain(struct fman_pcd *pcd)
{
\tstruct fman_pcd_ehash_table *t, *tmp;

\tlist_for_each_entry_safe(t, tmp, &pcd->fe_ehash_tables, node) {
\t\tint err = fman_pcd_ehash_table_free(pcd, t);

\t\tif (err)
\t\t\treturn err;
\t}
\treturn 0;
}
''', 1),
    ('''static void fman_pcd_ehash_flow_clear_all(struct fman_pcd *pcd)''',
     '''static int fman_pcd_ehash_flow_clear_all(struct fman_pcd *pcd)''', 1),
    ('''\t\tfman_pcd_ehash_flow_clear_all(pcd);
\t\tmutex_unlock(&pcd->fe_lock);
\t\treturn count;
''',
     '''\t\terr = fman_pcd_ehash_flow_clear_all(pcd);
\t\tmutex_unlock(&pcd->fe_lock);
\t\treturn err ? err : count;
''', 1),
    ('''\t\tfman_pcd_ehash_flow_clear_all(pcd);
\t\tmutex_unlock(&pcd->fe_lock);
\t\treturn 0;
''',
     '''\t\tint err = fman_pcd_ehash_flow_clear_all(pcd);

\t\tmutex_unlock(&pcd->fe_lock);
\t\treturn err;
''', 1),
]


CALLER_DONE = "F-269 fail-closed teardown callers"
CALLER_EDITS = [
    ('''static int fman_pcd_fe_port_del(struct fman_pcd *pcd, u8 port_id)
{
\tstruct muram_info *muram = fman_get_muram(pcd->fman);
\tstruct fman *fman = fman_pcd_get_fman(pcd);
\tstruct fman_pcd_fe_port *fp;
\tstruct fman_port *port;
\tu32 params_off;
''',
     '''static int fman_pcd_fe_port_del(struct fman_pcd *pcd, u8 port_id)
{
\tstruct muram_info *muram = fman_get_muram(pcd->fman);
\tstruct fman *fman = fman_pcd_get_fman(pcd);
\tstruct fman_pcd_fe_port *fp;
\tstruct fman_port *port;
\tu32 params_off;
\tint err;
''', 1),
    ('''\tfp = fman_pcd_fe_port_find(pcd, port_id);
\tif (!fp)
\t\treturn -ENOENT;

\t/*
\t * Inverse of set(): unhook the ctrl-params page management index
''',
     '''\tfp = fman_pcd_fe_port_find(pcd, port_id);
\tif (!fp)
\t\treturn -ENOENT;
\tif (fp->table_v4) {
\t\terr = fman_pcd_ehash_table_free(pcd, fp->table_v4);
\t\tif (err)
\t\t\treturn err;
\t\tfp->table_v4 = NULL;
\t}

\t/*
\t * Inverse of set(): unhook the ctrl-params page management index
''', 1),
    ('''\t/* F-220(port-del-free): release this port's v4 table instance
\t * (list_del + flow drain + dma_free + int-buf refcount put). */
\tif (fp->table_v4) {
\t\tfman_pcd_ehash_table_free(pcd, fp->table_v4);
\t\tfp->table_v4 = NULL;
\t}
''', '', 1),
    ('''static void fman_pcd_fe_port_drain(struct fman_pcd *pcd)
{
\tstruct fman_pcd_fe_port *fp, *tmp;

\tlist_for_each_entry_safe(fp, tmp, &pcd->fe_ports, node) {
\t\tfman_pcd_muram_free(pcd, fp->mgmt_off, fp->mgmt_size);
''',
     '''static int fman_pcd_fe_port_drain(struct fman_pcd *pcd)
{
\tstruct fman_pcd_fe_port *fp, *tmp;

\tlist_for_each_entry_safe(fp, tmp, &pcd->fe_ports, node) {
\t\tint err;

\t\tif (fp->table_v4) {
\t\t\terr = fman_pcd_ehash_table_free(pcd, fp->table_v4);
\t\t\tif (err)
\t\t\t\treturn err;
\t\t\tfp->table_v4 = NULL;
\t\t}
\t\tfman_pcd_muram_free(pcd, fp->mgmt_off, fp->mgmt_size);
''', 1),
    ('''\t\tif (fp->table_v4) {
\t\t\tfman_pcd_ehash_table_free(pcd, fp->table_v4);\t/* F-220(port-drain-free) */
\t\t\tfp->table_v4 = NULL;
\t\t}
\t\tlist_del(&fp->node);
\t\tkfree(fp);
\t}
}
''',
     '''\t\tlist_del(&fp->node);
\t\tkfree(fp);
\t}
\treturn 0;
}
''', 1),
    ('''static int __fman_pcd_fe_arm_disengage(struct fman_pcd *pcd,
\t\t\t\t       unsigned int port_id)
{
\tif (port_id < 0x08 || port_id >= 0x28)
''',
     '''static int __fman_pcd_fe_arm_disengage(struct fman_pcd *pcd,
\t\t\t\t       unsigned int port_id)
{
\tint err;

\tif (port_id < 0x08 || port_id >= 0x28)
''', 1),
    ('''\tif (pcd->fe_arming_v4_table) {
\t\tfman_pcd_ehash_table_free(pcd, pcd->fe_arming_v4_table);
\t\tpcd->fe_arming_v4_table = NULL;
\t}
\tfsleep(5000);
\tfman_pcd_fe_adv_offload(pcd, (u8)port_id, false);\t/* F-262 */
\tfman_pcd_fe_port_del(pcd, (u8)port_id);
''',
     '''\tif (pcd->fe_arming_v4_table) {
\t\terr = fman_pcd_ehash_table_free(pcd, pcd->fe_arming_v4_table);
\t\tif (err)
\t\t\treturn err;
\t\tpcd->fe_arming_v4_table = NULL;
\t}
\tfsleep(5000);
\tfman_pcd_fe_adv_offload(pcd, (u8)port_id, false);\t/* F-262 */
\terr = fman_pcd_fe_port_del(pcd, (u8)port_id);
\tif (err && err != -ENOENT)
\t\treturn err;
''', 1),
    ('''void fman_pcd_release(struct fman_pcd *pcd)
{
\tif (!pcd)
''',
     '''void fman_pcd_release(struct fman_pcd *pcd)
{
\tint err;

\tif (!pcd)
''', 1),
    ('''\tfman_pcd_fe_port_drain(pcd);
\tWARN_ON(!list_empty(&pcd->fe_ports));
\tfman_pcd_ehash_drain(pcd);
\tWARN_ON(!list_empty(&pcd->fe_ehash_tables));
''',
     '''\terr = fman_pcd_fe_port_drain(pcd);
\tif (!err)
\t\terr = fman_pcd_ehash_drain(pcd);
\tif (err) {
\t\tdev_crit(fman_get_dev(pcd->fman),
\t\t\t "fman_pcd: teardown SYNC failed; retaining PCD and DMA owners (%d)\\n",
\t\t\t err);
\t\treturn;
\t}
\tWARN_ON(!list_empty(&pcd->fe_ports));
\tWARN_ON(!list_empty(&pcd->fe_ehash_tables));
''', 1),
    ('''key_size);
\t}
''',
     '''key_size);
\t\telse if (drc != -ENOENT)
\t\t\treturn drc;
\t}
''', 1),
]

CLEAR_DONE = "F-269 clear error propagation"
CLEAR_EDITS = [
('''	else if (!strncmp(buf, "clear", 5)) {
		fman_pcd_ehash_drain(pcd);
		err = 0;
	}''',
 '''	else if (!strncmp(buf, "clear", 5)) {
		/* F-269 clear error propagation */
		err = fman_pcd_ehash_drain(pcd);
	}''', 1),
]

FULL_DISENGAGE_DONE = "F-269 full-disengage error propagation"
FULL_DISENGAGE_EDITS = [
('''    /* Step 1-3: SDK-correct ordered teardown (same as fe_arm disengage) */
    __fman_pcd_fe_arm_disengage(pcd, (u8)port_id);

    /* Step 4: de-wedge the port (zero workspace, re-init ring, restart BMI) */
    ret = fman_pcd_port_recover(pcd, (u8)port_id);
    if (ret)
        pr_warn("fman_pcd fe_disengage_full: port 0x%02x recover returned %d\\n",
            port_id, ret);
''',
 '''    /* F-269 full-disengage error propagation */
    ret = __fman_pcd_fe_arm_disengage(pcd, (u8)port_id);
    if (ret)
        return ret;

    /* Step 4: de-wedge the port (zero workspace, re-init ring, restart BMI) */
    ret = fman_pcd_port_recover(pcd, (u8)port_id);
    if (ret) {
        pr_warn("fman_pcd fe_disengage_full: port 0x%02x recover returned %d\\n",
            port_id, ret);
        return ret;
    }
''', 1),
]


def main():
    try:
        with open(PCD) as f:
            src = f.read()
    except FileNotFoundError:
        print(f"### F-269: FATAL: {PCD} not found")
        sys.exit(1)
    if DONE not in src:
        for i, (old, _new, want) in enumerate(EDITS):
            n = src.count(old)
            if n != want:
                print(f"### F-269: FATAL: anchor {i} in {PCD} found {n} times (expected {want})")
                sys.exit(1)
        for old, new, _want in EDITS:
            src = src.replace(old, new)
        for gone in ("prev_head", "old_native"):
            if gone in src:
                print(f"### F-269: FATAL: chained-record state survived: {gone}")
                sys.exit(1)
    else:
        print("### F-269: cumulative-node rewrite already applied")

    if SAFETY_DONE not in src:
        for i, (old, _new, want) in enumerate(SAFETY_EDITS):
            n = src.count(old)
            if n != want:
                print(f"### F-269: FATAL: ownership anchor {i} in {PCD} found {n} times (expected {want})")
                sys.exit(1)
        for old, new, _want in SAFETY_EDITS:
            src = src.replace(old, new)
        src = src.replace("bool poisoned;\n",
                          "bool poisoned;\t/* F-269 timeout ownership quarantine */\n",
                          1)
        src = src.replace(
            "/* A timeout poisons all mutations until a later SYNC covers every saved\n",
            "/* F-269 timeout ownership quarantine: a timeout poisons all mutations until a later SYNC covers every saved\n",
            1)
    else:
        print("### F-269: timeout ownership quarantine already applied")

    if CALLER_DONE not in src:
        for i, (old, _new, want) in enumerate(CALLER_EDITS):
            n = src.count(old)
            if n != want:
                print(f"### F-269: FATAL: teardown caller anchor {i} in {PCD} found {n} times (expected {want})")
                sys.exit(1)
        for old, new, _want in CALLER_EDITS:
            src = src.replace(old, new)
        src = src.replace(
            "static int fman_pcd_fe_port_del(",
            "/* F-269 fail-closed teardown callers */\nstatic int fman_pcd_fe_port_del(",
            1)
    else:
        print("### F-269: fail-closed teardown callers already applied")

    if CLEAR_DONE not in src:
        for i, (old, _new, want) in enumerate(CLEAR_EDITS):
            n = src.count(old)
            if n != want:
                print(f"### F-269: FATAL: clear propagation anchor {i} in {PCD} found {n} times (expected {want})")
                sys.exit(1)
        for old, new, _want in CLEAR_EDITS:
            src = src.replace(old, new, 1)
    else:
        for i, (_old, new, want) in enumerate(CLEAR_EDITS):
            n = src.count(new)
            if n != want:
                print(f"### F-269: FATAL: applied clear propagation {i} in {PCD} found {n} times (expected {want})")
                sys.exit(1)
        print("### F-269: clear error propagation already applied")

    if FULL_DISENGAGE_DONE not in src:
        for i, (old, _new, want) in enumerate(FULL_DISENGAGE_EDITS):
            n = src.count(old)
            if n != want:
                print(f"### F-269: FATAL: full-disengage anchor {i} in {PCD} found {n} times (expected {want})")
                sys.exit(1)
        for old, new, _want in FULL_DISENGAGE_EDITS:
            src = src.replace(old, new, 1)
    else:
        for i, (_old, new, want) in enumerate(FULL_DISENGAGE_EDITS):
            n = src.count(new)
            if n != want:
                print(f"### F-269: FATAL: applied full-disengage {i} in {PCD} found {n} times (expected {want})")
                sys.exit(1)
        print("### F-269: full-disengage error propagation already applied")

    src = src.replace("no SYNC, as before F-269",
                      "completed FPM SYNC required before reclaim", 1)

    with open(PCD, "w") as f:
        f.write(src)
    print("### F-269: cumulative buckets with timeout-generation quarantine")


if __name__ == "__main__":
    main()
