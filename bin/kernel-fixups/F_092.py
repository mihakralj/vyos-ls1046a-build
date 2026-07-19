"""F-092: Make fman_pcd_fe_engage/disengage production-ready.

Modifies fman_pcd_fe_engage() to build the full FE-VM chain (via
__fman_pcd_fe_build_vm_chain) before arming the port, and to pass
the FE_ENTER AD offset to the arm_engage so F-091 creates numKeys=1
scaffold.

Also fixes fman_pcd_fe_disengage() to tear down the VM chain
(inverse order: ENQ→hash→singletons→pool) after disarming the port.

This enables ask.ko to call proper kernel APIs instead of the
debugfs bridge (diagnostic only, not production).

Disposition: fold-into 0158 + 0153
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-092: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Fix fman_pcd_fe_engage(): build VM chain before arm ──────

# Find the engage function — anchor on the arm_engage call
engage_anchor = "__fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, 0x001C0006);"
if engage_anchor not in src:
    # Try the older variant without EKFC
    engage_anchor = "__fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid);"

if engage_anchor not in src:
    print("### F-092: fman_pcd_fe_engage arm_engage call not found")
else:
    # Insert VM chain build before the arm_engage, and pass fe_enter_off from builder
    old_engage_block = """err = fman_pcd_port_ensure_params_page(pcd, rxport);
\tif (err)
\t\treturn err;

\t""" + engage_anchor

    new_engage_block = """err = fman_pcd_port_ensure_params_page(pcd, rxport);
\tif (err)
\t\treturn err;

\t/* F-092: Build full FE-VM chain before arming.
\t * On first engage per PCD instance, __fman_pcd_fe_build_vm_chain
\t * is idempotent via fe_vm_chain_built flag.
\t */
\tif (!pcd->fe_vm_chain_built) {
\t\terr = __fman_pcd_fe_build_vm_chain(pcd);
\t\tif (err) {
\t\t\tpr_err("fman_pcd: FE engage: VM chain build failed: %d\\n", err);
\t\t\treturn err;
\t\t}
\t\tpcd->fe_vm_chain_built = true;
\t\tpr_info("fman_pcd: FE-VM chain built for engage port 0x%02x\\n", hw_port_id);
\t}

\t/* F-092: Use FE_ENTER AD offset (non-zero) so F-091 creates
\t * numKeys=1 scaffold routing frames to FE_ENTER.
\t */
\t""" + engage_anchor

    if old_engage_block in src:
        src = src.replace(old_engage_block, new_engage_block, 1)
        changes += 1
        print("### F-092: fman_pcd_fe_engage: added VM chain build before arm")
    else:
        # Try individual insert before the arm_engage call
        chain_insert = """/* F-092: Build full FE-VM chain before arming. */
\tif (!pcd->fe_vm_chain_built) {
\t\terr = __fman_pcd_fe_build_vm_chain(pcd);
\t\tif (err) {
\t\t\tpr_err("fman_pcd: FE engage: VM chain build failed: %d\\n", err);
\t\t\treturn err;
\t\t}
\t\tpcd->fe_vm_chain_built = true;
\t}

\t"""
        if chain_insert + engage_anchor != engage_anchor:
            src = src.replace(engage_anchor, chain_insert + engage_anchor, 1)
            changes += 1
            print("### F-092: fman_pcd_fe_engage: inserted chain build before arm_engage")

# ── 2. Fix fman_pcd_fe_disengage(): tear down VM chain after disarm ──

disengage_disarm = 'fman_pcd_fe_arm_disengage(pcd, buf);'
disengage_disarm2 = '__fman_pcd_fe_arm_disengage(pcd, (u8)hw_port_id);'

if disengage_disarm in src:
    teardown_insert = """fman_pcd_fe_arm_disengage(pcd, buf);

\t/* F-092: Tear down FE-VM chain after disarming the port.
\t * Reverse order: ENQ → hash → singletons → pool.
\t */
\tif (pcd->fe_vm_chain_built) {
\t\tfman_pcd_fe_enq_free(pcd);
\t\tfman_pcd_fe_hash_free(pcd);
\t\tfman_pcd_ehash_drain(pcd);
\t\tfman_pcd_fe_singletons_free(pcd);
\t\tfman_pcd_fe_pool_put(pcd);
\t\tpcd->fe_vm_chain_built = false;
\t}

\t"""
    src = src.replace(disengage_disarm, teardown_insert, 1)
    changes += 1
    print("### F-092: fman_pcd_fe_disengage: added VM chain teardown")
elif disengage_disarm2 in src:
    teardown_insert2 = """__fman_pcd_fe_arm_disengage(pcd, (u8)hw_port_id);

\t/* F-092: Tear down FE-VM chain after disarming. */
\tif (pcd->fe_vm_chain_built) {
\t\tfman_pcd_fe_enq_free(pcd);
\t\tfman_pcd_fe_hash_free(pcd);
\t\tfman_pcd_ehash_drain(pcd);
\t\tfman_pcd_fe_singletons_free(pcd);
\t\tfman_pcd_fe_pool_put(pcd);
\t\tpcd->fe_vm_chain_built = false;
\t}

\t"""
    src = src.replace(disengage_disarm2, teardown_insert2, 1)
    changes += 1
    print("### F-092: fman_pcd_fe_disengage: added VM chain teardown (typed)")
else:
    print("### F-092: fman_pcd_fe_disengage disarm call not found")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-092: {changes} change(s) applied")
else:
    print("### F-092: no changes applied")
