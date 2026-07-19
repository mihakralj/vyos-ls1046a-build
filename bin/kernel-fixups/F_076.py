"""F-076: Atomic fe_disengage_full debugfs write-only node.

Replaces the manual 7-step teardown (disengage→clear enter→clear enq→
clear hashfe→clear ehash→clear singletons→put pool) with a single atomic
write. The manual sequence crashes the board (F-076, 2026-07-18) because:
- R11: per-object clear verbs after fe_arm disengage double-free into gen_pool
- R10: ordering violation (params page must be zeroed while it still exists)

This node calls __fman_pcd_fe_arm_disengage() (the same SDK-correct 3-step
teardown fe_arm disengage uses) followed by fman_pcd_port_recover() to
de-wedge the port. No extra clear verbs. One write, atomic, safe.

Usage: echo 0x11 > /sys/kernel/debug/fman_pcd/0/fe_disengage_full

Disposition: permanent-with-justification (until full tree-canonical rewrite)
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (new debugfs only, no hot-path changes)
"""

import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
try:
    with open(path) as f:
        src = f.read()
except FileNotFoundError:
    print("### fman_pcd.c: F-076 file not found (may not exist on this kernel)")
    sys.exit(0)

changes = 0

# === 1. Forward-declare fman_pcd_fe_disengage_full_fops before fman_pcd_init ===
fwd_anchor = 'static const struct file_operations fman_pcd_fe_recover_fops;'
fwd_line = 'static const struct file_operations fman_pcd_fe_disengage_full_fops;'

if fwd_line not in src and fwd_anchor in src:
    src = src.replace(fwd_anchor, fwd_line + '\n' + fwd_anchor, 1)
    print("### fman_pcd.c: F-076 forward declaration of fe_disengage_full_fops")
    changes += 1

# === 2. Inject the write handler function ===
# Place it right before fman_pcd_fe_recover_write (or after if not found)
handler = r"""
/* F-076: atomic fe_disengage_full — SDK-correct ordered teardown + port de-wedge.
 * Replaces the manual 7-step sequence that crashes the board (double-free in gen_pool).
 * Order per FmPortDeleteFESupport:
 *   1. fe_port_del: zero +0x54, free mgmt index, free pool MURAM
 *   2. fe_arm_free_scaffold: free group/AD/AD tables
 *   3. kg_port_disarm_fe: clear RCCB, restore RSS scheme
 *   4. port_recover: de-wedge the port (zero workspace + ring, re-init BMI)
 */
static ssize_t fman_pcd_fe_disengage_full_write(struct file *filp,
    const char __user *buf, size_t count, loff_t *offp)
{
    struct fman_pcd *pcd = filp->private_data;
    char kbuf[16];
    unsigned int port_id;
    int ret;

    if (count >= sizeof(kbuf))
        count = sizeof(kbuf) - 1;
    if (copy_from_user(kbuf, buf, count))
        return -EFAULT;
    kbuf[count] = '\0';

    if (sscanf(kbuf, "%x", &port_id) != 1)
        return -EINVAL;
    if (port_id < 0x08 || port_id >= 0x28)
        return -EINVAL;

    pr_info("fman_pcd fe_disengage_full: port 0x%02x START\n", port_id);

    /* Step 1-3: SDK-correct ordered teardown (same as fe_arm disengage) */
    __fman_pcd_fe_arm_disengage(pcd, (u8)port_id);

    /* Step 4: de-wedge the port (zero workspace, re-init ring, restart BMI) */
    ret = fman_pcd_port_recover(pcd, (u8)port_id);
    if (ret)
        pr_warn("fman_pcd fe_disengage_full: port 0x%02x recover returned %d\n",
            port_id, ret);

    pr_info("fman_pcd fe_disengage_full: port 0x%02x DONE\n", port_id);
    return count;
}

static const struct file_operations fman_pcd_fe_disengage_full_fops = {
    .owner = THIS_MODULE,
    .write = fman_pcd_fe_disengage_full_write,
};
"""

# Find insertion point: right before fman_pcd_fe_recover_write
recover_anchor = "static ssize_t fman_pcd_fe_recover_write"
if recover_anchor not in src:
    print("### fman_pcd.c: F-076 WARNING: recover_write anchor not found")
    sys.exit(1)

if 'fman_pcd_fe_disengage_full_write' not in src:
    src = src.replace(recover_anchor, handler.strip() + '\n\n' + recover_anchor, 1)
    print("### fman_pcd.c: F-076 fe_disengage_full_write handler injected")
    changes += 1

# === 3. Register debugfs node alongside fe_arm and fe_recover ===
arm_anchor = 'debugfs_create_file("fe_arm", 0600,'
new_line = '\t\tdebugfs_create_file("fe_disengage_full", 0200, pcd->debugfs_dir, pcd, &fman_pcd_fe_disengage_full_fops);'

if '"fe_disengage_full"' not in src and arm_anchor in src:
    src = src.replace(arm_anchor, new_line + '\n' + arm_anchor, 1)
    print("### fman_pcd.c: F-076 fe_disengage_full debugfs registered")
    changes += 1

if changes:
    with open(path, 'w') as f:
        f.write(src)
    print(f"### fman_pcd.c: F-076 total changes: {changes}")
else:
    print("### fman_pcd.c: F-076 already applied, no changes")
