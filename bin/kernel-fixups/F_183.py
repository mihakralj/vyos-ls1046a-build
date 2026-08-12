"""F-183: E20 corrected track, step 2 — Delta-1 dispatch, adapted to what
.185 silicon survives. Three files: fman_keygen.c (CCBS word-3 kernel bug),
fman_pcd_kg.c (arm_fe dispatch model), fman_pcd.c (group-root scaffold +
F-148 numKeys pin).

CONTEXT (2026-08-12, E20 / decomp/experiments.md): F-181's first silicon
test stalled port 0x11 on the FIRST dispatched frame (FMFP_PS 0x80800000).
Confounds #1 and #2 of E20 were dispatch-side:

  (1) dispatch form = bare FE_ENTER root at RCCB — the KNOWN-stalling form
      (re-proven by E20, Path A 08-10, 0118 iter-48). F-182 v3 (08-09)
      validated the group-tree root form does NOT stall.
  (2) engage still wrote AC_CC mode (0x80000006) + KG_DIRECT rfpne
      (0x00480304). Path A proved AC_CC + dispatched frame stalls on .185
      mainline; vendor RFPNE = 0x00480200 (KG|CC_EN, NO DIRECT bit).

PROVEN ON .185 (the pieces this fixup assembles — each individually
non-stalling, per E20 "Proven-working" section):
  - CCBS-graft dispatch: KGSE_MODE stays EN|ENQUEUE_KG_DFLT_NIA
    (0x80500002) and KGSE_CCBS carries the group-table MURAM offset —
    BUT 210.10.1 reads CCBS from scheme window WORD 3 (0x10C, the struct
    field labelled kgse_bmch), NOT word 19 (0x14C, struct kgse_ccbs)
    where keygen_scheme_setup writes it. F-184 session (08-10) proved
    live: word-19 writes never fired the walk; an AR write of word 3 =
    group offset fired it with 1:1 miss-row delivery. This is a real
    kernel bug — fix it here.
  - Group-tree root at RCCB does not stall; the CC comparator is
    INSENSITIVE to match-table rows (5 negative variants, 08-10) — frames
    always take the miss slot. Consequence: FE_ENTER cannot ride a match
    leaf; it must sit in the MISS slot of a numKeys=0 group, so every
    frame reaches FE_ENTER and the ehash decides HIT/MISS.

CHANGES:
  A/B. fman_keygen.c keygen_scheme_setup(): stop writing cc_bits_sel to
       kgse_ccbs (word 19, ignored by silicon); write it to kgse_bmch
       (word 3) instead, AFTER F-051's kgse_bmch zeroing (which would
       otherwise clobber it), immediately before the scheme write.
  C.   fman_pcd_kg.c arm_fe(): next_engine 3 -> 2 (CCBS-implicit; AC_CC
       stalls), cc_bits_sel = the group-table offset (fe_enter_off, which
       F-183-E repoints at the scaffold group).
  D.   fman_pcd_kg.c arm_fe(): drop F-178's NIA_KG_DIRECT OR — rfpne
       stays 0x00480200 (vendor value; E20 confound #2).
  H.   fman_pcd_kg.c arm_fe() v6 slot: cc_bits_sel = 0 so the IPv6 slot
       can never inherit a stale CC graft.
  E.   fman_pcd.c __fman_pcd_fe_arm_engage(): the scaffold group ALWAYS
       ships numKeys=0; with an explicit FE_ENTER target, a verbatim copy
       of the caller's FE_ENTER AD goes into the miss slot (ato[0]) and
       RCCB is repointed at the group (F-165's bare-FE_ENTER-direct form
       is the stalling topology — superseded).
  F.   fman_pcd.c add_key's F-148 block: do NOT publish the numKeys bump
       to the group word — numKeys=1 would move the miss slot from ato[0]
       (FE_ENTER copy) to ato[1] (kernel enq-AD) and bypass the FE-VM
       entirely after the first flow insert.
  G.   dmesg: ENGAGED line no longer claims AC_CC.

TEARDOWN: unchanged — disarm_fe -> detach_cc restores next_engine=0 and
cc_bits_sel=0, re-running keygen_scheme_setup, which now clears word 3
(kgse_bmch stays 0 via F-051's zeroing); set_cc_base(0) clears RCCB and
the rfpne CC_EN bit.

Anchored on the exact derived state (F-051 mutate, F-148, F-165, F-178
outputs). Idempotent (per-block "F-183:" markers). CI-only build.
"""

import sys

changes = 0


def edit(path, blocks):
    """blocks: list of (name, marker, old, new). The marker string MUST
    appear in new -- it is the per-block idempotency token."""
    global changes
    with open(path) as f:
        src = f.read()
    file_changes = 0
    for name, marker, old, new in blocks:
        if marker not in new:
            print(f"### F-183: FATAL: block '{name}' marker {marker} not "
                  "embedded in its replacement text -- fixup bug.")
            sys.exit(1)
        if marker in src:
            print(f"### F-183: {name} already applied")
            continue
        if old not in src:
            print(f"### F-183: FATAL: '{name}' text not found verbatim in "
                  f"{path} -- source drifted. Refusing to guess.")
            sys.exit(1)
        src = src.replace(old, new, 1)
        file_changes += 1
        changes += 1
        print(f"### {path}: F-183 {name} applied")
    if file_changes:
        with open(path, "w") as f:
            f.write(src)


# ── fman_keygen.c ──────────────────────────────────────────────────────────
kg_blocks = [
    # A. Stop writing CCBS to window word 19 (struct kgse_ccbs).
    ("ccbs word-19 write removed",
     "F-183(ccbs-w19-removed)",
     "\tif (enable && scheme->next_engine == 2)\n"
     "\t\tscheme_regs.kgse_ccbs = scheme->cc_bits_sel;\n",
     "\t/* F-183(ccbs-w19-removed): the ccbs word-3 write sits below the\n"
     "\t * F-051 zeroing (it must land AFTER kgse_bmch is cleared there).\n"
     "\t * Writing kgse_ccbs here targets scheme window word 19 (0x14C),\n"
     "\t * which 210.10.1 does not read for CC dispatch -- F-184 board\n"
     "\t * proof 2026-08-10. kgse_ccbs stays 0 from the memset.\n"
     "\t */\n"),
    # B. Write CCBS to window word 3 (struct kgse_bmch) before the write.
    ("ccbs word-3 write (kgse_bmch)",
     "F-183(ccbs-w3-write)",
     "\t/* F-051: force-clear RSS mask/hash config for exact-match ehash */\n"
     "\tscheme_regs.kgse_bmch = 0;\n"
     "\tscheme_regs.kgse_bmcl = 0;\n"
     "\tscheme_regs.kgse_hc   = 0;\n"
     "\tscheme_regs.kgse_ekdv = 0;\n"
     "\t/* Write scheme registers */\n",
     "\t/* F-051: force-clear RSS mask/hash config for exact-match ehash */\n"
     "\tscheme_regs.kgse_bmch = 0;\n"
     "\tscheme_regs.kgse_bmcl = 0;\n"
     "\tscheme_regs.kgse_hc   = 0;\n"
     "\tscheme_regs.kgse_ekdv = 0;\n"
     "\t/* F-183(ccbs-w3-write): 210.10.1 reads the CCBS dispatch offset\n"
     "\t * from scheme window word 3 (0x10C), the field the kernel struct\n"
     "\t * labels kgse_bmch, NOT word 19 (kgse_ccbs). Proven live on .185\n"
     "\t * (F-184, 2026-08-10): word-19 writes never fired the CC walk;\n"
     "\t * an AR write of word 3 = group offset fired it with 1:1 miss-row\n"
     "\t * delivery. Must sit after F-051's kgse_bmch zeroing above,\n"
     "\t * which would otherwise clobber it.\n"
     "\t */\n"
     "\tif (enable && scheme->next_engine == 2)\n"
     "\t\tscheme_regs.kgse_bmch = scheme->cc_bits_sel & 0x00FFFFFF;\n"
     "\t/* Write scheme registers */\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_keygen.c", kg_blocks)

# ── fman_pcd_kg.c ──────────────────────────────────────────────────────────
pkg_blocks = [
    # C. arm_fe: CCBS-implicit dispatch model (AC_CC stalls on .185).
    ("arm_fe CCBS-implicit model",
     "F-183(arm-fe-ccbs-implicit)",
     "\t/*\n"
     "\t * Arm the FE datapath (Fork B, Phase 1 D9-B, 0132 v3).\n"
     "\t * Use AC_CC dispatch (next_engine=3, mode 0x80000006, KGSE_CCBS=0):\n"
     "\t * the FMan controller walks the CC root named by the BMI port's\n"
     "\t * fmbm_rccb (set in step 2 below), which points at the FE_ENTER\n"
     "\t * CONT_LOOKUP root AD (group table).  The CCBS graft (next_engine=2,\n"
     "\t */\n"
     "\tslot->next_engine    = 3;\n"
     "\tslot->cc_base_offset = 0;\n"
     "\tslot->cc_bits_sel    = 0;\n",
     "\t/*\n"
     "\t * F-183(arm-fe-ccbs-implicit): the ONLY dispatch form with every\n"
     "\t * element individually proven non-stalling on .185 (E20,\n"
     "\t * 2026-08-12): KGSE_MODE stays EN|ENQUEUE_KG_DFLT_NIA (0x80500002,\n"
     "\t * next_engine=2 branch), KGSE_CCBS carries the group-table MURAM\n"
     "\t * offset (fe_enter_off -- repointed at the scaffold group by\n"
     "\t * F-183-E), written to scheme window WORD 3 by the F-183\n"
     "\t * keygen_scheme_setup fix. AC_CC mode (next_engine=3, 0x80000006)\n"
     "\t * STALLS port 0x11 on the first dispatched frame on .185 mainline\n"
     "\t * (E20 replaying 0118 iter-48 + Path A 08-10). The CCBS word-3\n"
     "\t * graft is the form that fired the walk with 1:1 miss-row delivery\n"
     "\t * (F-184 session) and the form that carried 24M+ frames on ask20.\n"
     "\t */\n"
     "\tslot->next_engine    = 2;\n"
     "\tslot->cc_base_offset = 0;\n"
     "\tslot->cc_bits_sel    = fe_enter_off & 0x00FFFFFF;\n"),
    # D. arm_fe: drop F-178's KG_DIRECT OR (vendor rfpne = 0x00480200).
    ("arm_fe KG_DIRECT removed",
     "F-183(kg-direct-removed)",
     "\t/* F-178: arm_fe direct-scheme addressing. Vendor's real SetPcd()\n"
     "\t * ORs NIA_KG_DIRECT | physicalSchemeId into fmbm_rfpne for a\n"
     "\t * single-bound-scheme port (RM sec 4.4's SI/match-vector walk is\n"
     "\t * otherwise used, and this scheme's own mv is 0 -- not meant to\n"
     "\t * be reached that way). F-162 already wrote this helper but only\n"
     "\t * ever called it from the abandoned attach_cc() CC-graft path;\n"
     "\t * this is the FE-VM arm path every T-M3-R test actually uses.\n"
     "\t */\n"
     "\t(void)fman_port_set_kg_direct_scheme(rxport, id);\n",
     "\t/* F-183(kg-direct-removed): F-178's NIA_KG_DIRECT OR is gone from\n"
     "\t * this path. The vendor RFPNE for the ehash dispatch is\n"
     "\t * 0x00480200 (KG|CC_EN, no DIRECT bit) per the .106 static\n"
     "\t * oracle; E20 recorded the engage writing 0x00480304\n"
     "\t * (DIRECT|sch4) as confound #2. The rfpne value\n"
     "\t * fman_port_set_cc_base() wrote (0x00480200) stays unmodified.\n"
     "\t */\n"),
    # H. v6 slot: never inherit a stale CC graft.
    ("v6 slot cc_bits_sel zeroed",
     "F-183(v6-ccbs-zero)",
     "\t\t\t\tvs->ekfc = ekfc;\n"
     "\t\t\t\tvs->next_engine = 2;\t/* CC (AC_CC dispatch) */\n"
     "\t\t\t\tvs->hw_port_id = hw_port_id;\n",
     "\t\t\t\tvs->ekfc = ekfc;\n"
     "\t\t\t\tvs->next_engine = 2;\t/* CC (AC_CC dispatch) */\n"
     "\t\t\t\tvs->cc_bits_sel = 0;\t/* F-183(v6-ccbs-zero): v6 slot rides plain RSS; never graft CC onto it */\n"
     "\t\t\t\tvs->hw_port_id = hw_port_id;\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd_kg.c", pkg_blocks)

# ── fman_pcd.c ─────────────────────────────────────────────────────────────
pcd_blocks = [
    # E. Scaffold: numKeys=0 always; explicit FE_ENTER target rides the
    #    miss slot; RCCB always repointed at the group.
    ("scaffold group-root miss-slot FE_ENTER",
     "F-183(group-root-miss-slot)",
     "\t\t\t\tiowrite32be(((fe_enter_off != 0 ? 1 : 0) << 24) | (mto & 0xFFFFFF),\n"
     "\t\t\t\t\t    c + 0);\n"
     "\t\t\t\tiowrite32be((ato & 0xFFFFFF), c + 4);\n"
     "\t\t\t\tiowrite32be(0x4F000000, c + 8);\n"
     "\t\t\t\tiowrite32be(0, c + 12);\n"
     "\t\t\t\tc = (void __iomem *)\n"
     "\t\t\t\t\t(void *)fman_muram_offset_to_vbase(muram, ato);\n"
     "\t\t\t\tiowrite32be((u32)miss_fqid, c + 0);\n"
     "\t\t\t\tiowrite32be(0, c + 4);\n"
     "\t\t\t\tiowrite32be(0, c + 8);\n"
     "\t\t\t\tiowrite32be(0, c + 12);\n"
     "\t\t\t\tiowrite32be((u32)miss_fqid, c + 16);\n"
     "\t\t\t\tiowrite32be(0, c + 20);\n"
     "\t\t\t\tiowrite32be(0, c + 24);\n"
     "\t\t\t\tiowrite32be(0, c + 28);\n"
     "\t\t\t\tif (!fe_enter_off)\n"
     "\t\t\t\t\t/* F-165: only the pass-through (production) path\n"
     "\t\t\t\t\t * repoints fe_enter_off at the scaffold. An\n"
     "\t\t\t\t\t * explicit caller-supplied non-zero target (the\n"
     "\t\t\t\t\t * debugfs FE_ENTER-direct test path) must survive\n"
     "\t\t\t\t\t * unmodified -- the scaffold is still allocated\n"
     "\t\t\t\t\t * and tracked for cleanup either way, it's just\n"
     "\t\t\t\t\t * not what FMBM_RCCB ends up pointing at.\n"
     "\t\t\t\t\t */\n"
     "\t\t\t\t\tfe_enter_off = gro;\n",
     "\t\t\t\t/* F-183(group-root-miss-slot): Delta-1 dispatch -- the\n"
     "\t\t\t\t * group ALWAYS ships numKeys=0. With an explicit\n"
     "\t\t\t\t * FE_ENTER target, a verbatim copy of the caller's\n"
     "\t\t\t\t * FE_ENTER AD sits in the MISS slot (ato[0]): every\n"
     "\t\t\t\t * frame -> FE_ENTER -> the ehash decides HIT/MISS.\n"
     "\t\t\t\t * The CC comparator is proven INSENSITIVE to match\n"
     "\t\t\t\t * rows (5 negative variants, 2026-08-10), so a\n"
     "\t\t\t\t * match-leaf FE_ENTER is unreachable -- the miss\n"
     "\t\t\t\t * slot is the only dispatch the walker honors. Bare\n"
     "\t\t\t\t * FE_ENTER-at-RCCB (the old off!=0 form) STALLS the\n"
     "\t\t\t\t * port on the first dispatched frame (E20).\n"
     "\t\t\t\t */\n"
     "\t\t\t\tiowrite32be((0U << 24) | (mto & 0xFFFFFF), c + 0);\n"
     "\t\t\t\tiowrite32be((ato & 0xFFFFFF), c + 4);\n"
     "\t\t\t\tiowrite32be(0x4F000000, c + 8);\n"
     "\t\t\t\tiowrite32be(0, c + 12);\n"
     "\t\t\t\tc = (void __iomem *)\n"
     "\t\t\t\t\t(void *)fman_muram_offset_to_vbase(muram, ato);\n"
     "\t\t\t\tif (fe_enter_off) {\n"
     "\t\t\t\t\tvoid __iomem *fe = (void __iomem *)\n"
     "\t\t\t\t\t\t(void *)fman_muram_offset_to_vbase(muram, fe_enter_off);\n"
     "\t\t\t\t\t/* miss slot = verbatim copy of the caller's FE_ENTER AD */\n"
     "\t\t\t\t\tiowrite32be(ioread32be(fe + 0), c + 0);\n"
     "\t\t\t\t\tiowrite32be(ioread32be(fe + 4), c + 4);\n"
     "\t\t\t\t\tiowrite32be(ioread32be(fe + 8), c + 8);\n"
     "\t\t\t\t\tiowrite32be(ioread32be(fe + 12), c + 12);\n"
     "\t\t\t\t\t/* ato[1] (unused at numKeys=0): park a kernel-delivery enq-AD */\n"
     "\t\t\t\t\tiowrite32be((u32)miss_fqid, c + 16);\n"
     "\t\t\t\t\tiowrite32be(0, c + 20);\n"
     "\t\t\t\t\tiowrite32be(0, c + 24);\n"
     "\t\t\t\t\tiowrite32be(0, c + 28);\n"
     "\t\t\t\t} else {\n"
     "\t\t\t\t\tiowrite32be((u32)miss_fqid, c + 0);\n"
     "\t\t\t\t\tiowrite32be(0, c + 4);\n"
     "\t\t\t\t\tiowrite32be(0, c + 8);\n"
     "\t\t\t\t\tiowrite32be(0, c + 12);\n"
     "\t\t\t\t\tiowrite32be((u32)miss_fqid, c + 16);\n"
     "\t\t\t\t\tiowrite32be(0, c + 20);\n"
     "\t\t\t\t\tiowrite32be(0, c + 24);\n"
     "\t\t\t\t\tiowrite32be(0, c + 28);\n"
     "\t\t\t\t}\n"
     "\t\t\t\t/* F-183: RCCB always points at the group. F-165's\n"
     "\t\t\t\t * bare-FE_ENTER-direct form for off!=0 is the\n"
     "\t\t\t\t * stalling topology -- the group wrapper supersedes\n"
     "\t\t\t\t * it (the scaffold is tracked for cleanup either way).\n"
     "\t\t\t\t */\n"
     "\t\t\t\tfe_enter_off = gro;\n"),
    # F. F-148 block: pin numKeys at 0 in the group word.
    ("F-148 numKeys pinned 0",
     "F-183(numkeys-pinned)",
     "\t\t\t\tnkeys++;\n"
     "\t\t\t\tiowrite32be((nkeys << 24) | (gw0 & 0x00FFFFFF), gt);\n"
     "\n"
     "\t\t\t\tpr_info(\"fman_pcd: F-148 CC key[%u] written, nkeys=%u\\n\",\n"
     "\t\t\t\t\t nkeys - 1, nkeys);\n",
     "\t\t\t\tnkeys++;\n"
     "\t\t\t\t/* F-183(numkeys-pinned): do NOT publish the numKeys bump\n"
     "\t\t\t\t * to the group word. The walker is proven insensitive\n"
     "\t\t\t\t * to match rows (frames always take the miss slot);\n"
     "\t\t\t\t * publishing numKeys=1 would move the miss slot from\n"
     "\t\t\t\t * ato[0] (FE_ENTER copy) to ato[1] (kernel enq-AD)\n"
     "\t\t\t\t * and bypass the FE-VM entirely after the first flow\n"
     "\t\t\t\t * insert. The match-row + ato[0] writes above stay\n"
     "\t\t\t\t * (inert; ato[0] gets the same FE_ENTER copy).\n"
     "\t\t\t\t */\n"
     "\n"
     "\t\t\t\tpr_info(\"fman_pcd: F-148 CC key[%u] written (numKeys pinned 0, F-183)\\n\",\n"
     "\t\t\t\t\t nkeys - 1);\n"),
    # G. dmesg honesty: this is no longer AC_CC.
    ("ENGAGED dmesg text",
     "F-183(engaged-dmesg)",
     "\tpr_info(\"fman_pcd fe_arm: port 0x%02x ENGAGED (AC_CC)\\n\", port_id);\n",
     "\t/* F-183(engaged-dmesg): this is the CCBS-graft model, not AC_CC. */\n"
     "\tpr_info(\"fman_pcd fe_arm: port 0x%02x ENGAGED (CCBS-graft group-root)\\n\", port_id);\n"),
]
edit("drivers/net/ethernet/freescale/fman/fman_pcd.c", pcd_blocks)

if changes:
    print(f"### F-183 complete ({changes} blocks)")
else:
    print("### F-183 no changes applied")
    sys.exit(1)
