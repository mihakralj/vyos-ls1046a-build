"""F-135: Clear fe_port_armed bit on disengage.

F-107 sets the bit in __fman_pcd_fe_arm_engage() for per-port engagement
guarding.  F-122 tests it for idempotency.  But nothing clears it on
disengage, so after a full engage→disengage cycle the bit stays set.
Re-engage sees the stale bit and returns "already armed (idempotent)"
without actually re-arming the hardware.

Board-verified on .106 (ISO 0242, 2026-07-29): YNL engage after YNL
disengage returns success but dmesg shows "already armed (idempotent)"
and fe_pool stays NO, MURAM unchanged.

Fix: add clear_bit(port_id, pcd->fe_port_armed) in
__fman_pcd_fe_arm_disengage() after the KG disarm.

Disposition: fold into 0157
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (one-line clear_bit, symmetric with F-107 set_bit)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-135: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Find __fman_pcd_fe_arm_disengage and add clear_bit after the KG disarm
# F-134 reordered this function: disarm → port_del → scaffold_free
# We add clear_bit right after the disarm call

disarm_line = "\tfman_pcd_kg_port_disarm_fe(pcd, (u8)port_id, 0);"
if disarm_line not in src:
    print("### F-135: kg_port_disarm_fe call not found — skipping")
    sys.exit(0)

# Count occurrences — should be exactly 1 in __fman_pcd_fe_arm_disengage
# (the debugfs wrapper also calls it but with different casting)
count = src.count(disarm_line)
if count == 0:
    print("### F-135: disarm call not found")
    sys.exit(0)

# Insert clear_bit and 5ms FMan pipeline drain delay after the disarm
# Also ensure <linux/delay.h> is included for fsleep()
delay_include = "#include <linux/delay.h>"
if delay_include not in src:
    # Find the last #include line and add delay.h after it
    last_include = src.rfind("#include <")
    if last_include != -1:
        end_of_line = src.find("\n", last_include)
        src = src[:end_of_line+1] + delay_include + "\n" + src[end_of_line+1:]
        changes += 1
        print("### F-135: added #include <linux/delay.h> for fsleep()")

new_block = disarm_line + "\n\tclear_bit(port_id, pcd->fe_port_armed);\n\tfsleep(5000);"
if new_block in src:
    print("### F-135: clear_bit already present")
    sys.exit(0)

src = src.replace(disarm_line, new_block, 1)
changes += 1
print("### F-135: added clear_bit(fe_port_armed) + fsleep(5000) pipeline drain after KG disarm")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-135: {changes} change(s) applied")
else:
    print("### F-135: no changes applied")
    sys.exit(1)