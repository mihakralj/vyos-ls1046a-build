#!/bin/bash
# ci-setup-kernel-sdk.sh — SDK+ASK kernel config overrides and injection
# Alternative to ci-setup-kernel.sh for the ASK fast-path build.
#
# This replaces mainline DPAA1 drivers with NXP SDK drivers and applies
# ASK fast-path hooks. The two builds are mutually exclusive:
#   - ci-setup-kernel.sh     → mainline DPAA1 + VPP/AF_XDP path
#   - ci-setup-kernel-sdk.sh → SDK DPAA1 + ASK fast-path
#
# Called by: .github/workflows/auto-build.yml (when build_mode=sdk-ask)
set -ex
cd "${GITHUB_WORKSPACE:-.}"

### LS1046A kernel config
DEFCONFIG=vyos-build/scripts/package-build/linux-kernel/config/arm64/vyos_defconfig

# Remove upstream explicit disables that conflict with our overrides
sed -i '/CONFIG_DEVTMPFS_MOUNT/d'          "$DEFCONFIG"
sed -i '/CONFIG_CPU_FREQ_DEFAULT_GOV/d'    "$DEFCONFIG"
sed -i '/CONFIG_DEBUG_PREEMPT/d'           "$DEFCONFIG"

# Remove any mainline DPAA1 enables that would conflict with SDK
sed -i '/CONFIG_FSL_DPAA_ETH/d'            "$DEFCONFIG"
sed -i '/CONFIG_FSL_FMAN/d'                "$DEFCONFIG"
sed -i '/CONFIG_FSL_FMD_SHIM/d'            "$DEFCONFIG"
sed -i '/CONFIG_FORTIFY_SOURCE/d'          "$DEFCONFIG"

# Append all LS1046A kernel config fragments
# NOTE: ls1046a-sdk.config disables mainline and enables SDK drivers
# NOTE: ls1046a-ask.config enables ASK fast-path features
for frag in data/kernel-config/ls1046a-*.config; do
  echo "### Appending kernel config fragment: $(basename "$frag")"
  cat "$frag" >> "$DEFCONFIG"
done

### Kernel patches
KERNEL_BUILD=vyos-build/scripts/package-build/linux-kernel
KERNEL_PATCHES="$KERNEL_BUILD/patches/kernel"
mkdir -p "$KERNEL_PATCHES"

# Standard hardware patches (INA234, SFP rollball, swphy 10G)
cp data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch "$KERNEL_PATCHES/"
cp data/kernel-patches/4003-sfp-rollball-phylink-einval-fallback.patch "$KERNEL_PATCHES/"
cp data/kernel-patches/4004-swphy-support-10g-fixed-link-speed.patch "$KERNEL_PATCHES/"

# Stage phylink patch (still needed for SFP+ in SDK context)
cp data/kernel-patches/patch-phylink.py "$KERNEL_BUILD/"

# Stage SDK driver tarball
cp data/sdk-drivers.tar.zst "$KERNEL_BUILD/"

# Stage ASK hook injector, SDK driver injector, and source files
cp -r data/kernel-patches/ask/ "$KERNEL_BUILD/ask/"

# Stage SDK DTS and base board DTS (SDK DTS #includes the base)
cp data/dtb/mono-gateway-dk-sdk.dts "$KERNEL_BUILD/"
cp data/dtb/mono-gateway-dk.dts "$KERNEL_BUILD/"

### Injection block — runs inside build-kernel.sh after kernel source checkout
# This block is inserted into the kernel build script and runs in the kernel
# source directory. CWD variable points to the linux-kernel build directory.
cat > /tmp/kernel-inject.sh << 'INJECT_EOF'

# === SDK DPAA1 + ASK Fast-Path Injection ===

# 0. Install zstd for tarball extraction (may not be in build container)
which zstd >/dev/null 2>&1 || apt-get install -y --no-install-recommends zstd

# 1. Inject NXP SDK drivers into kernel tree
if [ -f "${CWD}/ask/inject-sdk-drivers.py" ] && [ -f "${CWD}/sdk-drivers.tar.zst" ]; then
  echo "SDK: Injecting NXP SDK DPAA1 drivers..."
  python3 "${CWD}/ask/inject-sdk-drivers.py" "$(pwd)" "${CWD}/sdk-drivers.tar.zst" || {
    echo "WARNING: Some SDK driver injection steps failed — check output above"
  }
fi

# 2. Patch phylink: trust SFP link over PCS in INBAND mode
PHYLINK_C=$(find . -path "*/net/phylink.c" -maxdepth 4 | head -1)
if [ -n "$PHYLINK_C" ] && [ -f "${CWD}/patch-phylink.py" ]; then
  python3 "${CWD}/patch-phylink.py" "$PHYLINK_C"
fi

# 3. Inject ASK hooks into kernel tree
if [ -f "${CWD}/ask/inject-ask-hooks.py" ]; then
  echo "ASK: Injecting fast-path hooks into kernel tree..."
  python3 "${CWD}/ask/inject-ask-hooks.py" "$(pwd)" || {
    echo "WARNING: Some ASK hooks failed — check output above"
  }
fi

# 4. Apply SDK driver ASK modifications
if [ -f "${CWD}/ask/5007-ask-sdk-driver-mods.patch" ]; then
  echo "ASK: Applying SDK driver modifications..."
  # Strip comment header lines before applying
  grep -v '^#' "${CWD}/ask/5007-ask-sdk-driver-mods.patch" | \
    patch --no-backup-if-mismatch -p1 --fuzz=3 || {
      echo "WARNING: Some SDK driver ASK hunks failed — manual fixup may be needed"
    }
fi

# 5. Apply remaining kernel hooks
if [ -f "${CWD}/ask/5008-ask-remaining-hooks.patch" ]; then
  echo "ASK: Applying remaining kernel hooks..."
  grep -v '^#' "${CWD}/ask/5008-ask-remaining-hooks.patch" | \
    patch --no-backup-if-mismatch -p1 --fuzz=3 || {
      echo "WARNING: Some remaining ASK hooks failed — manual fixup may be needed"
    }
fi

# 5a. Fix Kconfig ARM64 dependencies (ARCH_LAYERSCAPE doesn't exist in some configs)
# Patch 5009 adds ARM64 as alternative to ARCH_LAYERSCAPE for critical drivers:
# NET_VENDOR_FREESCALE, CLK_QORIQ, QORIQ_CPUFREQ, MMC_SDHCI_OF_ESDHC, CPE_FAST_PATH
# Also fixes nf_conn->mark regression from ASK patch
if [ -f "${CWD}/ask/5009-ask-kconfig-arm64-deps.patch" ]; then
  echo "ASK: Applying Kconfig ARM64 dependency fixes..."
  grep -v '^#' "${CWD}/ask/5009-ask-kconfig-arm64-deps.patch" | \
    patch --no-backup-if-mismatch -p1 --fuzz=3 || {
      echo "WARNING: Some 5009 Kconfig ARM64 dep fixes failed — may be pre-applied"
    }
fi

# 5b. Fix fmlib/kernel ABI to match pre-built fmc binary
if [ -f "${CWD}/ask/5010-ask-fmlib-abi-match.patch" ]; then
  echo "ASK: Applying fmlib ABI match patch..."
  grep -v '^#' "${CWD}/ask/5010-ask-fmlib-abi-match.patch" | \
    patch --no-backup-if-mismatch -p1 --fuzz=3 || {
      echo "WARNING: Some 5010 fmlib ABI hunks failed — may be pre-applied"
    }
fi

# 6. Copy SDK DTS + base board DTS into kernel tree and register for compilation
DTS_DIR=arch/arm64/boot/dts/freescale
if [ -f "${CWD}/mono-gateway-dk-sdk.dts" ]; then
  cp "${CWD}/mono-gateway-dk-sdk.dts" "$DTS_DIR/"
  echo "ASK: SDK DTS installed"
fi
if [ -f "${CWD}/mono-gateway-dk.dts" ]; then
  cp "${CWD}/mono-gateway-dk.dts" "$DTS_DIR/"
  echo "ASK: Base board DTS installed"
fi
# Add SDK DTB to the freescale Makefile so 'make dtbs' compiles it
if ! grep -q 'mono-gateway-dk-sdk' "$DTS_DIR/Makefile" 2>/dev/null; then
  echo 'dtb-$(CONFIG_ARCH_LAYERSCAPE) += mono-gateway-dk-sdk.dtb' >> "$DTS_DIR/Makefile"
  echo "ASK: SDK DTB added to Makefile"
fi
# Also add the base DTB (needed by mainline path too)
if ! grep -q 'mono-gateway-dk\.dtb' "$DTS_DIR/Makefile" 2>/dev/null; then
  echo 'dtb-$(CONFIG_ARCH_LAYERSCAPE) += mono-gateway-dk.dtb' >> "$DTS_DIR/Makefile"
  echo "ASK: Base DTB added to Makefile"
fi

# 7. Fix PHYLINK Kconfig — hidden tristate needs a prompt for user-enablement
# Mainline FSL_FMAN has "select PHYLINK" but SDK disables FSL_FMAN.
# Without a selector, make olddefconfig downgrades PHYLINK=y to =m,
# breaking CONFIG_SFP=y (depends on PHYLINK). Give PHYLINK a prompt
# so our defconfig CONFIG_PHYLINK=y sticks through olddefconfig.
PHYLINK_KC=$(find . -path "*/net/phy/Kconfig" -maxdepth 4 | head -1)
if [ -n "$PHYLINK_KC" ]; then
  if grep -q '^config PHYLINK' "$PHYLINK_KC"; then
    sed -i '/^config PHYLINK$/,/^\ttristate$/{s/^\ttristate$/\ttristate "General Ethernet PHY link framework"/}' "$PHYLINK_KC"
    echo "ASK: PHYLINK Kconfig — added prompt for user-enablement"
  fi
fi

# 8. Fix XGMAC_MDIO Kconfig — depends on FSL_FMAN which SDK disables.
# The xgmac_mdio.c driver is a standalone MDIO bus controller that probes
# fsl,fman-memac-mdio DT nodes. It has NO runtime dependency on mainline
# FSL_FMAN, but Kconfig gates it. Add FSL_SDK_FMAN as alternative dep.
XGMAC_KC=$(find . -path "*/ethernet/freescale/Kconfig" -maxdepth 5 | head -1)
if [ -n "$XGMAC_KC" ] && grep -q 'FSL_XGMAC_MDIO' "$XGMAC_KC"; then
  sed -i '/config FSL_XGMAC_MDIO/,/depends on/{s/depends on FSL_FMAN/depends on FSL_FMAN || FSL_SDK_FMAN/}' "$XGMAC_KC"
  echo "ASK: XGMAC_MDIO Kconfig — added FSL_SDK_FMAN as alternative dependency"
fi

# 10. Fix SPI_FSL_QUADSPI Kconfig — depends on ARCH_LAYERSCAPE (belt-and-suspenders
# with CONFIG_ARCH_LAYERSCAPE=y in board config, but patch as fallback)
SPI_KC=$(find . -path "*/spi/Kconfig" -maxdepth 4 | head -1)
if [ -n "$SPI_KC" ] && grep -q 'SPI_FSL_QUADSPI' "$SPI_KC"; then
  if grep -A2 'config SPI_FSL_QUADSPI' "$SPI_KC" | grep -q 'ARCH_LAYERSCAPE'; then
    sed -i '/config SPI_FSL_QUADSPI/,/depends on/{s/ARCH_LAYERSCAPE/ARCH_LAYERSCAPE || ARM64/}' "$SPI_KC"
    echo "ASK: SPI_FSL_QUADSPI Kconfig — added ARM64 as alternative dependency"
  fi
fi

# 11. Fix I2C_IMX Kconfig — depends on ARCH_MXC || ARCH_LAYERSCAPE (no COMPILE_TEST!)
I2C_KC=$(find . -path "*/i2c/busses/Kconfig" -maxdepth 5 | head -1)
if [ -n "$I2C_KC" ] && grep -q 'I2C_IMX' "$I2C_KC"; then
  if grep -A2 'config I2C_IMX' "$I2C_KC" | grep -q 'ARCH_LAYERSCAPE'; then
    sed -i '/config I2C_IMX/,/depends on/{s/ARCH_LAYERSCAPE/ARCH_LAYERSCAPE || ARM64/}' "$I2C_KC"
    echo "ASK: I2C_IMX Kconfig — added ARM64 as alternative dependency"
  fi
fi

echo "=== SDK + ASK kernel source injection complete ==="
INJECT_EOF

# Insert source injection block before "# Change name of Signing Cert" in build-kernel.sh
# This runs BEFORE make vyos_defconfig — correct for source/Kconfig modifications
sed -i '/# Change name of Signing Cert/r /tmp/kernel-inject.sh' "$KERNEL_BUILD/build-kernel.sh"
rm -f /tmp/kernel-inject.sh

### Second injection: config overrides AFTER make vyos_defconfig
# The first injection runs before .config exists (only source/Kconfig mods).
# This second injection runs after "make vyos_defconfig" creates .config,
# so scripts/config --set-val actually works.
cat > /tmp/kernel-config-override.sh << 'CONFIG_OVERRIDE_EOF'

# === Post-defconfig config overrides (runs after make vyos_defconfig) ===
echo "=== SDK+ASK: Forcing critical kernel configs after defconfig ==="

# Boot-critical: without these the kernel can't mount rootfs or open console
scripts/config --set-val CONFIG_DEVTMPFS y
scripts/config --set-val CONFIG_DEVTMPFS_MOUNT y
scripts/config --set-val CONFIG_MMC y
scripts/config --set-val CONFIG_MMC_BLOCK y
scripts/config --set-val CONFIG_MMC_SDHCI y
scripts/config --set-val CONFIG_MMC_SDHCI_PLTFM y
scripts/config --set-val CONFIG_MMC_SDHCI_OF_ESDHC y
scripts/config --set-val CONFIG_EXT4_FS y
scripts/config --set-val CONFIG_SQUASHFS y
scripts/config --set-val CONFIG_OVERLAY_FS y

# Platform: LS1046A is a Layerscape SoC
scripts/config --set-val CONFIG_ARCH_LAYERSCAPE y
scripts/config --set-val CONFIG_CLK_QORIQ y
scripts/config --set-val CONFIG_QORIQ_CPUFREQ y

# Network: SFP/PHYLINK/MDIO must be =y (not =m) for TFTP dev boot
scripts/config --set-val CONFIG_PHYLINK y
scripts/config --set-val CONFIG_SFP y
scripts/config --set-val CONFIG_MDIO_I2C y
scripts/config --set-val CONFIG_FSL_XGMAC_MDIO y

# Peripherals
scripts/config --set-val CONFIG_SPI_FSL_QUADSPI y
scripts/config --set-val CONFIG_I2C_IMX y
scripts/config --set-val CONFIG_GPIO_MPC8XXX y
scripts/config --set-val CONFIG_IMX2_WDT y
scripts/config --set-val CONFIG_SERIAL_OF_PLATFORM y

# SDK DPAA1 drivers (ensure they survived defconfig processing)
scripts/config --set-val CONFIG_FSL_SDK_DPA y
scripts/config --set-val CONFIG_FSL_SDK_BMAN y
scripts/config --set-val CONFIG_FSL_SDK_QMAN y
scripts/config --set-val CONFIG_FSL_SDK_FMAN y
scripts/config --set-val CONFIG_FSL_SDK_DPAA_ETH y
scripts/config --set-val CONFIG_STAGING y

# Diagnostic: dump critical config values
echo "=== SDK+ASK: Critical config check ==="
for sym in DEVTMPFS DEVTMPFS_MOUNT MMC MMC_SDHCI MMC_SDHCI_OF_ESDHC \
           EXT4_FS SQUASHFS OVERLAY_FS ARCH_LAYERSCAPE CLK_QORIQ \
           FSL_SDK_FMAN FSL_SDK_DPAA_ETH FSL_FMAN FSL_DPAA PHYLINK SFP \
           SERIAL_OF_PLATFORM FORTIFY_SOURCE; do
  val=$(grep "^CONFIG_${sym}=\|^# CONFIG_${sym} is not set" .config 2>/dev/null || echo "  NOT FOUND")
  echo "  CONFIG_${sym}: ${val}"
done
echo "=== SDK+ASK: Config override complete ==="
CONFIG_OVERRIDE_EOF

# Insert config overrides after "Generate environment file" in build-kernel.sh
# This line runs immediately after "make vyos_defconfig" writes .config
sed -i '/Generate environment file/r /tmp/kernel-config-override.sh' "$KERNEL_BUILD/build-kernel.sh"
rm -f /tmp/kernel-config-override.sh

echo "### SDK+ASK kernel setup complete"
