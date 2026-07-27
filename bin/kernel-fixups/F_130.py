"""F-130: Grow PCD MURAM arena from 64 KiB to 84 KiB.

F-127 (2026-07-27) proved the -12 on second-port engage is -ENOMEM from
fman_pcd_fe_port_set() → MURAM alloc for per-port FE buffer pool. The
arena is fragmented by the ehash int_buf (33280 B at offset 0x4c100).

With F-125(a) (scaffold-leak fix) and F-129 (production teardown) landed,
the arena lifecycle is correct — the int_buf is legitimately held while
any port is engaged. The fix is to grow the arena so two ports' pools
(~9029 B each) + the int_buf (33280 B) all fit with room for placement.

The contiguous MURAM extent from 0x4ac00 to 0x60000 is 86016 B (84 KiB).
Growing from 64 KiB to 84 KiB gives 20 KiB more headroom.

Disposition: permanent (or fold-into the #define)
Upstream-Status: Inappropriate [LS1046A Mono Gateway DK FE-VM]
Risk-Tier: A (constant change only, no logic)
"""

import sys, os

kroot = "drivers/net/ethernet/freescale/fman"
pcd_c = os.path.join(kroot, "fman_pcd.c")

if not os.path.exists(pcd_c):
    print("### F-130: fman_pcd.c not found — skipping")
    sys.exit(0)

with open(pcd_c) as f:
    src = f.read()

changes = 0

old = "#define FMAN_PCD_MURAM_RESERVED_BYTES\t(64U * 1024U)"
new = "#define FMAN_PCD_MURAM_RESERVED_BYTES\t(84U * 1024U)"

if old in src:
    src = src.replace(old, new, 1)
    changes += 1
    print("### F-130: grew PCD MURAM arena 64 KiB -> 84 KiB")
else:
    print("### F-130: ERROR — FMAN_PCD_MURAM_RESERVED_BYTES not found")
    sys.exit(1)

if changes:
    with open(pcd_c, "w") as f:
        f.write(src)
    print(f"### F-130: {changes} change(s) applied")
else:
    print("### F-130: no changes applied")
    sys.exit(1)