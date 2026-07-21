#!/usr/bin/env python3
"""F_106: rx_hook trace_printk diagnostics at every return-false point.

Adds trace_printk() at each goto out_unlock / return false in
af_xdp_pool_rx_hook() so ftrace reveals exactly which check rejects
VPP's frames.  Each check gets a unique label.

REMOVE this entire fixup once the root cause is identified and fixed.
"""
import sys, os

TARGET = "drivers/net/ethernet/freescale/dpaa/af_xdp_pool/af_xdp_pool_main.c"

def apply_diagnostics(content):
    """Add trace_printk at each rx_hook return-false point."""
    modified = False

    # Diagnostic 1: !priv
    old = "if (!priv)\nreturn false;"
    new = 'if (!priv) {\n\ttrace_printk("rx_hook: !priv\\n");\n\treturn false;\n}'
    if old in content:
        content = content.replace(old, new)
        modified = True

    # Diagnostic 2: band >= MAX_QBANDS
    old = "if (band >= DPAA1_XSK_MAX_QBANDS)\nreturn false;"
    new = 'if (band >= DPAA1_XSK_MAX_QBANDS) {\n\ttrace_printk("rx_hook: bad band %u\\n", band);\n\treturn false;\n}'
    if old in content:
        content = content.replace(old, new)
        modified = True

    # Diagnostic 3: bpid mismatch
    old = "if (fd->bpid != priv->xsk_bpid[band])\nreturn false;"
    new = 'if (fd->bpid != priv->xsk_bpid[band]) {\n\ttrace_printk("rx_hook: bpid mismatch fd=%u xsk=%d\\n", fd->bpid, priv->xsk_bpid[band]);\n\treturn false;\n}'
    if old in content:
        content = content.replace(old, new)
        modified = True

    # Diagnostic 4: !pool
    old = "if (!pool)\ngoto out_unlock;"
    new = 'if (!pool) {\n\ttrace_printk("rx_hook: !pool band=%u\\n", band);\n\tgoto out_unlock;\n}'
    if old in content:
        content = content.replace(old, new)
        modified = True

    # Diagnostic 5: dma lookup fails
    old = "if (!dpaa_xsk_chunk_head_from_dma(priv, band, addr, &head_idx))\ngoto out_unlock;"
    new = 'if (!dpaa_xsk_chunk_head_from_dma(priv, band, addr, &head_idx)) {\n\ttrace_printk("rx_hook: dma lookup fail addr=%llx band=%u\\n", (u64)addr, band);\n\tgoto out_unlock;\n}'
    if old in content:
        content = content.replace(old, new)
        modified = True

    # Diagnostic 6: !xdp
    old = "if (!xdp)\ngoto out_unlock;"
    new = 'if (!xdp) {\n\ttrace_printk("rx_hook: !xdp head_idx=%u\\n", head_idx);\n\tgoto out_unlock;\n}'
    if old in content:
        content = content.replace(old, new)
        modified = True

    # Diagnostic 7: !prog
    old = "if (!prog) {"
    # This one is multi-line, need to match the whole block
    old_block = """if (!prog) {
/* No XDP program attached: there is no XSKMAP to redirect
 * into, so we cannot zero-copy this frame.  Fall back to the
 * skbuf path (return false) -- the chunk stays owned by the
 * XSK pool free list and will be re-released on the next
 * FILL cycle.  This matches the 0096 read-side observation
 * that Recover is a no-op without a consumer program.
 */
goto out_unlock;
}"""
    new_block = """if (!prog) {
trace_printk("rx_hook: !prog\\n");
goto out_unlock;
}"""
    if old_block in content:
        content = content.replace(old_block, new_block)
        modified = True

    # Diagnostic 8: XDP program returned non-REDIRECT
    old = "case XDP_PASS:\ndefault:\n/* XDP_PASS / XDP_TX / XDP_ABORTED"
    # This is part of a switch, need to match the full default case
    old_default = """case XDP_PASS:
default:
/* XDP_PASS / XDP_TX / XDP_ABORTED: we have no skbuf-building
 * path for an XSK chunk here, so fall back to the mainline
 * skbuf path (return false).  The reverse-map slot stays
 * valid; no double handling.
 */
break;"""
    new_default = """case XDP_PASS:
default:
trace_printk("rx_hook: XDP act=%d (not REDIRECT)\\n", act);
break;"""
    if old_default in content:
        content = content.replace(old_default, new_default)
        modified = True

    return content, modified

def main():
    if len(sys.argv) > 1 and sys.argv[0] == "--check":
        with open(TARGET) as f:
            content = f.read()
        _, found = apply_diagnostics(content)
        if found:
            print("F_106: target patterns found, diagnostics can be applied")
        else:
            print("F_106: WARNING — some target patterns NOT found (already applied or drifted)")
        return 0

    with open(TARGET) as f:
        content = f.read()

    new_content, found = apply_diagnostics(content)
    if not found:
        print("F_106: ERROR — no target patterns found in", TARGET)
        return 1

    with open(TARGET, "w") as f:
        f.write(new_content)
    print("F_106: added rx_hook trace_printk diagnostics to", TARGET)
    return 0

if __name__ == "__main__":
    sys.exit(main())