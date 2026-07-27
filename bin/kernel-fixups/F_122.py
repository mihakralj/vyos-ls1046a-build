"""F-122: make fe_arm engage idempotent — return 0 when already engaged.

Symptom (2026-07-26, .185 ISO 2004): `vyos-offload-ask engage` exits non-zero
on an already-engaged port (`echo: write error: Invalid argument`, `fe_arm
engage failed`) even though the kernel engaged successfully. The debugfs
`fe_arm` write path has no already-engaged check, so a second engage reaches
`fman_pcd_kg_port_arm_fe()` which returns an error on the already-reprogrammed
scheme, and that error propagates as -EINVAL.

F-107 added a -EBUSY guard in `fman_pcd_fe_engage()` (the exported API path)
but NOT in `__fman_pcd_fe_arm_engage()` (the shared core called by both the
API and the debugfs path). The debugfs path therefore has no idempotency.

Fix: add a `test_bit(port_id, pcd->fe_port_armed)` check at the top of
`__fman_pcd_fe_arm_engage()`, right after the port_id range check and before
any allocation. Return 0 (not -EBUSY) — the caller asked for the port to be
engaged and it already is; the desired state is achieved. Also change the
F-107 -EBUSY guard in `fman_pcd_fe_engage()` to return 0 with a pr_info
(same idempotent contract, and the new check in the shared core makes the
API-path guard redundant but we keep it for defense-in-depth).

Disposition: fold-into 0157
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (idempotency guard only, no hot-path changes)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-122: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

# ── 1. Add idempotency check in __fman_pcd_fe_arm_engage() ──
# Insert after the port_id range check, before scaffold allocation.
# The anchor is the port_id range check that every version of this function has.

anchor_1 = """\tif (port_id < 0x08 || port_id >= 0x28)
\t\treturn -EINVAL;

\t/* Allocate the FE_ENTER scaffold"""

if anchor_1 in src:
    block_1 = """\tif (port_id < 0x08 || port_id >= 0x28)
\t\treturn -EINVAL;

\t/* F-122: idempotent engage — return success if already armed. */
\tif (test_bit(port_id, pcd->fe_port_armed)) {
\t\tpr_info("fman_pcd fe_arm: port 0x%02x already engaged (idempotent)\\n",
\t\t\tport_id);
\t\treturn 0;
\t}

\t/* Allocate the FE_ENTER scaffold"""
    src = src.replace(anchor_1, block_1, 1)
    changes += 1
    print("### F-122: added idempotency check in __fman_pcd_fe_arm_engage()")
else:
    print("### F-122: WARNING — anchor_1 not found in __fman_pcd_fe_arm_engage()")

# ── 2. Change F-107 -EBUSY guard in fman_pcd_fe_engage() to idempotent return 0 ──
# The F-107 guard is now redundant (the shared core has the check), but we
# keep it for defense-in-depth and change it from -EBUSY to 0.

anchor_2 = """\t/* F-107: refuse double-engage — prevents gen_pool double-free on disengage. */
\tif (test_bit(hw_port_id, pcd->fe_port_armed)) {
\t\tpr_warn("fman_pcd: FE engage port 0x%02x already armed\\n", hw_port_id);
\t\treturn -EBUSY;
\t}"""

block_2 = """\t/* F-107/F-122: idempotent engage — return success if already armed. */
\tif (test_bit(hw_port_id, pcd->fe_port_armed)) {
\t\tpr_info("fman_pcd: FE engage port 0x%02x already armed (idempotent)\\n",
\t\t\thw_port_id);
\t\treturn 0;
\t}"""

if anchor_2 in src:
    src = src.replace(anchor_2, block_2, 1)
    changes += 1
    print("### F-122: changed F-107 -EBUSY guard to idempotent return 0")
else:
    print("### F-122: WARNING — F-107 -EBUSY guard not found (may already be idempotent)")

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-122: {changes} change(s) applied")
else:
    print("### F-122: no changes applied — check anchors against staged tree")
    sys.exit(1)