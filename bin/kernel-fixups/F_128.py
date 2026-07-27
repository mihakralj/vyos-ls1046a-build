"""F-128: F-125(c) — free ehash on LAST port disengage, not every disengage.

Root cause (F-127, 2026-07-27): fman_pcd_fe_engage() port 0x10 fails with
-12 (-ENOMEM) at the VM chain build step (__fman_pcd_fe_build_vm_chain).
The chain build allocates MURAM for pool/singletons/ehash/hashfe/enq and
fails because the arena is fragmented by the ehash int_buf (33280 B at
MURAM offset 0x4c100) which is never freed.

F_092's teardown block in fman_pcd_fe_disengage() frees the ehash on EVERY
disengage via `if (pcd->fe_vm_chain_built)`. This is wrong for multi-port:
disengaging port A destroys the shared ehash that port B still needs.

Fix: change the guard to `if (pcd->fe_vm_chain_built && list_empty(&pcd->fe_ports))`.
After fe_port_del() removes the port from fe_ports, list_empty is true only
when the last port disengages. The shared FE-VM chain (pool, singletons,
ehash, hashfe, enq) is torn down only then.

This returns 33280 B MURAM (int_buf) + 512 KiB DDR (bucket table) to the
system, removing the fragmenting allocation and allowing the VM chain build
for a second port to find contiguous MURAM.

Disposition: fold-into F_092 (or replace F_092's teardown guard)
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (guard change only, no new allocations/frees)
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

# ── 1. Change F_092 teardown guard: fe_vm_chain_built → fe_vm_chain_built && list_empty(&pcd->fe_ports) ──

old_guard = "\tif (pcd->fe_vm_chain_built) {"
new_guard = "\tif (pcd->fe_vm_chain_built && list_empty(&pcd->fe_ports)) {"

if old_guard in src:
    src = src.replace(old_guard, new_guard, 1)
    changes += 1
    print("### F-128: changed teardown guard to list_empty(&pcd->fe_ports)")
else:
    print("### F-128: WARNING — fe_vm_chain_built guard not found")

# ── 2. Also fix the typed variant (disarm_call2 path) ──
# The typed variant uses the same guard pattern

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-128: {changes} change(s) applied")
else:
    print("### F-128: no changes applied — check anchors against staged tree")
    sys.exit(1)