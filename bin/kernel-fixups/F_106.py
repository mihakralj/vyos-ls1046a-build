#!/usr/bin/env python3
"""F_106 v2: rx_hook dev_info diagnostics at every return-false point.

Uses dev_info_ratelimited (which writes to dmesg, proven working by F_105)
instead of trace_printk. Adds a TOP-of-function diagnostic that prints
all key values on the first 5 invocations, then goes silent.

REMOVE this entire fixup once the root cause is identified and fixed.
"""
import sys, os

TARGET = "drivers/net/ethernet/freescale/dpaa/af_xdp_pool/af_xdp_pool_main.c"

def apply_diagnostics(content):
    """Add dev_info_ratelimited at each rx_hook return-false point."""
    modified = False

    # TOP diagnostic: print all key values on first 5 invocations
    # Insert right after the variable declarations, before the first check
    old = "if (!priv)\nreturn false;"
    new = """if (!priv)
\treturn false;

\t/* F_106 TOP diagnostic: print key values on first 5 invocations */
\t{
\t\tstatic int _diag_top;
\t\tif (_diag_top++ < 5)
\t\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t\t "rx_hook TOP: bpid=%u xsk_bpid[%u]=%d pool=%px prog=%px",
\t\t\t\t fd->bpid, band, priv->xsk_bpid[band],
\t\t\t\t rcu_dereference(priv->xsk_pool[band]),
\t\t\t\t READ_ONCE(priv->xdp_prog));
\t}

\tif (!priv)"""
    # Actually, the TOP diagnostic needs to go AFTER band is computed but BEFORE the first check.
    # Let me insert it after the band check instead.
    # Better approach: insert after "band = dpaa_fq_to_qband" line
    
    # Let me use a different anchor: insert after the band check
    old2 = "if (band >= DPAA1_XSK_MAX_QBANDS)\nreturn false;"
    new2 = """if (band >= DPAA1_XSK_MAX_QBANDS)
\treturn false;

\t/* F_106 TOP diagnostic: print key values on first 5 invocations */
\t{
\t\tstatic int _diag_top;
\t\tif (_diag_top++ < 5)
\t\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t\t "rx_hook TOP: bpid=%u xsk_bpid[%u]=%d pool=%px prog=%px",
\t\t\t\t fd->bpid, band, priv->xsk_bpid[band],
\t\t\t\t rcu_dereference(priv->xsk_pool[band]),
\t\t\t\t READ_ONCE(priv->xdp_prog));
\t}"""
    if old2 in content:
        content = content.replace(old2, new2)
        modified = True

    # Diagnostic 3: bpid mismatch (F_105 already covers this, but add dev_info_ratelimited)
    old3 = "if (fd->bpid != priv->xsk_bpid[band])\nreturn false;"
    new3 = """if (fd->bpid != priv->xsk_bpid[band]) {
\t\tstatic int _diag_bpid;
\t\tif (_diag_bpid++ < 5)
\t\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t\t "rx_hook DIAG2: bpid mismatch fd->bpid=%u xsk_bpid[%u]=%u",
\t\t\t\t fd->bpid, band, priv->xsk_bpid[band]);
\t\treturn false;
\t}"""
    if old3 in content:
        content = content.replace(old3, new3)
        modified = True

    # Diagnostic 4: !pool
    old4 = "if (!pool)\ngoto out_unlock;"
    new4 = """if (!pool) {
\t\tstatic int _diag_pool;
\t\tif (_diag_pool++ < 5)
\t\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t\t "rx_hook DIAG4: !pool band=%u", band);
\t\tgoto out_unlock;
\t}"""
    if old4 in content:
        content = content.replace(old4, new4)
        modified = True

    # Diagnostic 5: dma lookup fails
    old5 = "if (!dpaa_xsk_chunk_head_from_dma(priv, band, addr, &head_idx))\ngoto out_unlock;"
    new5 = """if (!dpaa_xsk_chunk_head_from_dma(priv, band, addr, &head_idx)) {
\t\tstatic int _diag_dma;
\t\tif (_diag_dma++ < 5)
\t\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t\t "rx_hook DIAG5: dma lookup fail addr=%llx band=%u",
\t\t\t\t (unsigned long long)addr, band);
\t\tgoto out_unlock;
\t}"""
    if old5 in content:
        content = content.replace(old5, new5)
        modified = True

    # Diagnostic 6: !xdp
    old6 = "if (!xdp)\ngoto out_unlock;"
    new6 = """if (!xdp) {
\t\tstatic int _diag_xdp;
\t\tif (_diag_xdp++ < 5)
\t\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t\t "rx_hook DIAG6: !xdp head_idx=%u", head_idx);
\t\tgoto out_unlock;
\t}"""
    if old6 in content:
        content = content.replace(old6, new6)
        modified = True

    # Diagnostic 7: !prog
    old7_block = """if (!prog) {
/* No XDP program attached: there is no XSKMAP to redirect
 * into, so we cannot zero-copy this frame.  Fall back to the
 * skbuf path (return false) -- the chunk stays owned by the
 * XSK pool free list and will be re-released on the next
 * FILL cycle.  This matches the 0096 read-side observation
 * that Recover is a no-op without a consumer program.
 */
goto out_unlock;
}"""
    new7_block = """if (!prog) {
\t\tstatic int _diag_prog;
\t\tif (_diag_prog++ < 5)
\t\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t\t "rx_hook DIAG7: !prog");
\t\tgoto out_unlock;
\t}"""
    if old7_block in content:
        content = content.replace(old7_block, new7_block)
        modified = True

    # Diagnostic 8: XDP program returned non-REDIRECT
    old8_default = """case XDP_PASS:
default:
/* XDP_PASS / XDP_TX / XDP_ABORTED: we have no skbuf-building
 * path for an XSK chunk here, so fall back to the mainline
 * skbuf path (return false).  The reverse-map slot stays
 * valid; no double handling.
 */
break;"""
    new8_default = """case XDP_PASS:
default:
\t\t{
\t\t\tstatic int _diag_act;
\t\t\tif (_diag_act++ < 5)
\t\t\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t\t\t "rx_hook DIAG8: XDP act=%d (not REDIRECT)", act);
\t\t}
\t\tbreak;"""
    if old8_default in content:
        content = content.replace(old8_default, new8_default)
        modified = True

    return content, modified

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
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
    print("F_106: added rx_hook dev_info diagnostics to", TARGET)
    return 0

if __name__ == "__main__":
    sys.exit(main())