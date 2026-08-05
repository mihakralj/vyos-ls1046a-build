"""F-160: fix fman_pcd_kg_port_attach_cc()'s KeyGen NIA dispatch mode --
next_engine=2 is a project-confirmed no-op for CC dispatch; the real
AC_CC encoding is next_engine=3.

CONTEXT (2026-08-04 code review, plans/CC-TREE-REBUILD-PLAN.md Phase 1):
board-tested and confirmed via a real HIT/MISS test on .185 (real
target_fqid pointing at eth3's genuine RX-default FQID, non-promiscuous
tcpdump on both interfaces): a byte-exact-correct CC-tree match table
(F-159) with FMBM_RCCB correctly bound (confirmed via dmesg: "FMBM_RCCB
bound to 0x4b600") STILL produces a clean MISS. Root cause traced through
the full patch history (0106 -> 0115 -> 0118 -> 0132 -> 0133):

fman_pcd_kg_port_attach_cc() (patch 0106, unchanged by 0118's revert)
sets `slot->next_engine = 2` on the KeyGen scheme. Patch 0133's own
commit message states explicitly, about this exact value: "per the
CC-dispatch truth table that encoding NEVER invokes the CC walk --
frames bypass into plain RSS." next_engine=2 keeps KGSE_MODE at plain
BMI-direct-enqueue (0x80500002) instead of flipping to FM_CTL|AC_CC
(0x80000006) -- so the KeyGen scheme's NIA never tells the port to
actually consult FMBM_RCCB, regardless of what's bound there or how
correct the match table is.

The real AC_CC encoding already exists in the codebase (patch 0133,
next_engine==3 branch in keygen_scheme_setup(), fman_keygen.c) but was
wired ONLY into the FE-VM/ehash arm path (fman_pcd_kg_port_arm_fe), never
into the CC-tree graft path (fman_pcd_kg_port_attach_cc) -- this fixup
closes that gap.

Two field changes, both required together:
  - next_engine: 2 -> 3 (CCBS-direct placebo -> real AC_CC)
  - cc_bits_sel: cc_group_off -> 0 (AC_CC requires KGSE_CCBS=0; the tree
    address lives entirely in FMBM_RCCB, which cc_test_install() already
    binds correctly via fman_port_set_cc_base() -- confirmed via dmesg,
    no changes needed there)

CAUTION (not dismissed, see plan doc): patch 0118 originally reverted
away from AC_CC because it stalled the FMan port on real hardware
(DUT 192.168.1.190, 2026-06-12) -- but that predates F-072 (2026-07-15),
which fixed the FE-VM workspace-pool bug plausibly responsible; patch
0133's later AC_CC re-introduction has run extensively via fe_arm since
without a reported stall. Test this fixup via the standalone cc_test
harness first (not a live ask.ko engage), watching dmesg for FMFP_PS[STL]
port-stall indications, before declaring success.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
kg_c = os.path.join(kroot, "fman_pcd_kg.c")

if not os.path.exists(kg_c):
    print("### F-160: fman_pcd_kg.c not found")
    sys.exit(0)

with open(kg_c) as f:
    src = f.read()

changes = 0

old_block = (
    "\tslot->next_engine    = 2;\n"
    "\tslot->cc_base_offset = 0;\n"
    "\tslot->cc_bits_sel    = cc_group_off;\n"
)
new_block = (
    "\t/* F-160 (2026-08-04): next_engine=2 is a confirmed no-op for CC\n"
    "\t * dispatch (patch 0133's own commit message: \"NEVER invokes the\n"
    "\t * CC walk\"). next_engine=3 is the real AC_CC encoding (KGSE_MODE\n"
    "\t * -> FM_CTL|AC_CC), already used by the FE-VM arm path -- this is\n"
    "\t * the first CC-tree graft to actually use it. AC_CC requires\n"
    "\t * KGSE_CCBS=0; the tree address lives in FMBM_RCCB instead,\n"
    "\t * already correctly bound by cc_test_install()'s call to\n"
    "\t * fman_port_set_cc_base() (confirmed via board dmesg). */\n"
    "\tslot->next_engine    = 3;\n"
    "\tslot->cc_base_offset = 0;\n"
    "\tslot->cc_bits_sel    = 0;\n"
)

if new_block in src:
    print("### F-160: fman_pcd_kg_port_attach_cc() AC_CC fix already present")
elif old_block in src:
    src = src.replace(old_block, new_block, 1)
    changes += 1
    print("### F-160: fman_pcd_kg_port_attach_cc() switched next_engine 2 -> 3 (real AC_CC)")
else:
    print(
        "### F-160: FATAL: expected fman_pcd_kg_port_attach_cc() field-init "
        "block not found verbatim -- source has likely drifted from what "
        "this fixup was written against (post-0118 state). Refusing to "
        "guess; fix the anchor text in F_160.py against the current "
        "fman_pcd_kg.c before retrying."
    )
    sys.exit(1)

if changes:
    with open(kg_c, "w") as f:
        f.write(src)
    print(f"### F-160: {changes} change(s) applied")
else:
    print("### F-160: no changes applied")
    sys.exit(1)
