"""F-147: Fix RCCB to point directly to FE_ENTER AD, not group table.

The settled architecture (2026-07-16, qdrant) requires RCCB → FE_ENTER direct
dispatch.  No CC group table, no CC node, no match table.  This was proven
working on 2026-07-04 (the only confirmed HIT in program history).

F-091 introduced a bug: it sets fe_enter_off = gro (the group table offset)
at the end of the scaffold block, overriding the correct fe_enter_off = ato+32
(the FE_ENTER AD offset).  This causes fman_port_set_cc_base() to write the
group table offset to RCCB instead of the FE_ENTER AD offset.

The CC engine reads the group table at RCCB, finds it empty (no entries),
and falls through to the default RSS path.  The FE-VM is never entered.

Fix: Remove the fe_enter_off = gro line.  fe_enter_off is already correctly
set to ato + 32 (the FE_ENTER AD offset) earlier in the scaffold block.

This aligns with the settled architecture: RCCB → FE_ENTER direct.

Must run AFTER F-091 (which introduced the bug).
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

# Find and remove the fe_enter_off = gro line that F-091 added
# The line appears as: fe_enter_off = gro;
# It's right before pcd->fe_scaffold_gro = gro;

old_line = "\t\t\t\tfe_enter_off = gro;\n\n\t\t\t\tpcd->fe_scaffold_gro = gro;"
new_line = "\t\t\t\tpcd->fe_scaffold_gro = gro;"

if old_line in src:
    src = src.replace(old_line, new_line, 1)
    changes += 1
    print("### F-147: removed fe_enter_off = gro (RCCB now points to FE_ENTER AD)")
else:
    # Try without the blank line
    old_line2 = "\t\t\t\tfe_enter_off = gro;\n\t\t\t\tpcd->fe_scaffold_gro = gro;"
    if old_line2 in src:
        src = src.replace(old_line2, "\t\t\t\tpcd->fe_scaffold_gro = gro;", 1)
        changes += 1
        print("### F-147: removed fe_enter_off = gro (compact variant)")
    else:
        # Try just the line itself
        old_line3 = "\t\t\t\tfe_enter_off = gro;"
        if old_line3 in src:
            src = src.replace(old_line3, "\t\t\t\t/* F-147: fe_enter_off stays as ato+32 (FE_ENTER AD) — RCCB direct */", 1)
            changes += 1
            print("### F-147: replaced fe_enter_off = gro with comment")
        else:
            print("### F-147: fe_enter_off = gro not found — may already be fixed")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-147: {changes} change(s) applied")
else:
    print("### F-147: no changes — may already be present")
    sys.exit(0)