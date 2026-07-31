"""F-147 v3: Restore fe_enter_off = gro (revert v1/v2).

The settled architecture (2026-07-16, qdrant) uses a HYBRID topology:
- RCCB → CONT_LOOKUP group table (at gro)
- numKeys=0 → miss-AD → kernel FQ (MISS delivery)
- numKeys>0 → FE_ENTER → FE-VM ehash (HIT path)

F-147 v1 removed fe_enter_off = gro, causing fe_enter_off to stay 0
(engage failed with -EINVAL).  v2 set fe_enter_off = pcd->fe_root_ad_off
(RCCB→FE_ENTER direct), which worked for HIT but broke MISS delivery —
ALL packets were consumed by the FE-VM with no kernel fallback.

v3 restores the original fe_enter_off = gro.  The group table at gro
handles MISS→kernel correctly (numKeys=0 pass-through).  The HIT path
(numKeys>0 → FE_ENTER) should now work because F-144 (word1 byte order)
and F-145 (contextSize=255) fixed the EXT_HASH FE descriptor.

This is a pure revert: the original 0132 code was architecturally correct
for the hybrid CONT_LOOKUP + FE-VM topology.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-147: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Check current state
if "fe_enter_off = pcd->fe_root_ad_off" in src:
    # v2 is active — revert to gro
    old = "\t\t\t\tfe_enter_off = pcd->fe_root_ad_off;\t/* F-147: RCCB → FE_ENTER direct */"
    new = "\t\t\t\tfe_enter_off = gro;\t/* F-147 v3: RCCB → group table (hybrid CONT_LOOKUP + FE-VM) */"
    if old in src:
        src = src.replace(old, new, 1)
        changes += 1
        print("### F-147 v3: restored fe_enter_off = gro (hybrid CONT_LOOKUP + FE-VM)")
    else:
        print("### F-147: v2 line not found with exact match")
elif "F-147" in src:
    print("### F-147: already at v3 (fe_enter_off = gro)")
elif "fe_enter_off = gro" in src:
    print("### F-147: fe_enter_off = gro already present (original code)")
else:
    print("### F-147: fe_enter_off assignment not found")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-147: {changes} change(s) applied")
else:
    print("### F-147: no changes needed")
    sys.exit(0)