"""F_116: NULL-guard the FE-VM flow-delete path (crash-safety).

Board panic 2026-07-24 (ISO 2042, .185): every offloaded TCP flow that
CLOSES makes the nft flowtable tear it down (FLOW_CLS_DESTROY), which calls
ask_flow_offload.c: fman_pcd_fe_flow_del(NULL, 0, NULL, 0) — with fm == NULL.

    fman_pcd_fe_flow_del(fm=NULL)
      -> fman_pcd_ehash_flow_clear_all(fman_get_pcd(NULL))   # fman_get_pcd(NULL)=NULL
        -> list_for_each_entry(t, &pcd->fe_ehash_tables, ...) # reads NULL+0x138

pc=fman_pcd_fe_flow_del+0x20, fault addr 0x138 == offsetof(fman_pcd,
fe_ehash_tables), x0=0, insn f9409c13 = ldr x19,[x0,#0x138]. Kernel panic.

The throughput gate (T-M5-6/12) never hit this because it measured STEADY
traffic and never let an offloaded flow tear down during the window.

This fixup adds two defensive NULL guards so a delete with no live pcd is a
safe no-op instead of a NULL deref:
 1. fman_pcd_ehash_flow_clear_all(): bail if pcd == NULL (the actual deref).
 2. fman_pcd_fe_flow_del(): return -ENODEV if there is no pcd for @fm.

Note: this is the CRASH-SAFETY half. The functional half (wiring a real
struct fman* + a per-key delete so per-flow FE-VM records are actually
managed) is a separate ask_flow_offload.c / fman_pcd.c change — the current
flow_del clears ALL flows and both flow_add/del are called with NULL fm.

Upstream-Status: Inappropriate [LS1046A DPAA1 FMan FE-VM]
Risk-Tier: A (two-line NULL guards, no silicon-path change)
"""

import os, sys

KROOT = "drivers/net/ethernet/freescale/fman"
PCD_C = os.path.join(KROOT, "fman_pcd.c")

if not os.path.exists(PCD_C):
    print("### F_116: fman_pcd.c not found")
    sys.exit(0)

with open(PCD_C) as f:
    src = f.read()

changes = 0

# ── 1. Guard fman_pcd_ehash_flow_clear_all() — the actual NULL deref site ──
clear_anchor = (
    "\tstruct fman_pcd_ehash_table *t;\n"
    "\n"
    "\tlist_for_each_entry(t, &pcd->fe_ehash_tables, node)"
)
clear_fixed = (
    "\tstruct fman_pcd_ehash_table *t;\n"
    "\n"
    "\tif (!pcd)\n"
    "\t\treturn;\n"
    "\n"
    "\tlist_for_each_entry(t, &pcd->fe_ehash_tables, node)"
)
if "if (!pcd)\n\t\treturn;\n\n\tlist_for_each_entry(t, &pcd->fe_ehash_tables" in src:
    print("### F_116: clear_all guard already present")
elif clear_anchor in src:
    src = src.replace(clear_anchor, clear_fixed, 1)
    changes += 1
    print("### F_116: added NULL guard to fman_pcd_ehash_flow_clear_all()")
else:
    print("### F_116: WARNING — clear_all anchor not found (layout drift?)")

# ── 2. Guard fman_pcd_fe_flow_del() so flow_del(NULL, ...) is a safe no-op ──
del_anchor = (
    "\t(void)key_size;\n"
    "\tfman_pcd_ehash_flow_clear_all(fman_get_pcd(fm));"
)
del_fixed = (
    "\t(void)key_size;\n"
    "\tif (!fman_get_pcd(fm))\n"
    "\t\treturn -ENODEV;\n"
    "\tfman_pcd_ehash_flow_clear_all(fman_get_pcd(fm));"
)
if "if (!fman_get_pcd(fm))\n\t\treturn -ENODEV;" in src:
    print("### F_116: fe_flow_del guard already present")
elif del_anchor in src:
    src = src.replace(del_anchor, del_fixed, 1)
    changes += 1
    print("### F_116: added NULL guard to fman_pcd_fe_flow_del()")
else:
    print("### F_116: WARNING — fe_flow_del anchor not found (layout drift?)")

if changes:
    with open(PCD_C, "w") as f:
        f.write(src)
    print("### F_116: %d change(s) applied" % changes)
else:
    print("### F_116: no changes applied")
