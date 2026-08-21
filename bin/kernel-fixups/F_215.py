"""F-215: gate the F-168 FMFP_EXTC[INV0] SYNC so it fires ONLY when no sibling
RX port is already engaged — the fix that makes multi-port ASK arm (IPv4 and
IPv6 alike) stable, matching vendor behavior.

ROOT CAUSE (vendor+mainline source study 2026-08-19, image 2129 dual-port wedge)
--------------------------------------------------------------------------------
FMFP_EXTC[INV0] is a GLOBAL per-FMan sync register (one fman->fpm_regs->fmfp_extc
for all ports). F-168 asserts it in fman_port_set_cc_base() on every arm and
polls up to 100000 times for the FMan controller to reach a quiescent point.
- Single-port arm: no live sibling -> controller quiesces -> INV0 clears in 0
  polls (harmless).
- IPv4 dual-port: the first port runs a SHALLOW pipeline (one mv=0 AC_CC scheme
  -> ehash MISS -> RX FQ) that drains fast, so the second port's INV0 still
  clears in 0 polls. Works today.
- IPv6 dual-port: by the time the SECOND port arms, the FIRST port is running a
  DEEP live pipeline (3 schemes + QLCV gating + CCOBASE=1 table1 dispatch) that
  keeps the FMan controller busy, so the global INV0 NEVER clears for the second
  port -> 100000-poll timeout (fmfp_extc=0x80000000) -> both ports go deaf ->
  management lost (image 2040 and 2129 both hard-wedged, needed cold boot).

The VENDOR (NCSW fm_port.c) NEVER asserts FMFP_EXTC INV0 on an arm path at all —
its only INV0 use is the deep-sleep quiesce, AFTER halting the whole FMan
(UPDATE_FPM_BRKC_SLP). FM_PORT_SetPCD arms ports incrementally on a live FMan
with only a PER-PORT parser stop + software locks, no global INV0.

THE FIX
-------
Assert INV0 only when this is the FIRST engaged port on the FMan; skip it
entirely once any sibling RX port is already engaged (the controller is
non-quiescent by definition then, so INV0 cannot clear and asserting it can only
wedge). This:
- preserves the proven single-port behavior (first port: assert, clears in 0
  polls, unchanged),
- preserves the proven IPv4 dual-port behavior (2nd port's INV0 was a 0-poll
  no-op anyway; now simply skipped),
- fixes IPv6 dual-port (2nd port no longer asserts the un-clearable global sync).

MECHANISM
---------
Add a per-FMan engaged-port counter fman->cc_engaged_ports with accessors
(mirroring F-167's fman_get/set_fpm_extc, since struct fman is opaque to
fman_port.c). In fman_port_set_cc_base():
  * arm (cc_muram_off != 0): assert+poll INV0 ONLY if fman_cc_engaged_get()==0,
    then fman_cc_engaged_inc().
  * teardown (cc_muram_off == 0): fman_cc_engaged_dec() (floored at 0).
The counter is mutated only from the arm/disarm path, which the PCD layer
serializes under its mutex, so no extra locking is needed. 1:1 with
engage/disengage (arm writes rccb=gro once, disarm writes rccb=0 once).

S0/SAFETY: INV0 register semantics unchanged; we only change WHEN it is
asserted, strictly narrowing it to the first-port case the vendor's fast-drain
path already tolerated. Readback/poll bounded + non-fatal as before. Qdrant +
vendor-source gated. Idempotent via F-215 markers. MUST run AFTER F-167 (struct
fman accessors pattern) and AFTER F-168 (rewrites its INV0 block). Place right
after F-168 in ci-setup-kernel.sh.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
fman_h = os.path.join(kroot, "fman.h")
fman_c = os.path.join(kroot, "fman.c")
port_c = os.path.join(kroot, "fman_port.c")

changes = 0


def fatal(msg):
    print(f"### F-215: FATAL: {msg}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────
# 1. fman.h: add the counter field + accessor declarations.
# ─────────────────────────────────────────────────────────────────────────
with open(fman_h) as f:
    h = f.read()

if "cc_engaged_ports" not in h:
    field_anchor = "\tstruct fman_keygen *keygen;\n"
    if field_anchor not in h:
        fatal("struct fman keygen field anchor not found in fman.h")
    h = h.replace(
        field_anchor,
        field_anchor +
        "\t/* F-215: count of RX ports currently engaged for AC_CC (non-zero\n"
        "\t * fmbm_rccb). The global FMFP_EXTC[INV0] SYNC is asserted only when\n"
        "\t * this is 0 (first port); a live sibling keeps the FMan controller\n"
        "\t * non-quiescent so INV0 could never clear -> multi-port arm wedge. */\n"
        "\tu8 cc_engaged_ports;\n",
        1)
    changes += 1
    print("### fman.h: F-215 cc_engaged_ports field added to struct fman")
else:
    print("### F-215: struct fman field already present")

if "u8 fman_cc_engaged_get(struct fman *fman);" not in h:
    decl_anchor = "void fman_set_fpm_extc(struct fman *fman, u32 val);\n"
    if decl_anchor not in h:
        fatal("fman_set_fpm_extc decl anchor not found in fman.h (F-167 must run first)")
    h = h.replace(
        decl_anchor,
        decl_anchor +
        "/* F-215: per-FMan engaged-port counter accessors (gate the INV0 SYNC). */\n"
        "u8 fman_cc_engaged_get(struct fman *fman);\n"
        "void fman_cc_engaged_inc(struct fman *fman);\n"
        "void fman_cc_engaged_dec(struct fman *fman);\n",
        1)
    changes += 1
    print("### fman.h: F-215 accessor declarations added")
else:
    print("### F-215: accessor declarations already present")

if changes:
    with open(fman_h, "w") as f:
        f.write(h)

# ─────────────────────────────────────────────────────────────────────────
# 2. fman.c: define the accessors after F-167's fman_set_fpm_extc export.
# ─────────────────────────────────────────────────────────────────────────
with open(fman_c) as f:
    c = f.read()

if "u8 fman_cc_engaged_get(struct fman *fman)" in c:
    print("### F-215: fman.c accessors already present")
else:
    c_anchor = "EXPORT_SYMBOL_GPL(fman_set_fpm_extc);\n"
    if c_anchor not in c:
        fatal("fman_set_fpm_extc export anchor not found in fman.c (F-167 must run first)")
    c = c.replace(
        c_anchor,
        c_anchor +
        "\n"
        "/* F-215: engaged-port counter. Tracks how many RX ports currently have\n"
        " * a non-zero fmbm_rccb (AC_CC engaged). Mutated only from the PCD\n"
        " * arm/disarm path (fman_port_set_cc_base), which the PCD layer\n"
        " * serializes; used to assert the global FMFP_EXTC[INV0] SYNC only for\n"
        " * the first engaged port. */\n"
        "u8 fman_cc_engaged_get(struct fman *fman)\n"
        "{\n"
        "\treturn fman ? fman->cc_engaged_ports : 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_cc_engaged_get);\n"
        "\n"
        "void fman_cc_engaged_inc(struct fman *fman)\n"
        "{\n"
        "\tif (fman)\n"
        "\t\tfman->cc_engaged_ports++;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_cc_engaged_inc);\n"
        "\n"
        "void fman_cc_engaged_dec(struct fman *fman)\n"
        "{\n"
        "\tif (fman && fman->cc_engaged_ports)\n"
        "\t\tfman->cc_engaged_ports--;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_cc_engaged_dec);\n",
        1)
    changes += 1
    with open(fman_c, "w") as f:
        f.write(c)
    print("### fman.c: F-215 accessor definitions added")

# ─────────────────────────────────────────────────────────────────────────
# 3. fman_port.c: gate F-168's INV0 block on first-port-only + maintain counter.
# ─────────────────────────────────────────────────────────────────────────
with open(port_c) as f:
    p = f.read()

if "F-215(sync-gate)" in p:
    print("### F-215: fman_port.c INV0 gate already applied")
else:
    # Anchor on F-168's exact block: the "if (cc_muram_off) {" that opens the
    # INV0 assertion, plus its fman_set_fpm_extc line. Wrap the assertion with
    # the first-port gate, and add the counter inc/dec.
    old = (
        "\tif (cc_muram_off) {\n"
        "\t\tconst u32 inv0 = 0x80000000U;\n"
        "\t\tconst unsigned int poll_max = 100000U;\n"
        "\t\tu32 extc;\n"
        "\t\tunsigned int i;\n"
        "\n"
        "\t\tfman_set_fpm_extc(port->fm, inv0);\n"
    )
    if old not in p:
        fatal("F-168 INV0 block anchor not found verbatim in fman_port.c "
              "(F-168 must run first / source drifted).")
    new = (
        "\t/* F-215(sync-gate): assert the GLOBAL FMFP_EXTC[INV0] SYNC only when\n"
        "\t * this is the FIRST engaged RX port on the FMan. A live sibling keeps\n"
        "\t * the FMan controller non-quiescent, so INV0 could never clear and\n"
        "\t * asserting it on the 2nd port wedges both (image 2040/2129). The\n"
        "\t * vendor never asserts INV0 on an arm path with siblings live. */\n"
        "\tif (cc_muram_off && fman_cc_engaged_get(port->fm) == 0) {\n"
        "\t\tconst u32 inv0 = 0x80000000U;\n"
        "\t\tconst unsigned int poll_max = 100000U;\n"
        "\t\tu32 extc;\n"
        "\t\tunsigned int i;\n"
        "\n"
        "\t\tfman_set_fpm_extc(port->fm, inv0);\n"
    )
    p = p.replace(old, new, 1)
    changes += 1

    # Maintain the counter: increment on arm, decrement on teardown. Insert the
    # accounting right after the SYNC block closes — anchor on the block's
    # closing brace + the SDK-concurrence comment that follows (F-168 tail).
    acct_anchor = (
        "\t/*\n"
        "\t * SDK FM_PORT_SetPCD concurrence: for the PRS->KG->CC chain the\n"
    )
    if acct_anchor not in p:
        fatal("post-INV0 SDK-concurrence comment anchor not found in fman_port.c")
    acct_new = (
        "\t/* F-215(sync-gate): maintain the engaged-port counter used above.\n"
        "\t * arm (cc_muram_off != 0) -> ++, teardown (== 0) -> -- (floored). */\n"
        "\tif (cc_muram_off)\n"
        "\t\tfman_cc_engaged_inc(port->fm);\n"
        "\telse\n"
        "\t\tfman_cc_engaged_dec(port->fm);\n"
        "\n"
        + acct_anchor
    )
    p = p.replace(acct_anchor, acct_new, 1)
    changes += 1
    with open(port_c, "w") as f:
        f.write(p)
    print("### fman_port.c: F-215 INV0 first-port gate + engaged-port counter applied")

if changes:
    print(f"### F-215 complete ({changes} change(s))")
else:
    print("### F-215 no changes (already present)")
    sys.exit(0)
