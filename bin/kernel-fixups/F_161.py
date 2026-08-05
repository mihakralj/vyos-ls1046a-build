"""F-161: fix cc_pack_key()'s KG composite AGAIN — this time to the EKFC
directly observed on real hardware, not an assumption.

CONTEXT (2026-08-05, CC-Tree Rebuild Plan Phase 1 board test):
F-159 rewrote cc_pack_key() to a 13-byte SIP|DIP|PROTO|SPORT|DPORT layout
(EKFC 0x001C0006), based on the EHASH/FE-VM path's CRC-64-confirmed field
order — but explicitly flagged that order as "not yet independently
observed against the CC comparator specifically."

F-160 (next_engine=2 -> 3) is the first change to actually engage the real
AC_CC walk, and board-testing it exposed the direct observation F-159 was
missing. dmesg on hwport 0x11's cc_test install/detach cycle:

    fsl_dpaa_fman: ASK2-DBG scheme4 hashing: ekfc=0x00180006 mv=0x0 hc=0x0 fqb=0x0
    fsl_dpaa_fman: ASK2-DBG scheme4 EKFC write: ekfc=0x00180006 (slot->ekfc=0x00000000)
    fman_pcd cc_test: port 0x11 tree installed, FMBM_RCCB bound to 0x4b600, KG CC-dispatched

scheme4 is kg_find_port_scheme()'s result for hwport 0x11 — the same scheme
fman_pcd_kg_port_attach_cc() grafts. Its real, live EKFC is 0x00180006 (KG_
SCH_KN_IPSRC1|IPDST1|L4PSRC|L4PDST — SIP|DIP|SPORT|DPORT, 12 bytes, NO
PROTO), confirmed directly by hardware, not 0x001C0006 as F-159 assumed.

cc_pack_key()'s software match-table layout must match what the KeyGen
hardware actually extracts and feeds into the CC comparator's compare
window — a mismatch here (F-159's 13-byte PROTO-included layout against a
real 12-byte no-PROTO extraction) is the leading suspect for the total
RX stall observed after F-160's install (both matching and non-matching
traffic silently vanish, requiring a reboot to recover): the CC compare
window reading past the KG's actual 12-byte extraction into unrelated/
uninitialized workspace bytes is a plausible cause of a hardware classify-
pipeline stall, not just a wrong-match miss.

This fixup drops the PROTO field F-159 added and closes the gap, matching
patch 0108's ORIGINAL byte count minus its SPI reservation:
  SIP(4)@0-3 | DIP(4)@4-7 | SPORT(2)@8-9 | DPORT(2)@10-11 (12 B; bytes
  12-15 stay wildcard/unset, matching CC_KEY_SIZE=16).

This is a hypothesis fix, not a confirmed one: it corrects the one
concrete, board-observed discrepancy (EKFC field content) but does not
by itself prove the CC compare-window-overrun theory of the RX stall.
Retest via cc_test after this lands; if the port still stalls on install,
the stall has a different root cause and this fix should be evaluated on
its own (does a genuine HIT/MISS distinction appear?) rather than assumed
to have fixed the stall too.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
cc_c = os.path.join(kroot, "fman_pcd_cc.c")

if not os.path.exists(cc_c):
    print("### F-161: fman_pcd_cc.c not found")
    sys.exit(0)

with open(cc_c) as f:
    src = f.read()

changes = 0

# ── fix cc_pack_key()'s field-packing block (F-159's PROTO-included layout
#    -> the board-confirmed real EKFC 0x00180006, no PROTO) ──
old_pack = (
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
new_pack = (
    "\t/* F-161 (2026-08-05): dpaa1 EKFC=0x00180006 order — board-confirmed\n"
    "\t * DIRECTLY (not assumed) via dmesg on hwport 0x11's own live scheme\n"
    "\t * (scheme4): \"ASK2-DBG scheme4 hashing: ekfc=0x00180006\", captured\n"
    "\t * during a cc_test install/detach cycle immediately preceding the\n"
    "\t * \"KG CC-dispatched\" log line for that same port. Supersedes F-159's\n"
    "\t * 0x001C0006 (SIP|DIP|PROTO|SPORT|DPORT), which was extrapolated\n"
    "\t * from the EHASH/FE-VM path and never independently confirmed\n"
    "\t * against this scheme. 0x00180006 = KG_SCH_KN_IPSRC1|IPDST1|\n"
    "\t * L4PSRC|L4PDST — SIP|DIP|SPORT|DPORT, no PROTO, 12 bytes.\n"
    "\t */\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_SRC_IP) {\n"
    "\t\tmemcpy(&key[0], &k->src_ip_be, 4);\n"
    "\t\tmsk[0] = msk[1] = msk[2] = msk[3] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_DST_IP) {\n"
    "\t\tmemcpy(&key[4], &k->dst_ip_be, 4);\n"
    "\t\tmsk[4] = msk[5] = msk[6] = msk[7] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_SRC_PORT) {\n"
    "\t\tkey[8] = (u8)(k->src_port_be & 0xff);\n"
    "\t\tkey[9] = (u8)(k->src_port_be >> 8);\n"
    "\t\tmsk[8] = msk[9] = 0xff;\n"
    "\t}\n"
    "\tif (k->present & FMAN_PCD_CC_HW_F_DST_PORT) {\n"
    "\t\tkey[10] = (u8)(k->dst_port_be & 0xff);\n"
    "\t\tkey[11] = (u8)(k->dst_port_be >> 8);\n"
    "\t\tmsk[10] = msk[11] = 0xff;\n"
    "\t}\n"
    "\t/* bytes 12..15: unused by this EKFC -- stay wildcard (mask 0). */\n"
)
if new_pack in src:
    print("### F-161: cc_pack_key() board-confirmed EKFC fix already present")
elif old_pack in src:
    src = src.replace(old_pack, new_pack, 1)
    changes += 1
    print("### F-161: cc_pack_key() rewritten to board-confirmed EKFC=0x00180006 order")
else:
    print(
        "### F-161: FATAL: expected F-159 cc_pack_key() field-packing block "
        "not found verbatim -- source has likely drifted (or F-159 did not "
        "apply before this fixup ran). Refusing to guess; F-161 must run "
        "after F-159 and its anchor text must match F-159's actual output."
    )
    sys.exit(1)

if changes:
    with open(cc_c, "w") as f:
        f.write(src)
    print(f"### F-161: {changes} change(s) applied")
else:
    print("### F-161: no changes applied")
    sys.exit(1)
