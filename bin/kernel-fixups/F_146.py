"""F-146: Populate CC group table with FE_ENTER AD pointer.

The CC engine reads the group table at rccb to find the default action.
Our code sets rccb via fman_port_set_cc_base() but never writes the group
table entries.  The group table is empty, so the CC engine falls through
to the default RSS path and the FE-VM is never entered.

The NXP SDK's FmPcdCcBuildFE writes the FE_ENTER AD pointer into the group
table.  We need to do the same.

Fix: After fman_pcd_kg_port_arm_fe() returns in __fman_pcd_fe_arm_engage(),
write the FE_ENTER AD pointer (fe_enter_off) to the group table at
pcd->fe_scaffold_gro.  The group table entry is a 32-byte AD with the
FE_ENTER offset in the first word.

Must run AFTER F-139 (which stores scaffold offsets in pcd).
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-146: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Find the fman_pcd_kg_port_arm_fe call in __fman_pcd_fe_arm_engage
# and add group table population after it.
# The call looks like:
#   err = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,
#                                  (u32)fe_enter_off, &saved_engine, ekfc);
#   if (err)
#       return err;

arm_call = "fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,"
if arm_call in src:
    # Find the error check after the call
    err_check = "\tif (err)\n\t\treturn err;"
    # Find the block containing the arm call and error check
    # Insert group table write between the call and the error check
    
    # The full block is:
    # err = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,
    #                                (u32)fe_enter_off, &saved_engine, ekfc);
    # if (err)
    #     return err;
    
    # We need to insert after the error check (so it only runs on success)
    # Write the FE_ENTER AD pointer to the group table
    
    gt_write = """\tif (err)
\t\treturn err;

\t/* F-146: Populate CC group table with FE_ENTER AD pointer.
\t * The CC engine reads this table at rccb to find the default action.
\t * Without this, the FE-VM is never entered (CC falls through to RSS).
\t * The group table is at pcd->fe_scaffold_gro (allocated above).
\t * Write a single-entry group table pointing to the FE_ENTER AD.
\t */
\tif (pcd->fe_scaffold_gro) {
\t\tstruct muram_info *muram = fman_get_muram(pcd->fman);
\t\tvoid __iomem *gt = fman_muram_offset_to_vbase(muram,
\t\t\t\t\t\t\t    pcd->fe_scaffold_gro);
\t\tiowrite32be((u32)fe_enter_off, gt + 0);
\t\tiowrite32be(0, gt + 4);
\t\tiowrite32be(0, gt + 8);
\t\tiowrite32be(0, gt + 12);
\t\tiowrite32be(0, gt + 16);
\t\tiowrite32be(0, gt + 20);
\t\tiowrite32be(0, gt + 24);
\t\tiowrite32be(0, gt + 28);
\t}"""

    if "F-146" not in src:
        # Find the exact location: after "if (err)\n\t\treturn err;" following the arm call
        # We need to find the specific instance near the arm call
        arm_block = "\terr = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,\n\t\t\t\t    (u32)fe_enter_off, &saved_engine, ekfc);\n\tif (err)\n\t\treturn err;"
        if arm_block in src:
            src = src.replace(arm_block, gt_write, 1)
            changes += 1
            print("### F-146: populated CC group table with FE_ENTER AD pointer")
        else:
            # Try without ekfc parameter (older version)
            arm_block2 = "\terr = fman_pcd_kg_port_arm_fe(pcd, (u8)port_id,\n\t\t\t\t    (u32)fe_enter_off, &saved_engine);\n\tif (err)\n\t\treturn err;"
            if arm_block2 in src:
                src = src.replace(arm_block2, gt_write.replace(", ekfc", ""), 1)
                changes += 1
                print("### F-146: populated CC group table (no ekfc variant)")
            else:
                print("### F-146: arm call block not found")
    else:
        print("### F-146: group table population already present")
else:
    print("### F-146: arm_fe call not found")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-146: {changes} change(s) applied")
else:
    print("### F-146: no changes — may already be present")
    sys.exit(0)