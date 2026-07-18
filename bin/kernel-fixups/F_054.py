import re

path = "drivers/net/ethernet/freescale/fman/fman_pcd.c"
with open(path) as f:
    src = f.read()

# Fix 1: MUX - replace the context_build call with direct AD write
# Target: fman_pcd_fe_context_build(fe, FMAN_FE_MUX_CTX_OFF, &p);
old_mux_call = "fman_pcd_fe_context_build(fe, FMAN_FE_MUX_CTX_OFF, &p);"
new_mux_call = "iowrite32be(FMAN_FE_TYPE_MUX | (u32)enq->muram_off, fe); /* F-054: direct AD write, not context_build */"
if old_mux_call in src:
    # Also remove the lines that set up the context params for MUX
    # (memset, p.type, p.u.mux.next_fe_off) since they're no longer used
    old_block = (
        "\t\tmemset(&p, 0, sizeof(p));\n"
        "\t\tp.type = FMAN_FE_TYPE_MUX;\n"
        "\t\tp.u.mux.next_fe_off = enq->muram_off;\n"
        "\t\tfman_pcd_fe_context_build(fe, FMAN_FE_MUX_CTX_OFF, &p);"
    )
    new_block = (
        "\t\t/* F-054: MUX AD word 0 = type|next-FE. context_build wrote at\n"
        "\t\t * AD+0 overwriting the type header, crashing hardware. */\n"
        "\t\tiowrite32be(FMAN_FE_TYPE_MUX | (u32)enq->muram_off, fe);"
    )
    if old_block in src:
        src = src.replace(old_block, new_block)
        print("### fman_pcd.c: F-054 MUX AD direct write (block replace)")
    else:
        # Fallback: just replace the single line
        src = src.replace(old_mux_call, new_mux_call)
        print("### fman_pcd.c: F-054 MUX AD direct write (line replace)")

# Fix 2: Transition - replace context_build with direct AD word 1 write
old_trans_call = "fman_pcd_fe_context_build(fe, FMAN_FE_TRANSITION_CTX_OFF, &p);"
new_trans_call = "iowrite32be((u32)pcd->fe_exit_off, (u32 __iomem *)fe + 1); /* F-054: direct AD word 1 write */"
if old_trans_call in src:
    old_tblock = (
        "\t\tmemset(&p, 0, sizeof(p));\n"
        "\t\tp.type = FMAN_FE_TYPE_TRANSITION;\n"
        "\t\tp.u.transition.next_ad_off = pcd->fe_exit_off;\n"
        "\t\tfman_pcd_fe_context_build(fe, FMAN_FE_TRANSITION_CTX_OFF, &p);"
    )
    new_tblock = (
        "\t\t/* F-054: Transition AD word 1 = next-AD offset.\n"
        "\t\t * Same context_build corruption bug as MUX. */\n"
        "\t\tiowrite32be((u32)pcd->fe_exit_off, (u32 __iomem *)fe + 1);"
    )
    if old_tblock in src:
        src = src.replace(old_tblock, new_tblock)
        print("### fman_pcd.c: F-054 Transition AD direct write (block)")
    else:
        src = src.replace(old_trans_call, new_trans_call)
        print("### fman_pcd.c: F-054 Transition AD direct write (line)")

with open(path, "w") as f:
    f.write(src)
