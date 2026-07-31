"""F-091 v3: Modify __fman_pcd_fe_arm_engage to support HIT via scaffold.

When fe_enter_off != 0, instead of skipping the CONT_LOOKUP scaffold,
create it with numKeys=1 (dynamic write only -- no engage-time HIT-AD
content write; see v3 note below).  The miss-AD still routes to kernel
FQ for non-matching flows.

v3 (2026-07-31): REMOVED the engage-time HIT-AD write at ato+32 that
v1/v2 (this session) added.  `ato` is allocated as exactly 32 bytes and
is already fully used by two pre-existing 16-byte miss-AD copies
(ato+0..15, ato+16..31); any write at ato+32+ is a MURAM buffer overflow
into whatever object gen_pool placed next, regardless of content.  This
branch is dead in production (fman_pcd_fe_engage() always passes
fe_enter_off=0) and redundant with F-148 v4, which correctly writes the
real HIT-AD content to the in-bounds ato+0 slot when a flow is inserted.
See the inline comment at the removal site for full detail.

The HIT gate test now relies entirely on F-148 v4's flow-insert-time
write for HIT-AD population: engage (numKeys stays 0 at engage time in
production; debugfs engage with non-zero offset sets numKeys=1 but
writes no AD content), insert ehash flow (F-148 v4 writes key + real
HIT-AD to slot ato+0), matching frames go through FE_ENTER → EXT_HASH →
HIT → ENQ → TX FQ.

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

# F-091 v4 (2026-07-31): mto MUST be 64 bytes (2 rows x 32B), not 16.
#
# The CC match table format (authoritative in-tree implementation, patch
# 0098 cc_pack_key + RM 8.7.4.2): each match row is key(16B) followed by
# mask(16B) = 2 * CC_KEY_SIZE = 32 bytes per row, and the table has
# (num_keys+1) rows (trailing row = miss slot).  For our single-key
# scaffold (numKeys 0 -> 1 after the first flow insert) that is 2 rows x
# 32B = 64 bytes.  A 16-byte allocation holds only a bare key with NO
# mask and NO trailing row -- the CC engine would read uninitialized
# MURAM as the mask, so the key comparison could never match, which is
# why HIT dispatch has failed on every attempt with this scaffold.
# Keep in sync with F_125.py (unwind alloc/free) and F_139.py (free size).
alloc16 = "\t\t\tmto = fman_pcd_muram_alloc(pcd, 16);"
alloc64 = "\t\t\tmto = fman_pcd_muram_alloc(pcd, 64);"
if alloc16 in src and alloc64 not in src:
    src = src.replace(alloc16, alloc64, 1)
    changes += 1
    print("### F-091 v4: mto alloc 16 -> 64 B (key+mask rows per RM 8.7.4.2)")
elif alloc64 in src:
    print("### F-091 v4: mto alloc already 64 B")

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

# F-091 v3 (2026-07-31): DO NOT add a third HIT-AD entry at ato+32.
#
# `ato` is allocated as exactly 32 bytes (fman_pcd_muram_alloc(pcd, 32)).
# The pre-existing scaffold code above already fills the FULL 32 bytes
# with two duplicate 16-byte miss-AD copies (c+0..15 and c+16..31). Any
# write at c+32 or beyond is 16+ bytes PAST THE END of this allocation --
# a genuine MURAM buffer overflow into whatever object gen_pool placed
# next, regardless of what content is written there.
#
# v1 (this session) wrote a raw fe_enter_off offset to c+32 (decodes as
# a bogus enqueue-AD per RM 8.7.4.3 -- wrong content, AND out of bounds).
# v2 (this session) fixed the CONTENT (a real 4-word FE_ENTER AD copy via
# ioread32be/iowrite32be) but left the out-of-bounds LOCATION unfixed.
#
# v3 deletes this branch entirely. It is:
#   1. Dead in production: fman_pcd_fe_engage() (F-092) always calls
#      __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, ...) with
#      fe_enter_off hardcoded 0, so "if (fe_enter_off != 0)" never fires
#      during normal ask.ko engage.
#   2. Redundant: F-148 v4 already writes the correct HIT-AD content to
#      ato+0 (the in-bounds first-key slot) when a flow is inserted via
#      fman_pcd_ehash_add_key(), overwriting one of the two pre-existing
#      miss-AD duplicates. This matches RM 8.7.4.3's AD-table sizing
#      formula "(num_keys+1)*16 bytes" for the numKeys 0->1 transition
#      (32 bytes = (1+1)*16, exactly what's allocated).
#
# No replacement code is inserted. The pre-existing "iowrite32be(0, c + 28);"
# (last byte of the two-miss-AD-copy block) is left untouched.
hit_anchor = "iowrite32be(0, c + 28);"
if hit_anchor not in src:
    print("### F-091: FATAL: last AD write anchor 'iowrite32be(0, c + 28)' not found")
    sys.exit(1)
else:
    print("### F-091 v3: no HIT-AD write added at ato+32 (would overflow 32B buffer; F-148 v4 covers the in-bounds case)")

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
