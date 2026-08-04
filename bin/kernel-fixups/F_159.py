"""F-159: fix cc_pack_key()'s KG composite to THIS branch's real EKFC, and
add a raw-byte ground-truth dump to the CC-tree test harness (cc_test).

CONTEXT (2026-08-04 code review, plans/CC-TREE-REBUILD-PLAN.md Phase 0):
patch 0108 rewrote cc_pack_key() (drivers/net/ethernet/freescale/fman/
fman_pcd_cc.c) to emit [SIP(4)|DIP(4)|SPI(4)=0|SPORT(2)|DPORT(2)] = 16 bytes,
citing "KGSE_EKFC 0x00180206" -- that is the sibling ask20 branch's EKFC
scheme. THIS branch's real, silicon-verified EKFC is 0x001C0006 = SIP|DIP|
PROTO|SPORT|DPORT (specs/fman-keygen-flow-key-spec.md section 3.1: MSB-first
descending order, CONFIRMED 2026-07-13 by CRC-64 hardware match against the
EHASH/DDR workspace key -- the same order, not yet independently observed
against the CC comparator specifically, see specs/
cc-comparator-compare-window-hypothesis.md). Running the 0107/0108 debugfs
CC-tree harness as-is would very likely produce a false-negative MISS caused
by this layout mismatch, not real evidence about CC-tree/silicon capability.

This fixup:
  1. Rewrites cc_pack_key()'s field-packing block to SIP(4)@0-3 | DIP(4)@4-7
     | PROTO(1)@8 | SPORT(2)@9-10 | DPORT(2)@11-12 (13 B; bytes 13-15 stay
     wildcard/unset, matching CC_KEY_SIZE=16). Field-internal byte order
     (how SPORT/DPORT's two bytes are laid out) is left exactly as 0108
     wrote it -- only field POSITION and the missing PROTO field are fixed;
     that narrower byte-order question is out of scope here.
  2. Extends fman_pcd_cc_seq_dump() (the read handler behind the existing
     `cc_test` debugfs node from patch 0107) to hex-dump each installed
     tree's raw match-table bytes (64 B: 2 rows x key16+mask16), so a board
     session can directly SEE what was written -- same ground-truth
     philosophy as F-158's fe_scaffold, applied to the CC-tree harness
     instead of the FE-VM/ehash scaffold.

This is a hypothesis fix, not a confirmed one: the field ORDER above is the
best available evidence (independently silicon-validated for the EHASH
path), but has never been directly observed against the CC comparator's own
compare window. The dump added here is what makes that direct observation
possible on the next board session -- do not treat a subsequent board HIT/
MISS result as settling the layout question without also reading this dump.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
cc_c = os.path.join(kroot, "fman_pcd_cc.c")

if not os.path.exists(cc_c):
    print("### F-159: fman_pcd_cc.c not found")
    sys.exit(0)

with open(cc_c) as f:
    src = f.read()

changes = 0

# ── 1. fix cc_pack_key()'s field-packing block ──
old_pack = (
    "\tif (k->present & FMAN_PCD_CC_HW_F_SRC_IP) {\n"
    "\t\tmemcpy(&key[0], &k->src_ip_be, 4);\n"
    "\t\tmsk[0] = msk[1] = msk[2] = msk[3] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_DST_IP) {\n"
    "\t\tmemcpy(&key[4], &k->dst_ip_be, 4);\n"
    "\t\tmsk[4] = msk[5] = msk[6] = msk[7] = 0xff;\n"
    "\t}\n"
    "\t/* bytes 8..11: IPsec SPI \u2014 exact-match zero (HW-proven shape). */\n"
    "\tmsk[8] = msk[9] = msk[10] = msk[11] = 0xff;\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_SRC_PORT) {\n"
    "\t\tkey[12] = (u8)(k->src_port_be & 0xff);\n"
    "\t\tkey[13] = (u8)(k->src_port_be >> 8);\n"
    "\t\tmsk[12] = msk[13] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_DST_PORT) {\n"
    "\t\tkey[14] = (u8)(k->dst_port_be & 0xff);\n"
    "\t\tkey[15] = (u8)(k->dst_port_be >> 8);\n"
    "\t\tmsk[14] = msk[15] = 0xff;\n"
    "\t}\n"
)
new_pack = (
    "\t/* F-159 (2026-08-04): dpaa1 EKFC=0x001C0006 order (MSB-first\n"
    "\t * descending, CONFIRMED 2026-07-13 for the EHASH path by CRC-64\n"
    "\t * hardware match; NOT yet independently observed against the CC\n"
    "\t * comparator -- see the cc_test dump this fixup also adds, and\n"
    "\t * specs/cc-comparator-compare-window-hypothesis.md). Replaces the\n"
    "\t * ask20-branch composite (SIP|DIP|SPI|SPORT|DPORT, EKFC 0x00180206)\n"
    "\t * patch 0108 wrote, which does not match this branch's KG scheme.\n"
    "\t */\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_SRC_IP) {\n"
    "\t\tmemcpy(&key[0], &k->src_ip_be, 4);\n"
    "\t\tmsk[0] = msk[1] = msk[2] = msk[3] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_DST_IP) {\n"
    "\t\tmemcpy(&key[4], &k->dst_ip_be, 4);\n"
    "\t\tmsk[4] = msk[5] = msk[6] = msk[7] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_PROTO) {\n"
    "\t\tkey[8] = k->proto;\n"
    "\t\tmsk[8] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_SRC_PORT) {\n"
    "\t\tkey[9] = (u8)(k->src_port_be & 0xff);\n"
    "\t\tkey[10] = (u8)(k->src_port_be >> 8);\n"
    "\t\tmsk[9] = msk[10] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_DST_PORT) {\n"
    "\t\tkey[11] = (u8)(k->dst_port_be & 0xff);\n"
    "\t\tkey[12] = (u8)(k->dst_port_be >> 8);\n"
    "\t\tmsk[11] = msk[12] = 0xff;\n"
    "\t}\n"
    "\t/* bytes 13..15: unused by this EKFC -- stay wildcard (mask 0). */\n"
)
if new_pack in src:
    print("### F-159: cc_pack_key() dpaa1 EKFC fix already present")
elif old_pack in src:
    src = src.replace(old_pack, new_pack, 1)
    changes += 1
    print("### F-159: cc_pack_key() rewritten to dpaa1 EKFC=0x001C0006 order")
else:
    print(
        "### F-159: FATAL: expected patch-0108 cc_pack_key() field-packing "
        "block not found verbatim -- source has likely drifted from what "
        "this fixup was written against. Refusing to guess; fix the anchor "
        "text in F_159.py against the current fman_pcd_cc.c before retrying."
    )
    sys.exit(1)

# ── 2. extend fman_pcd_cc_seq_dump() with a raw match-table hex dump ──
old_dump = (
    "\tmutex_lock(fman_pcd_get_lock(pcd));\n"
    "\tif (list_empty(head))\n"
    "\t\tseq_puts(m, \"(no CC trees installed)\\n\");\n"
    "\telse\n"
    "\t\tlist_for_each_entry(t, head, node)\n"
    "\t\t\tseq_printf(m,\n"
    "\t\t\t\t   \"port 0x%02x: %u keys, group=0x%lx match=0x%lx ad=0x%lx\\n\",\n"
    "\t\t\t\t   t->port_id, t->num_keys, t->group_off,\n"
    "\t\t\t\t   t->match_off, t->ad_off);\n"
    "\tmutex_unlock(fman_pcd_get_lock(pcd));\n"
)
new_dump = (
    "\tmutex_lock(fman_pcd_get_lock(pcd));\n"
    "\tif (list_empty(head)) {\n"
    "\t\tseq_puts(m, \"(no CC trees installed)\\n\");\n"
    "\t} else {\n"
    "\t\tstruct muram_info *muram = fman_get_muram(fman_pcd_get_fman(pcd));\n"
    "\n"
    "\t\tlist_for_each_entry(t, head, node) {\n"
    "\t\t\tseq_printf(m,\n"
    "\t\t\t\t   \"port 0x%02x: %u keys, group=0x%lx match=0x%lx ad=0x%lx\\n\",\n"
    "\t\t\t\t   t->port_id, t->num_keys, t->group_off,\n"
    "\t\t\t\t   t->match_off, t->ad_off);\n"
    "\t\t\t/* F-159: raw match-table ground truth -- (num_keys) rows of\n"
    "\t\t\t * key(16B)+mask(16B)=32B, so the actual written bytes can be\n"
    "\t\t\t * checked against the cc_pack_key() composite directly,\n"
    "\t\t\t * instead of trusting the packer blindly (same philosophy as\n"
    "\t\t\t * F-158's fe_scaffold dump for the FE-VM/ehash path). */\n"
    "\t\t\tif (muram && t->match_off) {\n"
    "\t\t\t\tvoid __iomem *mt = (void __iomem *)\n"
    "\t\t\t\t\tfman_muram_offset_to_vbase(muram, t->match_off);\n"
    "\t\t\t\tunsigned int i, len = (unsigned int)t->num_keys * 32;\n"
    "\n"
    "\t\t\t\tseq_puts(m, \"  match table (row = 16B key + 16B mask):\");\n"
    "\t\t\t\tfor (i = 0; i < len; i++) {\n"
    "\t\t\t\t\tif (i % 16 == 0)\n"
    "\t\t\t\t\t\tseq_printf(m, \"\\n    %04x:\", i);\n"
    "\t\t\t\t\tseq_printf(m, \" %02x\",\n"
    "\t\t\t\t\t\t   ioread8((u8 __iomem *)mt + i));\n"
    "\t\t\t\t}\n"
    "\t\t\t\tseq_puts(m, \"\\n\");\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
    "\tmutex_unlock(fman_pcd_get_lock(pcd));\n"
)
if new_dump in src:
    print("### F-159: cc_test match-table hex dump already present")
elif old_dump in src:
    src = src.replace(old_dump, new_dump, 1)
    changes += 1
    print("### F-159: fman_pcd_cc_seq_dump() extended with raw match-table hex dump")
else:
    print(
        "### F-159: FATAL: expected fman_pcd_cc_seq_dump() body not found "
        "verbatim -- source has likely drifted from what this fixup was "
        "written against (patch 0107). Refusing to guess; fix the anchor "
        "text in F_159.py against the current fman_pcd_cc.c before retrying."
    )
    sys.exit(1)

if changes:
    with open(cc_c, "w") as f:
        f.write(src)
    print(f"### F-159: {changes} change(s) applied")
else:
    print("### F-159: no changes applied")
    sys.exit(1)
