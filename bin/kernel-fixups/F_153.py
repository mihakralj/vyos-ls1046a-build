"""F-153: Fix 0146's MUX/TRANSITION wiring -- MUX must go through TRANSITION to ENQ.

Per arch/fman-microcode-210-programming-reference.md Section 7.5/7.6/7.1 line 60:
the proven FE-VM HIT chain is:

    EXT_HASH --HIT--> MUX --> TRANSITION --> ENQ --> TX FQ

Patch 0146's fman_pcd_fe_build_contexts() wires this WRONG:

    MUX.next_fe_off      = enq->muram_off        (skips TRANSITION entirely)
    TRANSITION.next_ad_off = pcd->fe_exit_off    (mislabeled "MISS -> Exit",
                                                   but TRANSITION is a MUX-HIT-
                                                   branch relay per Section 7.6,
                                                   not a MISS-path object --
                                                   MISS is EXT_HASH's own
                                                   missNextFE, w6, unrelated)

Section 7.3 line 384 confirms ENQ's proven role requires arriving via this
exact three-hop chain ("MUX -> TRANSITION -> ENQ -> TX FQ"), not a direct
MUX -> ENQ jump.  This fixup corrects both assignments:

    MUX.next_fe_off        = pcd->fe_transition_off
    TRANSITION.next_ad_off = enq->muram_off

Must run AFTER 0146 (which defines fman_pcd_fe_build_contexts()).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-153: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# --- Fix 1: MUX context. HIT -> TRANSITION (not directly -> ENQ) ---
old_mux = """	/* MUX context: HIT -> ENQ */
	if (pcd->fe_mux_off && enq) {
		fe = fman_muram_offset_to_vbase(muram, pcd->fe_mux_off);
		memset(&p, 0, sizeof(p));
		p.type = FMAN_FE_TYPE_MUX;
		p.u.mux.next_fe_off = enq->muram_off;
		fman_pcd_fe_context_build(fe, FMAN_FE_MUX_CTX_OFF, &p);
	}"""

new_mux = """	/* F-153: MUX context. HIT -> TRANSITION (proven chain per
	 * microcode reference Sec 7.5/7.1: MUX -> TRANSITION -> ENQ).
	 * Was: MUX -> ENQ directly, skipping TRANSITION entirely. */
	if (pcd->fe_mux_off && pcd->fe_transition_off) {
		fe = fman_muram_offset_to_vbase(muram, pcd->fe_mux_off);
		memset(&p, 0, sizeof(p));
		p.type = FMAN_FE_TYPE_MUX;
		p.u.mux.next_fe_off = pcd->fe_transition_off;
		fman_pcd_fe_context_build(fe, FMAN_FE_MUX_CTX_OFF, &p);
	}"""

if old_mux in src:
    src = src.replace(old_mux, new_mux, 1)
    changes += 1
    print("### F-153: fixed MUX context — HIT -> TRANSITION (was -> ENQ directly)")
elif "F-153" in src:
    print("### F-153: MUX fix already present")
else:
    print("### F-153: MUX context anchor not found")

# --- Fix 2: TRANSITION context. -> ENQ (not -> EXIT) ---
old_transition = """	/* Transition: MISS -> Exit (deallocate) */
	if (pcd->fe_transition_off && pcd->fe_exit_off) {
		fe = fman_muram_offset_to_vbase(muram,
					pcd->fe_transition_off);
		memset(&p, 0, sizeof(p));
		p.type = FMAN_FE_TYPE_TRANSITION;
		p.u.transition.next_ad_off = pcd->fe_exit_off;
		fman_pcd_fe_context_build(fe, FMAN_FE_TRANSITION_CTX_OFF, &p);
	}"""

new_transition = """	/* F-153: TRANSITION context. -> ENQ (proven chain per microcode
	 * reference Sec 7.6/7.1). TRANSITION is a MUX-HIT-branch relay,
	 * NOT a MISS-path object -- MISS is EXT_HASH's own missNextFE
	 * (w6), unrelated to this singleton. Was mislabeled "MISS -> Exit"
	 * and wired to fe_exit_off, which orphaned the HIT chain. */
	if (pcd->fe_transition_off && enq) {
		fe = fman_muram_offset_to_vbase(muram,
					pcd->fe_transition_off);
		memset(&p, 0, sizeof(p));
		p.type = FMAN_FE_TYPE_TRANSITION;
		p.u.transition.next_ad_off = enq->muram_off;
		fman_pcd_fe_context_build(fe, FMAN_FE_TRANSITION_CTX_OFF, &p);
	}"""

if old_transition in src:
    src = src.replace(old_transition, new_transition, 1)
    changes += 1
    print("### F-153: fixed TRANSITION context — -> ENQ (was -> EXIT)")
elif "F-153" in src and "TRANSITION context. -> ENQ" in src:
    print("### F-153: TRANSITION fix already present")
else:
    print("### F-153: TRANSITION context anchor not found")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-153: {changes} change(s) applied")
else:
    print("### F-153: no changes — may already be present")
    sys.exit(0)