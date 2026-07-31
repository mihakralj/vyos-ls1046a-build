"""F-148 v2: Write flow key to CC match table on ehash flow insert.

The FE-VM ehash HIT path (Fork-B) has never been validated on silicon because
the hardware KG hash differs from software CRC64 (F-141).  The CC-tree
exact-match path (Fork-A) was proven at M3 and M5 (10.259 Gbps) but is
limited to 32 flows.

This fixup bridges the gap: when a flow is inserted into the ehash, also
write the key bytes to the CC match table and update numKeys in the group
table.  The CC engine does exact-match comparison against the match table
entries, dispatching HIT→FE_ENTER and MISS→kernel FQ.

v2: Use per-port scaffold fields (fp->scaffold_mto/gro/ato) instead of
    singleton pcd->fe_scaffold_* (which F-139 zeroed).  Walk pcd->fe_ports
    to find the first armed port's scaffold.

Changes:
1. Add struct fman_pcd *pcd back-pointer to struct fman_pcd_ehash_table
2. Set t->pcd in fman_pcd_ehash_table_set
3. In fman_pcd_ehash_add_key: after inserting into ehash, find the armed
   port's scaffold, write key bytes to the CC match table, and increment
   numKeys in the group table.

Must run AFTER F-091 (scaffold creation), F-139 (per-port scaffold), and
0128 (fman_pcd_ehash_add_key).
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
# Find the list_add tail and add CC match table write before it.
# v2: Use per-port scaffold fields (fp->scaffold_*) via pcd->fe_ports walk.
list_add = "\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */"
if list_add in src:
    # Check if v1 code is already present (has pcd->fe_scaffold_mto)
    if "pcd->fe_scaffold_mto" in src:
        # v1 code present — replace with v2
        old_v1 = """\t/* F-148: Write key to CC match table for exact-match dispatch.
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
        cc_write_v2 = """\t/* F-148 v2: Write key to CC match table for exact-match dispatch.
\t * Walks pcd->fe_ports to find the armed port's per-port scaffold
\t * (fp->scaffold_mto/gro/ato, set by F-139).  The CC engine compares
\t * the extracted key against match table entries; HIT→FE_ENTER, MISS→kernel.
\t * Limited to FMAN_CC_MAX_STATIC_KEYS (32) entries.
\t */
\tif (t->pcd) {
\t\tstruct fman_pcd_fe_port *fp;
\t\tunsigned long mto = 0, gro = 0, ato = 0;

\t\tlist_for_each_entry(fp, &t->pcd->fe_ports, node) {
\t\t\tif (fp->scaffold_gro) {
\t\t\t\tgro = fp->scaffold_gro;
\t\t\t\tmto = fp->scaffold_mto;
\t\t\t\tato = fp->scaffold_ato;
\t\t\t\tbreak;
\t\t\t}
\t\t}
\t\tif (mto && gro) {
\t\t\tvoid __iomem *mt = (void __iomem *)fman_muram_offset_to_vbase(
\t\t\t\tfman_get_muram(t->pcd->fman), mto);
\t\t\tvoid __iomem *gt = (void __iomem *)fman_muram_offset_to_vbase(
\t\t\t\tfman_get_muram(t->pcd->fman), gro);
\t\t\tu32 nkeys;
\t\t\tint i;

\t\t\tnkeys = (ioread32be(gt) >> 24) & 0xFF;
\t\t\tif (nkeys < 32) {
\t\t\t\tfor (i = 0; i < key_size; i++)
\t\t\t\t\tiowrite8(key[i], mt + nkeys * 16 + i);
\t\t\t\tfor (; i < 16; i++)
\t\t\t\t\tiowrite8(0, mt + nkeys * 16 + i);

\t\t\t\tif (ato) {
\t\t\t\t\tvoid __iomem *at = (void __iomem *)fman_muram_offset_to_vbase(
\t\t\t\t\t\tfman_get_muram(t->pcd->fman), ato);
\t\t\t\t\tiowrite32be((u32)t->pcd->fe_root_ad_off, at + nkeys * 16 + 0);
\t\t\t\t\tiowrite32be(0, at + nkeys * 16 + 4);
\t\t\t\t\tiowrite32be(0, at + nkeys * 16 + 8);
\t\t\t\t\tiowrite32be(0, at + nkeys * 16 + 12);
\t\t\t\t}

\t\t\t\tnkeys++;
\t\t\t\tiowrite32be((nkeys << 24) | (ioread32be(gt) & 0x00FFFFFF), gt);
\t\t\t}
\t\t}
\t}

\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */"""
        src = src.replace(old_v1, cc_write_v2, 1)
        changes += 1
        print("### F-148 v2: replaced v1 singleton scaffold with per-port walk")
    else:
        print("### F-148: v1 code not found, v2 not applied")
else:
    print("### F-148: list_add not found in ehash_add_key")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-148: {changes} change(s) applied")
else:
    print("### F-148: no changes — may already be present")
    sys.exit(0)