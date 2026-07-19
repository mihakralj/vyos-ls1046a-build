"""F_098 (T-P1-4 / F-12): Retype fman_pcd_fe_context_build to use struct fman_ddr_region.

Patch 0135 uses void __iomem *ctx with iowrite32be/memcpy_toio to write
to the FE workspace context in DDR.  DDR is NOT iomem — it's CPU-accessible
DRAM.  iowrite32be has MMIO barrier semantics that are wrong for DDR.

This fixup:
 1. Defines struct fman_ddr_region { void *cpu; } in fman_pcd.c
 2. Retypes fman_pcd_fe_context_build() signature (void __iomem *ctx ->
    struct fman_ddr_region *ctx)
 3. Replaces iowrite32be(..., (u32 __iomem *)(ctx + off)) with
    __raw_writel(cpu_to_be32(...), (u32 *)((u8 *)ctx->cpu + off))
 4. Replaces memcpy_toio(ctx+off, ...) with plain memcpy

On ARM64 BE (our target): cpu_to_be32 is no-op, __raw_writel is plain store.
On ARM64 LE: cpu_to_be32 swaps to BE, __raw_writel stores as-is -> correct.

Callers (0146) pass the ctx struct pointer unchanged.

Disposition: fold-into 0135
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

# ── 1. Define struct fman_ddr_region ─────────────────────────────

DDR_TYPE = "/* FE workspace context lives in DDR (NOT MMIO). */\n" \
    "struct fman_ddr_region {\n" \
    "\tvoid *cpu;\t/* CPU-accessible base (kernel virtual address) */\n" \
    "};\n\n"

FE_TYPE_ANCHOR = "/* FE descriptor type codes (word0 bits 24-29) - SDK fm_pcd.h. */"

if "struct fman_ddr_region" not in src and FE_TYPE_ANCHOR in src:
    src = src.replace(FE_TYPE_ANCHOR, DDR_TYPE + FE_TYPE_ANCHOR, 1)
    print("### F_098: injected struct fman_ddr_region")
    changes += 1
elif "struct fman_ddr_region" in src:
    print("### F_098: struct fman_ddr_region already present")
else:
    print("### F_098: WARNING FE_TYPE_ANCHOR not found")

# ── 2. Retype function signature ─────────────────────────────────

OLD_SIG = "int fman_pcd_fe_context_build(void __iomem *ctx, u16 offset,"
if src.count(OLD_SIG) != 1:
    print("### F_098: WARNING old sig count=%d (expected 1)" % src.count(OLD_SIG))
else:
    src = src.replace(OLD_SIG, "int fman_pcd_fe_context_build(struct fman_ddr_region *ctx, u16 offset,", 1)
    print("### F_098: retyped signature")
    changes += 1

# ── 3. Replace memcpy_toio(ctx + offset, ...) ────────────────────

old_mc = "memcpy_toio(ctx + offset"
if old_mc in src:
    src = src.replace(old_mc, "memcpy((u8 *)ctx->cpu + offset")
    print("### F_098: replaced memcpy_toio")
    changes += 1

# ── 4. Replace iowrite32be(..., (u32 __iomem *)(ctx + ...)) ──────
# There are 5 instances in the context_build function:
#   Enq rspid|fqid:  iowrite32be(((u32)p->u.enq.rspid...), (u32 __iomem *)(ctx + offset));
#   Enq ppid:         iowrite32be((u32)p->u.enq.ppid << 16, (u32 __iomem *)(ctx + offset + 4));
#   Mux next_fe:      iowrite32be((u32)p->u.mux.next_fe_off, (u32 __iomem *)(ctx + offset));
#   Transition next:  iowrite32be((u32)p->u.transition.next_ad_off, (u32 __iomem *)(ctx + offset));

IO_PAIRS = [
    # (old_str, new_str)
    ('iowrite32be(((u32)p->u.enq.rspid << 24) | p->u.enq.fqid,\n\t\t\t    (u32 __iomem *)(ctx + offset));',
     '__raw_writel(cpu_to_be32(((u32)p->u.enq.rspid << 24) | p->u.enq.fqid),\n\t\t\t     (u32 *)((u8 *)ctx->cpu + offset));'),

    ('iowrite32be((u32)p->u.enq.ppid << 16,\n\t\t\t    (u32 __iomem *)(ctx + offset + 4));',
     '__raw_writel(cpu_to_be32((u32)p->u.enq.ppid << 16),\n\t\t\t     (u32 *)((u8 *)ctx->cpu + offset + 4));'),

    ('iowrite32be((u32)p->u.mux.next_fe_off,\n\t\t\t    (u32 __iomem *)(ctx + offset));',
     '__raw_writel(cpu_to_be32((u32)p->u.mux.next_fe_off),\n\t\t\t     (u32 *)((u8 *)ctx->cpu + offset));'),

    ('iowrite32be((u32)p->u.transition.next_ad_off,\n\t\t\t    (u32 __iomem *)(ctx + offset));',
     '__raw_writel(cpu_to_be32((u32)p->u.transition.next_ad_off),\n\t\t\t     (u32 *)((u8 *)ctx->cpu + offset));'),

    # line-continuation variant (0135 line 60-63): ctx + offset) on one line
    ('iowrite32be(((u32)p->u.enq.rspid << 24) | p->u.enq.fqid,\n\t\t\t    (u32 __iomem *)(ctx + offset)));',
     '__raw_writel(cpu_to_be32(((u32)p->u.enq.rspid << 24) | p->u.enq.fqid),\n\t\t\t     (u32 *)((u8 *)ctx->cpu + offset)));'),

    ('iowrite32be((u32)p->u.enq.ppid << 16,\n\t\t\t    (u32 __iomem *)(ctx + offset + 4)));',
     '__raw_writel(cpu_to_be32((u32)p->u.enq.ppid << 16),\n\t\t\t     (u32 *)((u8 *)ctx->cpu + offset + 4)));'),

    ('iowrite32be((u32)p->u.mux.next_fe_off,\n\t\t\t    (u32 __iomem *)(ctx + offset)));',
     '__raw_writel(cpu_to_be32((u32)p->u.mux.next_fe_off),\n\t\t\t     (u32 *)((u8 *)ctx->cpu + offset)));'),

    ('iowrite32be((u32)p->u.transition.next_ad_off,\n\t\t\t    (u32 __iomem *)(ctx + offset)));',
     '__raw_writel(cpu_to_be32((u32)p->u.transition.next_ad_off),\n\t\t\t     (u32 *)((u8 *)ctx->cpu + offset)));'),
]

total_iow = 0
for old_iow, new_iow in IO_PAIRS:
    if old_iow in src:
        src = src.replace(old_iow, new_iow, 1)
        total_iow += 1

if total_iow:
    print("### F_098: replaced %d iowrite32be call(s)" % total_iow)
    changes += total_iow
else:
    print("### F_098: no iowrite32be replacements matched (may be zero? checking...)")

if changes:
    with open(PCD_C, "w") as f:
        f.write(src)
    print("### F_098: %d change(s) applied" % changes)
else:
    print("### F_098: no changes needed")
