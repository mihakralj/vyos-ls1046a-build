#!/usr/bin/env python3
"""F-079-LEAKFIX: Rewrite fman_pcd_fe_buffer_teardown with muram_free calls.
Finds the teardown function by signature, replaces its body with one
that reads the index offset from +0x54 before zeroing it, extracts
the pool offset from the index bytes, and frees both MURAM allocations.
"""
import sys

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

fn = "static void fman_pcd_fe_buffer_teardown"
pos = src.find(fn)
if pos < 0:
    sys.exit(0)

body_start = src.find("{", pos)
depth = 0
body_end = body_start
for i in range(body_start, len(src)):
    if src[i] == "{":
        depth += 1
    elif src[i] == "}":
        depth -= 1
        if depth == 0:
            body_end = i + 1
            break

new_body = """{
    struct muram_info *muram = fman_get_muram(pcd->fman);
    u32 pp_off, idx_off, pool_off;
    u8 tnums;
    void __iomem *pp, *idx;
    static const unsigned int BMI_FIFO_UNITS = 0x100;

    if (!muram || !port)
        return;
    tnums = fman_port_get_total_tnums(port);
    if (!tnums)
        return;
    pp_off = fman_port_get_params_page(port);
    if (IS_ERR_VALUE(pp_off))
        return;
    pp = fman_muram_offset_to_vbase(muram, pp_off);

    /* Read index offset BEFORE zeroing, extract pool offset, free both */
    idx_off = ioread32be((void __iomem *)((u8 __iomem *)pp + 0x54));
    iowrite32be(0, (void __iomem *)((u8 __iomem *)pp + 0x54));

    if (idx_off && idx_off != 0xFFFFFFFF) {
        idx = fman_muram_offset_to_vbase(muram, idx_off);
        pool_off = ioread32be(idx) & 0x00FFFFFF;
        if (pool_off)
            fman_pcd_muram_free(pcd, pool_off, tnums * BMI_FIFO_UNITS * 2);
        fman_pcd_muram_free(pcd, idx_off, 5 + tnums);
    }
}"""

src = src[:body_start] + new_body + src[body_end:]
with open(path, "w") as f:
    f.write(src)
print("### fman_pcd.c: F-079-LEAKFIX v3 teardown rewritten with muram_free")
