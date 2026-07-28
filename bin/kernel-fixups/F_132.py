"""F-132: Free FM_CTL params pages on last port disengage.

The params pages (CC_CTRL_PARAMS_PAGE_SZ = 256 B per port) are allocated
by fman_pcd_port_ensure_params_page() during engage and are idempotent
(cached per port).  They are NOT freed on disengage, contributing to the
persistent MURAM residual that fragments the arena for re-engage.

This fixup adds params page teardown to the F-129 teardown block in
fman_pcd_fe_disengage().  It iterates the fe_ports list (which is empty
at this point — ports were already removed by fe_port_del) and clears
the params page via fman_port_set_params_page(port, 0, NULL), then
frees the MURAM via fman_pcd_muram_free().

Since fe_ports is empty when the F-129 guard runs, we instead iterate
the pcd->cc_trees list to find ports that had params pages allocated.
Each CC tree has a port_id; we look up the rxport and free its params
page.

Actually, the simpler approach: the params page offset is cached in the
fman_port via fman_port_get_params_page().  We can iterate all known
ports (0x08-0x27) and free any that have a params page set.

Disposition: fold into F-129 teardown
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (frees small persistent allocations, idempotent)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-132: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# Find the F-129 teardown block (inserted by F_129.py)
# Anchor on the pr_info line that F-129 adds
anchor = '\t\tpr_info("fman_pcd: F-129 tearing down FE-VM chain (last port disengaged)\\n");'
if anchor not in src:
    print("### F-132: F-129 teardown block not found — skipping (F-129 may not have run)")
    sys.exit(0)

# Insert params page cleanup BEFORE the existing teardown calls
# The teardown block currently does: enq_free, hash_free, ehash_drain,
# singletons_free, fe_pool_put.  Add params page cleanup before these.

params_cleanup = """\t\t/* F-132: Free FM_CTL params pages allocated by
\t\t * fman_pcd_port_ensure_params_page() during engage.
\t\t * These are 256 B per port, cached (idempotent), and
\t\t * never freed on disengage — they contribute to the
\t\t * persistent MURAM residual that fragments the arena.
\t\t */
\t\t{
\t\t\tstruct fman *fm = fman_pcd_get_fman(pcd);
\t\t\tint pi;
\t\t\tfor (pi = 0x08; pi < 0x28; pi++) {
\t\t\t\tstruct fman_port *port = fman_port_lookup_rx(fm, pi);
\t\t\t\tu32 off;
\t\t\t\tif (!port)
\t\t\t\t\tcontinue;
\t\t\t\toff = fman_port_get_params_page(port);
\t\t\t\tif (off) {
\t\t\t\t\tfman_port_set_params_page(port, 0, NULL);
\t\t\t\t\tfman_pcd_muram_free(pcd, off,
\t\t\t\t\t\tCC_CTRL_PARAMS_PAGE_SZ);
\t\t\t\t}
\t\t\t}
\t\t}
"""

# Insert before the first teardown call (enq_free)
enq_free_anchor = "\t\tfman_pcd_fe_enq_free(pcd);"
if enq_free_anchor not in src:
    print("### F-132: enq_free call not found in teardown block — skipping")
    sys.exit(0)

src = src.replace(enq_free_anchor, params_cleanup + enq_free_anchor, 1)
changes += 1
print("### F-132: added params page cleanup to F-129 teardown block")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-132: {changes} change(s) applied")
else:
    print("### F-132: no changes applied")
    sys.exit(1)