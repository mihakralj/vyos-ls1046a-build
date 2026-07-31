"""F-148 v4: Write flow key to CC match table on ehash insert, increment numKeys.

The FE-VM ehash path (Fork-B) cannot produce a HIT because the CONT_LOOKUP
group table has numKeys=0, routing ALL frames to the miss-AD → kernel.
The FE-VM is never entered, so the ehash DDR table is never consulted.

To enter the FE-VM, the CC engine must match a key in the group table's
match table.  This requires:
1. numKeys > 0 in the group table
2. Per-flow match entries in the match table (mto)
3. Per-flow HIT-AD entries in the AD table (ato) pointing to FE_ENTER

This fixup writes the flow key to the CC match table and increments numKeys
when a flow is inserted via fman_pcd_ehash_add_key().  The CC engine does
exact-match comparison; matching frames go to FE_ENTER → EXT_HASH → ehash
lookup → HIT → MUX → TRANSITION → ENQ.  Non-matching frames go to miss-AD
→ kernel.

Limited to FMAN_CC_MAX_STATIC_KEYS (32) entries.  The ehash DDR table
provides scale beyond 32 by allowing hash-based lookup within the FE-VM.

v4 fixes bug (a) found by code review: the HIT-AD slot must contain a COPY
of the four words at pcd->fe_root_ad_off (the standalone FE_ENTER AD per
microcode reference Sec 7.7: w0=0x40800000, w1=0, w2=0x000000F6,
w3=EXT_HASH offset), not the raw offset NUMBER written as word0.  v3's
"iowrite32be(fe_root_ad_off, at+0)" decoded as an RM 8.7.4.3 enqueue-AD
to a nonexistent FQID -- the fix is a 4-word ioread/iowrite copy.

v3: Uses per-port scaffold fields (fp->scaffold_mto/gro/ato) via
    pcd->fe_ports walk.  Adds pr_info diagnostics for debugging.

Must run AFTER F-091 (scaffold creation), F-139 (per-port scaffold),
and 0128 (fman_pcd_ehash_add_key).
"""

import sys, os

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
    print("### F-148: FATAL: dev field not found in ehash_table struct")
    sys.exit(1)

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
    print("### F-148: FATAL: t->dev assignment not found")
    sys.exit(1)

# ── 3. In fman_pcd_ehash_add_key: write key to CC match table ──
list_add = "\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */"
if list_add in src:
    cc_write = """\t/* F-148 v4: Write key to CC match table so the CC engine can dispatch
\t * matching frames to FE_ENTER.  Without this, numKeys=0 routes ALL
\t * frames to the miss-AD and the FE-VM ehash is never consulted.
\t * Limited to 32 entries (FMAN_CC_MAX_STATIC_KEYS).
\t *
\t * v4 fixes bug (a): the HIT-AD slot must contain a COPY of the four
\t * words already at pcd->fe_root_ad_off (the standalone FE_ENTER AD:
\t * w0=0x40800000 CONT_LOOKUP|NIA_ORDER_RESTOR, w1=0, w2=0x000000F6
\t * OPC_FE_ENTER, w3=EXT_HASH offset -- microcode reference Sec 7.7),
\t * NOT the raw offset NUMBER written as word0.  A bare word0 with a
\t * zero top byte decodes as an enqueue-AD to FQID=that number (RM
\t * 8.7.4.3), which is a nonexistent FQ -- v3's bug.
\t */
\tif (t->pcd) {
\t\tstruct fman_pcd_fe_port *fp;
\t\tunsigned long mto = 0, gro = 0, ato = 0;

\t\t/* Find the armed port's per-port scaffold (F-139). */
\t\tlist_for_each_entry(fp, &t->pcd->fe_ports, node) {
\t\t\tif (fp->scaffold_gro) {
\t\t\t\tgro = fp->scaffold_gro;
\t\t\t\tmto = fp->scaffold_mto;
\t\t\t\tato = fp->scaffold_ato;
\t\t\t\tbreak;
\t\t\t}
\t\t}
\t\tif (mto && gro) {
\t\t\tstruct muram_info *muram = fman_get_muram(t->pcd->fman);
\t\t\tvoid __iomem *mt = (void __iomem *)fman_muram_offset_to_vbase(muram, mto);
\t\t\tvoid __iomem *gt = (void __iomem *)fman_muram_offset_to_vbase(muram, gro);
\t\t\tu32 gw0 = ioread32be(gt);
\t\t\tu32 nkeys = (gw0 >> 24) & 0xFF;
\t\t\tint i;

\t\t\tif (nkeys < 32) {
\t\t\t\tfor (i = 0; i < key_size; i++)
\t\t\t\t\tiowrite8(key[i], mt + nkeys * 16 + i);
\t\t\t\tfor (; i < 16; i++)
\t\t\t\t\tiowrite8(0, mt + nkeys * 16 + i);

\t\t\t\tif (ato && t->pcd->fe_root_ad_off) {
\t\t\t\t\tvoid __iomem *at = (void __iomem *)fman_muram_offset_to_vbase(muram, ato);
\t\t\t\t\tvoid __iomem *root = (void __iomem *)fman_muram_offset_to_vbase(
\t\t\t\t\t\tmuram, t->pcd->fe_root_ad_off);
\t\t\t\t\t/* Copy the real FE_ENTER AD content (4 words), not the offset. */
\t\t\t\t\tiowrite32be(ioread32be(root + 0), at + nkeys * 16 + 0);
\t\t\t\t\tiowrite32be(ioread32be(root + 4), at + nkeys * 16 + 4);
\t\t\t\t\tiowrite32be(ioread32be(root + 8), at + nkeys * 16 + 8);
\t\t\t\t\tiowrite32be(ioread32be(root + 12), at + nkeys * 16 + 12);
\t\t\t\t}

\t\t\t\tnkeys++;
\t\t\t\tiowrite32be((nkeys << 24) | (gw0 & 0x00FFFFFF), gt);

\t\t\t\tpr_info(\"fman_pcd: F-148 CC key[%u] written, nkeys=%u\\n\",
\t\t\t\t\t nkeys - 1, nkeys);
\t\t\t} else {
\t\t\t\tpr_warn(\"fman_pcd: F-148 CC match table full (32 keys)\\n\");
\t\t\t}
\t\t}
\t}

\tlist_add(&flow->node, &t->flows);\t/* head-add => LIFO drain */"""
    if "F-148 v4" not in src:
        # Remove any prior v3 block first (idempotent replace)
        v3_marker_start = src.find("\t/* F-148 v3:")
        if v3_marker_start != -1:
            v3_marker_end = src.find(list_add, v3_marker_start) + len(list_add)
            src = src[:v3_marker_start] + cc_write + src[v3_marker_end:]
            changes += 1
            print("### F-148 v4: replaced v3 with corrected HIT-AD content copy (bug a fix)")
        else:
            src = src.replace(list_add, cc_write, 1)
            changes += 1
            print("### F-148 v4: added CC match table key write with corrected HIT-AD copy")
    else:
        print("### F-148: v4 code already present")
else:
    print("### F-148: FATAL: list_add not found in ehash_add_key")
    sys.exit(1)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-148: {changes} change(s) applied")
else:
    print("### F-148: no changes — may already be present")
    sys.exit(0)