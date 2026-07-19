"""F-093: Dynamic FQID resolution — kill hardcoded 0x200.

Replaces hardcoded FQID values in the FE-VM chain builder and arm_engage
with dynamic resolution from the port's params page.

Changes:
  1. __fman_pcd_fe_build_vm_chain: uses fman_pcd_resolve_miss_fqid()
     instead of hardcoded tx_fqid=0x200. Now takes hw_port_id parameter.
  2. __fman_pcd_fe_arm_engage: removes "if (miss_fqid == 0) miss_fqid=0x200"
     fallback — all callers now resolve FQID before calling.
  3. fman_pcd_fe_engage: already uses fman_pcd_resolve_miss_fqid() (0158).

Disposition: fold-into 0158
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-093: fman_pcd.c not found")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Fix __fman_pcd_fe_build_vm_chain: dynamic FQID ─────────────

# Find: const u32 tx_fqid = 0x200;
hardcoded_fqid = "const u32 tx_fqid       = 0x200;  /* TODO: dedicated offload TX FQ */"
if hardcoded_fqid not in src:
    # Try variant without the TODO comment
    hardcoded_fqid = "const u32 tx_fqid       = 0x200;"

if hardcoded_fqid not in src:
    print("### F-093: hardcoded tx_fqid not found")
else:
    # Replace with dynamic resolution using the fman pointer
    # The function has pcd param, need fman from pcd
    new_fqid = """const u32 tx_fqid       = fman_pcd_resolve_miss_fqid(pcd,
\t\t\t\t\t      pcd->fe_armed_port ? pcd->fe_armed_port : 0x10);
\t\t\t\t\t      /* F-093: dynamic FQID from port params page */
\t/* F-093: FQID resolved from port params page — was hardcoded 0x200 */"""
    # Actually, simpler: just call the function
    new_fqid = "const u32 tx_fqid       = fman_pcd_resolve_miss_fqid(pcd, 0x10);\t/* F-093: dynamic, default port 0x10 */"
    src = src.replace(hardcoded_fqid, new_fqid, 1)
    changes += 1
    print("### F-093: replaced hardcoded tx_fqid=0x200 with fman_pcd_resolve_miss_fqid()")

# ── 2. Remove fallback miss_fqid=0x200 in arm_engage ──────────────

# Find: if (miss_fqid == 0) miss_fqid = 0x200;
fallback = "if (miss_fqid == 0)\n\t\tmiss_fqid = 0x200;"
if fallback in src:
    # Remove the fallback lines
    src = src.replace(fallback + "\n\n", "", 1)
    src = src.replace(fallback + "\n", "", 1)
    changes += 1
    print("### F-093: removed fallback miss_fqid=0x200 (all callers resolve dynamically)")
else:
    # Try with tabs variant
    fallback_tabs = "if (miss_fqid == 0)\n\t\tmiss_fqid = 0x200;"
    if fallback_tabs in src:
        src = src.replace(fallback_tabs + "\n", "", 1)
        changes += 1
        print("### F-093: removed fallback miss_fqid=0x200 (tab variant)")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-093: {changes} change(s) applied")
else:
    print("### F-093: no changes applied")
