"""F-129: Add FE-VM chain teardown to production fman_pcd_fe_disengage().

F-127 (2026-07-27) proved that the -12 on second-port engage comes from
fman_pcd_fe_port_set() → MURAM alloc → -ENOMEM, because the ehash int_buf
(33280 B at 0x4c100) is never freed on disengage.

Root cause: F_092 inserted the VM chain teardown into the DEBUGFS
fe_arm write handler (matching `fman_pcd_fe_arm_disengage(pcd, buf)`),
NOT into the production fman_pcd_fe_disengage() which calls
__fman_pcd_fe_arm_disengage().  The production path has ZERO teardown.

v4 (2026-07-28): v1-v3 all used src.replace(disarm_line, ..., 1) which
matched the FIRST occurrence of __fman_pcd_fe_arm_disengage(pcd, hw_port_id)
in the file — the DEBUGFS handler (fman_pcd_fe_arm_write), NOT the
production fman_pcd_fe_disengage().  This is the exact same bug class as
F-092 v1.  Board-validated on .185 (ISO 1835): disengage works but F-129
teardown never fires, ehash int_buf held, fe_pool engaged=YES.

v4 fix: scope the search to the production function body by anchoring on
the function signature, then finding the disarm call within that scope.
Same pattern as F_092.py v2.  Tries both `void` and `int` return types
since the signature may vary across patch versions.

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
#
# Try multiple possible function signatures (patch 0153 uses `void`,
# but fixups or later patches may change it to `int`).

prod_sigs = [
    "void fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id)",
    "int fman_pcd_fe_disengage(struct fman *fm, u8 hw_port_id)",
]

prod_idx = -1
matched_sig = None
for sig in prod_sigs:
    idx = src.find(sig)
    if idx != -1:
        prod_idx = idx
        matched_sig = sig
        break

if prod_idx == -1:
    print("### F-129: ERROR — production fman_pcd_fe_disengage not found (tried void and int)")
    sys.exit(1)

print(f"### F-129: found production fn with signature: {matched_sig}")

# Find the function body
func_body_start = src.index("{", prod_idx)

# Find end of function — look for the next function definition or EXPORT_SYMBOL
# after the function body.  The function ends at the closing brace before
# the next top-level declaration.
# Strategy: find EXPORT_SYMBOL_GPL(fman_pcd_fe_disengage) or the next function.
export_idx = src.find("EXPORT_SYMBOL_GPL(fman_pcd_fe_disengage);", func_body_start)
next_fn_idx = src.find("\nint fman_pcd_fe_flow_add", func_body_start)
if next_fn_idx == -1:
    next_fn_idx = src.find("\nvoid fman_pcd_fe_flow_add", func_body_start)

# Use whichever comes first after the function body
end_markers = []
if export_idx != -1:
    end_markers.append(export_idx)
if next_fn_idx != -1:
    end_markers.append(next_fn_idx)

if not end_markers:
    print("### F-129: ERROR — cannot find end of production fe_disengage()")
    sys.exit(1)

func_end = min(end_markers)
func_scope = src[func_body_start:func_end]

disarm_line = "\t__fman_pcd_fe_arm_disengage(pcd, hw_port_id);"
if disarm_line not in func_scope:
    # The function might still use the debugfs-style string call.
    # Check if some earlier fixup already rewrote it.
    alt_disarm = "\tfman_pcd_fe_arm_disengage(pcd, buf);"
    if alt_disarm in func_scope:
        print("### F-129: found debugfs-style disarm call in production fn — replacing with production-style")
        # Replace the debugfs-style call with production-style + teardown
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
        abs_pos = func_body_start + func_scope.find(alt_disarm)
        src = src[:abs_pos] + teardown_block + src[abs_pos+len(alt_disarm):]
        changes += 1
        print("### F-129: replaced debugfs-style disarm with production-style + teardown (v4)")
    else:
        print("### F-129: ERROR — disarm call not found in production fe_disengage()")
        sys.exit(1)
else:
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