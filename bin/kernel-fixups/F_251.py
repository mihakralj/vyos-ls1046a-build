"""F-251 (T-M6-2 B2, 2026-09-16): fix fman_pcd_cc_seq_dump()'s match-table
print-length bug and add a live AD-table dump -- the missing oracle for
directly verifying what the CC leaf's enqueue action actually contains in
hardware, needed to keep chasing plan §8.2b (CC-hit -> CPU-bypassed
hardware forward still not observed despite FMan's own QMI counters
proving the AD's enqueue action genuinely succeeds at the enqueue step).

Two independent fixes, found while looking for exactly this AD-dump
capability:

1. BUG: the match-table dump hardcodes `len = num_keys * 32` (assuming
   every key is the default 16-byte key+mask pair), but CC_KEY_SIZE_L2
   (bridge_l2 trees, this session's own work) is 15 bytes, so the real
   per-row width is 2*15=30, not 32. Every debugfs readback of a bridge_l2
   tree's match table this session printed 2 bytes PAST the real match
   record into whatever MURAM content follows it -- cosmetic (those 2
   bytes happened to be masked-out/wildcard positions the readback also
   correctly showed as the trailing zeros, so it did not change this
   session's conclusions about DA/PORT_ID correctness), but wrong, and
   would matter for a wider dual_lane (46B) or v6 (40B) tree too. Fixed by
   using the tree's own t->key_size instead of the hardcoded constant --
   same fix class as the struct's own key_size field comment already
   flags ("cc_write_group0() needs the REAL per-row width... not just the
   global v4 constant").

2. GAP: no debugfs read path ever showed the AD table content at all --
   only the match table. Every prior diagnosis of the §8.2b mystery
   (FMan QMI counters, ethtool driver stats, tcpdump at the far end) was
   necessarily indirect, since there was no way to directly read back
   what fqid/NIA/NADEN bits actually landed in the live AD-table hardware
   structure versus what the software (cc_write_leaf_ad()) believes it
   wrote. Adds that dump, right after the match table, for every
   installed key's row plus the trailing miss row (t->num_keys + 1 rows
   x CC_AD_ENTRY_SIZE bytes each).

Must run after 0195-era CC infrastructure (fman_pcd_cc_seq_dump,
CC_AD_ENTRY_SIZE must already exist). Idempotent.
"""

import os
import sys

cc_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc.c"

if not os.path.exists(cc_c):
    print(f"### F-251: {cc_c} not found")
    sys.exit(0)

marker = "F-251(cc-seq-dump-ad-table)"

with open(cc_c) as f:
    src = f.read()

if marker in src:
    print("### F-251: already applied")
    sys.exit(0)

if "fman_pcd_cc_seq_dump" not in src or "CC_AD_ENTRY_SIZE" not in src:
    print("### F-251: FATAL: fman_pcd_cc_seq_dump/CC_AD_ENTRY_SIZE not found")
    sys.exit(1)

# ---------------------------------------------------------------------
# 1. Fix the hardcoded "* 32" match-table print length.
# ---------------------------------------------------------------------
anchor1 = (
    "\t\t\t\tunsigned int i, len = (unsigned int)t->num_keys * 32;\n"
)
n = src.count(anchor1)
if n != 1:
    print(f"### F-251: FATAL: expected 1 hardcoded-32 anchor, found {n}")
    sys.exit(1)
new1 = (
    f"\t\t\t\t/* {marker}: was hardcoded * 32 (assumes the default 16B\n"
    "\t\t\t\t * key+mask pair); a bridge_l2 tree's real row is 2*15=30B,\n"
    "\t\t\t\t * dual_lane's is 2*46=92B, etc. -- use the tree's own\n"
    "\t\t\t\t * key_size, same fix class as cc_write_group0()'s own\n"
    "\t\t\t\t * comment already flags. */\n"
    "\t\t\t\tunsigned int i, len = (unsigned int)t->num_keys * 2 * t->key_size;\n"
)
src = src.replace(anchor1, new1, 1)

# ---------------------------------------------------------------------
# 2. Add an AD-table dump right after the match-table dump's closing
#    "seq_puts(m, \"\\n\");" (inside the "if (muram && t->match_off)"
#    block, before its closing brace).
# ---------------------------------------------------------------------
anchor2 = (
    "\t\t\t\t\tseq_printf(m, \" %02x\",\n"
    "\t\t\t\t\t\t   ioread8((u8 __iomem *)mt + i));\n"
    "\t\t\t\t}\n"
    "\t\t\t\tseq_puts(m, \"\\n\");\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
    "\tmutex_unlock(fman_pcd_get_lock(pcd));\n"
    "}\n"
)
if anchor2 not in src:
    print("### F-251: FATAL: match-table closing anchor not found")
    sys.exit(1)
if src.count(anchor2) != 1:
    print(f"### F-251: FATAL: anchor2 not unique ({src.count(anchor2)})")
    sys.exit(1)
new2 = (
    "\t\t\t\t\tseq_printf(m, \" %02x\",\n"
    "\t\t\t\t\t\t   ioread8((u8 __iomem *)mt + i));\n"
    "\t\t\t\t}\n"
    "\t\t\t\tseq_puts(m, \"\\n\");\n"
    "\t\t\t}\n"
    f"\t\t\t/* {marker}: AD table -- the missing oracle for §8.2b.\n"
    "\t\t\t * (num_keys+1) rows (the trailing row is the miss AD),\n"
    "\t\t\t * CC_AD_ENTRY_SIZE=16B each: word0|word1|word2|word3 in\n"
    "\t\t\t * that byte order, matching cc_write_leaf_ad()'s own\n"
    "\t\t\t * iowrite32be() layout -- fqid (or RES_DATA_FLOW for a\n"
    "\t\t\t * soft fall-through), hm>>4 (0 if no HMTD), the NIA word\n"
    "\t\t\t * (CC_NIA_FMCTL_PRE_BMI_ENQ, optionally |NADEN|EXTENDED),\n"
    "\t\t\t * 0. Lets a live install be checked against what the\n"
    "\t\t\t * software believes it wrote, not just trusted. */\n"
    "\t\t\tif (muram && t->ad_off) {\n"
    "\t\t\t\tvoid __iomem *adt = (void __iomem *)\n"
    "\t\t\t\t\tfman_muram_offset_to_vbase(muram, t->ad_off);\n"
    "\t\t\t\tunsigned int j, ad_len = ((unsigned int)t->num_keys + 1) *\n"
    "\t\t\t\t\tCC_AD_ENTRY_SIZE;\n"
    "\n"
    "\t\t\t\tseq_puts(m, \"  AD table (row = 16B: w0 w1 w2 w3):\");\n"
    "\t\t\t\tfor (j = 0; j < ad_len; j++) {\n"
    "\t\t\t\t\tif (j % 16 == 0)\n"
    "\t\t\t\t\t\tseq_printf(m, \"\\n    %04x:\", j);\n"
    "\t\t\t\t\tseq_printf(m, \" %02x\",\n"
    "\t\t\t\t\t\t   ioread8((u8 __iomem *)adt + j));\n"
    "\t\t\t\t}\n"
    "\t\t\t\tseq_puts(m, \"\\n\");\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
    "\tmutex_unlock(fman_pcd_get_lock(pcd));\n"
    "}\n"
)
src = src.replace(anchor2, new2, 1)

with open(cc_c, "w") as f:
    f.write(src)
print("### fman_pcd_cc.c: F-251 AD-table dump added, match-table print-length bug fixed")
