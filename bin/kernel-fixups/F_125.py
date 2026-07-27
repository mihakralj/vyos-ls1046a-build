"""F-125: make FE-VM engage transactional (stop orphaning the FE_ENTER scaffold).

Measured on .185 and .106 (ISO 2026.07.27-0255), byte-identically: every failed
engage leaked exactly 304 bytes of the 64 KiB PCD MURAM arena, monotonically
(51514 -> 51818 -> 52122 across successive attempts), reclaimable only by
reboot. A first failure additionally leaked ~7957 bytes.

Mechanism. __fman_pcd_fe_arm_engage()'s fe_enter_off == 0 branch does:

    gro = fman_pcd_muram_alloc(pcd, 256);
    mto = fman_pcd_muram_alloc(pcd, 16);
    ato = fman_pcd_muram_alloc(pcd, 32);      /* 256 + 16 + 32 = 304 */
    ...
    pcd->fe_scaffold_gro = gro; ... mto; ... ato;

    err = fman_pcd_kg_port_arm_fe(...);
    if (err)
            return err;                       /* <-- scaffold left allocated */

Two holes, both here:

  1. A partial allocation failure strands whichever of the three succeeded —
     IS_ERR_VALUE only guards the *use* of the offsets, never frees them.
  2. When fman_pcd_kg_port_arm_fe() fails, the function returns with
     pcd->fe_scaffold_* still populated. The NEXT engage attempt takes the
     fe_enter_off == 0 path again and OVERWRITES those fields, orphaning the
     previous triple permanently. That overwrite — not a missing free on the
     disengage path — is the 304 bytes/attempt.

__fman_pcd_fe_arm_disengage() already calls fman_pcd_fe_arm_free_scaffold(),
so a *successful* engage followed by disengage is clean. Only the failure path
leaks. This fixup therefore reuses the existing helper rather than adding a
second one, and closes exactly the two holes above.

The stranded triples are also the fragmentation source: each orphan sits mid
arena, so the surviving free space stops being contiguous even while the byte
total still looks healthy (43253 used / 22283 free failed a fresh engage that
the byte-identical state at cold boot satisfied).

Deliberately NOT touched: the ehash table and its 33280-byte int_buf, which are
still held with zero ports engaged. Separate allocation site, separate change,
independently revertible. See F-125 in plans/ASK2-MASTER-PLAN.md.

Disposition: permanent — allocation-lifecycle correctness.
"""

import sys, os, re

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

changes = 0

if not os.path.exists(pcd_c):
    print("### F-125: fman_pcd.c not found — skipping")
    sys.exit(0)

src = open(pcd_c).read()

if "fman_pcd_fe_arm_free_scaffold" not in src:
    print("### F-125: WARNING — fman_pcd_fe_arm_free_scaffold() absent; refusing to patch")
    sys.exit(0)

# ── 1. Unwind a partial scaffold allocation ───────────────────────────
old_alloc = """\t\t\tgro = fman_pcd_muram_alloc(pcd, 256);
\t\t\tmto = fman_pcd_muram_alloc(pcd, 16);
\t\t\tato = fman_pcd_muram_alloc(pcd, 32);
\t\t\tif (!IS_ERR_VALUE(gro) && !IS_ERR_VALUE(mto) &&
\t\t\t    !IS_ERR_VALUE(ato)) {"""

new_alloc = """\t\t\tgro = fman_pcd_muram_alloc(pcd, 256);
\t\t\tmto = fman_pcd_muram_alloc(pcd, 16);
\t\t\tato = fman_pcd_muram_alloc(pcd, 32);
\t\t\tif (IS_ERR_VALUE(gro) || IS_ERR_VALUE(mto) ||
\t\t\t    IS_ERR_VALUE(ato)) {
\t\t\t\t/*
\t\t\t\t * F-125: unwind the partial allocation. The old
\t\t\t\t * code only guarded the *use* of these offsets, so
\t\t\t\t * whichever of the three succeeded was stranded for
\t\t\t\t * the lifetime of the module.
\t\t\t\t */
\t\t\t\tif (!IS_ERR_VALUE(gro))
\t\t\t\t\tfman_pcd_muram_free(pcd, gro, 256);
\t\t\t\tif (!IS_ERR_VALUE(mto))
\t\t\t\t\tfman_pcd_muram_free(pcd, mto, 16);
\t\t\t\tif (!IS_ERR_VALUE(ato))
\t\t\t\t\tfman_pcd_muram_free(pcd, ato, 32);
\t\t\t\tpr_warn("fman_pcd fe_arm: FE_ENTER scaffold alloc failed (needs 304 B of PCD MURAM)\\n");
\t\t\t\treturn -ENOMEM;
\t\t\t}
\t\t\tif (1) {"""

if "F-125: unwind the partial allocation" in src:
    print("### F-125: partial-alloc unwind already applied")
elif old_alloc in src:
    src = src.replace(old_alloc, new_alloc, 1)
    changes += 1
    print("### F-125: partial scaffold allocation now unwinds")
else:
    print("### F-125: WARNING — scaffold alloc block not found (layout drift?)")

# ── 2. Release the scaffold when the KG arm fails ─────────────────────
#
# Anchored by REGEX, not an exact multi-line literal. 42 fixups mutate this
# same file and several rewrite this exact region (F_091 wraps the scaffold
# block; F_097 injects a verify gate immediately before this call), so a
# literal anchor silently no-ops in CI while still matching a local tree —
# which is exactly what happened on run 30237744833: part 1 applied, part 2
# reported "kg_port_arm_fe block not found", and the ISO shipped without the
# actual leak fix.
kg_re = re.compile(
    r'(err\s*=\s*fman_pcd_kg_port_arm_fe\(pcd,[^;]*?;\s*\n)'   # the call
    r'([ \t]*)if \(err\)\s*\n'                                  # if (err)
    r'[ \t]*return err;',                                          # return err;
    re.S)

if "F-125: release the scaffold we just built" in src:
    print("### F-125: KG-arm unwind already applied")
else:
    m = kg_re.search(src)
    if not m:
        print("### F-125: ERROR — kg_port_arm_fe + 'if (err) return err;' not found")
        print("### F-125: refusing to continue; the leak fix would silently no-op")
        sys.exit(1)
    ind = m.group(2)
    repl = (m.group(1)
            + ind + "if (err) {\n"
            + ind + "\t/*\n"
            + ind + "\t * F-125: release the scaffold we just built. Returning with\n"
            + ind + "\t * pcd->fe_scaffold_* still populated meant the next engage\n"
            + ind + "\t * re-entered the fe_enter_off == 0 path and OVERWROTE those\n"
            + ind + "\t * fields, orphaning this triple permanently — 304 bytes per\n"
            + ind + "\t * failed attempt, monotonic, reclaimable only by reboot.\n"
            + ind + "\t *\n"
            + ind + "\t * Guarded on fe_armed_port so a failure while another port is\n"
            + ind + "\t * already armed does not pull the scaffold out from under it.\n"
            + ind + "\t */\n"
            + ind + "\tif (!pcd->fe_armed_port)\n"
            + ind + "\t\tfman_pcd_fe_arm_free_scaffold(pcd);\n"
            + ind + "\treturn err;\n"
            + ind + "}")
    src = src[:m.start()] + repl + src[m.end():]
    changes += 1
    print("### F-125: KG-arm failure now releases the scaffold")

if changes:
    open(pcd_c, "w").write(src)
print("### F-125: %d change(s) applied" % changes)
