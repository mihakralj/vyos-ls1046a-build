#!/usr/bin/env python3
"""F_105: rx_hook diagnostics — log why frames are rejected.

Adds ratelimited dev_info prints at each return-false point in
af_xdp_pool_rx_hook() so we can identify which check rejects VPP's frames.
Each check gets 5 prints then goes silent.

REMOVE this entire fixup once the root cause is identified and fixed.
"""
import sys, os

TARGET = "drivers/net/ethernet/freescale/dpaa/af_xdp_pool/af_xdp_pool_main.c"

def apply_diagnostics(content):
    """Add diagnostic prints at each rx_hook return-false point."""
    
    # Diagnostic 1: bpid mismatch (most likely culprit)
    old = "if (fd->bpid != priv->xsk_bpid[band])\nreturn false;"
    new = """if (fd->bpid != priv->xsk_bpid[band]) {
\tstatic int _diag_bpid;
\tif (_diag_bpid++ < 5)
\t\tdev_info(priv->net_dev->dev.parent,
\t\t\t "rx_hook DIAG: bpid mismatch fd->bpid=%u xsk_bpid[%u]=%u",
\t\t\t fd->bpid, band, priv->xsk_bpid[band]);
\treturn false;
}"""
    if old in content:
        content = content.replace(old, new)
        return content, True
    return content, False

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        with open(TARGET) as f:
            content = f.read()
        _, found = apply_diagnostics(content)
        if found:
            print("F_105: target patterns found, diagnostics can be applied")
        else:
            print("F_105: WARNING — target patterns NOT found (already applied or drifted)")
        return 0
    
    with open(TARGET) as f:
        content = f.read()
    
    new_content, found = apply_diagnostics(content)
    if not found:
        print("F_105: ERROR — target pattern not found in", TARGET)
        return 1
    
    with open(TARGET, "w") as f:
        f.write(new_content)
    print("F_105: added rx_hook diagnostics to", TARGET)
    return 0

if __name__ == "__main__":
    sys.exit(main())
