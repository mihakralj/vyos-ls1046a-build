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

# 8. Force SFP/PHYLINK built-in after all patches (re-resolve dependencies)
scripts/config --set-val CONFIG_PHYLINK y
scripts/config --set-val CONFIG_SFP y
scripts/config --set-val CONFIG_MDIO_I2C y

echo "=== SDK + ASK kernel injection complete ==="
INJECT_EOF

# Insert injection block before "# Change name of Signing Cert" in build-kernel.sh
sed -i '/# Change name of Signing Cert/r /tmp/kernel-inject.sh' "$KERNEL_BUILD/build-kernel.sh"
rm -f /tmp/kernel-inject.sh

echo "### SDK+ASK kernel setup complete"