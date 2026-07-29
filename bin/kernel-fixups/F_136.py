"""F-136: Keep FE-VM chain warm across disengage/re-engage cycles.

The F-129 teardown frees the entire FE-VM chain (ehash int_buf, FE pool,
singletons, enq, hash) on last port disengage.  This causes two problems:

1. Arena fragmentation: the ehash int_buf (33280 B) is freed and
   re-allocated on every cycle.  On re-engage, it gets placed at a
   different offset, fragmenting the arena so the second port's ~9 KB
   pool cannot be placed → -12 ENOMEM.

2. Disengage hang: freeing MURAM while BMI may still have frames in
   flight through the armed ports causes bus lockup → hard hang.
   The longer the ports are armed, the more likely a frame is in flight.

Fix: keep the FE-VM chain (ehash, pool, singletons, enq, hash) allocated
across disengage/re-engage cycles.  On disengage, only disarm the KG
and remove per-port resources (buffer pools, mgmt blocks).  The shared
chain stays warm.  On re-engage, F-092 v3 detects the existing chain
(via !list_empty(&pcd->fe_ehash_tables)) and skips re-allocation.

The chain is only freed on module unload (ask_hw_pcd_teardown).

Tradeoff: ~36 KB MURAM permanently allocated after first engage.
This is acceptable because:
- The arena is 84 KiB, leaving ~48 KiB for per-port pools
- The chain is shared across all ports — it's not per-port overhead
- Module unload still frees everything

Board-verified 2026-07-29 on .106 (ISO 0402): disengage works but
Cycle 2 re-engage fails with -12 ENOMEM due to arena fragmentation.

Disposition: fold into F-129
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (removes frees from teardown, keeps chain warm)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-136: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Find the F-129 teardown block and comment out the chain-freeing calls.
# The teardown currently does: enq_free, hash_free, ehash_drain,
# singletons_free, fe_pool_put.
# We keep the teardown structure but skip the chain-freeing calls,
# leaving only the per-port cleanup (which fe_port_del already handles).

# Anchor: the F-129 pr_info line
anchor = '\t\tpr_info("fman_pcd: F-129 tearing down FE-VM chain (last port disengaged)\\n");'
if anchor not in src:
    print("### F-136: F-129 teardown block not found — skipping")
    sys.exit(0)

# Replace the teardown calls with a comment explaining the warm-chain strategy
old_teardown = """\t\tpr_info("fman_pcd: F-129 tearing down FE-VM chain (last port disengaged)\\n");
\t\tfman_pcd_fe_enq_free(pcd);
\t\tfman_pcd_fe_hash_free(pcd);
\t\tfman_pcd_ehash_drain(pcd);
\t\tfman_pcd_fe_singletons_free(pcd);
\t\tfman_pcd_fe_pool_put(pcd);
\t\tpcd->fe_vm_chain_built = false;"""

new_teardown = """\t\tpr_info("fman_pcd: F-129 last port disengaged — keeping FE-VM chain warm\\n");
\t\t/* F-136: Keep the FE-VM chain (ehash, pool, singletons, enq, hash)
\t\t * allocated across disengage/re-engage cycles.  Freeing and
\t\t * re-allocating the ehash int_buf (33280 B) fragments the arena
\t\t * and causes -12 ENOMEM on the second port's re-engage.
\t\t * The chain stays warm; F-092 v3 detects it on re-engage and
\t\t * skips re-allocation.  Per-port resources (buffer pools, mgmt)
\t\t * are freed by fman_pcd_fe_port_del() called earlier.
\t\t *
\t\t * The chain is freed on module unload via ask_hw_pcd_teardown().
\t\t */
\t\t/* fman_pcd_fe_enq_free(pcd); */
\t\t/* fman_pcd_fe_hash_free(pcd); */
\t\t/* fman_pcd_ehash_drain(pcd); */
\t\t/* fman_pcd_fe_singletons_free(pcd); */
\t\t/* fman_pcd_fe_pool_put(pcd); */
\t\t/* pcd->fe_vm_chain_built = false; */"""

if old_teardown in src:
    src = src.replace(old_teardown, new_teardown, 1)
    changes += 1
    print("### F-136: FE-VM chain kept warm across disengage/re-engage cycles")
else:
    print("### F-136: F-129 teardown block format mismatch — skipping")
    sys.exit(0)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-136: {changes} change(s) applied")
else:
    print("### F-136: no changes applied")
    sys.exit(1)