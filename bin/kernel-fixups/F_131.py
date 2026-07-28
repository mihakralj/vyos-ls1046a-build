"""F-131: Guard fman_pcd_muram_free() against kexec-stale MURAM offsets.

After a kexec reboot, the new kernel's gen_pool has a fresh chunk at a
potentially different muram_offset.  Offsets from the previous kernel are
not valid in this pool, and calling gen_pool_free() on them hits BUG() in
lib/genalloc.c:518 (gen_pool_free_owner).

Board-verified 2026-07-28 on .185 (ISO 0422): disengaging from kexec-preserved
stale state triggered:
  kernel BUG at lib/genalloc.c:508!
  gen_pool_free_owner+0x104/0x110
  fman_pcd_muram_free+0x3c/0xa0
  fman_pcd_fe_pool_free+0x188/0x1d8
  fman_pcd_fe_pool_put+0x5c/0x88
  fman_pcd_fe_disengage+0xa4/0xb8

Fix: call gen_pool_has_addr() before gen_pool_free().  If the offset is not
in the current pool, log a warning, adjust the budget counter, and return
without calling gen_pool_free().  This catches ALL stale-offset scenarios
(kexec, warm reboot, any corruption), not just the FE pool.

Disposition: permanent guard
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK kexec+MURAM]
Risk-Tier: A (single-point defense in the MURAM free path)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-131: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Anchor: the gen_pool_free() call in fman_pcd_muram_free()
anchor = "\tgen_pool_free(pcd->muram_pool, offset, size);"
if anchor not in src:
    print("### F-131: ERROR — gen_pool_free call not found in fman_pcd_muram_free()")
    sys.exit(1)

# Count occurrences — must be exactly 1
count = src.count(anchor)
if count != 1:
    print(f"### F-131: ERROR — expected 1 gen_pool_free call, found {count}")
    sys.exit(1)

replacement = """\t/* F-131: Guard against kexec-stale MURAM offsets.
\t * After a kexec reboot, the new kernel's gen_pool has a fresh
\t * chunk at a potentially different muram_offset.  Offsets from
\t * the previous kernel are not valid in this pool, and calling
\t * gen_pool_free() on them hits BUG() in lib/genalloc.c.
\t * gen_pool_has_addr() walks the chunk list and returns false
\t * for any address outside the current pool's range.
\t */
\tif (!gen_pool_has_addr(pcd->muram_pool, offset, size)) {
\t\tpr_warn("fman_pcd: refusing to free stale MURAM off 0x%lx (size %zu) — "
\t\t\t"likely kexec reboot, offset not in current gen_pool\\n",
\t\t\toffset, size);
\t\t/* Budget accounting: still decrement muram_used so the
\t\t * debugfs budget doesn't drift.  The stale offset is
\t\t * unrecoverable — the previous kernel's MURAM is gone.
\t\t */
\t\tmutex_lock(&pcd->lock);
\t\tif (pcd->muram_used >= size)
\t\t\tpcd->muram_used -= size;
\t\tmutex_unlock(&pcd->lock);
\t\treturn;
\t}
\tgen_pool_free(pcd->muram_pool, offset, size);"""

src = src.replace(anchor, replacement, 1)
changes += 1
print("### F-131: added gen_pool_has_addr() guard to fman_pcd_muram_free()")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-131: {changes} change(s) applied")
else:
    print("### F-131: no changes applied")
    sys.exit(1)