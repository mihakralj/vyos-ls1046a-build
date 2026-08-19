"""F-211 (T-M6-1 IPv6 productization, step 3): arm the second (IPv6) KeyGen
scheme at engage — the in-kernel reproduction of bin/kg-lcv-probe.py's proven
exp-apply/exp-ccobase recipe. GATED on fsl_dpaa_fman.v6_enable (F-210); v4 byte-
identical when OFF.

BACKGROUND — why this is now safe to implement (F-140 Part B was deferred)
-------------------------------------------------------------------------
F-140 deferred the v6 scheme arm because it assumed the live port ran
NIA_KG_DIRECT (F-178), under which a second scheme can never be selected. That
assumption is now stale: F-183 REMOVED F-178's NIA_KG_DIRECT OR from the arm_fe
path (see F-183(kg-direct-removed)); the production port runs rfpne=0x00480200
(KG|CC_EN, no DIRECT bit), i.e. the SI/match-vector walk is ALREADY active. The
v4 scheme currently has match_vector=0, so the walk selects it as "first enabled
scheme (mv=0 matches every frame)". To add v6 we simply:
  * give the v4 scheme match_vector = V4BIT (so it stops matching v6 frames),
  * arm a second scheme with match_vector = V6BIT, cc_base_offset = 1
    (CCOBASE=1 -> KGSE_MODE 0x81000006 via F-209 -> dispatches to table1's node
    written by F-210 at gro+16), ekfc = 0x801C0006, next_engine = 3 (AC_CC),
  * bind the v6 scheme into the port's scheme partition,
  * commit the v6 node's word3 miss-NIA to the v6 scheme's own base FQID.
F-212 adds the parser LCV split that makes QLCV carry V4BIT for IPv4 frames and
V6BIT for IPv6 frames, which is what the (QLCV & kgse_mv)==kgse_mv walk keys off.

This is EXACTLY the sequence exp-apply + exp-ccobase ran on eth1 (2026-08-19)
that produced the clean IPv6 HIT into table1 (pkt_count 0->3) with the v4 scheme
still hitting table0 (distinct kgse_spc on both) — READBACK OK.

SLOT MECHANICS (verified against 0097/0132/0158 + F-183/F-185/F-186)
--------------------------------------------------------------------
  * keygen = fman->keygen; schemes live in keygen->schemes[0..31].
  * kg_find_port_scheme(keygen, hw_port_id, &id) -> the v4 slot (already armed
    next_engine=3 by F-185).
  * free v6 slot: first schemes[i].used==false (mirrors kg_alloc_scheme_id's
    first-fit; the arm path holds the pcd lock so the scan is race-free).
  * keygen_scheme_setup(keygen, sid, true) writes the slot to HW via the AR
    indirect protocol (kgse_mv = slot->match_vector at 0097:200; CCOBASE via
    F-209; EKFC via 0158).
  * keygen_bind_port_to_schemes(keygen, sid, true) sets the port's scheme-bind
    bit (the in-kernel equivalent of the probe's port_sp_write sp|=1<<(31-sid)).
  * v6 node word3 = the v6 scheme's own base_fqid (== v4 base_fqid: same port,
    same BM pool — E25's cross-port-drop rule), written at gro+16+12.

SAFETY / S0
-----------
Default OFF: with fman_pcd_v6_enabled()==false NONE of this runs; the v4 slot
keeps match_vector=0 and there is no second scheme — byte-identical to today.
Every scheme write is followed by keygen_scheme_setup (which mainline readback-
verifies via the AR GO/ERR poll). Qdrant gate satisfied: kgse_mv/CCOBASE/SI-walk
selection cross-checked against arch/fman-microcode-210-programming-reference.md
and the passing 2026-08-19 dual-scheme silicon proof (scheme2/scheme5 distinct
kgse_spc). One variable per the plan: F-211 only arms the scheme; F-212 only
splits the LCV; F-210 only writes the node.

Must run AFTER F-183 (anchors on F-183(kg-direct-removed)) and AFTER F-186 (miss
capture) and AFTER F-209 (CCOBASE encode) and AFTER F-140 (table1). Placed after
F-210 in ci-setup-kernel.sh. Idempotent via the F-211 markers.
"""

import os
import sys

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
kg_c = os.path.join(kroot, "fman_pcd_kg.c")
ih = os.path.join(kroot, "fman_pcd_internal.h")

# LCV / match-vector bits — MUST match bin/kg-lcv-probe.py defaults and F-205's
# example bits (v4=0x40000000, v6=0x80000000). Single-source here; F-212 uses
# the same literals for the parser LCV split so QLCV & kgse_mv lines up.
V4BIT = "0x40000000U"
V6BIT = "0x80000000U"

changes = 0


def fatal(msg):
    print(f"### F-211: FATAL: {msg}")
    sys.exit(1)


def apply_one(path, name, marker, old, new):
    global changes
    with open(path) as f:
        s = f.read()
    if marker not in new:
        fatal(f"marker {marker} not embedded in replacement for '{name}'")
    if marker in s:
        print(f"### F-211: {name} already applied")
        return
    if old not in s:
        fatal(f"'{name}' anchor not found verbatim in {path} — source drifted.")
    s = s.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(s)
    changes += 1
    print(f"### {path}: F-211 {name} applied")


# ─────────────────────────────────────────────────────────────────────────
# 1. fman_pcd.c: helper to commit the v6 node's word3 miss-NIA at gro+16+12.
#    (F-185's setter writes table0's word3 at node_off+12; the v6 node lives at
#    node_off+16, so its word3 is node_off+28.)
# ─────────────────────────────────────────────────────────────────────────
apply_one(
    pcd_c,
    "v6 node miss-nia helper",
    "F-211(v6-miss-nia)",
    "/* Debugfs wrapper — parse string, delegate to __fman_pcd_fe_arm_engage(). */\n",
    "/* F-211(v6-miss-nia): write word3 (miss NIA) of the IPv6 vendor node,\n"
    " * which F-210 places at node_off+16 (gro+16, CCOBASE=1). Its word3 is\n"
    " * therefore at node_off+28. No-op unless v6 is enabled and the node form\n"
    " * was built (table1 present). Mirror of fman_pcd_fe_node_set_miss_nia()\n"
    " * for the v4 node at +12.\n"
    " */\n"
    "void fman_pcd_fe_v6node_set_miss_nia(struct fman_pcd *pcd, u32 node_off,\n"
    "\t\t\t\t     u32 nia)\n"
    "{\n"
    "\tstruct muram_info *muram = fman_get_muram(pcd->fman);\n"
    "\tvoid __iomem *nd;\n"
    "\n"
    "\tif (!fman_pcd_v6_enabled() || !muram || !node_off ||\n"
    "\t    list_empty(&pcd->fe_ehash_tables))\n"
    "\t\treturn;\n"
    "\tnd = (void __iomem *)(void *)\n"
    "\t\tfman_muram_offset_to_vbase(muram, node_off);\n"
    "\tiowrite32be(nia, nd + 28);\n"
    "}\n"
    "\n"
    "/* Debugfs wrapper — parse string, delegate to __fman_pcd_fe_arm_engage(). */\n",
)

# ─────────────────────────────────────────────────────────────────────────
# 2. fman_pcd_internal.h: declare the v6 helper.
# ─────────────────────────────────────────────────────────────────────────
apply_one(
    ih,
    "v6 miss-nia helper decl",
    "F-211(v6-miss-nia-decl)",
    "/* F-210(v6-enable-decl): master gate for the dormant IPv6 FE path. */\n"
    "bool fman_pcd_v6_enabled(void);\n",
    "/* F-210(v6-enable-decl): master gate for the dormant IPv6 FE path. */\n"
    "bool fman_pcd_v6_enabled(void);\n"
    "/* F-211(v6-miss-nia-decl): commit the v6 node's word3 miss-NIA (gro+28). */\n"
    "void fman_pcd_fe_v6node_set_miss_nia(struct fman_pcd *pcd, u32 node_off,\n"
    "\t\t\t\t     u32 nia);\n",
)

# ─────────────────────────────────────────────────────────────────────────
# 3. fman_pcd_kg.c: arm the v6 scheme at the arm_fe success tail.
#    Anchor on F-183(kg-direct-removed)'s comment block + the return 0 tail.
# ─────────────────────────────────────────────────────────────────────────
arm_anchor = (
    "\t/* F-183(kg-direct-removed): F-178's NIA_KG_DIRECT OR is gone from\n"
    "\t * this path. The vendor RFPNE for the ehash dispatch is\n"
    "\t * 0x00480200 (KG|CC_EN, no DIRECT bit) per the .106 static\n"
    "\t * oracle; E20 recorded the engage writing 0x00480304\n"
    "\t * (DIRECT|sch4) as confound #2. The rfpne value\n"
    "\t * fman_port_set_cc_base() wrote (0x00480200) stays unmodified.\n"
    "\t */\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_arm_fe);\n"
)

arm_new = (
    "\t/* F-183(kg-direct-removed): F-178's NIA_KG_DIRECT OR is gone from\n"
    "\t * this path. The vendor RFPNE for the ehash dispatch is\n"
    "\t * 0x00480200 (KG|CC_EN, no DIRECT bit) per the .106 static\n"
    "\t * oracle; E20 recorded the engage writing 0x00480304\n"
    "\t * (DIRECT|sch4) as confound #2. The rfpne value\n"
    "\t * fman_port_set_cc_base() wrote (0x00480200) stays unmodified.\n"
    "\t */\n"
    "\n"
    "\t/* F-211(v6-scheme-arm): productize the 2026-08-19 exp-apply recipe.\n"
    "\t * GATED on fman_pcd_v6_enabled(); v4 byte-identical when OFF (v4 slot\n"
    "\t * keeps match_vector=0, no second scheme). With v6 on: give v4 its own\n"
    "\t * match-vector bit, clone it into a free slot as the v6 scheme with\n"
    "\t * CCOBASE=1 + V6 match-vector + EKFC 0x801C0006, bind it into the\n"
    "\t * port, and commit the v6 node's miss FQID. F-183 already removed\n"
    "\t * KG-direct so the SI/match-vector walk is live; F-212 splits the\n"
    "\t * parser LCV so QLCV carries V4BIT for v4 frames and V6BIT for v6.\n"
    "\t */\n"
    "\tif (fman_pcd_v6_enabled()) {\n"
    "\t\tstruct keygen_scheme *v4slot, *v6slot;\n"
    "\t\tu8 v4id = 0, v6id = 0;\n"
    "\t\tint i, verr;\n"
    "\n"
    "\t\tmutex_lock(lock);\n"
    "\t\tv4slot = kg_find_port_scheme(keygen, hw_port_id, &v4id);\n"
    "\t\tif (!v4slot) {\n"
    "\t\t\tmutex_unlock(lock);\n"
    "\t\t\tpr_warn(\"fman_pcd fe_arm: F-211 v4 slot vanished; v6 arm skipped\\n\");\n"
    "\t\t\treturn 0;\n"
    "\t\t}\n"
    "\n"
    "\t\t/* find a free scheme slot for v6 (first-fit under the lock) */\n"
    "\t\tv6slot = NULL;\n"
    "\t\tfor (i = 0; i < FM_KG_MAX_NUM_OF_SCHEMES; i++) {\n"
    "\t\t\tif (!keygen->schemes[i].used) {\n"
    "\t\t\t\tv6id = (u8)i;\n"
    "\t\t\t\tv6slot = &keygen->schemes[i];\n"
    "\t\t\t\tbreak;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t\tif (!v6slot) {\n"
    "\t\t\tmutex_unlock(lock);\n"
    "\t\t\tpr_warn(\"fman_pcd fe_arm: F-211 no free scheme slot for v6; v6 arm skipped\\n\");\n"
    "\t\t\treturn 0;\n"
    "\t\t}\n"
    "\n"
    "\t\t/* 1) v4 scheme: match only IPv4 frames now (was mv=0 = match-all) */\n"
    "\t\tv4slot->match_vector = " + V4BIT + ";\n"
    "\t\tv4slot->used = false;\n"
    "\t\t(void)keygen_scheme_setup(keygen, v4id, true);\n"
    "\n"
    "\t\t/* 2) v6 scheme: clone the v4 slot, then override for IPv6 */\n"
    "\t\t*v6slot = *v4slot;\n"
    "\t\tv6slot->hw_port_id    = hw_port_id;\n"
    "\t\tv6slot->next_engine   = 3;\t\t/* AC_CC */\n"
    "\t\tv6slot->cc_base_offset = 1;\t\t/* CCOBASE=1 -> table1 node (F-210) */\n"
    "\t\tv6slot->cc_bits_sel   = 0;\n"
    "\t\tv6slot->match_vector  = " + V6BIT + ";\n"
    "\t\tv6slot->ekfc          = 0x801C0006U;\t/* PORT_ID|SIP|DIP|PROTO|SPORT|DPORT */\n"
    "\t\tv6slot->used          = false;\n"
    "\t\tverr = keygen_scheme_setup(keygen, v6id, true);\n"
    "\t\tif (verr) {\n"
    "\t\t\tv6slot->used = false;\n"
    "\t\t\tmutex_unlock(lock);\n"
    "\t\t\tpr_warn(\"fman_pcd fe_arm: F-211 v6 scheme_setup failed (%d); v6 arm skipped\\n\", verr);\n"
    "\t\t\treturn 0;\n"
    "\t\t}\n"
    "\n"
    "\t\t/* 3) bind the v6 scheme into this port's scheme partition */\n"
    "\t\t(void)keygen_bind_port_to_schemes(keygen, v6id, true);\n"
    "\t\tmutex_unlock(lock);\n"
    "\n"
    "\t\t/* 4) v6 node word3 = the v6 scheme's own base FQID (same port/pool\n"
    "\t\t * as v4 per E25). The node lives at gro+16 (F-210).\n"
    "\t\t */\n"
    "\t\tfman_pcd_fe_v6node_set_miss_nia(pcd, fe_enter_off, miss_fqid);\n"
    "\t\tpr_info(\"fman_pcd fe_arm: F-211 v6 scheme %u armed on port 0x%02x (v4 scheme %u mv=%#x, v6 mv=%#x, CCOBASE=1)\\n\",\n"
    "\t\t\tv6id, hw_port_id, v4id, " + V4BIT + ", " + V6BIT + ");\n"
    "\t}\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(fman_pcd_kg_port_arm_fe);\n"
)

apply_one(kg_c, "arm_fe v6 scheme arm", "F-211(v6-scheme-arm)",
          arm_anchor, arm_new)

if changes:
    print(f"### F-211 complete ({changes} change(s))")
else:
    print("### F-211 no changes applied (already present)")
    sys.exit(0)
