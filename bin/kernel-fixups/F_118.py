"""F_118 (Fix B, part 2/3): per-key delete unit-test hook on the fe_flow node.

F-117 added fman_pcd_ehash_del_key() (per-key silicon collision-chain unlink),
but the only caller is ask.ko's FLOW_CLS_DESTROY — reachable only via a live
armed FE-VM HIT, whose arm/teardown path is crash-prone.  The fe_flow debugfs
node exposes only "add" and "clear" (clear-ALL), so there was no way to exercise
the per-key delete deterministically.

This fixup adds a "del <keyhex>" verb to fman_pcd_fe_flow_write() that routes to
fman_pcd_ehash_del_key() on ehash table 0.  With it, Fix B's collision-chain
unlink is unit-testable through pure ehash ops (fe_ehash set -> fe_flow add x2
-> fe_flow del <key>), needing NO fe_arm / fe_pool / hit-engage — sidestepping
the crash-prone arming path entirely.

Additive only: the "add" and "clear" branches are untouched.  Reuses the
existing helpers fman_pcd_hexval(), fman_pcd_ehash_table_by_index() (0128) and
fman_pcd_ehash_del_key() (F-117), so it MUST run after F-117.

Upstream-Status: Inappropriate [LS1046A DPAA1 FMan FE-VM ehash test hook]
Risk-Tier: A (additive debugfs verb; no change to existing datapaths)
"""

import os, sys

KROOT = "drivers/net/ethernet/freescale/fman"
PCD_C = os.path.join(KROOT, "fman_pcd.c")

if not os.path.exists(PCD_C):
    print("### F_118: fman_pcd.c not found")
    sys.exit(0)

with open(PCD_C) as f:
    src = f.read()

# Anchor: the end of the "clear" branch immediately followed by the "add"
# sscanf in fman_pcd_fe_flow_write().  Insert the "del" branch between them.
anchor = (
    "\t\tfman_pcd_ehash_flow_clear_all(pcd);\n"
    "\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\treturn count;\n"
    "\t}\n"
    "\n"
    "\tnf = sscanf(buf, \"add %u %113s %lx\", &tbl_idx, keytok, &enq_fe_off);"
)

del_branch = (
    "\t\tfman_pcd_ehash_flow_clear_all(pcd);\n"
    "\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\treturn count;\n"
    "\t}\n"
    "\n"
    "\t/*\n"
    "\t * F-118: per-key delete unit-test hook — \"del <keyhex>\" removes the one\n"
    "\t * flow whose key matches (F-117 fman_pcd_ehash_del_key on table 0), so\n"
    "\t * Fix B's collision-chain unlink is testable without arming the FE VM.\n"
    "\t */\n"
    "\tif (!strncmp(buf, \"del \", 4)) {\n"
    "\t\tchar dkeytok[2 * FMAN_EHASH_FLOW_KEY_MAX + 2];\n"
    "\t\tu8 dkey[FMAN_EHASH_FLOW_KEY_MAX];\n"
    "\t\tu8 dks = 0;\n"
    "\t\tconst char *dk;\n"
    "\t\tstruct fman_pcd_ehash_table *dt;\n"
    "\t\tint derr;\n"
    "\n"
    "\t\tif (sscanf(buf, \"del %113s\", dkeytok) != 1) {\n"
    "\t\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\t\treturn -EINVAL;\n"
    "\t\t}\n"
    "\t\tfor (dk = dkeytok; dk[0] && dk[1]; dk += 2) {\n"
    "\t\t\tint hi = fman_pcd_hexval(dk[0]);\n"
    "\t\t\tint lo = fman_pcd_hexval(dk[1]);\n"
    "\n"
    "\t\t\tif (hi < 0 || lo < 0 ||\n"
    "\t\t\t    dks >= FMAN_EHASH_FLOW_KEY_MAX) {\n"
    "\t\t\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\t\t\treturn -EINVAL;\n"
    "\t\t\t}\n"
    "\t\t\tdkey[dks++] = (u8)((hi << 4) | lo);\n"
    "\t\t}\n"
    "\t\tif (dks == 0 || dkeytok[2 * dks] != '\\0') {\n"
    "\t\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\t\treturn -EINVAL;\n"
    "\t\t}\n"
    "\t\tdt = fman_pcd_ehash_table_by_index(pcd, 0);\n"
    "\t\tif (!dt) {\n"
    "\t\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\t\treturn -ENODEV;\n"
    "\t\t}\n"
    "\t\tderr = fman_pcd_ehash_del_key(dt, dkey, dks);\n"
    "\t\tmutex_unlock(&pcd->fe_lock);\n"
    "\t\treturn derr ? derr : count;\n"
    "\t}\n"
    "\n"
    "\tnf = sscanf(buf, \"add %u %113s %lx\", &tbl_idx, keytok, &enq_fe_off);"
)

if "F-118: per-key delete unit-test hook" in src:
    print("### F_118: del verb already present")
elif anchor in src:
    src = src.replace(anchor, del_branch, 1)
    with open(PCD_C, "w") as f:
        f.write(src)
    print("### F_118: added 'del <key>' verb to fman_pcd_fe_flow_write()")
else:
    print("### F_118: WARNING — fe_flow_write clear/add anchor not found (layout drift?)")
