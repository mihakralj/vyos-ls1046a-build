"""F-214 (IPv6 productization fix, gated): program KeyGen classification-plan
entry group 0 to pass-all (0xffffffff) so QLCV = plan_mask & LCV actually
carries the parser LCV into the scheme match-vector walk.

ROOT CAUSE (vendor+mainline source study 2026-08-19)
----------------------------------------------------
Scheme selection matches (QLCV & kgse_mv) == kgse_mv where
QLCV = CP_entry_mask[CPGBASE | (CPID & CPGMASK)] & parser_LCV. Mainline
keygen_init() binds every port to CPP=0 (keygen_write_cpp(...,0)) but NEVER
writes any classification-plan entry mask vector, so CP entry 0 is left at its
hardware-reset value. If that reset value is 0 (or anything without the v4/v6
bits), QLCV is zeroed and ANY scheme with kgse_mv != 0 fails to match
(FM_FD_ERR_NO_SCHEME) — exactly the v4-deaf symptom seen when F-211 narrowed the
v4 scheme's mv. The vendor's empty classification-plan group explicitly writes
0xffffffff into all 8 CP entries (fm_kg.c FmPcdKgBuildClsPlanGrp: numOptions=0 ->
oredVectors=0 -> entry = ~0 = 0xffffffff), via fman_kg_write_cls_plan() over the
indirect CP RAM. This fixup ports that single missing write.

WHAT IT DOES
------------
Adds keygen_cls_plan0_passall(keygen, hw_port_id): on the FIRST v6-enabled port
only, writes 0xffffffff to all 8 CP-entry words in the KG indirect window
(fmkg_indirect[0..7]), issues the cls-plan-entry Action Register
(GO|WRITE|SEL_CLS_PLAN_ENTRY | grp0<<16 | 0xff<<8(entries) | hw_port_id), waits,
then reads the entries back and verifies all 0xffffffff (S6 R10.2). It sets
keygen->cls_plan0_passall=true only after successful readback. Every later port
engage returns immediately WITHOUT touching the AR/window. CPP=0 (set by
keygen_init) already selects global group 0 for every port, so one write is
sufficient and pass-all group 0 => QLCV = LCV. Exported for fman_pcd_kg.c.

This one-time rule is correctness-critical: image 2040 proved single-port eth4
three-scheme selection works (v4/v6/catch-all spc all move, NO_SCHEME=0), but
engaging port 0x11 after live port 0x10 rewrote the GLOBAL group0 and caused
FMFP_EXTC SYNC timeout (INV0 stuck 0x80000000), wedging both ports. Rewriting a
shared CP group while the first pipeline is active is not harmless; skip all AR
access after the verified first initialization. F-211 calls under the shared
PCD mutex, so the first initialization is serialized.

Also wires the call into F-211's v6 arm block (before the F-212 LCV split) so it
runs only when v6 is enabled — v4-only production is untouched.

REGISTER FACTS (mainline fman_keygen.c + vendor fman_kg.c, cross-checked)
- FM_KG_KGAR_SEL_CLS_PLAN_ENTRY = 0x01000000 (already #defined in fman_keygen.c)
- FM_KG_KGAR_GO=0x80000000, WRITE=0x0, READ=0x40000000, ERR=0x20000000
- FM_KG_KGAR_NUM_SHIFT=16 (group), entries_mask<<8 (WSEL_SHIFT=8), port in low bits
- indirect CP window: (struct){u32 kgcpe[8]} overlaid on regs->fmkg_indirect[0]
- FM_KG_NUM_CLS_PLAN_ENTR = 8, group 0, entries_mask 0xff = all 8

SAFETY / S0
-----------
Gated: only called from the v6 arm path (default-OFF fsl_dpaa_fman.v6_enable).
Readback-verified. Writes a shared global CP entry to pass-all, which is the
vendor default for a no-options NetEnv — it cannot make v4 worse (mv=0 always
matched; and with pass-all + LCV split, v4 mv=0x40000000 now matches only v4
frames). Qdrant/vendor-source gated. Idempotent via F-214 markers.

Must run AFTER the keygen internal header exists (F-211 era) and AFTER F-211
(anchors on F-211's arm block) and AFTER F-212 is present is NOT required (F-214
inserts before the LCV-split call using F-211's bind line as anchor). Place in
ci-setup-kernel.sh right after F-212.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
kg_c = os.path.join(kroot, "fman_keygen.c")
ih = os.path.join(kroot, "fman_keygen_internal.h")
pcd_kg_c = os.path.join(kroot, "fman_pcd_kg.c")

changes = 0


def fatal(msg):
    print(f"### F-214: FATAL: {msg}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────
# 0. fman_keygen_internal.h: add the one-time state flag to struct fman_keygen.
# ─────────────────────────────────────────────────────────────────────────
with open(ih) as f:
    ihsrc = f.read()

if "cls_plan0_passall" not in ihsrc:
    struct_anchor = ("\tstruct keygen_scheme schemes[FM_KG_MAX_NUM_OF_SCHEMES];\n"
                     "\tstruct fman_kg_regs __iomem *keygen_regs;\n")
    if struct_anchor not in ihsrc:
        fatal("struct fman_keygen body anchor not found in internal header")
    struct_new = (
        "\tstruct keygen_scheme schemes[FM_KG_MAX_NUM_OF_SCHEMES];\n"
        "\tstruct fman_kg_regs __iomem *keygen_regs;\n"
        "\t/* F-214: set once the global classification-plan group 0 has been\n"
        "\t * written pass-all (0xffffffff). Rewriting the shared CP group while\n"
        "\t * another port is already live times out FMFP_EXTC SYNC and wedges\n"
        "\t * both ports (image 2040), so this must happen exactly once. */\n"
        "\tbool cls_plan0_passall;\n"
    )
    ihsrc = ihsrc.replace(struct_anchor, struct_new, 1)
    with open(ih, "w") as f:
        f.write(ihsrc)
    changes += 1
    print("### fman_keygen_internal.h: F-214 cls_plan0_passall flag added to struct")
else:
    print("### F-214: struct flag already present")


# ─────────────────────────────────────────────────────────────────────────
# 1. fman_keygen.c: add the pass-all CP-entry-0 writer + WSEL define.
# ─────────────────────────────────────────────────────────────────────────
with open(kg_c) as f:
    src = f.read()

if "keygen_cls_plan0_passall" in src:
    print("### F-214: keygen_cls_plan0_passall already present")
else:
    # Anchor after keygen_bind_port_to_schemes() (exported, guaranteed present).
    anchor = "EXPORT_SYMBOL_GPL(keygen_bind_port_to_schemes);\n"
    if anchor not in src:
        fatal("keygen_bind_port_to_schemes export anchor not found in fman_keygen.c")

    # WSEL shift for the entries_mask field of the cls-plan AR (vendor: 8).
    wsel_def = ""
    if "FM_KG_KGAR_CLS_PLAN_WSEL_SHIFT" not in src:
        wsel_def = "#define FM_KG_KGAR_CLS_PLAN_WSEL_SHIFT\t8\n"

    func = (
        anchor +
        "\n" + wsel_def +
        "/* F-214: classification-plan entry group 0 = pass-all (0xffffffff).\n"
        " * Mainline binds every port CPP=0 but never writes a CP-entry mask, so\n"
        " * QLCV = CP_entry0_mask & LCV can be zeroed by an uninitialized CP RAM\n"
        " * word -> every kgse_mv!=0 scheme misses (FM_FD_ERR_NO_SCHEME). The\n"
        " * vendor's empty NetEnv group writes 0xffffffff into all 8 CP entries.\n"
        " * This ports that one write for @hw_port_id's selected group 0.\n"
        " * Returns 0 / -EINVAL / -EIO(readback).\n"
        " */\n"
        "int keygen_cls_plan0_passall(struct fman_keygen *keygen, u8 hw_port_id)\n"
        "{\n"
        "\tstruct fman_kg_regs __iomem *regs;\n"
        "\tu32 ar, rv;\n"
        "\tint i, err;\n"
        "\n"
        "\tif (!keygen || !keygen->keygen_regs)\n"
        "\t\treturn -EINVAL;\n"
        "\n"
        "\t/* One-time only: rewriting the shared global CP group 0 while another\n"
        "\t * port is already live times out FMFP_EXTC SYNC and wedges both ports\n"
        "\t * (image 2040). Group 0 is global (every port binds CPP=0), so a\n"
        "\t * single verified write suffices; later port engages skip all AR\n"
        "\t * access. Serialized by the PCD mutex the caller (F-211) holds. */\n"
        "\tif (keygen->cls_plan0_passall)\n"
        "\t\treturn 0;\n"
        "\tregs = keygen->keygen_regs;\n"
        "\n"
        "\t/* 8 CP-entry mask words live at the start of the indirect window. */\n"
        "\tfor (i = 0; i < 8; i++)\n"
        "\t\tiowrite32be(0xffffffff, &regs->fmkg_indirect[i]);\n"
        "\n"
        "\t/* AR: GO|WRITE|SEL_CLS_PLAN_ENTRY | grp0<<16 | entries(0xff)<<8 | port */\n"
        "\tar = FM_KG_KGAR_GO | FM_KG_KGAR_WRITE |\n"
        "\t     FM_KG_KGAR_SEL_CLS_PLAN_ENTRY |\n"
        "\t     ((u32)0 << FM_KG_KGAR_NUM_SHIFT) |\n"
        "\t     ((u32)0xff << FM_KG_KGAR_CLS_PLAN_WSEL_SHIFT) |\n"
        "\t     (u32)hw_port_id;\n"
        "\terr = keygen_write_ar_wait(regs, ar);\n"
        "\tif (err) {\n"
        "\t\tpr_err(\"fman_keygen: F-214 cls-plan0 write AR failed (%d)\\n\", err);\n"
        "\t\treturn err;\n"
        "\t}\n"
        "\n"
        "\t/* Readback-verify entry 0 (S6 R10.2). */\n"
        "\tar = FM_KG_KGAR_GO | FM_KG_KGAR_READ |\n"
        "\t     FM_KG_KGAR_SEL_CLS_PLAN_ENTRY |\n"
        "\t     ((u32)0 << FM_KG_KGAR_NUM_SHIFT) |\n"
        "\t     ((u32)0xff << FM_KG_KGAR_CLS_PLAN_WSEL_SHIFT) |\n"
        "\t     (u32)hw_port_id;\n"
        "\terr = keygen_write_ar_wait(regs, ar);\n"
        "\tif (err) {\n"
        "\t\tpr_err(\"fman_keygen: F-214 cls-plan0 read AR failed (%d)\\n\", err);\n"
        "\t\treturn err;\n"
        "\t}\n"
        "\trv = ioread32be(&regs->fmkg_indirect[0]);\n"
        "\tif (rv != 0xffffffff) {\n"
        "\t\tpr_err(\"fman_keygen: F-214 cls-plan0 readback 0x%08x != 0xffffffff\\n\",\n"
        "\t\t       rv);\n"
        "\t\treturn -EIO;\n"
        "\t}\n"
        "\tkeygen->cls_plan0_passall = true;\n"
        "\tpr_info(\"fman_keygen: F-214 cls-plan group0 pass-all set once (via port 0x%02x)\\n\",\n"
        "\t\thw_port_id);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(keygen_cls_plan0_passall);\n"
    )
    src = src.replace(anchor, func, 1)
    changes += 1
    with open(kg_c, "w") as f:
        f.write(src)
    print("### fman_keygen.c: F-214 keygen_cls_plan0_passall added")

# ─────────────────────────────────────────────────────────────────────────
# 2. fman_keygen_internal.h: declare the new export.
# ─────────────────────────────────────────────────────────────────────────
with open(ih) as f:
    hsrc = f.read()

if "keygen_cls_plan0_passall" in hsrc:
    print("### F-214: internal header decl already present")
else:
    h_anchor = ("int keygen_bind_port_to_schemes(struct fman_keygen *keygen, u8 scheme_id,\n"
                "\t\t\t\tbool bind);\n")
    if h_anchor not in hsrc:
        fatal("keygen_bind_port_to_schemes decl anchor not found in internal header")
    h_block = (
        h_anchor +
        "\n"
        "/* F-214: set classification-plan entry group 0 = pass-all (0xffffffff)\n"
        " * so QLCV = plan_mask & LCV carries the parser LCV into scheme select. */\n"
        "int keygen_cls_plan0_passall(struct fman_keygen *keygen, u8 hw_port_id);\n"
    )
    hsrc = hsrc.replace(h_anchor, h_block, 1)
    changes += 1
    with open(ih, "w") as f:
        f.write(hsrc)
    print("### fman_keygen_internal.h: F-214 decl added")

# ─────────────────────────────────────────────────────────────────────────
# 3. fman_pcd_kg.c: call it from F-211's v6 arm block, right after the v6
#    scheme is bound (before the mutex_unlock), so QLCV passes before traffic.
# ─────────────────────────────────────────────────────────────────────────
with open(pcd_kg_c) as f:
    psrc = f.read()

if "F-214(call)" in psrc:
    print("### F-214: arm-site call already present")
else:
    # Anchor on F-211's bind line (unique to the v6 arm block).
    call_anchor = "\t\t(void)keygen_bind_port_to_schemes(keygen, v6id, true);\n"
    if call_anchor not in psrc:
        fatal("F-211 v6 bind anchor not found in fman_pcd_kg.c (F-211 must run first)")
    call_new = (
        call_anchor +
        "\t\t/* F-214(call): make QLCV = plan_mask & LCV pass the parser LCV\n"
        "\t\t * (CP entry 0 pass-all); without this an uninitialized CP mask can\n"
        "\t\t * zero QLCV and every kgse_mv!=0 scheme misses (NO_SCHEME). */\n"
        "\t\t(void)keygen_cls_plan0_passall(keygen, hw_port_id);\n"
    )
    psrc = psrc.replace(call_anchor, call_new, 1)
    changes += 1
    with open(pcd_kg_c, "w") as f:
        f.write(psrc)
    print("### fman_pcd_kg.c: F-214 pass-all call wired into F-211 v6 arm")

if changes:
    print(f"### F-214 complete ({changes} change(s))")
else:
    print("### F-214 no changes (already present)")
    sys.exit(0)
