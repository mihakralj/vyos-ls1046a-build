#!/bin/bash
# ci-setup-kernel.sh — Kernel config overrides and build-kernel.sh injection
# Called by: .github/workflows/auto-build.yml "Setup kernel config" step
# Expects: GITHUB_WORKSPACE set
set -ex
cd "${GITHUB_WORKSPACE:-.}"

### LS1046A kernel config (DPAA1/FMan networking, eMMC, serial, MTD/SPI for FMan firmware)
DEFCONFIG=vyos-build/scripts/package-build/linux-kernel/config/arm64/vyos_defconfig

# Remove upstream explicit disables that conflict with our overrides.
# kconfig defconfig processing doesn't reliably let later entries win
# when an earlier "# CONFIG_X is not set" is present.  Removing conflicting
# lines before appending ensures our values stick after make vyos_defconfig.
sed -i '/CONFIG_DEVTMPFS_MOUNT/d'          "$DEFCONFIG"
sed -i '/CONFIG_CPU_FREQ_DEFAULT_GOV/d'     "$DEFCONFIG"
sed -i '/CONFIG_STRICT_DEVMEM/d'            "$DEFCONFIG"
sed -i '/CONFIG_IO_STRICT_DEVMEM/d'         "$DEFCONFIG"
sed -i '/CONFIG_DEBUG_PREEMPT/d'            "$DEFCONFIG"

# Append all LS1046A kernel config fragments
for frag in data/kernel-config/ls1046a-*.config; do
  echo "### Appending kernel config fragment: $(basename "$frag")"
  cat "$frag" >> "$DEFCONFIG"
done

### USDPAA kernel patches: BMan/QMan exports + /dev/fsl-usdpaa chardev
# Single combined patch: exports BMan/QMan symbols, adds portal
# reservation, and adds Kconfig/Makefile for fsl_usdpaa_mainline.
# fsl_usdpaa_mainline.c is copied separately (1453 lines, too large
# for a unified diff). DTS reserved-memory is in mono-gateway-dk.dts.
KERNEL_BUILD=vyos-build/scripts/package-build/linux-kernel
KERNEL_PATCHES="$KERNEL_BUILD/patches/kernel"
mkdir -p "$KERNEL_PATCHES"
cp data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch "$KERNEL_PATCHES/"
cp data/kernel-patches/4003-sfp-rollball-phylink-einval-fallback.patch "$KERNEL_PATCHES/"
cp data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch "$KERNEL_PATCHES/"

# Stage the USDPAA source file for copy into kernel tree during build.
# build-kernel.sh applies patches from patches/kernel/ then runs make.
# We inject a cp command after the patch loop to place the .c file.
cp data/kernel-patches/fsl_usdpaa_mainline.c "$KERNEL_BUILD/"
cp data/kernel-patches/patch-phylink.py "$KERNEL_BUILD/"

# Inject .c file copy before "# Change name of Signing Cert" in build-kernel.sh.
awk '/# Change name of Signing Cert/ {
  print "# Copy USDPAA mainline driver source (too large for unified diff)"
  print "if [ -f \"${CWD}/fsl_usdpaa_mainline.c\" ]; then"
  print "  echo \"I: Copy fsl_usdpaa_mainline.c to drivers/soc/fsl/qbman/\""
  print "  cp \"${CWD}/fsl_usdpaa_mainline.c\" drivers/soc/fsl/qbman/fsl_usdpaa_mainline.c"
  print "fi"
  print ""
  print "# Patch phylink: trust SFP link over PCS in INBAND mode (LS1046A XFI regression)"
  print "PHYLINK_C=$(find . -path \"*/net/phylink.c\" -maxdepth 4 | head -1)"
  print "if [ -n \"$PHYLINK_C\" ] && [ -f \"${CWD}/patch-phylink.py\" ]; then"
  print "  python3 \"${CWD}/patch-phylink.py\" \"$PHYLINK_C\""
  print "fi"
} { print }' "$KERNEL_BUILD/build-kernel.sh" > /tmp/build-kernel-patched.sh
mv /tmp/build-kernel-patched.sh "$KERNEL_BUILD/build-kernel.sh"
chmod +x "$KERNEL_BUILD/build-kernel.sh"

echo "### Kernel setup complete"
