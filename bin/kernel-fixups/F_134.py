"""F-134: Reorder __fman_pcd_fe_arm_disengage — disarm KG BEFORE freeing MURAM.

Root cause of the second-cycle disengage hang (board-observed 2026-07-29 on
.185, ISO 2026.07.28-1505): __fman_pcd_fe_arm_disengage() frees the scaffold
MURAM (gro/mto/ato = 304 B) via fman_pcd_fe_arm_free_scaffold() BEFORE
writing FMBM_RCCB=0 via fman_pcd_kg_port_disarm_fe().  The FMBM_RCCB still
points at the scaffold's gro (FE_ENTER root AD) when we free it.  If a frame
hits the BMI between the free and the register write, the BMI dereferences
freed/unmapped MURAM → bus lockup → hard hang (SSH dead, watchdog reboot).

The first cycle usually survives because the port is idle (no traffic).  The
second cycle has frames in-flight from the re-engage → hang is reproducible.

Fix: move fman_pcd_kg_port_disarm_fe() BEFORE fman_pcd_fe_port_del() and
fman_pcd_fe_arm_free_scaffold().  The KG disarm writes FMBM_RCCB=0 and
restores the KG scheme to RSS, stopping all CC/FE-VM frame dispatch.  Only
then is it safe to free the MURAM the CC/FE-VM path was using.

Disposition: fold into 0157
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (reorder-only, no new code)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-134: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Find __fman_pcd_fe_arm_disengage function
sig = "static int __fman_pcd_fe_arm_disengage(struct fman_pcd *pcd,"
if sig not in src:
    print("### F-134: __fman_pcd_fe_arm_disengage not found — skipping")
    sys.exit(0)

func_start = src.index(sig)
body_start = src.index("{", func_start)

# Find the end of the function — look for the next function or debugfs wrapper
next_markers = []
for marker in ["\n/* Debugfs wrapper", "\nstatic int fman_pcd_fe_arm_disengage",
               "\nstatic void fman_pcd_fe_arm_free_scaffold",
               "\nstatic int fman_pcd_fe_arm_show"]:
    idx = src.find(marker, body_start)
    if idx != -1:
        next_markers.append(idx)

if not next_markers:
    print("### F-134: cannot find end of __fman_pcd_fe_arm_disengage")
    sys.exit(1)

func_end = min(next_markers)
func_body = src[body_start:func_end]

# The current order (from 0157):
#   fman_pcd_fe_port_del(pcd, (u8)port_id);
#   fman_pcd_fe_arm_free_scaffold(pcd);
#   fman_pcd_kg_port_disarm_fe(pcd, (u8)port_id, 0);
#
# We need to move the disarm BEFORE the port_del and scaffold free.
# Strategy: find all three lines, extract the disarm, remove it from its
# current position, insert it before port_del.

port_del_line = "\tfman_pcd_fe_port_del(pcd, (u8)port_id);"
scaffold_line = "\tfman_pcd_fe_arm_free_scaffold(pcd);"
disarm_line = "\tfman_pcd_kg_port_disarm_fe(pcd, (u8)port_id, 0);"

if port_del_line not in func_body:
    print("### F-134: port_del not found in __fman_pcd_fe_arm_disengage")
    sys.exit(1)
if scaffold_line not in func_body:
    print("### F-134: scaffold_free not found in __fman_pcd_fe_arm_disengage")
    sys.exit(1)
if disarm_line not in func_body:
    print("### F-134: disarm not found in __fman_pcd_fe_arm_disengage")
    sys.exit(1)

# Build the new block: disarm first, then port_del, then scaffold_free
old_block = port_del_line + "\n\n" + scaffold_line + "\n\n" + disarm_line
new_block = (disarm_line + "\n\n" + port_del_line + "\n" + scaffold_line)

if old_block not in src:
    # Try with single newlines
    old_block = port_del_line + "\n" + scaffold_line + "\n" + disarm_line
    if old_block not in src:
        # Try with the exact whitespace from the file
        # Find the actual text between these lines
        pd_pos = src.find(port_del_line, body_start)
        sf_pos = src.find(scaffold_line, pd_pos)
        dis_pos = src.find(disarm_line, sf_pos)
        if pd_pos == -1 or sf_pos == -1 or dis_pos == -1:
            print("### F-134: cannot locate all three lines precisely")
            sys.exit(1)
        old_block = src[pd_pos:dis_pos + len(disarm_line)]
        new_block = disarm_line + "\n" + src[pd_pos:dis_pos]
        # Remove the trailing disarm_line from the old block (it's now at the front)
        # Actually, simpler: just build the new block from the extracted parts
        middle = src[pd_pos:dis_pos]  # port_del through scaffold_free (includes disarm_line at end)
        # middle ends with disarm_line, we want it without
        middle_no_disarm = middle[:middle.rfind(disarm_line)]
        new_block = disarm_line + "\n" + middle_no_disarm

if old_block in src:
    src = src.replace(old_block, new_block, 1)
    changes += 1
    print("### F-134: reordered __fman_pcd_fe_arm_disengage — KG disarm BEFORE MURAM free")
else:
    print("### F-134: could not match the exact three-line block")
    sys.exit(1)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-134: {changes} change(s) applied")
else:
    print("### F-134: no changes applied")
    sys.exit(1)