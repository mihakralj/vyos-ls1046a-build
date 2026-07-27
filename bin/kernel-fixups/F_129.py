"""F-129: Add FE-VM chain teardown to production fman_pcd_fe_disengage().

F-127 (2026-07-27) proved that the -12 on second-port engage comes from
fman_pcd_fe_port_set() → MURAM alloc → -ENOMEM, because the ehash int_buf
(33280 B at 0x4c100) is never freed on disengage.

Root cause: F_092 inserted the VM chain teardown into the DEBUGFS
fe_arm write handler (matching `fman_pcd_fe_arm_disengage(pcd, buf)`),
NOT into the production fman_pcd_fe_disengage() which calls
__fman_pcd_fe_arm_disengage().  The production path has ZERO teardown.

F-128 changed the guard on the debugfs-only block — it never executes
in the production YNL/genl path.  Board-verified 2026-07-27 on .185
(ISO 0645): disengage port 0x11 → ehash int_buf still refcount=1,
33280 B held, fe_pool engaged=YES.

Fix: insert the same teardown block (with list_empty guard) into
fman_pcd_fe_disengage() after __fman_pcd_fe_arm_disengage().

Disposition: fold-into F_092 (replace the debugfs-only insertion)
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (adds teardown to production path, reuses existing helpers)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-129: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── Insert VM chain teardown into fman_pcd_fe_disengage() ──
# The production function calls __fman_pcd_fe_arm_disengage(pcd, hw_port_id)
# and has NO teardown.  Insert after the disarm call, before pr_info.

disarm_line = "\t__fman_pcd_fe_arm_disengage(pcd, hw_port_id);"

if disarm_line not in src:
    print("### F-129: ERROR — __fman_pcd_fe_arm_disengage call not found in fe_disengage()")
    sys.exit(1)

teardown_block = """\t__fman_pcd_fe_arm_disengage(pcd, hw_port_id);
\t/* F-129: Tear down shared FE-VM chain on last port disengage.
\t * Gate on list_empty, not fe_vm_chain_built: the ehash/fe_pool may
\t * exist from FMan microcode pre-init (U-Boot loads ucode into MURAM)
\t * and fe_vm_chain_built is only set by F_092's build block, which is
\t * skipped when objects already exist (idempotent).  Check the ehash
\t * table directly — if it has an int_buf, the chain was built.
\t */
\tif (list_empty(&pcd->fe_ports) && !list_empty(&pcd->fe_ehash_tables)) {
\t\tfman_pcd_fe_enq_free(pcd);
\t\tfman_pcd_fe_hash_free(pcd);
\t\tfman_pcd_ehash_drain(pcd);
\t\tfman_pcd_fe_singletons_free(pcd);
\t\tfman_pcd_fe_pool_put(pcd);
\t\tpcd->fe_vm_chain_built = false;
\t}
"""

src = src.replace(disarm_line, teardown_block, 1)
changes += 1
print("### F-129: inserted VM chain teardown into fman_pcd_fe_disengage()")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-129: {changes} change(s) applied")
else:
    print("### F-129: no changes applied")
    sys.exit(1)