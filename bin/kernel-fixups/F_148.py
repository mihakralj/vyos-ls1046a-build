"""F-148: Write flow key to CC match table on ehash flow insert.

The FE-VM ehash HIT path (Fork-B) has never been validated on silicon because
the hardware KG hash differs from the software CRC64 (F-141).  The CC-tree
exact-match path (Fork-A) was proven at M3 and M5 (10.259 Gbps) but is
limited to 32 flows.

This fixup bridges the gap: when a flow is inserted into the ehash, also
write the key bytes to the CC match table and update numKeys in the group
table.  The CC engine does exact-match comparison against the match table
entries, dispatching HIT→FE_ENTER and MISS→kernel FQ.

Changes:
1. Add struct fman_pcd *pcd back-pointer to struct fman_pcd_ehash_table
2. Set t->pcd in fman_pcd_ehash_table_set
3. In fman_pcd_ehash_add_key: after inserting into ehash, write key bytes
   to the CC match table (pcd->fe_scaffold_mto + flow_index * 16) and
   increment numKeys in the group table (pcd->fe_scaffold_gro)

The match table entry format: 16 bytes (key_size bytes of key + padding).
The AD table at pcd->fe_scaffold_ato has numKeys+1 entries:
  AD[0..numKeys-1]: HIT-AD pointing to FE_ENTER
  AD[numKeys]:      miss-AD pointing to kernel FQ

Must run AFTER F-091 (which creates the scaffold with mto/ato) and AFTER
0128 (which defines fman_pcd_ehash_add_key).
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-148: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Add pcd back-pointer to struct fman_pcd_ehash_table ──
# Find the struct definition and add pcd field
# The struct has: struct device *dev; (from 0130)
dev_field = "\tstruct device *dev;\t\t/* DMA device (fman_get_dev), for record alloc */"
if dev_field in src:
    new_dev = "\tstruct device *dev;\t\t/* DMA device (fman_get_dev), for record alloc */\n\tstruct fman_pcd *pcd;\t\t/* F-148: back-pointer for CC match table */"
    if "F-148" not in src:
        src = src.replace(dev_field, new_dev, 1)
        changes += 1
        print("### F-148: added pcd back-pointer to ehash_table")
    else:
        print("### F-148: pcd back-pointer already present")
else:
    print("### F-148: dev field not found in ehash_table struct")

# ── 2. Set t->pcd in fman_pcd_ehash_table_set ──
# Find where t->dev is set and add t->pcd after it
set_dev = "\tt->dev = dev;"
if set_dev in src:
    set_pcd = "\tt->dev = dev;\n\tt->pcd = pcd;\t/* F-148 */"
    if "t->pcd = pcd" not in src:
        src = src.replace(set_dev, set_pcd, 1)
        changes += 1
        print("### F-148: set t->pcd in ehash_table_set")
    else:
        print("### F-148: t->pcd already set")
else:
    print("### F-148: t->dev assignment not found")

# ── 3. In fman_pcd_ehash_add_key: write key to CC match table ──
# Find the list_add tail and add CC match table write before it
list_add = "\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */"
if list_add in src:
    cc_write = """\t/* F-148: Write key to CC match table for exact-match dispatch.
\t * The CC engine compares the extracted key against match table entries.
\t * Matching frames go to HIT-AD (FE_ENTER); non-matching to miss-AD (kernel).
\t * Limited to FMAN_CC_MAX_STATIC_KEYS (32) entries.
\t */
\tif (t->pcd && t->pcd->fe_scaffold_mto) {
\t\tvoid __iomem *mt = (void __iomem *)fman_muram_offset_to_vbase(
\t\t\tfman_get_muram(t->pcd->fman), t->pcd->fe_scaffold_mto);
\t\tvoid __iomem *gt = (void __iomem *)fman_muram_offset_to_vbase(
\t\t\tfman_get_muram(t->pcd->fman), t->pcd->fe_scaffold_gro);
\t\tu32 nkeys;
\t\tint i;

\t\tnkeys = (ioread32be(gt) >> 24) & 0xFF;
\t\tif (nkeys < 32) {
\t\t\t/* Write key bytes to match table entry nkeys */
\t\t\tfor (i = 0; i < key_size; i++)
\t\t\t\tiowrite8(key[i], mt + nkeys * 16 + i);
\t\t\t/* Pad remaining bytes with zero */
\t\t\tfor (; i < 16; i++)
\t\t\t\tiowrite8(0, mt + nkeys * 16 + i);

\t\t\t/* Write HIT-AD at ato + nkeys*16 pointing to FE_ENTER */
\t\t\tif (t->pcd->fe_scaffold_ato) {
\t\t\t\tvoid __iomem *at = (void __iomem *)fman_muram_offset_to_vbase(
\t\t\t\t\tfman_get_muram(t->pcd->fman), t->pcd->fe_scaffold_ato);
\t\t\t\tiowrite32be((u32)t->pcd->fe_root_ad_off, at + nkeys * 16 + 0);
\t\t\t\tiowrite32be(0, at + nkeys * 16 + 4);
\t\t\t\tiowrite32be(0, at + nkeys * 16 + 8);
\t\t\t\tiowrite32be(0, at + nkeys * 16 + 12);
\t\t\t}

\t\t\t/* Increment numKeys in group table */
\t\t\tnkeys++;
\t\t\tiowrite32be((nkeys << 24) | (ioread32be(gt) & 0x00FFFFFF), gt);
\t\t}
\t}

\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */"""
    if "F-148" not in src:
        src = src.replace(list_add, cc_write, 1)
        changes += 1
        print("### F-148: added CC match table key write in ehash_add_key")
    else:
        print("### F-148: CC match table write already present")
else:
    print("### F-148: list_add not found in ehash_add_key")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-148: {changes} change(s) applied")
else:
    print("### F-148: no changes — may already be present")
    sys.exit(0)