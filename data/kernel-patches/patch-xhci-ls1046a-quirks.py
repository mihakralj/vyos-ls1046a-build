#!/usr/bin/env python3
"""
patch-xhci-ls1046a-quirks.py — inject LS1046A DWC3 xHCI quirks into
drivers/usb/host/xhci-plat.c::xhci_plat_quirks().

Adds XHCI_AVOID_BEI | XHCI_TRUST_TX_LENGTH whenever the parent device
chain (or its fwnode counterpart) reports compatible "fsl,ls1046a-dwc3"
or "snps,dwc3". Required because dwc3_host_init() creates the xhci-hcd
platform child with of_node=NULL and only fwnode set — the original
unified-diff patch using of_device_is_compatible() never matched at
runtime, so the quirks word stayed at 0x0000008002000010 (no
AVOID_BEI 0x8000, no TRUST_TX_LENGTH 0x400) and USB-storage probe
killed the host controller during USB live boot.

Usage:  python3 patch-xhci-ls1046a-quirks.py <kernel-source-dir>

Idempotent: a sentinel comment is written and re-runs detect it.
"""
import sys
from pathlib import Path

SENTINEL = "/* LS1046A DWC3 quirks injected by patch-xhci-ls1046a-quirks.py */"

ANCHOR = "static void xhci_plat_quirks(struct device *dev, struct xhci_hcd *xhci)\n{\n\tstruct xhci_plat_priv *priv = xhci_to_priv(xhci);\n\n\txhci->quirks |= priv->quirks;\n}\n"

REPLACEMENT = """static void xhci_plat_quirks(struct device *dev, struct xhci_hcd *xhci)
{
\tstruct xhci_plat_priv *priv = xhci_to_priv(xhci);
\tstruct device *tmpdev;
\tbool ls1046a_dwc3 = false;

\txhci->quirks |= priv->quirks;

\t""" + SENTINEL + """
\t/* dwc3_host_init() sets fwnode (not of_node) on the xhci platform
\t * child, so we walk both up the parent chain. Match the LS1046A-
\t * specific compatible AND the generic snps,dwc3 (dwc3 core).
\t */
\tfor (tmpdev = dev; tmpdev; tmpdev = tmpdev->parent) {
\t\tif (tmpdev->of_node &&
\t\t    (of_device_is_compatible(tmpdev->of_node, "fsl,ls1046a-dwc3") ||
\t\t     of_device_is_compatible(tmpdev->of_node, "snps,dwc3"))) {
\t\t\tls1046a_dwc3 = true;
\t\t\tbreak;
\t\t}
\t\tif (tmpdev->fwnode &&
\t\t    (fwnode_device_is_compatible(tmpdev->fwnode, "fsl,ls1046a-dwc3") ||
\t\t     fwnode_device_is_compatible(tmpdev->fwnode, "snps,dwc3"))) {
\t\t\tls1046a_dwc3 = true;
\t\t\tbreak;
\t\t}
\t}
\tif (ls1046a_dwc3) {
\t\txhci->quirks |= XHCI_AVOID_BEI | XHCI_TRUST_TX_LENGTH;
\t\tdev_info(dev, "LS1046A DWC3 quirks applied (AVOID_BEI|TRUST_TX_LENGTH)\\n");
\t}
}
"""


def main(ksrc):
    target = Path(ksrc) / "drivers/usb/host/xhci-plat.c"
    if not target.exists():
        print(f"ERROR: {target} does not exist")
        sys.exit(1)

    text = target.read_text()

    if SENTINEL in text:
        print(f"I: {target.name} already patched (sentinel present) — skipping")
        return

    if ANCHOR not in text:
        # Try also with mainline tab style differences (some trees use spaces)
        # Print a diagnostic and fail loudly so build doesn't silently regress.
        print(f"ERROR: Anchor block not found in {target}")
        print("       Looked for the canonical xhci_plat_quirks() body — kernel")
        print("       version mismatch or upstream churn. Inspect the file.")
        sys.exit(2)

    new_text = text.replace(ANCHOR, REPLACEMENT, 1)
    target.write_text(new_text)
    print(f"I: LS1046A DWC3 xHCI quirks injected into {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])