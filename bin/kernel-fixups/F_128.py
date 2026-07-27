"""F-128: F-125(c) — free ehash on LAST port disengage (guard on debugfs teardown).

F_092 v2 (2026-07-27) moved the VM chain build into the production
fman_pcd_fe_engage(), so fe_vm_chain_built is now set in the production
path. F_129 handles the production teardown with the list_empty guard.

This fixup changes the guard on the DEBUGFS teardown block (inserted by
older F_092 versions). With F_092 v2 no longer inserting debugfs teardown,
this is a no-op on current builds but kept for idempotency.

Disposition: delete after F_092 v2 is confirmed in CI
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-128: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

old_guard = "\tif (pcd->fe_vm_chain_built) {"
new_guard = "\tif (pcd->fe_vm_chain_built && list_empty(&pcd->fe_ports)) {"

if old_guard in src:
    src = src.replace(old_guard, new_guard, 1)
    changes += 1
    print("### F-128: changed teardown guard to list_empty(&pcd->fe_ports)")
else:
    print("### F-128: guard not found (F_092 v2 may have removed debugfs teardown) — skipping")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-128: {changes} change(s) applied")
else:
    print("### F-128: no changes applied (non-fatal with F_092 v2)")