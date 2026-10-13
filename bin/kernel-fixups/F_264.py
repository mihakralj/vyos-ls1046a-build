"""F-264 (control-plane scaling, 2026-10-10): index ehash flows by bucket, so
key lookups cost one collision chain instead of every flow in the table.

fman_pcd_ehash_del_key() (also called by F-219's evict-before-insert on every
add) and fman_pcd_fe_flow_get_stats() (F-228) each found a record by
memcmp-scanning the whole t->flows list under pcd->fe_lock, and del_key()
scanned the list a second time for a mid-chain predecessor. nf_flowtable asks
for the stats of every hardware flow on each gc pass (~1 s), so N flows cost
O(N^2) per pass under one mutex. Measured on .185 (image 1723, 2026-10-10):
2048 bidirectional flows gave 61 D-state kworkers blocked in
fman_pcd_fe_flow_get_stats and load average 46; at 8192 flows inserts fell to
zero, ~2,000 kworkers piled up and only 1,177 flows reached hardware.

Every record already lives in exactly one DDR bucket, chosen by
fman_pcd_ehash_bucket_index(key) and stored in flow->index. A per-table
hlist array t->bidx[mask + 1] mirrors that: add_key() links the flow into its
bucket next to the existing list_add(), drain and del_key() unlink it next to
the existing list_del(), and both lookups walk only bidx[bucket(key)]. The
predecessor search walks bidx[x->index] (a DDR chain only ever holds keys of
its own bucket). t->flows is unchanged and still drives the LIFO drain and
the debugfs dumps. Same lock, same SYNC, same F-256 keep-on-timeout path; no
register, descriptor or record-format change. Index memory: (mask + 1) list
heads per table (256 KiB at mask 0x7fff), kvcalloc'd with the table.
Idempotent; every anchor must match its expected count before anything is
written. After F-263.
"""

import sys

MARK = "F-264"
PCD = "drivers/net/ethernet/freescale/fman/fman_pcd.c"

FIND_OLD = """	list_for_each_entry(f, &t->flows, node) {
		if (f->key_size == key_size &&
		    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,
			    key, key_size)) {
			x = f;
			break;
		}
	}
	if (!x)
		return -ENOENT;
"""
FIND_NEW = """	/* F-264: only the key's own bucket can hold it. */
	hlist_for_each_entry(f, &t->bidx[fman_pcd_ehash_bucket_index(key, key_size,
						t->hash_shift, t->hash_mask)], bnode) {
		if (f->key_size == key_size &&
		    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,
			    key, key_size)) {
			x = f;
			break;
		}
	}
	if (!x)
		return -ENOENT;
"""

STATS_OLD = """	list_for_each_entry(f, &t->flows, node) {
		if (f->key_size == key_size &&
		    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,
			    key, key_size)) {
			hit = f;
			break;
		}
	}
"""
STATS_NEW = """	/* F-264: walk the key's bucket only; nf_flowtable polls every hardware
	 * flow each gc pass, so a whole-table scan here was O(N^2) per pass. */
	if (key_size == t->key_size)
		hlist_for_each_entry(f, &t->bidx[fman_pcd_ehash_bucket_index(key,
					key_size, t->hash_shift, t->hash_mask)], bnode) {
			if (f->key_size == key_size &&
			    !memcmp((u8 *)f->record + FMAN_EHASH_FLOW_KEY_OFF,
				    key, key_size)) {
				hit = f;
				break;
			}
		}
"""

EDITS = [
    # struct fields
    ("\tstruct list_head flows;\t\t/* sec.5 inserted flow records (DDR) */\n};",
     "\tstruct list_head flows;\t\t/* sec.5 inserted flow records (DDR) */\n"
     "\tstruct hlist_head *bidx;\t/* F-264: flows by bucket index, mask + 1 heads */\n};", 1),
    ("struct fman_pcd_ehash_flow {\n\tstruct list_head node;\n",
     "struct fman_pcd_ehash_flow {\n\tstruct list_head node;\n"
     "\tstruct hlist_node bnode;\t/* F-264: link in t->bidx[index] */\n", 1),
    # table lifetime
    ("\tt = kzalloc(sizeof(*t), GFP_KERNEL);\n\tif (!t)\n\t\treturn -ENOMEM;\n\n"
     "\terr = fman_pcd_ehash_int_buf_get(pcd);",
     "\tt = kzalloc(sizeof(*t), GFP_KERNEL);\n\tif (!t)\n\t\treturn -ENOMEM;\n"
     "\t/* F-264: per-bucket flow index (zeroed heads are empty lists). */\n"
     "\tt->bidx = kvcalloc((size_t)mask + 1, sizeof(*t->bidx), GFP_KERNEL);\n"
     "\tif (!t->bidx) {\n\t\tkfree(t);\n\t\treturn -ENOMEM;\n\t}\n\n"
     "\terr = fman_pcd_ehash_int_buf_get(pcd);", 1),
    ("err_free_t:\n\tkfree(t);\n\treturn err;",
     "err_free_t:\n\tkvfree(t->bidx);\t/* F-264 */\n\tkfree(t);\n\treturn err;", 1),
    ("\tfman_pcd_ehash_flow_drain(t);\n"
     "\tdma_free_coherent(t->dev, t->table_size, t->table_base, t->table_dma);\n\tkfree(t);",
     "\tfman_pcd_ehash_flow_drain(t);\n"
     "\tdma_free_coherent(t->dev, t->table_size, t->table_base, t->table_dma);\n"
     "\tkvfree(t->bidx);\t/* F-264 */\n\tkfree(t);", 1),
    # link / unlink next to the existing list operations
    ("\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */\n\treturn 0;",
     "\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */\n"
     "\thlist_add_head(&flow->bnode, &t->bidx[index]);\t/* F-264 */\n\treturn 0;", 1),
    ("\t\t*flow->bucket_h = flow->prev_head;\n\t\tlist_del(&flow->node);\n",
     "\t\t*flow->bucket_h = flow->prev_head;\n\t\tlist_del(&flow->node);\n"
     "\t\thlist_del(&flow->bnode);\t/* F-264 */\n", 1),
    ("\tlist_del(&x->node);\n\n\t/* F-254:",
     "\tlist_del(&x->node);\n\thlist_del(&x->bnode);\t/* F-264 */\n\n\t/* F-254:", 1),
    # lookups
    (" * collision chain AND the prev_head LIFO-drain invariant intact. O(n).",
     " * collision chain AND the prev_head LIFO-drain invariant intact. O(chain)\n"
     " * since F-264 (bucket index).", 1),
    (FIND_OLD, FIND_NEW, 1),
    ("\t\tlist_for_each_entry(y, &t->flows, node) {\n\t\t\tu64 y_next;\n",
     "\t\thlist_for_each_entry(y, &t->bidx[x->index], bnode) {\t/* F-264 */\n\t\t\tu64 y_next;\n", 1),
    (STATS_OLD, STATS_NEW, 1),
]


def main():
    try:
        with open(PCD) as f:
            src = f.read()
    except FileNotFoundError:
        print(f"### F-264: FATAL: {PCD} not found")
        sys.exit(1)
    if MARK in src:
        print("### F-264: already applied")
        return
    if "F-263" not in src:
        print("### F-264: FATAL: F-263 not applied (must run after F-263)")
        sys.exit(1)
    for i, (old, _new, want) in enumerate(EDITS):
        n = src.count(old)
        if n != want:
            print(f"### F-264: FATAL: anchor {i} in {PCD} found {n} times (expected {want})")
            sys.exit(1)
    for old, new, _want in EDITS:
        src = src.replace(old, new)
    with open(PCD, "w") as f:
        f.write(src)
    print("### F-264: ehash flows indexed by bucket; del_key/get_stats are O(chain)")


if __name__ == "__main__":
    main()
