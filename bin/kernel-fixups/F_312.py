"""F-312 (2026-11-13): ehash stats read under its own spinlock, not fe_lock.

The ASK2 64 B flow ladder on .185 (image 2026.11.12-0524) offloaded every flow
up to 65536 with zero loss, but from about 24k flows each install drove the
DUT into a kworker convoy: load average 17 -> 150, ~1000 D-state tasks (the
nf_ft_offload_stats workqueue cap), all blocked at
fman_pcd_fe_flow_get_stats_checked+0x6c, i.e. mutex_lock(&pcd->fe_lock).

Since F-264 the stats lookup is a ~1-2 us bucket walk, but it shared fe_lock
with inserts and deletes, which hold it across FMFP_EXTC[INV0] SYNC busy-polls
(F-177 on every insert, F-254/F-269 on delete and bucket rebuild). Once a queue
forms, every reader pays a sleep/wakeup handoff and insert workers wait in the
same queue, so installs and stats polls slow each other down.

The reader now takes a new innermost spinlock, pcd->fe_stats_lock, and never
fe_lock. Writers keep fe_lock and additionally take fe_stats_lock (order
fe_lock -> fe_stats_lock, nothing acquired under it, nothing sleeps under it)
around every change of what the reader can reach:

- t->bidx: link in add_key, unlink on the add_key error path, in del_key and
  in flow_drain (records and flow structs are freed after the unlock);
- pcd->fe_ehash_tables: table publish in table_set, unpublish in table_free;
- pcd->fe_ports and fp->table_v4: port publish in port_set, unlink in
  port_del (kfree after the unlock);
- table_free unpublishes the table (list_del and every fp->table_v4 == t set
  to NULL) under the spinlock BEFORE dma_pool_destroy/kvfree/kfree; until now
  t was freed while fp->table_v4 still pointed at it, which was safe only
  because one mutex covered both steps.

Record contents the reader uses (key at FMAN_EHASH_FLOW_KEY_OFF, owner,
owner_token, key_size) are written before the link and never rewritten while
linked; the silicon writes only the counters at +256..+279. Independent review
2026-11-13 (qdrant): RCU rejected as larger with no benefit; mutex_trylock
rejected because a failed read is a keepalive and idle flows would never age.

Idempotent (marker); every anchor must match exactly once.
Host check: bin/test-fe-stats-lock.py.
"""

import sys

PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
MARK = "F-312"

EDITS = (
    ("struct field",
     "\tstruct mutex fe_lock;\n",
     "\tstruct mutex fe_lock;\n"
     "\t/* F-312: innermost; guards what the stats reader walks (t->bidx,\n"
     "\t * fe_ehash_tables, fe_ports, fp->table_v4). Never sleep under it. */\n"
     "\tspinlock_t fe_stats_lock;\n"),
    ("init",
     "\tmutex_init(&pcd->fe_lock);\n",
     "\tmutex_init(&pcd->fe_lock);\n"
     "\tspin_lock_init(&pcd->fe_stats_lock);\t/* F-312 */\n"),
    ("port publish",
     "\tfp->table_v4 = pcd->fe_arming_v4_table;\n"
     "\tlist_add_tail(&fp->node, &pcd->fe_ports);\n",
     "\tspin_lock(&pcd->fe_stats_lock);\t/* F-312 */\n"
     "\tfp->table_v4 = pcd->fe_arming_v4_table;\n"
     "\tlist_add_tail(&fp->node, &pcd->fe_ports);\n"
     "\tspin_unlock(&pcd->fe_stats_lock);\n"),
    ("port unlink",
     "\tlist_del(&fp->node);\n",
     "\tspin_lock(&pcd->fe_stats_lock);\t/* F-312 */\n"
     "\tlist_del(&fp->node);\n"
     "\tspin_unlock(&pcd->fe_stats_lock);\n"),
    ("table publish",
     "\tlist_add_tail(&t->node, &pcd->fe_ehash_tables);\n",
     "\tspin_lock(&pcd->fe_stats_lock);\t/* F-312 */\n"
     "\tlist_add_tail(&t->node, &pcd->fe_ehash_tables);\n"
     "\tspin_unlock(&pcd->fe_stats_lock);\n"),
    ("port_del redundant store",
     "\t\tif (err)\n"
     "\t\t\treturn err;\n"
     "\t\tfp->table_v4 = NULL;\n"
     "\t}\n",
     "\t\tif (err)\n"
     "\t\t\treturn err;\n"
     "\t\t/* F-312: table_free cleared fp->table_v4 under fe_stats_lock. */\n"
     "\t}\n"),
    ("table_free locals",
     "static int fman_pcd_ehash_table_free(struct fman_pcd *pcd,\n"
     "\t\t\t\t     struct fman_pcd_ehash_table *t)\n"
     "{\n"
     "\tint err;\n",
     "static int fman_pcd_ehash_table_free(struct fman_pcd *pcd,\n"
     "\t\t\t\t     struct fman_pcd_ehash_table *t)\n"
     "{\n"
     "\tstruct fman_pcd_fe_port *fp;\n"
     "\tint err;\n"),
    ("table unpublish",
     "\tlist_del(&t->node);\n",
     "\t/* F-312: unpublish from every reader path before any free. */\n"
     "\tspin_lock(&pcd->fe_stats_lock);\n"
     "\tlist_del(&t->node);\n"
     "\tlist_for_each_entry(fp, &pcd->fe_ports, node)\n"
     "\t\tif (fp->table_v4 == t)\n"
     "\t\t\tfp->table_v4 = NULL;\n"
     "\tspin_unlock(&pcd->fe_stats_lock);\n"),
    ("add_key link",
     "\thlist_add_head(&flow->bnode, &t->bidx[index]);\t/* F-264 */\n",
     "\tspin_lock(&t->pcd->fe_stats_lock);\t/* F-312 */\n"
     "\thlist_add_head(&flow->bnode, &t->bidx[index]);\t/* F-264 */\n"
     "\tspin_unlock(&t->pcd->fe_stats_lock);\n"),
    ("add_key error unlink",
     "\t\thlist_del(&flow->bnode);\n"
     "\t\tdma_pool_free(t->rec_pool, r, rdma);\n",
     "\t\tspin_lock(&t->pcd->fe_stats_lock);\t/* F-312 */\n"
     "\t\thlist_del(&flow->bnode);\n"
     "\t\tspin_unlock(&t->pcd->fe_stats_lock);\n"
     "\t\tdma_pool_free(t->rec_pool, r, rdma);\n"),
    ("drain unlink",
     "\t\tlist_del(&flow->node);\n"
     "\t\thlist_del(&flow->bnode);\n"
     "\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);\n",
     "\t\tlist_del(&flow->node);\n"
     "\t\tspin_lock(&t->pcd->fe_stats_lock);\t/* F-312 */\n"
     "\t\thlist_del(&flow->bnode);\n"
     "\t\tspin_unlock(&t->pcd->fe_stats_lock);\n"
     "\t\tdma_pool_free(t->rec_pool, flow->record, flow->record_dma);\n"),
    ("del_key unlink",
     "\thlist_del(&x->bnode);\t/* F-264 */\n",
     "\tspin_lock(&t->pcd->fe_stats_lock);\t/* F-312 */\n"
     "\thlist_del(&x->bnode);\t/* F-264 */\n"
     "\tspin_unlock(&t->pcd->fe_stats_lock);\n"),
)

GETTER = "static int fman_pcd_fe_flow_get_stats_checked(struct fman *fm, u8 hw_port_id,\n"
COMMENT_OLD = " * the counters under pcd->fe_lock with a dma_rmb() before the loads (the FE\n"
COMMENT_NEW = (" * the counters under pcd->fe_stats_lock (F-312; not fe_lock, which writers\n"
               " * hold across FMFP_EXTC SYNC polls) with a dma_rmb() before the loads (the FE\n")


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise ValueError(f"{label}: anchor found {n} times (expected 1)")
    return text.replace(old, new)


def main():
    try:
        src = open(PCD).read()
    except FileNotFoundError as exc:
        print(f"### F-312: FATAL: {exc}")
        return 1
    if MARK in src:
        print("### F-312: already applied")
        return 0
    try:
        for label, old, new in EDITS:
            src = replace_once(src, old, new, label)
        src = replace_once(src, COMMENT_OLD, COMMENT_NEW, "getter comment")
        if src.count(GETTER) != 1:
            raise ValueError("get_stats_checked: definition not found exactly once")
        start = src.index(GETTER)
        end = src.index("\n}\n", start) + 3
        func = src[start:end]
        if func.count("mutex_lock(&pcd->fe_lock);") != 1 or func.count("mutex_unlock(&pcd->fe_lock);") != 3:
            raise ValueError("get_stats_checked: expected 1 lock / 3 unlock sites")
        func = func.replace("mutex_lock(&pcd->fe_lock);", "spin_lock(&pcd->fe_stats_lock);\t/* F-312 */")
        func = func.replace("mutex_unlock(&pcd->fe_lock);", "spin_unlock(&pcd->fe_stats_lock);")
        src = src[:start] + func + src[end:]
    except ValueError as exc:
        print(f"### F-312: FATAL: {exc}")
        return 1
    with open(PCD, "w") as stream:
        stream.write(src)
    print("### F-312: ehash stats read under fe_stats_lock (spinlock), writers publish/unlink under it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
