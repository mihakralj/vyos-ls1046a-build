"""F-091: Modify __fman_pcd_fe_arm_engage to support HIT via scaffold.

When fe_enter_off != 0, instead of skipping the CONT_LOOKUP scaffold,
create it with numKeys=1 and a match-all entry that dispatches to FE_ENTER.
The miss-AD still routes to kernel FQ for non-matching flows.

This enables the HIT gate test: engage with FE_ENTER AD, insert ehash flow,
matching frames go through FE_ENTER → EXT_HASH → HIT → ENQ → TX FQ.

Disposition: fold-into 0158
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-091: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Find the scaffold block: "if (fe_enter_off == 0) {" 
scaffold_start = "if (fe_enter_off == 0) {"
if scaffold_start not in src:
    print("### F-091: scaffold block not found")
    sys.exit(0)

# Find the closing of the scaffold block and the subsequent arm_fe call
# We'll modify the block to handle fe_enter_off != 0 with numKeys=1
# The anchor: after the scaffold block, before fman_pcd_kg_port_arm_fe

# Strategy: wrap the existing scaffold block in if (fe_enter_off == 0) {...} else {...}
# where the else creates scaffold with numKeys=1 → FE_ENTER

old_block = """if (fe_enter_off == 0) {
\t\tstruct muram_info *muram = fman_get_muram(pcd->fman);
\t\tunsigned long gro, mto, ato;

\t\tif (muram) {
\t\t\tgro = fman_pcd_muram_alloc(pcd, 256);
\t\t\tmto = fman_pcd_muram_alloc(pcd, 16);
\t\t\tato = fman_pcd_muram_alloc(pcd, 32);
\t\t\tif (!IS_ERR_VALUE(gro) && !IS_ERR_VALUE(mto) &&
\t\t\t    !IS_ERR_VALUE(ato)) {
\t\t\t\tvoid __iomem *c;

\t\t\t\tc = (void __iomem *)
\t\t\t\t\tfman_muram_offset_to_vbase(muram, gro);
\t\t\t\tiowrite32be((0u << 24) | (mto & 0xFFFFFF),
\t\t\t\t\t    c + 0);
\t\t\t\tiowrite32be((ato & 0xFFFFFF), c + 4);
\t\t\t\tiowrite32be(0x4F000000, c + 8);
\t\t\t\tiowrite32be(0, c + 12);
\t\t\t\tc = (void __iomem *)
\t\t\t\t\tfman_muram_offset_to_vbase(muram, ato);
\t\t\t\tiowrite32be((u32)miss_fqid, c + 0);
\t\t\t\tiowrite32be(0, c + 4);
\t\t\t\tiowrite32be(0, c + 8);
\t\t\t\tiowrite32be(0, c + 12);
\t\t\t\tiowrite32be((u32)miss_fqid, c + 16);
\t\t\t\tiowrite32be(0, c + 20);
\t\t\t\tiowrite32be(0, c + 24);
\t\t\t\tiowrite32be(0, c + 28);
\t\t\t\tfe_enter_off = gro;

\t\t\t\tpcd->fe_scaffold_gro = gro;
\t\t\t\tpcd->fe_scaffold_mto = mto;
\t\t\t\tpcd->fe_scaffold_ato = ato;
\t\t\t}
\t\t}
\t}"""

if old_block not in src:
    # Try without tabs, with spaces
    print("### F-091: exact scaffold block not found — trying variant...")
    # Check for variant patterns
    if "if (fe_enter_off == 0)" not in src:
        print("### F-091: FATAL: fe_enter_off guard not found at all")
        sys.exit(1)

# The key change: replace "if (fe_enter_off == 0)" with unconditional scaffold + HIT option
# Instead of matching the whole block, let's change the logic:
# Always create scaffold, but if fe_enter_off != 0, set numKeys=1 and point at FE_ENTER

# Find the simplest anchor to modify
anchor1 = "iowrite32be((0u << 24) | (mto & 0xFFFFFF),"
if anchor1 not in src:
    print("### F-091: FATAL: numKeys write anchor not found")
    sys.exit(1)

# Replace the numKeys write to be dynamic: 0 for pass-through, 1 for HIT
old_write = "iowrite32be((0u << 24) | (mto & 0xFFFFFF),"
new_write = "iowrite32be(((fe_enter_off != 0 ? 1 : 0) << 24) | (mto & 0xFFFFFF),"
if old_write in src and new_write not in src:
    src = src.replace(old_write, new_write, 1)
    changes += 1
    print("### F-091: numKeys dynamic (0 for scaffold, 1 for HIT)")

# Now we need to add the HIT AD entry after the miss-AD entries.
# The ato table has 8 words: 4 for miss-AD[0], 4 for miss-AD[1]
# We need to add: HIT-AD[0] pointing at FE_ENTER (word0=0x80000000|fe_enter_off, rest=0)
# The HIT AD goes at ato + 32 (after the two miss-AD slots)

# Find the last write to ato (the second miss_fqid write)
hit_anchor = "iowrite32be(0, c + 28);"
if hit_anchor not in src:
    print("### F-091: FATAL: last AD write anchor 'iowrite32be(0, c + 28)' not found")
    sys.exit(1)
else:
    # v2 FIX (2026-07-31): The original code wrote raw fe_enter_off as word0,
    # which decodes as a bogus enqueue-AD to a nonexistent FQID (RM 8.7.4.3).
    # Per microcode reference Sec 7.7, the correct FE_ENTER AD content is:
    #   w0=0x40800000 (CONT_LOOKUP|NIA_ORDER_RESTOR)
    #   w1=0x00000000
    #   w2=0x000000F6 (OPC_FE_ENTER)
    #   w3=next-FE MURAM offset
    # We read the live FE_ENTER AD from MURAM and copy all 4 words.
    hit_insert = """iowrite32be(0, c + 28);
\t\t\t\t/* F-091 v2: if numKeys>0, copy real FE_ENTER AD to HIT-AD slot.
\t\t\t\t * Reads 4 words from the live FE_ENTER AD at fe_enter_off
\t\t\t\t * and writes them to ato+32 (HIT-AD[0]).  Per microcode
\t\t\t\t * reference Sec 7.7: w0=0x40800000, w1=0, w2=0xF6, w3=next-FE.
\t\t\t\t */
\t\t\t\tif (fe_enter_off != 0) {
\t\t\t\t\tvoid __iomem *fe_ad = (void __iomem *)
\t\t\t\t\t\tfman_muram_offset_to_vbase(muram, fe_enter_off);
\t\t\t\t\tiowrite32be(ioread32be(fe_ad + 0), c + 32);
\t\t\t\t\tiowrite32be(ioread32be(fe_ad + 4), c + 36);
\t\t\t\t\tiowrite32be(ioread32be(fe_ad + 8), c + 40);
\t\t\t\t\tiowrite32be(ioread32be(fe_ad + 12), c + 44);
\t\t\t\t}"""
    if "F-091 v2" not in src:
        src = src.replace(hit_anchor, hit_insert, 1)
        changes += 1
        print("### F-091 v2: HIT-AD copies real FE_ENTER AD (4-word ioread32be/iowrite32be)")
    else:
        print("### F-091 v2: HIT-AD copy already present")

# Also remove the outer if-guard and always allocate scaffold
# (since we always need the scaffold now)
guard = "if (fe_enter_off == 0) {\n\t\tstruct muram_info *muram"
if guard in src:
    new_guard = "{\n\t\tstruct muram_info *muram"
    src = src.replace(guard, new_guard, 1)
    changes += 1
    print("### F-091: removed fe_enter_off==0 guard (always create scaffold)")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-091: {changes} change(s) applied")
else:
    print("### F-091: no changes applied")
