---
name: build-check
description: Validate VyOS LS1046A build environment, kernel configs, and cross-compilation flags
---
# VyOS LS1046A Build Audit Protocol

1. Check `Makefile` or build scripts for correct target architecture (`aarch64` / `arm64`).
2. Verify kernel `.config` options for NXP LS1046A drivers (e.g., DPAA1 / FMan ethernet, SEC 5.x crypto engine, QORIQ PCIe).
3. Ensure device tree source (`.dts` / `.dtsi`) patches adhere to standard Linux kernel formatting.
4. Scan Dockerfile/Podman scripts to confirm build dependencies for VyOS ISO/img targets are pinned.
