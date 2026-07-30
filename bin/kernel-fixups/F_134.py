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

Fix: move fman_pcd_kg_port_disarm_fe() BEFORE fman_pcd_fe_port_del().
The KG disarm writes FMBM_RCCB=0 and restores the KG scheme to RSS, stopping
all CC/FE-VM frame dispatch.  Only then is it safe to free the MURAM the
CC/FE-VM path was using.

NOTE: As of F-139, the scaffold is per-port and freed in fe_port_del().
The fman_pcd_fe_arm_free_scaffold() call is a no-op.  This fixup now only
reorders disarm before port_del.

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

# Find the end of the function
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

port_del_line = "\tfman_pcd_fe_port_del(pcd, (u8)port_id);"
disarm_line = "\tfman_pcd_kg_port_disarm_fe(pcd, (u8)port_id, 0);"

if port_del_line not in func_body:
    print("### F-134: port_del not found in __fman_pcd_fe_arm_disengage")
    sys.exit(1)
if disarm_line not in func_body:
    print("### F-134: disarm not found in __fman_pcd_fe_arm_disengage")
    sys.exit(1)

# Check if already reordered (disarm before port_del)
pd_pos = src.find(port_del_line, body_start)
dis_pos = src.find(disarm_line, body_start)
if dis_pos < pd_pos:
    print("### F-134: already reordered (disarm before port_del) — skipping")
    sys.exit(0)

# Current order: port_del → ... → disarm
# We need: disarm → port_del
# Extract the block from port_del through disarm, move disarm to front
old_block = src[pd_pos:dis_pos + len(disarm_line)]
# Remove disarm from the end
middle = src[pd_pos:dis_pos]
new_block = disarm_line + "\n" + middle

if old_block in src:
    src = src.replace(old_block, new_block, 1)
    changes += 1
    print("### F-134: reordered __fman_pcd_fe_arm_disengage — KG disarm BEFORE port_del")
else:
    print("### F-134: could not match the exact block — may already be reordered")
    sys.exit(0)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-134: {changes} change(s) applied")
else:
    print("### F-134: no changes applied")
    sys.exit(1)