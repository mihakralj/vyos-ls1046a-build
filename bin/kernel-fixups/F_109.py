"""F-109: Export fman_pcd_fe_enq_get_offset() — eliminate debugfs loopback.

Adds a kernel API to retrieve the ENQ FE MURAM offset so ask.ko can
call fman_pcd_fe_flow_add() directly instead of parsing debugfs output
via filp_open() + kernel_read().

The function returns the MURAM offset of the first ENQ FE object in
pcd->fe_enq, or 0 if the pool is not engaged.

Disposition: fold-into 0153
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (new export, no hot-path changes)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
hdr = "include/linux/fsl/fman_pcd.h"

changes = 0

# ── 1. Add declaration to fman_pcd.h ────────────────────────────────

if not os.path.exists(hdr):
    print("### F-109: fman_pcd.h not found")
else:
    with open(hdr) as f:
        src = f.read()

    # Insert after fman_pcd_fe_flow_del declaration
    anchor = "int fman_pcd_fe_flow_del(struct fman *fm, u8 hw_port_id,"
    if anchor in src and "fman_pcd_fe_enq_get_offset" not in src:
        # Find the end of the flow_del declaration (closing semicolon line)
        decl_end = "const u8 *key, u8 key_size);"
        if decl_end in src:
            new_decl = """const u8 *key, u8 key_size);

/* F-109: Return the MURAM offset of the first ENQ FE object, or 0 if
 * the FE pool is not engaged.  Replaces debugfs fe_enq parsing. */
unsigned long fman_pcd_fe_enq_get_offset(struct fman *fm);
"""
            src = src.replace(decl_end, new_decl, 1)
            changes += 1
            print("### F-109: added fman_pcd_fe_enq_get_offset declaration to header")

    with open(hdr, "w") as f:
        f.write(src)

# ── 2. Add implementation to fman_pcd.c ─────────────────────────────

if not os.path.exists(pcd_c):
    print("### F-109: fman_pcd.c not found")
else:
    with open(pcd_c) as f:
        src = f.read()

    # Insert after fman_pcd_fe_flow_del implementation
    impl_anchor = "EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del);"
    if impl_anchor in src and "fman_pcd_fe_enq_get_offset" not in src:
        new_impl = """EXPORT_SYMBOL_GPL(fman_pcd_fe_flow_del);

/* F-109: Return the MURAM offset of the first ENQ FE object.
 * Returns 0 if the FE pool is not engaged (fe_refcount == 0) or
 * the enq list is empty.  Caller does NOT need to hold fe_lock
 * for a read — the list is stable once the pool is engaged. */
unsigned long fman_pcd_fe_enq_get_offset(struct fman *fm)
{
\tstruct fman_pcd *pcd;
\tstruct fman_pcd_fe_obj *obj;

\tif (!fm)
\t\treturn 0;
\tpcd = fman_get_pcd(fm);
\tif (!pcd || pcd->fe_refcount == 0)
\t\treturn 0;

\tobj = list_first_entry_or_null(&pcd->fe_enq,
\t\t\t\t       struct fman_pcd_fe_obj, node);
\treturn obj ? obj->muram_off : 0;
}
EXPORT_SYMBOL_GPL(fman_pcd_fe_enq_get_offset);
"""
        src = src.replace(impl_anchor, new_impl, 1)
        changes += 1
        print("### F-109: added fman_pcd_fe_enq_get_offset implementation")

    with open(pcd_c, "w") as f:
        f.write(src)

if changes:
    print(f"### F-109: {changes} change(s) applied")
else:
    print("### F-109: no changes applied")