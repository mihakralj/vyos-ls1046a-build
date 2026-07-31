"""F-147: Fix RCCB to point directly to FE_ENTER AD (pcd->fe_root_ad_off).

The settled architecture (2026-07-16, qdrant) requires RCCB → FE_ENTER direct
dispatch.  No CC group table, no CC node, no match table.  This was proven
working on 2026-07-04 (the only confirmed HIT in program history).

The original 0132 patch sets fe_enter_off = gro (the group table offset),
causing fman_port_set_cc_base() to write the group table offset to RCCB.
The CC engine reads the empty group table and falls through to RSS.

F-147 v1 removed the fe_enter_off = gro line, but fe_enter_off then stayed 0
(the parameter value), causing fman_pcd_kg_port_arm_fe() to fail with -EINVAL
(!fe_enter_off check).

F-147 v2: Replace fe_enter_off = gro with fe_enter_off = pcd->fe_root_ad_off
(the FE_ENTER AD offset, set by fman_pcd_fe_enter_build()).  This makes RCCB
point directly to the FE_ENTER AD, matching the settled architecture.

Must run AFTER F-091 (which modifies the scaffold block).
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

# Replace fe_enter_off = gro with fe_enter_off = pcd->fe_root_ad_off
# The line appears in the scaffold block, right before pcd->fe_scaffold_gro = gro

old_line = "\t\t\t\tfe_enter_off = gro;"
new_line = "\t\t\t\tfe_enter_off = pcd->fe_root_ad_off;\t/* F-147: RCCB → FE_ENTER direct */"

if old_line in src:
    src = src.replace(old_line, new_line, 1)
    changes += 1
    print("### F-147: fe_enter_off = gro → pcd->fe_root_ad_off (RCCB → FE_ENTER direct)")
else:
    # Check if already fixed
    if "fe_enter_off = pcd->fe_root_ad_off" in src:
        print("### F-147: already fixed (fe_enter_off = pcd->fe_root_ad_off)")
    elif "F-147" in src:
        print("### F-147: F-147 comment already present")
    else:
        print("### F-147: fe_enter_off = gro not found — may have been removed by v1")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-147: {changes} change(s) applied")
else:
    print("### F-147: no changes — may already be present")
    sys.exit(0)