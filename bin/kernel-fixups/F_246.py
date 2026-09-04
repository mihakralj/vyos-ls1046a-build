"""F-246 (T-M6-8 VLAN-v6 dig, 2026-09-04): global FMan Parser soft-parser
execution-unit enable (FMPR_RPIMAC bit 0), the mechanism found missing
after a deep-combed comparison against the real vendor SDK source and
dpa_app's real init sequence.

WHY: F-245's slot-0 test (specs/ask2-soft-parser-lcv-scheme-select.md
Sec6l) proved the soft-parser trigger silent even on the one HXS slot
independently known to activate on every real frame -- ruling out
"wrong slot" and pointing at something global. Tracing dpa_app's real
startup sequence (/mnt/builds/ASK/dpa_app/dpa.c:258-265,826): it calls
FM_PCD_Disable() BEFORE loading soft-parser code/PCD config, then
fmc_execute() re-enables PCD at the very end ("before fmc_execute
enables PCD" -- dpa.c's own comment). FM_PCD_Enable()/Disable() call
PrsEnable()/PrsDisable() (fm_prs.c), which call fman_prs_enable()/
fman_prs_disable() (fman_prs.c) -- these toggle FMPR_RPIMAC bit 0
(FM_PCD_PRS_RPIMAC_EN, fsl_fman_prs.h), a GLOBAL, whole-parser-block
register (struct fman_prs_regs, base FM_MM_PRS = FMAN_BASE+0xc7000 --
the SAME block this project's SP_CODE_PHYS_BASE already targets). The
register block itself sits at PRS_REGS_OFFSET=0x840 past that base
(fm_prs.c:112, fm_prs.h:308: p_FmPcdPrsRegs = baseAddr + PRS_REGS_OFFSET),
and fmpr_rpimac is the struct's *second* word (fsl_fman_prs.h:50-52:
fmpr_rpclim then fmpr_rpimac) -- so the real absolute offset from
SP_CODE_PHYS_BASE is 0x840+0x04 = 0x844, completely distinct from the
per-port pmda[] shadow RAM (pcac/ssa/lcv) this project has been
bracketing all along.

CORRECTION (2026-09-04, same day, post board-test): the first cut of
this fixup used byte offset +0x04 -- missing the +0x840 register-block
offset entirely -- so every "board-tested" read/write in this fixup's
original form landed at SP_CODE_PHYS_BASE+0x004, inside the soft-parser
code RAM's reserved/empty header region (reads as 0 for the same reason
the whole unloaded code region reads as 0), not the real FMPR_RPIMAC
control register. The F-246 board test's "confirmed FMPR_RPIMAC=1 via
dmesg, still silent" result never touched the real register at all --
it was a complete no-op that happened to read back what it wrote (RAM,
not a live bit). This alone fully explains F-243/F-244/F-245/F-246's
identical silent result: the PRS_HDR_SW_PRS_EN trigger bit correctly
tells the hard parser to branch into soft-parser code, and (as far as
this fixup ever actually tested) the execution-unit enable was never
touched at all. Fixed below to the correct 0x844 offset; retest needed
before drawing any further conclusion about the soft-parser mechanism.

Adds `sp_global_enable`/`sp_global_disable` cc_test debugfs verbs
(read-modify-write FMPR_RPIMAC bit 0, S6 R10.2 readback-verified, plain
control register -- not a per-port shadow-RAM class, so no
stop_port_hwp bracket needed, matching the vendor's own PrsEnable()/
PrsDisable() which write it directly). Global (not per-port): affects
every port. Setting it alone is inert (no port's pmda[].ssa points at
soft-parser code unless separately armed via F-243/F-245's sp_arm), so
it's safe to test standalone before combining with an armed hook.

Must run after F-243 (SP_CODE_PHYS_BASE/SP_CODE_REGION_SIZE anchors).
Idempotent.
"""

import os
import sys

cc_test_c = "drivers/net/ethernet/freescale/fman/fman_pcd_cc_test.c"

if not os.path.exists(cc_test_c):
    print(f"### F-246: {cc_test_c} not found")
    sys.exit(0)

marker = "F-246(sp-global-enable)"

with open(cc_test_c) as f:
    src = f.read()

if marker in src:
    print("### F-246: already applied")
    sys.exit(0)

# ---------------------------------------------------------------------
# 1. cc_test_sp_global_enable()/disable(), anchored right after
#    cc_test_sp_load().
# ---------------------------------------------------------------------
anchor1 = (
    "\tiounmap(base);\n"
    "\tpr_info(\"fman_pcd cc_test: sp_load OK, %zu-byte LCV-injection PoC at code offset 0x%03x\\n\",\n"
    "\t\tsizeof(cc_test_sp_poc_code32), SP_USER_CODE_BYTE_OFF);\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
)
if anchor1 not in src:
    print("### F-246: FATAL: cc_test_sp_load() end anchor not found -- F-243 must run first")
    sys.exit(1)
if src.count(anchor1) != 1:
    print(f"### F-246: FATAL: anchor1 not unique ({src.count(anchor1)})")
    sys.exit(1)

global_fns = (
    f"/* {marker}: FMPR_RPIMAC (FM_MM_PRS+PRS_REGS_OFFSET+0x04, i.e.\n"
    " * SP_CODE_PHYS_BASE+0x844 -- fm_prs.c:112 baseAddr+PRS_REGS_OFFSET\n"
    " * (fm_prs.h:308, =0x840) locates struct fman_prs_regs, whose 2nd\n"
    " * word is fmpr_rpimac, fsl_fman_prs.h:50-52) bit 0 -- the global,\n"
    " * whole-parser-block soft-parser EXECUTION UNIT enable. Distinct\n"
    " * from pmda[]'s per-port shadow RAM (no stop_port_hwp bracket needed\n"
    " * -- matches the vendor's own PrsEnable()/PrsDisable(), fman_prs.c,\n"
    " * which write this register directly with no port bracket either).\n"
    " * CORRECTION 2026-09-04: original cut of this fixup used +0x04\n"
    " * (missing the +0x840 register-block offset), landing inside the\n"
    " * soft-parser code RAM's empty header instead of the real register\n"
    " * -- board test never actually exercised FMPR_RPIMAC. Retest\n"
    " * needed with the corrected offset before drawing conclusions. */\n"
    "#define SP_RPIMAC_BYTE_OFF 0x844U\n"
    "#define SP_RPIMAC_EN 0x00000001U\n"
    "\n"
    "static int cc_test_sp_global_set(bool enable)\n"
    "{\n"
    "\tvoid __iomem *base;\n"
    "\tu32 val, rb;\n"
    "\n"
    "\tbase = ioremap(SP_CODE_PHYS_BASE, SP_CODE_REGION_SIZE);\n"
    "\tif (!base)\n"
    "\t\treturn -ENOMEM;\n"
    "\n"
    "\tval = ioread32be(base + SP_RPIMAC_BYTE_OFF);\n"
    "\tif (enable)\n"
    "\t\tval |= SP_RPIMAC_EN;\n"
    "\telse\n"
    "\t\tval &= ~SP_RPIMAC_EN;\n"
    "\tiowrite32be(val, base + SP_RPIMAC_BYTE_OFF);\n"
    "\n"
    "\trb = ioread32be(base + SP_RPIMAC_BYTE_OFF);\n"
    "\tiounmap(base);\n"
    "\tif (rb != val) {\n"
    "\t\tpr_err(\"fman_pcd cc_test: sp_global_%s readback mismatch (wrote 0x%08x, read 0x%08x)\\n\",\n"
    "\t\t       enable ? \"enable\" : \"disable\", val, rb);\n"
    "\t\treturn -EIO;\n"
    "\t}\n"
    "\tpr_info(\"fman_pcd cc_test: soft-parser execution unit %s (FMPR_RPIMAC=0x%08x)\\n\",\n"
    "\t\tenable ? \"globally enabled\" : \"globally disabled\", rb);\n"
    "\treturn 0;\n"
    "}\n"
    "\n"
)
src = src.replace(anchor1, anchor1 + global_fns, 1)

# ---------------------------------------------------------------------
# 2. dispatch: sp_global_enable / sp_global_disable verbs, anchored
#    right before the sp_load dispatch clause.
# ---------------------------------------------------------------------
anchor2 = (
    "\t} else if (strncmp(kbuf, \"sp_load\", 7) == 0) {\n"
)
if anchor2 not in src:
    print("### F-246: FATAL: sp_load dispatch anchor not found -- F-243 must run first")
    sys.exit(1)
if src.count(anchor2) != 1:
    print(f"### F-246: FATAL: anchor2 not unique ({src.count(anchor2)})")
    sys.exit(1)
new2 = (
    f"\t}} else if (strncmp(kbuf, \"sp_global_enable\", 16) == 0) {{\n"
    f"\t\t/* {marker}: turn the soft-parser execution unit ON, globally\n"
    "\t\t * (all ports). Inert unless some port's pmda[].ssa also points\n"
    "\t\t * at loaded code (sp_arm). */\n"
    "\t\tret = cc_test_sp_global_set(true);\n"
    "\t\tif (ret == 0)\n"
    "\t\t\tret = count;\n"
    "\t} else if (strncmp(kbuf, \"sp_global_disable\", 18) == 0) {\n"
    f"\t\t/* {marker}: turn it back off (mainline default state). */\n"
    "\t\tret = cc_test_sp_global_set(false);\n"
    "\t\tif (ret == 0)\n"
    "\t\t\tret = count;\n"
    + anchor2
)
src = src.replace(anchor2, new2, 1)

with open(cc_test_c, "w") as f:
    f.write(src)
print("### fman_pcd_cc_test.c: F-246 sp_global_enable/sp_global_disable verbs added")
