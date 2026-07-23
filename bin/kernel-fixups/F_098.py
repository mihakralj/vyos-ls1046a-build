"""F_098 (T-P1-4 / F-12): Fix fman_pcd_fe_context_build pointer dereference.

Fixup F_098 originally tried to retype `void __iomem *ctx` to `struct fman_ddr_region *ctx`.
However, callers in fman_pcd.c pass raw MURAM/DDR virtual base pointers (void __iomem *).
Dereferencing `ctx->cpu` on a raw base pointer reads MURAM descriptor data as a pointer,
triggering a level 0 translation fault (000200000000010a) and kernel panic on engage.

This fixup ensures:
 1. fman_pcd_fe_context_build() accepts a raw virtual pointer `void __iomem *ctx`.
 2. Writes use `iowrite32be` / `memcpy_toio` directly on `(ctx + offset)` without `ctx->cpu` dereference.
"""

import os, sys

KROOT = "drivers/net/ethernet/freescale/fman"
PCD_C = os.path.join(KROOT, "fman_pcd.c")

if not os.path.exists(PCD_C):
    print("### F_098: fman_pcd.c not found")
    sys.exit(0)

with open(PCD_C) as f:
    src = f.read()

changes = 0

# Revert any bad retyping of ctx->cpu back to (ctx + offset)
if "ctx->cpu" in src:
    src = src.replace("(u8 *)ctx->cpu + offset", "ctx + offset")
    src = src.replace("((u8 *)ctx->cpu + offset)", "(ctx + offset)")
    src = src.replace("struct fman_ddr_region *ctx", "void __iomem *ctx")
    print("### F_098: fixed ctx->cpu dereference back to raw pointer (ctx + offset)")
    changes += 1

if changes:
    with open(PCD_C, "w") as f:
        f.write(src)
    print("### F_098: %d change(s) applied" % changes)
else:
    print("### F_098: no changes needed")
