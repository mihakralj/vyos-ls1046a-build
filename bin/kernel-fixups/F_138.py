"""F-138: DIAGNOSTIC — add printk to scaffold alloc/free to trace 304 B/cycle leak.

The 5-cycle test shows +304 B PCD gen_pool residual per cycle.  The engage
delta is 17,546 B (scaffold 304 + 2× per-port pools 17,242).  The disengage
frees 17,242 B (pools) but NOT the 304 B scaffold.  This fixup adds printk
to verify whether fman_pcd_fe_arm_free_scaffold() is actually called and
whether the tracking variables are non-zero.

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
# Find the tracking variable assignments
alloc_anchor = '\t\t\tpcd->fe_scaffold_gro = gro;'
if alloc_anchor in src:
    new_alloc = alloc_anchor + '\n\t\t\tpr_info("fman_pcd: F-138 scaffold ALLOC gro=0x%lx mto=0x%lx ato=0x%lx\\n", gro, mto, ato);'
    if new_alloc not in src:
        src = src.replace(alloc_anchor, new_alloc, 1)
        changes += 1
        print("### F-138: added scaffold ALLOC printk")
else:
    print("### F-138: scaffold alloc anchor not found")

# 2. Add printk to scaffold free
free_func = "static void fman_pcd_fe_arm_free_scaffold(struct fman_pcd *pcd)"
if free_func in src:
    # Find the function body opening brace
    func_idx = src.index(free_func)
    brace_idx = src.index("{", func_idx)
    # Insert printk at the top of the function
    insert = '{\n\tpr_info("fman_pcd: F-138 scaffold FREE gro=0x%lx mto=0x%lx ato=0x%lx\\n", pcd->fe_scaffold_gro, pcd->fe_scaffold_mto, pcd->fe_scaffold_ato);'
    if insert not in src:
        src = src[:brace_idx] + insert + src[brace_idx+1:]
        changes += 1
        print("### F-138: added scaffold FREE printk")
else:
    print("### F-138: scaffold_free function not found")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-138: {changes} change(s) applied")
else:
    print("### F-138: no changes applied")
    sys.exit(1)