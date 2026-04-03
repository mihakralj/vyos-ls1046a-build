# Restoring DPDK DPAA1 PMD Infrastructure

**Archived:** 2026-04-03
**Reason:** RC#31 — DPDK `dpaa_bus` probe initializes ALL BMan/QMan hardware globally,
killing kernel-managed FMan interfaces. Cannot coexist with kernel in mixed mode.
See `plans/VPP-DPAA-PMD-VS-AFXDP.md` for full analysis.

**Current production path:** AF_XDP via `vyos-1x-010-vpp-platform-bus.patch` (~3.5 Gbps)

## What Was Archived

| Archived File | Original Location | Purpose |
|---|---|---|
| `bin/ci-build-dpdk-plugin.sh` | `bin/ci-build-dpdk-plugin.sh` | CI step: builds DPDK 24.11 + VPP dpdk_plugin.so with DPAA1 mempool patches |
| `data/kernel-patches/fsl_usdpaa_mainline.c` | `data/kernel-patches/fsl_usdpaa_mainline.c` | `/dev/fsl-usdpaa` chardev (1453 lines, NXP ABI-compatible) |
| `data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch` | `data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch` | BMan/QMan symbol exports + portal reservation + Kconfig |
| `data/kernel-config/ls1046a-usdpaa.config` | `data/kernel-config/ls1046a-usdpaa.config` | `CONFIG_FSL_USDPAA_MAINLINE=y` + `STRICT_DEVMEM` disable |
| `data/dpdk-portal-mmap.patch` | `data/dpdk-portal-mmap.patch` | DPDK `process.c` portal mmap (CE/CI windows) |
| `data/strlcpy-shim.c` | `data/strlcpy-shim.c` | BSD strlcpy/strlcat for glibc 2.36 target |
| `data/cmake/CMakeLists.txt` | `data/cmake/CMakeLists.txt` | Out-of-tree CMake for VPP DPDK plugin build |
| `data/hooks/97-dpaa-dpdk-plugin.chroot` | `data/hooks/97-dpaa-dpdk-plugin.chroot` | Live-build hook: deploys DPAA dpdk_plugin.so + binutils |

## CI Changes Made

1. **`.github/workflows/auto-build.yml`** — Removed "Build DPDK + VPP DPAA Plugin" step
2. **`bin/ci-setup-kernel.sh`** — Removed:
   - `sed` lines stripping `CONFIG_STRICT_DEVMEM` / `CONFIG_IO_STRICT_DEVMEM` from defconfig
   - Copy of `9001-usdpaa-bman-qman-exports-and-driver.patch` to kernel patches
   - Copy of `fsl_usdpaa_mainline.c` to kernel build dir
   - `awk` injection that copies `.c` into kernel tree during build
3. **Kernel config** — `STRICT_DEVMEM` re-enabled (default upstream, no longer stripped)

## Restoration Steps

To restore DPDK DPAA1 PMD support (e.g., for all-DPDK+LCP mode):

### 1. Move files back

```bash
git mv archive/dpaa-pmd/bin/ci-build-dpdk-plugin.sh bin/
git mv archive/dpaa-pmd/data/kernel-patches/fsl_usdpaa_mainline.c data/kernel-patches/
git mv archive/dpaa-pmd/data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch data/kernel-patches/
git mv archive/dpaa-pmd/data/kernel-config/ls1046a-usdpaa.config data/kernel-config/
git mv archive/dpaa-pmd/data/dpdk-portal-mmap.patch data/
git mv archive/dpaa-pmd/data/strlcpy-shim.c data/
git mv archive/dpaa-pmd/data/cmake/CMakeLists.txt data/cmake/
git mv archive/dpaa-pmd/data/hooks/97-dpaa-dpdk-plugin.chroot data/hooks/
```

### 2. Re-add CI build step to `auto-build.yml`

Insert before "Build VyOS ISO" step:

```yaml
      - name: Build DPDK + VPP DPAA Plugin
        run: bin/ci-build-dpdk-plugin.sh
```

### 3. Restore `bin/ci-setup-kernel.sh` USDPAA injection

Add back to the `sed` block (before the `for frag` loop):
```bash
sed -i '/CONFIG_STRICT_DEVMEM/d'            "$DEFCONFIG"
sed -i '/CONFIG_IO_STRICT_DEVMEM/d'         "$DEFCONFIG"
```

Add back after the `mkdir -p "$KERNEL_PATCHES"` line:
```bash
cp data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch "$KERNEL_PATCHES/"
```

Add back the `.c` file staging + awk injection (copy from git history or this doc):
```bash
cp data/kernel-patches/fsl_usdpaa_mainline.c "$KERNEL_BUILD/"
```

And modify the `awk` injection to include the USDPAA copy block before the phylink patch block.

### 4. Verify `data/cmake/` directory exists

```bash
mkdir -p data/cmake
```

## Future Directions

- **All-DPDK+LCP mode**: All interfaces under DPDK, VPP LCP creates TAP mirrors. Sidesteps RC#31. ~9.4 Gbps.
- **CDX-assisted DPAA PMD**: NXP ASK CDX primitives to scope `dpaa_bus` init. Requires proprietary microcode.
- **Upstream DPDK fix**: Scope `dpaa_bus` probe to specific portals/FQs only (DPDK code changes).

See `plans/VPP-DPAA-PMD-VS-AFXDP.md` and `plans/ASK-ANALYSIS.md` for detailed analysis.