"""F-138: DIAGNOSTIC — add printk to scaffold alloc/free to trace 304 B/cycle leak.

The 5-cycle test shows +304 B PCD gen_pool residual per cycle.  The engage
delta is 17,546 B (scaffold 304 + 2× per-port pools 17,242).  The disengage
frees 17,242 B (pools) but NOT the 304 B scaffold.  This fixup adds printk
to verify whether the scaffold is allocated and freed correctly.

As of F-139, the scaffold is per-port (fp->scaffold_*) and freed in
fman_pcd_fe_port_del().  This diagnostic verifies the fix.

DELETE this fixup after root cause is confirmed.
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-138: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# 1. Add printk to scaffold allocation in __fman_pcd_fe_arm_engage
# The scaffold is stored in the singleton pcd->fe_scaffold_* during engage,
# then copied to per-port fp->scaffold_* in fe_port_set (F-139).
# Anchor on the singleton assignment.
alloc_anchor = '\t\t\tpcd->fe_scaffold_gro = gro;'
if alloc_anchor in src:
    new_alloc = alloc_anchor + '\n\t\t\tpr_info("fman_pcd: F-138 scaffold ALLOC port=0x%x gro=0x%lx mto=0x%lx ato=0x%lx\\n", (u8)port_id, gro, mto, ato);'
    if new_alloc not in src:
        src = src.replace(alloc_anchor, new_alloc, 1)
        changes += 1
        print("### F-138: added scaffold ALLOC printk (singleton)")
else:
    # Try per-port style (post-F_139)
    alloc_anchor = '\t\t\tfp->scaffold_gro = gro;'
    if alloc_anchor in src:
        new_alloc = alloc_anchor + '\n\t\t\tpr_info("fman_pcd: F-138 scaffold ALLOC port=0x%x gro=0x%lx mto=0x%lx ato=0x%lx\\n", (u8)port_id, gro, mto, ato);'
        if new_alloc not in src:
            src = src.replace(alloc_anchor, new_alloc, 1)
            changes += 1
            print("### F-138: added scaffold ALLOC printk (per-port)")
    else:
        print("### F-138: scaffold alloc anchor not found")

# 2. Add printk to scaffold free in fe_port_del
# Find the scaffold free block added by patch 0123
free_anchor = 'if (fp->scaffold_ato)'
if free_anchor in src:
    insert = '\tpr_info("fman_pcd: F-138 scaffold FREE port=0x%x gro=0x%lx mto=0x%lx ato=0x%lx\\n", fp->port_id, fp->scaffold_gro, fp->scaffold_mto, fp->scaffold_ato);\n\t'
    if 'F-138 scaffold FREE' not in src:
        src = src.replace(free_anchor, insert + free_anchor, 1)
        changes += 1
        print("### F-138: added per-port scaffold FREE printk")
else:
    print("### F-138: scaffold free anchor not found in fe_port_del")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-138: {changes} change(s) applied")
else:
    print("### F-138: no changes applied")
    sys.exit(1)