"""F-174: strip DEALLOCATE from TRANSITION and EXIT FEs (T-M3-R attempt 7).

CONTEXT (2026-08-07): F-172 (real key+mask) and F-173 (write barrier) both
still show every frame -- matching or not -- converging on the same FQID,
and a live register read confirmed FMBM_RFPNE/FMBM_RCCB are both correctly
wired to our chain (rules out "chain never engaged"). qdrant surfaced an
old, apparently-lost fixup, F-062e (2026-07-14): "DEALLOCATE on EXIT FE
frees frame buffer, then hardware returns to KeyGen scheme which
dispatches deallocated frame to fqb ... QMan FD corruption. Fix: remove
DEALLOCATE from both Transition and EXIT in fman_pcd_fe_singletons_build().
After FE-VM, scheme dispatches intact frame to fqb."

Patch 0124 (the current base singletons patch) still has DEALLOCATE set on
BOTH: Transition (`p.flags = FMAN_FE_EXIT_DEALLOCATE | FMAN_FE_TRANSITION_AD_FROM_WS`)
and Exit (`p.flags = FMAN_FE_EXIT_DEALLOCATE`) -- F-062e's fix was either
never folded into 0124 or was later lost. This matters because Transition
sits on the HIT path (EXT_HASH w5 -> MUX -> Transition -> ENQ, per F-153),
not just the MISS path (EXT_HASH w6 -> Exit). If DEALLOCATE causes
fallthrough-to-default dispatch on BOTH branches, a genuine HIT would
deallocate at Transition and fall through to the same default delivery a
MISS reaches via Exit -- before ENQ's own distinguishing FQID write ever
takes effect. This would explain the entire session's "everything
converges on the same FQID regardless of match" symptom in one stroke,
independent of AD species, key content, mask, or write ordering -- all of
which this session has already tested and ruled out individually.

Fix: strip FMAN_FE_EXIT_DEALLOCATE from both flag assignments, restoring
F-062e's historical fix. Purely additive/corrective -- no other fe_* verb
or struct field touched.
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

changes = 0

old_transition = (
    "\tp.flags = FMAN_FE_EXIT_DEALLOCATE | FMAN_FE_TRANSITION_AD_FROM_WS;\n"
)
new_transition = (
    "\tp.flags = FMAN_FE_TRANSITION_AD_FROM_WS;\t/* F-174: DEALLOCATE stripped, see F-062e */\n"
)

if "F-174: DEALLOCATE stripped" in src:
    print("### F-174: already applied")
elif old_transition in src:
    src = src.replace(old_transition, new_transition, 1)
    changes += 1
    print("### fman_pcd.c: F-174 DEALLOCATE stripped from Transition")
else:
    print(
        "### F-174: FATAL: expected Transition flags line not found "
        "verbatim -- patch 0124 may not have applied, or source has "
        "drifted. Refusing to guess."
    )
    sys.exit(1)

old_exit = "\tp.flags = FMAN_FE_EXIT_DEALLOCATE;\n"
new_exit = "\tp.flags = 0;\t/* F-174: DEALLOCATE stripped, see F-062e */\n"

if old_exit in src:
    src = src.replace(old_exit, new_exit, 1)
    changes += 1
    print("### fman_pcd.c: F-174 DEALLOCATE stripped from Exit")
else:
    print(
        "### F-174: FATAL: expected Exit flags line not found verbatim "
        "-- patch 0124 may not have applied, or source has drifted. "
        "Refusing to guess."
    )
    sys.exit(1)

if changes:
    with open(path, "w") as f:
        f.write(src)
    print(f"### fman_pcd.c: F-174 {changes} change(s) applied")
else:
    print("### fman_pcd.c: F-174 no changes applied")
    sys.exit(1)
