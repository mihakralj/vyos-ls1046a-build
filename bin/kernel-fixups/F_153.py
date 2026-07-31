"""F-153 v2: Fix MUX/TRANSITION wiring in F-054's direct-iowrite output.

F-054 replaces patch 0146's fman_pcd_fe_build_contexts() calls with direct
iowrite32be writes.  F-054's output wires:

  MUX word0 = FMAN_FE_TYPE_MUX | enq->muram_off   (MUX -> ENQ directly)
  TRANSITION word1 = pcd->fe_exit_off              (TRANSITION -> EXIT)

Per arch/fman-microcode-210-programming-reference.md Sec 7.5/7.6/7.1 (line 60)
and Sec 7.3 (line 384), the proven FE-VM HIT chain is:

  EXT_HASH --HIT--> MUX --> TRANSITION --> ENQ --> TX FQ

F-054's wiring skips TRANSITION entirely (MUX -> ENQ direct) and misroutes
TRANSITION to EXIT (a MISS-path object).  This fixup corrects both:

  MUX word0 = FMAN_FE_TYPE_MUX | pcd->fe_transition_off  (MUX -> TRANSITION)
  TRANSITION word1 = enq->muram_off                       (TRANSITION -> ENQ)

v2: Targets F-054's output (direct iowrite32be), not the original 0146
    context-builder code which F-054 already removed.  This is why v1's
    anchors failed ("MUX context anchor not found" in CI log).

Must run AFTER F-054 (which replaces the context builder with direct writes).
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

# --- Fix 1: MUX word0. F-054 writes: iowrite32be(FMAN_FE_TYPE_MUX | (u32)enq->muram_off, fe);
# Change enq->muram_off to pcd->fe_transition_off ---
old_mux = "iowrite32be(FMAN_FE_TYPE_MUX | (u32)enq->muram_off, fe);"
new_mux = "iowrite32be(FMAN_FE_TYPE_MUX | (u32)pcd->fe_transition_off, fe);\t/* F-153: MUX -> TRANSITION (was -> ENQ) */"

if old_mux in src:
    src = src.replace(old_mux, new_mux, 1)
    changes += 1
    print("### F-153 v2: fixed MUX word0 — -> TRANSITION (was -> ENQ)")
else:
    # Try with F-054+F-055 comment variant
    old_mux2 = "iowrite32be(FMAN_FE_TYPE_MUX | (u32)enq->muram_off, fe); /* F-054+F-055: SDK-correct MUX context at AD+4 */"
    if old_mux2 in src:
        src = src.replace(old_mux2, new_mux, 1)
        changes += 1
        print("### F-153 v2: fixed MUX word0 (F-054+F-055 comment variant)")
    else:
        print("### F-153 v2: FATAL: MUX iowrite32be not found — check F-054 output")
        sys.exit(1)

# --- Fix 2: TRANSITION word1. F-054 writes: iowrite32be((u32)pcd->fe_exit_off, (u32 __iomem *)fe + 1);
# Change pcd->fe_exit_off to enq->muram_off ---
old_trans = "iowrite32be((u32)pcd->fe_exit_off, (u32 __iomem *)fe + 1);"
new_trans = "iowrite32be((u32)enq->muram_off, (u32 __iomem *)fe + 1);\t/* F-153: TRANSITION -> ENQ (was -> EXIT) */"

if old_trans in src:
    src = src.replace(old_trans, new_trans, 1)
    changes += 1
    print("### F-153 v2: fixed TRANSITION word1 — -> ENQ (was -> EXIT)")
else:
    # Try with F-054 comment variant
    old_trans2 = "iowrite32be((u32)pcd->fe_exit_off, (u32 __iomem *)fe + 1); /* F-054: direct AD word 1 write */"
    if old_trans2 in src:
        src = src.replace(old_trans2, new_trans, 1)
        changes += 1
        print("### F-153 v2: fixed TRANSITION word1 (F-054 comment variant)")
    else:
        print("### F-153 v2: FATAL: TRANSITION iowrite32be not found — check F-054 output")
        sys.exit(1)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-153 v2: {changes} change(s) applied")
else:
    print("### F-153 v2: no changes — may already be present")
    sys.exit(0)