---
name: dts-diff
description: Inspect and validate Device Tree Source changes for NXP Layerscape hardware
---
# Device Tree Review Protocol

1. Examine git diffs for `.dts` and `.dtsi` files under the kernel patch tree.
2. Check `reg` ranges, memory nodes, interrupt vectors, and ethernet MAC/PHY interfaces against standard LS1046A reference manuals.
3. Verify that new nodes do not cause address space collisions in the DPAA1 block.
