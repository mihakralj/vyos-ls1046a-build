"""F-092: Make fman_pcd_fe_engage/disengage production-ready.

Inserts VM chain build before arm_engage in fman_pcd_fe_engage(),
and VM chain teardown after disarm in fman_pcd_fe_disengage().

Uses simple line insertion (not block replacement) to avoid format
string and anchor matching issues.

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

# ── 1. Insert VM chain build before arm_engage in fe_engage() ──

# Find the arm_engage call INCLUDING the err = assignment
arm_call = "\terr = __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, 0x001C0006);"
if arm_call not in src:
    # Try without EKFC
    arm_call = "\terr = __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid);"

if arm_call not in src:
    # Try without err = prefix (just the call, for safety)
    arm_call = "__fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, 0x001C0006);"
    if arm_call not in src:
        arm_call = "__fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid);"

if arm_call not in src:
    print("### F-092: arm_engage call not found in fe_engage()")
else:
    chain_build = """/* F-092: Build FE-VM chain before arming (idempotent). */
\tif (!pcd->fe_vm_chain_built) {
\t\terr = __fman_pcd_fe_build_vm_chain(pcd);
\t\tif (err) {
\t\t\tpr_err("fman_pcd: FE engage: VM chain build failed: %d\\n", err);
\t\t\treturn err;
\t\t}
\t\tpcd->fe_vm_chain_built = true;
\t}

\t"""
    src = src.replace(arm_call, chain_build + arm_call, 1)
    changes += 1
    print("### F-092: inserted VM chain build before arm_engage")

# ── 2. Insert VM chain teardown after disarm in fe_disengage() ──

disarm_call = "fman_pcd_fe_arm_disengage(pcd, buf);"
disarm_call2 = "__fman_pcd_fe_arm_disengage(pcd, (u8)hw_port_id);"

teardown = """fman_pcd_fe_arm_disengage(pcd, buf);
\t/* F-092: Tear down FE-VM chain after disarming. */
\tif (pcd->fe_vm_chain_built) {
\t\tfman_pcd_fe_enq_free(pcd);
\t\tfman_pcd_fe_hash_free(pcd);
\t\tfman_pcd_ehash_drain(pcd);
\t\tfman_pcd_fe_singletons_free(pcd);
\t\tfman_pcd_fe_pool_put(pcd);
\t\tpcd->fe_vm_chain_built = false;
\t}
"""

if disarm_call in src:
    src = src.replace(disarm_call, teardown, 1)
    changes += 1
    print("### F-092: inserted VM chain teardown in fe_disengage()")
elif disarm_call2 in src:
    teardown2 = "__fman_pcd_fe_arm_disengage(pcd, (u8)hw_port_id);\n" + teardown[teardown.index("\t/*"):]
    src = src.replace(disarm_call2, teardown2, 1)
    changes += 1
    print("### F-092: inserted VM chain teardown (typed variant)")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-092: {changes} change(s) applied")
else:
    print("### F-092: no changes applied")
