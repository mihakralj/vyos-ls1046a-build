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

v4 (2026-07-28): v1-v3 all used src.replace(disarm_line, ..., 1) which
matched the FIRST occurrence of __fman_pcd_fe_arm_disengage(pcd, hw_port_id)
in the file — the DEBUGFS handler (fman_pcd_fe_arm_write), NOT the
production fman_pcd_fe_disengage().  This is the exact same bug class as
F-092 v1.  Board-validated on .185 (ISO 1835): disengage works but F-129
teardown never fires, ehash int_buf held, fe_pool engaged=YES.

v4 fix: scope the search to the production function body by anchoring on
the function signature, then finding the disarm call within that scope.
Same pattern as F_092.py v2.

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

# ── Insert VM chain teardown into PRODUCTION fman_pcd_fe_disengage() ──
# v4: Scope to the production function, not the debugfs handler.
# The debugfs handler (fman_pcd_fe_arm_write) also calls
# __fman_pcd_fe_arm_disengage() and appears EARLIER in the file.
# src.replace(..., 1) matches that first occurrence — wrong function.

prod_sig = "int fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id)"
prod_idx = src.find(prod_sig)
if prod_idx == -1:
    print("### F-129: ERROR — production fman_pcd_fe_disengage not found")
    sys.exit(1)

# Find the function body
func_body_start = src.index("{", prod_idx)
# Find end of function (next EXPORT_SYMBOL)
export_idx = src.find("EXPORT_SYMBOL_GPL(fman_pcd_fe_disengage);", func_body_start)
if export_idx == -1:
    print("### F-129: ERROR — EXPORT_SYMBOL_GPL not found after fe_disengage")
    sys.exit(1)

func_scope = src[func_body_start:export_idx]

disarm_line = "\t__fman_pcd_fe_arm_disengage(pcd, hw_port_id);"
if disarm_line not in func_scope:
    print("### F-129: ERROR — disarm call not found in production fe_disengage()")
    sys.exit(1)

teardown_block = """\t__fman_pcd_fe_arm_disengage(pcd, hw_port_id);
\t/* F-129: Tear down shared FE-VM chain on last port disengage.
\t * Gate on fe_refcount (set by fe_pool_get, cleared by fe_pool_put).
\t * When the last port disengages and fe_ports is empty, tear down
\t * the shared chain (pool, singletons, ehash, hashfe, enq).
\t */
\tif (list_empty(&pcd->fe_ports) && pcd->fe_refcount) {
\t\tpr_info("fman_pcd: F-129 tearing down FE-VM chain (last port disengaged)\\n");
\t\tfman_pcd_fe_enq_free(pcd);
\t\tfman_pcd_fe_hash_free(pcd);
\t\tfman_pcd_ehash_drain(pcd);
\t\tfman_pcd_fe_singletons_free(pcd);
\t\tfman_pcd_fe_pool_put(pcd);
\t\tpcd->fe_vm_chain_built = false;
\t}
"""

# Find the exact position in the full source and insert before it
abs_pos = func_body_start + func_scope.find(disarm_line)
if abs_pos > func_body_start and src[abs_pos:abs_pos+len(disarm_line)] == disarm_line:
    src = src[:abs_pos] + teardown_block + src[abs_pos+len(disarm_line):]
    changes += 1
    print("### F-129: inserted VM chain teardown into production fe_disengage() (v4 scoped)")
else:
    print("### F-129: ERROR — disarm call position mismatch in production fn")
    sys.exit(1)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-129: {changes} change(s) applied")
else:
    print("### F-129: no changes applied")
    sys.exit(1)