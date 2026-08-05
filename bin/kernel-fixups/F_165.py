"""F-165: fe_arm engage with an explicit non-zero offset must not be
silently overwritten by the CONT_LOOKUP scaffold (Task #26 follow-up).

CONTEXT (2026-08-05 board session on .185): F-091 made
__fman_pcd_fe_arm_engage() unconditionally build a CONT_LOOKUP scaffold
group table (gro/mto/ato) on every call, then does:

    fe_enter_off = gro;

-- unconditionally overwriting whatever fe_enter_off the caller passed
in with the scaffold's own freshly-allocated offset. This is dead code
for production (fman_pcd_fe_engage(), the function ask_hw_offload_engage()
calls, always passes fe_enter_off=0, so `gro` is exactly what it wants
there) -- but it also silently defeats the debugfs `fe_arm engage <port>
<off_hex> ...` verb's own documented ability to point FMBM_RCCB at an
arbitrary caller-built target.

Board-confirmed via ask-pcd-regdump.py (direct /dev/mem register read,
not just dmesg): built a real FE_ENTER root AD at MURAM offset 0x57000
(byte-verified against arch/fman-microcode-210-programming-reference.md
§7.7's documented encoding), armed via
`fe_arm engage 11 57000 292 801c0006`, then read fmbm_rccb live at
0x057100 -- 256 bytes past the intended target, exactly `gro`'s
freshly-allocated offset. The CC engine was walking an empty/
uninitialized scaffold match table, not the carefully-built ehash
chain -- fully explaining the earlier "byte-correct chain, still MISS"
result (arch/fman-microcode-210-programming-reference.md §10.5a): the
ehash lookup was never reached at all.

THE FIX: only apply the fe_enter_off = gro overwrite when the caller's
own fe_enter_off was 0 to begin with (i.e. pass-through/production
mode). When the caller explicitly passed a non-zero target (the
debugfs FE_ENTER-direct test path), leave it alone. The scaffold
(gro/mto/ato) still gets allocated and tracked for cleanup either way
-- this only changes what FMBM_RCCB ends up pointing at, not the
allocation/teardown bookkeeping, so no new leak is introduced.

SCOPE: this is deliberately narrower than the earlier (uncommitted,
discarded) F-164 attempt, which tried to remove the scaffold outright
and make FE_ENTER-direct the production default -- that broke every
port engage (fe_root_ad_off is never built in the production call
path) and never actually changed the hardcoded EKFC either. F-165
touches nothing on the fe_enter_off==0 path: fman_pcd_fe_engage()'s
production behavior (CONT_LOOKUP pass-through scaffold, the current
shipping M2/M5 mechanism) is completely unaffected. Only the debugfs
`fe_arm engage <port> <nonzero-off> ...` path changes.

Disposition: debugfs-test-only. Not claiming this fixes the MISS by
itself -- it fixes the test methodology so the ehash chain (and F-163's
key format) can actually be exercised by traffic for the first time.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-165: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

old = "\t\t\t\tfe_enter_off = gro;"
new = (
    "\t\t\t\tif (!fe_enter_off)\n"
    "\t\t\t\t\t/* F-165: only the pass-through (production) path\n"
    "\t\t\t\t\t * repoints fe_enter_off at the scaffold. An\n"
    "\t\t\t\t\t * explicit caller-supplied non-zero target (the\n"
    "\t\t\t\t\t * debugfs FE_ENTER-direct test path) must survive\n"
    "\t\t\t\t\t * unmodified -- the scaffold is still allocated\n"
    "\t\t\t\t\t * and tracked for cleanup either way, it's just\n"
    "\t\t\t\t\t * not what FMBM_RCCB ends up pointing at.\n"
    "\t\t\t\t\t */\n"
    "\t\t\t\t\tfe_enter_off = gro;"
)

if new in src:
    print("### F-165: already applied")
elif old in src:
    src = src.replace(old, new, 1)
    changes += 1
    print("### F-165: fe_enter_off = gro guarded on caller's original value being 0")
else:
    print(
        "### F-165: FATAL: expected 'fe_enter_off = gro;' line not found "
        "verbatim in __fman_pcd_fe_arm_engage() -- source has likely "
        "drifted (e.g. F-139's per-port scaffold-field rename touched "
        "nearby lines) since this fixup was written. Refusing to guess; "
        "fix the anchor text in F_165.py against the current fman_pcd.c "
        "before retrying."
    )
    sys.exit(1)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-165: {changes} change(s) applied")
else:
    print("### F-165: no changes applied")
    sys.exit(1)
