"""F-093: Dynamic FQID resolution — kill hardcoded 0x200.

Replaces hardcoded FQID values in the FE-VM chain builder and arm_engage
with dynamic resolution from the port's params page.

R1 (2026-07-19): Chain builder reverted to hardcoded 0x200.
  fman_pcd_resolve_miss_fqid() returns 0 for port 0x10 because the port
  params page isn't allocated yet when __fman_pcd_fe_build_vm_chain runs
  (params page is set up DURING arm_engage, which runs AFTER chain build).
  Dynamic resolution kept in arm_engage path where params page is available.

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

# ── 1. Keep chain builder FQID hardcoded (params page not ready yet) ──

# The chain builder runs BEFORE arm_engage.  At that point the port's
# params page hasn't been allocated, so dynamic FQID resolution fails.
# Keep the known-good hardcoded value for now.
# 
# We check for the OLD hardcoded line (pre-R1 revert) or the R1 revert:
hardcoded_fqid = "const u32 tx_fqid       = fman_pcd_resolve_miss_fqid(pcd, 0x10);\t/* F-093: dynamic, default port 0x10 */"
new_hardcoded  = "const u32 tx_fqid       = 0x200;\t/* F-093-R1: hardcoded — params page not yet built here */"

if hardcoded_fqid in src:
    src = src.replace(hardcoded_fqid, new_hardcoded, 1)
    changes += 1
    print("### F-093-R1: reverted chain builder FQID to 0x200 (params page not ready)")
else:
    # Check if the original hardcoded is still present (pre-F-093)
    orig = "const u32 tx_fqid       = 0x200;  /* TODO: dedicated offload TX FQ */"
    orig_short = "const u32 tx_fqid       = 0x200;"
    if orig in src:
        print("### F-093-R1: original hardcoded 0x200 still present — no change needed")
    elif orig_short in src:
        print("### F-093-R1: original hardcoded 0x200 still present — no change needed")
    elif "tx_fqid" not in src:
        print("### F-093-R1: tx_fqid not found in file")
    else:
        print("### F-093-R1: chain builder FQID already at some other value")

# ── 2. Remove fallback miss_fqid=0x200 in arm_engage (keep) ──────

fallback = "if (miss_fqid == 0)\n\t\tmiss_fqid = 0x200;"
if fallback in src:
    src = src.replace(fallback + "\n\n", "", 1)
    src = src.replace(fallback + "\n", "", 1)
    changes += 1
    print("### F-093: removed fallback miss_fqid=0x200 (all callers resolve dynamically)")
else:
    print("### F-093: fallback already removed or not found")

# ── 3. Keep arm_engage dynamic FQID (params page IS available here) ──
# The fman_pcd_fe_engage() function calls:
#   1. fman_pcd_port_ensure_params_page() → params page allocated
#   2. __fman_pcd_fe_arm_engage() with miss_fqid from resolve_miss_fqid()
# At this point params page exists, so dynamic resolution works.
# No change needed here — the fman_pcd_resolve_miss_fqid() call in
# fman_pcd_fe_engage() is from patch 0158 and works correctly.

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-093: {changes} change(s) applied")
else:
    print("### F-093: no changes applied")
