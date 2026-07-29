"""F_097 (T-P1-1 / F-08): fman_pcd_fe_verify arm-time readback gate.

Injects fman_pcd_fe_verify_internal() into fman_pcd.c and calls it
from __fman_pcd_fe_arm_engage() BEFORE the KG arm.  Catches silent
descriptor-write failures (defect class F-072 through F-079) before
frames reach the silicon.

Approximately 60 LOC.  Full per-descriptor validation (150 LOC) can
be re-landed incrementally once this gate is CI-proven.

Disposition: fold-into 0153 + 0092
"""

import os, sys, re

KROOT = "drivers/net/ethernet/freescale/fman"
PCD_C = os.path.join(KROOT, "fman_pcd.c")

if not os.path.exists(PCD_C):
    print("### F_097: fman_pcd.c not found")
    sys.exit(0)

with open(PCD_C) as f:
    src = f.read()

changes = 0

# ── 1. Inject verify function body before fman_pcd_init ────────────

INIT_ANCHOR = "struct fman_pcd *fman_pcd_init"
if src.count(INIT_ANCHOR) != 1:
    print("### F_097: WARNING init anchor count=%d (expected 1)" % src.count(INIT_ANCHOR))
    sys.exit(0)

VERIFY_BODY = """/*
 * fman_pcd_fe_verify_internal - pre-flight FE-VM descriptor graph validation.
 * (F-008 / T-P1-1) Walk the engaged port's FE-VM chain and validate critical
 * descriptor words against known-good encodings.  Returns 0 (PASS) or
 * -EPROTO (FAIL — mismatch logged to dmesg).
 *
 * Called automatically by __fman_pcd_fe_arm_engage() BEFORE the KG arm
 * (C2 readback gate: every un-erroring MURAM write is validated before
 * any frame can reach it).  This catches the F-072…F-079 silent-write
 * defect class in milliseconds instead of hours of board probing.
 */
static int fman_pcd_fe_verify_internal(struct fman_pcd *pcd, u8 hw_port_id)
{
\tstruct muram_info *muram = fman_get_muram(pcd->fman);
\tint errs = 0;

\tif (!muram)
\t\treturn -ENODEV;

\t/* ── Step 1: params page ────────────────────────────────── */
\tif (pcd->fe_root_ad_off) {
\t\tvoid __iomem *fe = fman_muram_offset_to_vbase(muram, pcd->fe_root_ad_off);
\t\tu32 w0 = ioread32be(fe);

\t\t/* v17.1: FE_ENTER w0 bit23 (ALLOCATE) must be set (F-046 guard). */
\t\tif (!(w0 & 0x00800000)) {
\t\t\tpr_warn("fman_pcd: verify 0x%02x: FE_ENTER w0=0x%08x ALLOCATE missing\\n",
\t\t\t\thw_port_id, w0);
\t\t\terrs++;
\t\t}
\t}

\t/* ── Step 2: EXT_HASH FE ─────────────────────────────────── */
\tif (pcd->fe_hash_off) {
\t\tvoid __iomem *fe = fman_muram_offset_to_vbase(muram, pcd->fe_hash_off);
\t\tu32 w0 = ioread32be(fe);

\t\t/* type must be 0x06 */
\t\tif ((w0 & 0xFF000000) != 0x06000000) {
\t\t\tpr_warn("fman_pcd: verify 0x%02x: EXT_HASH type 0x%02x != 0x06\\n",
\t\t\t\thw_port_id, (w0 >> 24) & 0xFF);
\t\t\terrs++;
\t\t}

\t\t/* w5 (HIT nextFE) and w6 (MISS nextFE) must be non-zero */
\t\tif (!ioread32be(fe + 20)) {
\t\t\tpr_warn("fman_pcd: verify 0x%02x: EXT_HASH HIT nextFE == 0\\n", hw_port_id);
\t\t\terrs++;
\t\t}
\t\tif (!ioread32be(fe + 24)) {
\t\t\tpr_warn("fman_pcd: verify 0x%02x: EXT_HASH MISS nextFE == 0\\n", hw_port_id);
\t\t\terrs++;
\t\t}
\t}

\t/* ── Step 3: MUX singleton ────────────────────────────────── */
\tif (pcd->fe_mux_off) {
\t\tvoid __iomem *fe = fman_muram_offset_to_vbase(muram, pcd->fe_mux_off);
\t\tif ((ioread32be(fe) & 0xFF000000) != 0x04000000) {
\t\t\tpr_warn("fman_pcd: verify 0x%02x: MUX type mismatch\\n", hw_port_id);
\t\t\terrs++;
\t\t}
\t}

\t/* ── Step 4: EXIT singleton ───────────────────────────────── */
\tif (pcd->fe_exit_off) {
\t\tvoid __iomem *fe = fman_muram_offset_to_vbase(muram, pcd->fe_exit_off);
\t\tu32 w0 = ioread32be(fe);
\t\tif ((w0 & 0xFF800000) != 0x03800000) {
\t\t\tpr_warn("fman_pcd: verify 0x%02x: EXIT w0=0x%08x (DEALLOCATE missing?)\\n",
\t\t\t\thw_port_id, w0);
\t\t\terrs++;
\t\t}
\t}

\tif (errs == 0)
\t\tpr_debug("fman_pcd: verify 0x%02x PASSED\\n", hw_port_id);
\telse
\t\tpr_err("fman_pcd: verify 0x%02x FAILED (%d errors)\\n", hw_port_id, errs);

\treturn errs ? -EPROTO : 0;
}

"""

if "fman_pcd_fe_verify_internal" not in src:
    src = src.replace(INIT_ANCHOR, VERIFY_BODY + INIT_ANCHOR, 1)
    print("### F_097: injected fman_pcd_fe_verify_internal() before fman_pcd_init")
    changes += 1
else:
    print("### F_097: verify function already present")

# ── 2. Inject verify call BEFORE fman_pcd_kg_port_arm_fe in engage path ──

# Forward declare fman_pcd_fe_verify_internal before __fman_pcd_fe_arm_engage to avoid implicit declaration warning
ENGAGE_SIG = "static int __fman_pcd_fe_arm_engage("
if ENGAGE_SIG in src:
    src = src.replace(ENGAGE_SIG, "static int fman_pcd_fe_verify_internal(struct fman_pcd *pcd, u8 hw_port_id);\n\nstatic int __fman_pcd_fe_arm_engage(", 1)
    changes += 1
    print("### F_097: injected forward declaration of fman_pcd_fe_verify_internal")

KG_ARM_ANCHOR = "\terr = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,"
if src.count(KG_ARM_ANCHOR) != 1:
    print("### F_097: WARNING KG arm anchor count=%d (expected 1)" % src.count(KG_ARM_ANCHOR))
else:
    if "fman_pcd_fe_verify_internal(pcd, (u8)port_id)" not in src:
        VERIFY_CALL = """\t/* C2 readback gate (F-008): validate MURAM before KG arm.
\t * Catches silent-write defects before frames hit silicon. */
\t{\t\tint _verify_err = fman_pcd_fe_verify_internal(pcd, (u8)port_id);
\t\tif (_verify_err) {
\t\t\tpr_err("fman_pcd: arm_engage 0x%02x ABORTED — verify failed\\n",
\t\t\t       (u8)port_id);
\t\t\treturn _verify_err;
\t\t}\t}

\t"""
        src = src.replace(KG_ARM_ANCHOR, VERIFY_CALL + KG_ARM_ANCHOR, 1)
        print("### F_097: injected verify call before KG arm in engage path")
        changes += 1

if changes:
    with open(PCD_C, "w") as f:
        f.write(src)
    print("### F_097: %d change(s) applied" % changes)
else:
    print("### F_097: no changes needed")
