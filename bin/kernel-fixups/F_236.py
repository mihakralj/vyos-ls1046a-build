"""F-236 (T-M6-8 VLAN-v6 V6-2): let a CC-tree-targeting KeyGen scheme
(next_engine == 2) opt into the same 46-byte dual-lane GEC extraction the
FE/ehash scheme (next_engine == 3) already uses, instead of the plain
14-byte EKFC composite fman_pcd_kg_port_attach_cc() programs today.

WHY A FIXUP, NOT A PATCH
------------------------
The GEC-write block this widens does not exist in the git-committed source --
it is inserted by F-224 (bin/kernel-fixups/F_224.py) at build time, gated
`if (scheme->next_engine == 3)`. A static .patch file cannot reference code
that F-224 itself creates, so this has to run AFTER F-224, as a fixup, exactly
like F-224 is itself a fixup rather than a patch for the same reason (it
replaces code the *previous* stage of the pipeline produced).

WHAT IT DOES
------------
1. fman_keygen_internal.h: adds `bool gec_dual_lane;` to `struct
   keygen_scheme` (a NEW field on the *scheme* struct -- distinct from
   F-214's `cls_plan0_passall`, which lives on the *keygen driver* struct).
   Defaults false for every existing next_engine==2 caller
   (fman_pcd_kg_port_attach_cc()), so the shipped v4-only VLAN CC-tree path
   is untouched.
2. fman_keygen.c: widens F-224's own
     if (scheme->next_engine == 3) {
   to
     if (scheme->next_engine == 3 ||
         (scheme->next_engine == 2 && scheme->gec_dual_lane)) {
   The register-write code inside is F-224's, byte-for-byte unchanged --
   this only broadens which schemes can reach it. Fatal if F-224's marker
   isn't present yet (ordering precondition).
3. fman_pcd_kg.c: adds fman_pcd_kg_port_attach_cc_dual(), a near-duplicate
   of the existing fman_pcd_kg_port_attach_cc() (which stays byte-for-byte
   untouched -- this is a NEW function, not a modification of the shipped
   one) that additionally sets slot->gec_dual_lane = true before calling
   keygen_scheme_setup(). ekfc stays 0x801C0006 as the non-zero trigger for
   keygen_scheme_setup()'s `if (scheme->ekfc)` gate (see F-224's own comment
   in fman_keygen.c) -- its actual value is irrelevant once the widened GEC
   branch overrides kgse_ekfc to 0, exactly mirroring how F-224's own
   next_engine==3 path already works.

Companion regular patch (0186, kernel/common/patches/board/): adds
struct fman_pcd_cc_hw_spec.dual_lane (drives fman_pcd_cc_static_install() to
use cc_pack_key_dual()/CC_KEY_SIZE_DUAL, added dormant in 0185), the public
fman_pcd_kg_port_attach_cc_dual() declaration, and a cc_test debugfs verb
("install_v6") that arms ONE v6 CC key (no HMTD -- plain enqueue) via this
new attach function, for the V6-2 silicon de-risk: prove a v6 CC-tree HIT
with the dual-lane key on a sacrificial port before anything production-side
depends on this path.

Must run AFTER F-224 (anchors on its "F-224(dual-lane-gec)" marker) and
after the keygen internal header/fman_pcd_kg.c exist (F-211 era, same as
F-214). Idempotent via per-section markers, same convention as F-214.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
kg_c = os.path.join(kroot, "fman_keygen.c")
ih = os.path.join(kroot, "fman_keygen_internal.h")
pcd_kg_c = os.path.join(kroot, "fman_pcd_kg.c")

changes = 0


def fatal(msg):
    print(f"### F-236: FATAL: {msg}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────
# 0. fman_keygen_internal.h: add gec_dual_lane to struct keygen_scheme.
# ─────────────────────────────────────────────────────────────────────────
with open(ih) as f:
    ihsrc = f.read()

if "gec_dual_lane" not in ihsrc:
    struct_anchor = (
        "\tu32 cc_bits_sel;\t/* MURAM byte offset of the CC root group\n"
        "\t\t\t\t * table (KGSE_CCBS).  Non-zero dispatches\n"
        "\t\t\t\t * the CC walk (next_engine == 2); the NIA\n"
        "\t\t\t\t * stays BMI direct-enqueue (HW-proven).\n"
        "\t\t\t\t */\n"
        "};\n"
    )
    if struct_anchor not in ihsrc:
        fatal("struct keygen_scheme cc_bits_sel anchor not found in internal header")
    struct_new = (
        "\tu32 cc_bits_sel;\t/* MURAM byte offset of the CC root group\n"
        "\t\t\t\t * table (KGSE_CCBS).  Non-zero dispatches\n"
        "\t\t\t\t * the CC walk (next_engine == 2); the NIA\n"
        "\t\t\t\t * stays BMI direct-enqueue (HW-proven).\n"
        "\t\t\t\t */\n"
        "\tbool gec_dual_lane;\t/* T-M6-8 VLAN-v6 F-236: when set alongside\n"
        "\t\t\t\t * next_engine == 2, keygen_scheme_setup()\n"
        "\t\t\t\t * writes the F-224 46-byte dual-lane GEC\n"
        "\t\t\t\t * extraction instead of the plain EKFC\n"
        "\t\t\t\t * composite. False for every existing\n"
        "\t\t\t\t * next_engine==2 caller (shipped v4 VLAN CC\n"
        "\t\t\t\t * tree unaffected).\n"
        "\t\t\t\t */\n"
        "};\n"
    )
    ihsrc = ihsrc.replace(struct_anchor, struct_new, 1)
    with open(ih, "w") as f:
        f.write(ihsrc)
    changes += 1
    print("### fman_keygen_internal.h: F-236 gec_dual_lane flag added to struct keygen_scheme")
else:
    print("### F-236: struct flag already present")


# ─────────────────────────────────────────────────────────────────────────
# 1. fman_keygen.c: widen F-224's GEC-write gate to also cover next_engine==2
#    schemes with gec_dual_lane set.
# ─────────────────────────────────────────────────────────────────────────
with open(kg_c) as f:
    src = f.read()

if "F-236(cc-dual-lane)" in src:
    print("### F-236: GEC gate already widened")
else:
    if "F-224(dual-lane-gec)" not in src:
        fatal("F-224 marker not found in fman_keygen.c -- F-224 must run before F-236")

    anchor = "\t\tif (scheme->next_engine == 3) {\n"
    if anchor not in src:
        fatal("F-224 next_engine==3 GEC gate not found in fman_keygen.c")

    new = (
        "\t\t/* F-236(cc-dual-lane): a CC-tree-targeting scheme (next_engine==2)\n"
        "\t\t * opts into the same dual-lane GEC extraction via gec_dual_lane,\n"
        "\t\t * set only by fman_pcd_kg_port_attach_cc_dual() -- every existing\n"
        "\t\t * next_engine==2 caller (fman_pcd_kg_port_attach_cc(), the shipped\n"
        "\t\t * v4 VLAN CC-tree path) leaves it false, so this OR is a no-op for\n"
        "\t\t * production today. */\n"
        "\t\tif (scheme->next_engine == 3 ||\n"
        "\t\t    (scheme->next_engine == 2 && scheme->gec_dual_lane)) {\n"
    )
    src = src.replace(anchor, new, 1)
    changes += 1
    with open(kg_c, "w") as f:
        f.write(src)
    print("### fman_keygen.c: F-236 GEC gate widened to next_engine==2+gec_dual_lane")


# ─────────────────────────────────────────────────────────────────────────
# 2. fman_pcd_kg.c: add fman_pcd_kg_port_attach_cc_dual(), a new function
#    (fman_pcd_kg_port_attach_cc() itself stays byte-for-byte untouched).
# ─────────────────────────────────────────────────────────────────────────
with open(pcd_kg_c) as f:
    psrc = f.read()

if "fman_pcd_kg_port_attach_cc_dual" in psrc:
    print("### F-236: fman_pcd_kg_port_attach_cc_dual already present")
else:
    anchor = "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc);\n"
    if anchor not in psrc:
        fatal("fman_pcd_kg_port_attach_cc export anchor not found in fman_pcd_kg.c")

    func = (
        anchor +
        "\n"
        "/*\n"
        " * fman_pcd_kg_port_attach_cc_dual() - like fman_pcd_kg_port_attach_cc(),\n"
        " * but arms the port's scheme for F-224/F-236 dual-lane GEC extraction\n"
        " * (46-byte family-discriminated key, matches cc_pack_key_dual()) instead\n"
        " * of the plain 14-byte EKFC composite. T-M6-8 VLAN-v6 V6-2. See\n"
        " * fman_pcd_kg_port_attach_cc()'s own comment for the CC-graft recipe --\n"
        " * identical here except for the gec_dual_lane flag.\n"
        " */\n"
        "int fman_pcd_kg_port_attach_cc_dual(struct fman_pcd *pcd, u8 hw_port_id,\n"
        "				    u32 cc_group_off)\n"
        "{\n"
        "	struct fman_keygen *keygen;\n"
        "	struct keygen_scheme *slot;\n"
        "	struct fman *fman;\n"
        "	struct mutex *lock;\n"
        "	u32 saved_ccbs, saved_ekfc;\n"
        "	u8 saved_engine, id;\n"
        "	bool saved_dual;\n"
        "	int err;\n"
        "\n"
        "	if (!pcd || !cc_group_off)\n"
        "		return -EINVAL;\n"
        "	if (hw_port_id < 0x08 || hw_port_id >= 0x28)\n"
        "		return -EINVAL;\n"
        "\n"
        "	fman = fman_pcd_get_fman(pcd);\n"
        "	if (!fman || !fman->keygen)\n"
        "		return -ENXIO;\n"
        "	keygen = fman->keygen;\n"
        "	lock = fman_pcd_get_lock(pcd);\n"
        "\n"
        "	mutex_lock(lock);\n"
        "\n"
        "	slot = kg_find_port_scheme(keygen, hw_port_id, &id);\n"
        "	if (!slot) {\n"
        "		mutex_unlock(lock);\n"
        "		return -ENODEV;\n"
        "	}\n"
        "\n"
        "	saved_engine = slot->next_engine;\n"
        "	saved_ccbs   = slot->cc_bits_sel;\n"
        "	saved_ekfc   = slot->ekfc;\n"
        "	saved_dual   = slot->gec_dual_lane;\n"
        "\n"
        "	slot->next_engine    = 2;\n"
        "	slot->cc_base_offset = 0;\n"
        "	slot->cc_bits_sel    = cc_group_off;\n"
        "	slot->ekfc           = 0x801C0006;	/* non-zero EKFC gate trigger only;\n"
        "						 * F-236's widened branch overrides\n"
        "						 * kgse_ekfc to 0 and writes GEC\n"
        "						 * instead, exactly like F-224's own\n"
        "						 * next_engine==3 path. */\n"
        "	slot->gec_dual_lane  = true;\n"
        "\n"
        "	slot->used = false;\n"
        "	err = keygen_scheme_setup(keygen, id, true);\n"
        "	if (err) {\n"
        "		slot->next_engine   = saved_engine;\n"
        "		slot->cc_bits_sel   = saved_ccbs;\n"
        "		slot->ekfc          = saved_ekfc;\n"
        "		slot->gec_dual_lane = saved_dual;\n"
        "		slot->used = false;\n"
        "		(void)keygen_scheme_setup(keygen, id, true);\n"
        "		mutex_unlock(lock);\n"
        "		return err;\n"
        "	}\n"
        "\n"
        "	{\n"
        "		struct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
        "\n"
        "		if (rxport)\n"
        "			(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
        "	}\n"
        "\n"
        "	mutex_unlock(lock);\n"
        "	return 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_dual);\n"
    )
    psrc = psrc.replace(anchor, func, 1)
    changes += 1
    with open(pcd_kg_c, "w") as f:
        f.write(psrc)
    print("### fman_pcd_kg.c: F-236 fman_pcd_kg_port_attach_cc_dual added")

if changes:
    print(f"### F-236 complete ({changes} change(s))")
else:
    print("### F-236 no changes (already present)")
    sys.exit(0)
