"""F-094: Retype fman_pcd_fe_flow_add to use structured fman_pcd_fe_flow_action.

Replaces the raw (const u8 *key, u8 key_size, unsigned long enq_off)
parameters with a single const struct fman_pcd_fe_flow_action *.

The struct is defined in include/linux/fsl/fman_pcd.h and includes:
  - key[FMAN_FE_FLOW_KEY_MAX] — flow key bytes (MSB-first extraction order)
  - key_size — actual key length in bytes
  - enq_off — ENQ FE MURAM offset for HIT dispatch
  - flags — reserved for future use

This is a breaking API change. The old callers (ask.ko) are updated.
Do this BEFORE M5 flow automation depends on the current signature.

Disposition: permanent — API contract cleanup
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")
hdr = "include/linux/fsl/fman_pcd.h"

changes = 0

# ── 1. Add struct definition to fman_pcd.h ─────────────────────────

if not os.path.exists(hdr):
    print("### F-094: fman_pcd.h not found")
else:
    with open(hdr) as f:
        src = f.read()

    struct_def = """
/* F-094: Structured flow action — replaces raw (key, key_size, enq_off) params.
 * Key is MSB-first EKFC extraction order (SIPDIPPROTOSPORTDPORT).
 */
#define FMAN_FE_FLOW_KEY_MAX   56   /* fits 256B DDR record minus header */
struct fman_pcd_fe_flow_action {
\tu8   key[FMAN_FE_FLOW_KEY_MAX];
\tu8   key_size;
\tunsigned long enq_off;\t\t/* ENQ FE MURAM offset for HIT dispatch */
\tu32  flags;\t\t\t/* reserved for future use */
};

"""

    # Insert before the fe_engage declaration
    # F-157 (2026-08-01) extended fe_engage to 3-arg; match either form.
    anchor2 = "int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id);"
    anchor3 = "int fman_pcd_fe_engage(struct fman *fm, u8 hw_port_id,\n\t\t\t u32 enq_fqid);"
    if "fman_pcd_fe_flow_action" not in src:
        if anchor2 in src:
            src = src.replace(anchor2, struct_def + anchor2, 1)
            changes += 1
            print("### F-094: added fman_pcd_fe_flow_action struct to header (2-arg anchor)")
        elif anchor3 in src:
            src = src.replace(anchor3, struct_def + anchor3, 1)
            changes += 1
            print("### F-094: added fman_pcd_fe_flow_action struct to header (3-arg anchor)")
        else:
            # Fallback: insert before any fman_pcd_fe_flow_add declaration.
            fall = "int  fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id"
            if fall in src:
                src = src.replace(fall, struct_def + fall, 1)
                changes += 1
                print("### F-094: added fman_pcd_fe_flow_action struct (fallback anchor)")
            else:
                print("### F-094: WARNING — could not locate header insertion anchor")
    else:
        print("### F-094: fman_pcd_fe_flow_action already in header")

    # Update the flow_add declaration
    old_decl = """int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,
\t\t\t const u8 *key, u8 key_size, unsigned long enq_off);"""
    new_decl = """int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,
\t\t\t const struct fman_pcd_fe_flow_action *action);"""
    if old_decl in src:
        src = src.replace(old_decl, new_decl, 1)
        changes += 1
        print("### F-094: updated flow_add declaration")
    elif "fman_pcd_fe_flow_add" in src:
        # Already changed? Check
        pass

    with open(hdr, "w") as f:
        f.write(src)

# ── 2. Update implementation in fman_pcd.c ──────────────────────────

if not os.path.exists(pcd_c):
    print("### F-094: fman_pcd.c not found")
else:
    with open(pcd_c) as f:
        src = f.read()

    # Update flow_add implementation
    old_impl = """int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,
\t\t\t const u8 *key, u8 key_size, unsigned long enq_off)
{
\tstruct fman_pcd *pcd;
\tstruct fman_pcd_ehash_table *t;

\tif (!fm || !key || key_size == 0)
\t\treturn -EINVAL;
\tpcd = fman_get_pcd(fm);
\tif (!pcd)
\t\treturn -ENXIO;

\tt = fman_pcd_ehash_table_by_index(pcd, 0);
\tif (!t)
\t\treturn -ENODEV;

\treturn fman_pcd_ehash_add_key(t, key, key_size, enq_off);
}"""

    new_impl = """int fman_pcd_fe_flow_add(struct fman *fm, u8 hw_port_id,
\t\t\t const struct fman_pcd_fe_flow_action *action)
{
\tstruct fman_pcd *pcd;
\tstruct fman_pcd_ehash_table *t;

\tif (!fm || !action || action->key_size == 0)
\t\treturn -EINVAL;
\tpcd = fman_get_pcd(fm);
\tif (!pcd)
\t\treturn -ENXIO;

\tt = fman_pcd_ehash_table_by_index(pcd, 0);
\tif (!t)
\t\treturn -ENODEV;

\treturn fman_pcd_ehash_add_key(t, action->key, action->key_size,
\t\t\t\t      action->enq_off);
}"""

    if old_impl in src:
        src = src.replace(old_impl, new_impl, 1)
        changes += 1
        print("### F-094: updated flow_add implementation")
    else:
        print("### F-094: flow_add implementation not found (already retyped?)")

    with open(pcd_c, "w") as f:
        f.write(src)

if changes:
    print(f"### F-094: {changes} change(s) applied")
else:
    print("### F-094: no changes applied")
