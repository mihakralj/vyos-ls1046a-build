"""F-238 (T-M6-8 VLAN-v6 V6-2c): isolating experiment -- does the CC-tree
comparator need a real nonzero EKFC known-field bit to see a GEC-sourced
composite at all, independent of the GEC content itself?

WHY THIS EXISTS
----------------
install_v6 / install_v4gec (board patches 0186/0187, board-tested
2026-09-02) proved F-236's CC-tree GEC opt-in genuinely reprograms the
scheme (ekfc really zeroed, GEC words really written -- confirmed via
"ASK2-DBG scheme EKFC write: ekfc=0x00000000" in dmesg both times) but a
real, confirmed-arriving matching frame STILL misses the CC-tree
comparator -- for BOTH IPv4 and IPv6, using the identical all-GEC
(EKFC=0) machinery. This is a clean, family-independent negative result:
whatever makes a KeyGen-extracted composite visible to the CC-tree's
CONT_LOOKUP comparator does not fire when EKFC=0, even though the exact
same GEC words are independently proven (F-223, 2026-08-21) to correctly
feed the EHASH/FE-VM hash engine.

A qdrant/RM alignment review found the likely reason: this project's own
history (F-190, 2026-08-31) established the CC comparator's behavior is
sensitive to the scheme's EKFC value specifically, not just the final
extracted bytes -- and the vendor NEVER ships an EKFC=0 all-GEC scheme in
production (every real vendor scheme uses EKFC for known fields, GEC only
as a fallback for what EKFC can't reach). F-224's all-GEC dual-lane
design was this project's own invention for the EHASH path and had never
been exercised against the CC-tree comparator before this session.

WHAT IT DOES
------------
Widens the F-236 gec_dual_lane branch (which currently always forces
kgse_ekfc = 0) to skip that zeroing when a new gec_keep_ekfc flag is set,
so the scheme's real EKFC value (set by the new
fman_pcd_kg_port_attach_cc_dual_ekfc() caller to 0x80000000,
KG_SCH_KN_PORT_ID alone -- the single known field already established
(F-183/F-190) to extract a deterministic 0x00 byte on this ucode, and
already zeroed-dv0/dv1-safe via F-179's unconditional handling whenever
EKFC is nonzero) survives into the register write instead of being
zeroed. The six GEC words are written exactly as before, unchanged.

1. fman_keygen_internal.h: adds `bool gec_keep_ekfc;` to
   `struct keygen_scheme` (a third flag alongside F-236's gec_dual_lane).
2. fman_keygen.c: wraps F-236's `scheme_regs.kgse_ekfc = 0;` in
   `if (!scheme->gec_keep_ekfc)`. False for every existing gec_dual_lane
   caller (fman_pcd_kg_port_attach_cc_dual(), the install_v6/v4gec test
   verbs), so this is a no-op for everything built so far; next_engine==3
   (the shipped EHASH path) never sets it either.
3. fman_pcd_kg.c: adds fman_pcd_kg_port_attach_cc_dual_ekfc(), a near-
   duplicate of fman_pcd_kg_port_attach_cc_dual() (itself untouched) that
   additionally sets gec_keep_ekfc = true and ekfc = 0x80000000 instead
   of 0x801C0006 (which would still work as a nonzero gate trigger, but
   0x80000000 alone -- PORT_ID only -- is the minimal, single-known-field
   test: does ANY real known field suffice, not "does this specific
   EKFC bitmask suffice").

Must run AFTER F-236 (anchors on its own exported
fman_pcd_kg_port_attach_cc_dual() -- a function F-236 itself creates).
Idempotent via per-section markers, same convention as F-214/F-236.

Companion regular patch (kernel/common/patches/board/): a new
cc_pack_key variant reserving a leading PORT_ID byte (0x00, matching
what EKFC now genuinely extracts) ahead of the existing 46-byte
dual-lane content -- since "generic extracts APPEND after known EKFC
fields" (vendor source), a nonzero EKFC composite is 1 byte WIDER than
the all-GEC one, not the same width with different content -- plus a
cc_test verb to arm it and repeat the install_v6 silicon experiment.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
kg_c = os.path.join(kroot, "fman_keygen.c")
ih = os.path.join(kroot, "fman_keygen_internal.h")
pcd_kg_c = os.path.join(kroot, "fman_pcd_kg.c")

changes = 0


def fatal(msg):
    print(f"### F-238: FATAL: {msg}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────
# 0. fman_keygen_internal.h: add gec_keep_ekfc to struct keygen_scheme.
# ─────────────────────────────────────────────────────────────────────────
with open(ih) as f:
    ihsrc = f.read()

if "gec_keep_ekfc" not in ihsrc:
    if "gec_dual_lane" not in ihsrc:
        fatal("F-236's gec_dual_lane field not found -- F-236 must run before F-238")

    struct_anchor = (
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
    if struct_anchor not in ihsrc:
        fatal("struct keygen_scheme gec_dual_lane anchor not found in internal header")
    struct_new = (
        "\tbool gec_dual_lane;\t/* T-M6-8 VLAN-v6 F-236: when set alongside\n"
        "\t\t\t\t * next_engine == 2, keygen_scheme_setup()\n"
        "\t\t\t\t * writes the F-224 46-byte dual-lane GEC\n"
        "\t\t\t\t * extraction instead of the plain EKFC\n"
        "\t\t\t\t * composite. False for every existing\n"
        "\t\t\t\t * next_engine==2 caller (shipped v4 VLAN CC\n"
        "\t\t\t\t * tree unaffected).\n"
        "\t\t\t\t */\n"
        "\tbool gec_keep_ekfc;\t/* T-M6-8 VLAN-v6 F-238: when set alongside\n"
        "\t\t\t\t * gec_dual_lane, keygen_scheme_setup() does\n"
        "\t\t\t\t * NOT zero kgse_ekfc after writing the GEC\n"
        "\t\t\t\t * words -- it preserves whatever nonzero\n"
        "\t\t\t\t * ekfc the caller set (isolating experiment:\n"
        "\t\t\t\t * does the CC-tree comparator need a real\n"
        "\t\t\t\t * known-field bit set to see a GEC-sourced\n"
        "\t\t\t\t * composite at all?). False for every\n"
        "\t\t\t\t * existing gec_dual_lane caller.\n"
        "\t\t\t\t */\n"
        "};\n"
    )
    ihsrc = ihsrc.replace(struct_anchor, struct_new, 1)
    with open(ih, "w") as f:
        f.write(ihsrc)
    changes += 1
    print("### fman_keygen_internal.h: F-238 gec_keep_ekfc flag added to struct keygen_scheme")
else:
    print("### F-238: struct flag already present")


# ─────────────────────────────────────────────────────────────────────────
# 1. fman_keygen.c: guard F-236's kgse_ekfc = 0 zeroing on gec_keep_ekfc.
# ─────────────────────────────────────────────────────────────────────────
with open(kg_c) as f:
    src = f.read()

if "F-238(ekfc-trigger-test)" in src:
    print("### F-238: ekfc-preserve guard already present")
else:
    if "F-236(cc-dual-lane)" not in src:
        fatal("F-236 marker not found in fman_keygen.c -- F-236 must run before F-238")

    anchor = (
        "\t\t/* F-236(cc-dual-lane): a CC-tree-targeting scheme (next_engine==2)\n"
        "\t\t * opts into the same dual-lane GEC extraction via gec_dual_lane,\n"
        "\t\t * set only by fman_pcd_kg_port_attach_cc_dual() -- every existing\n"
        "\t\t * next_engine==2 caller (fman_pcd_kg_port_attach_cc(), the shipped\n"
        "\t\t * v4 VLAN CC-tree path) leaves it false, so this OR is a no-op for\n"
        "\t\t * production today. */\n"
        "\t\tif (scheme->next_engine == 3 ||\n"
        "\t\t    (scheme->next_engine == 2 && scheme->gec_dual_lane)) {\n"
        "\t\t\tscheme_regs.kgse_ekfc = 0;\n"
    )
    if anchor not in src:
        fatal("F-236 widened GEC gate + ekfc-zero line not found in fman_keygen.c")

    new = (
        "\t\t/* F-236(cc-dual-lane): a CC-tree-targeting scheme (next_engine==2)\n"
        "\t\t * opts into the same dual-lane GEC extraction via gec_dual_lane,\n"
        "\t\t * set only by fman_pcd_kg_port_attach_cc_dual() -- every existing\n"
        "\t\t * next_engine==2 caller (fman_pcd_kg_port_attach_cc(), the shipped\n"
        "\t\t * v4 VLAN CC-tree path) leaves it false, so this OR is a no-op for\n"
        "\t\t * production today. */\n"
        "\t\tif (scheme->next_engine == 3 ||\n"
        "\t\t    (scheme->next_engine == 2 && scheme->gec_dual_lane)) {\n"
        "\t\t\t/* F-238(ekfc-trigger-test): install_v6/install_v4gec\n"
        "\t\t\t * (T-M6-8 V6-2) board-proved an all-GEC (EKFC=0) composite\n"
        "\t\t\t * is invisible to the CC-tree comparator for BOTH families,\n"
        "\t\t\t * even though the identical GEC words correctly feed the\n"
        "\t\t\t * EHASH/FE-VM hash engine (F-223) and the register writes\n"
        "\t\t\t * land exactly as programmed. gec_keep_ekfc (set only by\n"
        "\t\t\t * fman_pcd_kg_port_attach_cc_dual_ekfc(), T-M6-8 V6-2c) is\n"
        "\t\t\t * an isolating experiment: preserve whatever nonzero EKFC\n"
        "\t\t\t * the caller already put in slot->ekfc (PORT_ID-only,\n"
        "\t\t\t * 0x80000000, 1 deterministic zero byte on this ucode)\n"
        "\t\t\t * instead of zeroing it, to test whether ANY nonzero known\n"
        "\t\t\t * field -- not this GEC content -- is what makes the\n"
        "\t\t\t * composite CC-visible. Every existing dual_lane caller\n"
        "\t\t\t * (fman_pcd_kg_port_attach_cc_dual(), install_v6/v4gec)\n"
        "\t\t\t * leaves gec_keep_ekfc false, so this branch is unchanged\n"
        "\t\t\t * for them; next_engine==3 (ehash) never sets it either. */\n"
        "\t\t\tif (!scheme->gec_keep_ekfc)\n"
        "\t\t\t\tscheme_regs.kgse_ekfc = 0;\n"
    )
    src = src.replace(anchor, new, 1)
    changes += 1
    with open(kg_c, "w") as f:
        f.write(src)
    print("### fman_keygen.c: F-238 ekfc-preserve guard added")


# ─────────────────────────────────────────────────────────────────────────
# 2. fman_pcd_kg.c: add fman_pcd_kg_port_attach_cc_dual_ekfc().
# ─────────────────────────────────────────────────────────────────────────
with open(pcd_kg_c) as f:
    psrc = f.read()

if "fman_pcd_kg_port_attach_cc_dual_ekfc" in psrc:
    print("### F-238: fman_pcd_kg_port_attach_cc_dual_ekfc already present")
else:
    anchor = "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_dual);\n"
    if anchor not in psrc:
        fatal("F-236's fman_pcd_kg_port_attach_cc_dual export anchor not found -- F-236 must run before F-238")

    func = (
        anchor +
        "\n"
        "/*\n"
        " * fman_pcd_kg_port_attach_cc_dual_ekfc() - like\n"
        " * fman_pcd_kg_port_attach_cc_dual(), but sets gec_keep_ekfc (F-238) so\n"
        " * keygen_scheme_setup() preserves a real nonzero EKFC (KG_SCH_KN_PORT_ID\n"
        " * alone, 0x80000000 -- a single known field that F-183/F-190 already\n"
        " * established extracts a deterministic 0x00 byte on this ucode, and\n"
        " * which F-179 already zeroes kgse_dv0/dv1 for whenever EKFC is nonzero)\n"
        " * instead of forcing kgse_ekfc to 0. T-M6-8 VLAN-v6 V6-2c isolating\n"
        " * experiment: install_v6/install_v4gec (board-tested 2026-09-02) proved\n"
        " * an all-GEC (EKFC=0) composite is invisible to the CC-tree comparator\n"
        " * for both families, even though the identical GEC words correctly feed\n"
        " * the EHASH hash engine and the register writes land exactly as\n"
        " * programmed -- this tests whether ANY nonzero known-field bit, not this\n"
        " * GEC content, is what makes a composite CC-visible at all.\n"
        " */\n"
        "int fman_pcd_kg_port_attach_cc_dual_ekfc(struct fman_pcd *pcd, u8 hw_port_id,\n"
        "\t\t\t\t\t u32 cc_group_off)\n"
        "{\n"
        "\tstruct fman_keygen *keygen;\n"
        "\tstruct keygen_scheme *slot;\n"
        "\tstruct fman *fman;\n"
        "\tstruct mutex *lock;\n"
        "\tu32 saved_ccbs, saved_ekfc;\n"
        "\tu8 saved_engine, id;\n"
        "\tbool saved_dual, saved_keep;\n"
        "\tint err;\n"
        "\n"
        "\tif (!pcd || !cc_group_off)\n"
        "\t\treturn -EINVAL;\n"
        "\tif (hw_port_id < 0x08 || hw_port_id >= 0x28)\n"
        "\t\treturn -EINVAL;\n"
        "\n"
        "\tfman = fman_pcd_get_fman(pcd);\n"
        "\tif (!fman || !fman->keygen)\n"
        "\t\treturn -ENXIO;\n"
        "\tkeygen = fman->keygen;\n"
        "\tlock = fman_pcd_get_lock(pcd);\n"
        "\n"
        "\tmutex_lock(lock);\n"
        "\n"
        "\tslot = kg_find_port_scheme(keygen, hw_port_id, &id);\n"
        "\tif (!slot) {\n"
        "\t\tmutex_unlock(lock);\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n"
        "\n"
        "\tsaved_engine = slot->next_engine;\n"
        "\tsaved_ccbs   = slot->cc_bits_sel;\n"
        "\tsaved_ekfc   = slot->ekfc;\n"
        "\tsaved_dual   = slot->gec_dual_lane;\n"
        "\tsaved_keep   = slot->gec_keep_ekfc;\n"
        "\n"
        "\tslot->next_engine    = 2;\n"
        "\tslot->cc_base_offset = 0;\n"
        "\tslot->cc_bits_sel    = cc_group_off;\n"
        "\tslot->ekfc           = 0x80000000;\t/* KG_SCH_KN_PORT_ID only: the\n"
        "\t\t\t\t\t\t * one known field guaranteed\n"
        "\t\t\t\t\t\t * to extract a deterministic\n"
        "\t\t\t\t\t\t * 0x00 byte on this ucode\n"
        "\t\t\t\t\t\t * (F-183/F-190), preserved\n"
        "\t\t\t\t\t\t * (not zeroed) because\n"
        "\t\t\t\t\t\t * gec_keep_ekfc is set below. */\n"
        "\tslot->gec_dual_lane  = true;\n"
        "\tslot->gec_keep_ekfc  = true;\n"
        "\n"
        "\tslot->used = false;\n"
        "\terr = keygen_scheme_setup(keygen, id, true);\n"
        "\tif (err) {\n"
        "\t\tslot->next_engine   = saved_engine;\n"
        "\t\tslot->cc_bits_sel   = saved_ccbs;\n"
        "\t\tslot->ekfc          = saved_ekfc;\n"
        "\t\tslot->gec_dual_lane = saved_dual;\n"
        "\t\tslot->gec_keep_ekfc = saved_keep;\n"
        "\t\tslot->used = false;\n"
        "\t\t(void)keygen_scheme_setup(keygen, id, true);\n"
        "\t\tmutex_unlock(lock);\n"
        "\t\treturn err;\n"
        "\t}\n"
        "\n"
        "\t{\n"
        "\t\tstruct fman_port *rxport = fman_port_lookup_rx(fman, hw_port_id);\n"
        "\n"
        "\t\tif (rxport)\n"
        "\t\t\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n"
        "\t}\n"
        "\n"
        "\tmutex_unlock(lock);\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_attach_cc_dual_ekfc);\n"
    )
    psrc = psrc.replace(anchor, func, 1)
    changes += 1
    with open(pcd_kg_c, "w") as f:
        f.write(psrc)
    print("### fman_pcd_kg.c: F-238 fman_pcd_kg_port_attach_cc_dual_ekfc added")

if changes:
    print(f"### F-238 complete ({changes} change(s))")
else:
    print("### F-238 no changes (already present)")
    sys.exit(0)
