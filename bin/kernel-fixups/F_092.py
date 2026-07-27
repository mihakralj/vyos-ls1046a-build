"""F-092: Make fman_pcd_fe_engage/disengage production-ready.

Inserts VM chain build before arm_engage in fman_pcd_fe_engage(),
and VM chain teardown after disarm in fman_pcd_fe_disengage().

v2 (2026-07-27): The original replace(..., 1) matched the FIRST occurrence
of the arm_engage call, which is in the DEBUGFS handler (earlier in the
file). The production fman_pcd_fe_engage() is later. Fix: scope the
search to the production function body by anchoring on the function
signature, then finding the arm_engage call within that scope.

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

# ── 1. Insert VM chain build before arm_engage in production fe_engage() ──

# Find the PRODUCTION function (not the debugfs handler)
prod_sig = "int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id)"
prod_idx = src.find(prod_sig)
if prod_idx == -1:
    print("### F-092: production fman_pcd_fe_engage not found")
else:
    # Find the arm_engage call within this function
    func_body_start = src.index("{", prod_idx)
    # Find end of function (next EXPORT_SYMBOL or next function at file scope)
    export_idx = src.find("EXPORT_SYMBOL_GPL(fman_pcd_fe_engage);", func_body_start)
    if export_idx == -1:
        print("### F-092: EXPORT_SYMBOL_GPL not found after fe_engage")
    else:
        func_scope = src[func_body_start:export_idx]
        arm_call = "\terr = __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid, 0x001C0006);"
        if arm_call not in func_scope:
            arm_call = "\terr = __fman_pcd_fe_arm_engage(pcd, hw_port_id, 0, miss_fqid);"
        if arm_call not in func_scope:
            print("### F-092: arm_engage call not found in production fe_engage()")
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
            # Replace within the full source, scoped to the production function
            old_block = arm_call
            new_block = chain_build + arm_call
            # Find the exact position in the full source
            abs_pos = func_body_start + func_scope.find(arm_call)
            if abs_pos > func_body_start and src[abs_pos:abs_pos+len(arm_call)] == arm_call:
                src = src[:abs_pos] + chain_build + src[abs_pos:]
                changes += 1
                print("### F-092: inserted VM chain build before arm_engage (production fn)")
            else:
                print("### F-092: arm_engage position mismatch")

# ── 2. Insert VM chain teardown after disarm in production fe_disengage() ──

# F-129 already handles this. Skip the debugfs-only teardown insertion.
# The old disarm_call anchor matched the debugfs handler, not production.
# Keep this section as a no-op for backward compat; F-129 does the real work.

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-092: {changes} change(s) applied")
else:
    print("### F-092: no changes applied")