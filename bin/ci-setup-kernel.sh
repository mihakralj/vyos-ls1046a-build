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
sed -i '/CONFIG_DEBUG_PREEMPT/d'            "$DEFCONFIG"

# Append all LS1046A kernel config fragments
# NOTE: ls1046a-usdpaa.config moved to archive/dpaa-pmd/ (DPDK PMD archived)
for frag in data/kernel-config/ls1046a-*.config; do
  echo "### Appending kernel config fragment: $(basename "$frag")"
  cat "$frag" >> "$DEFCONFIG"
done

### Kernel patches (INA234 hwmon, SFP rollball PHY)
KERNEL_BUILD=vyos-build/scripts/package-build/linux-kernel
KERNEL_PATCHES="$KERNEL_BUILD/patches/kernel"
mkdir -p "$KERNEL_PATCHES"
cp data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch "$KERNEL_PATCHES/"
cp data/kernel-patches/4003-sfp-rollball-phylink-einval-fallback.patch "$KERNEL_PATCHES/"

# Stage phylink patch script for injection into build-kernel.sh
cp data/kernel-patches/patch-phylink.py "$KERNEL_BUILD/"

# Stage FMD Shim source for injection into build-kernel.sh
cp data/kernel-patches/fsl_fmd_shim.c "$KERNEL_BUILD/"

# Inject phylink patch + FMD Shim before "# Change name of Signing Cert" in build-kernel.sh.
awk '/# Change name of Signing Cert/ {
  print "# Patch phylink: trust SFP link over PCS in INBAND mode (LS1046A XFI regression)"
  print "PHYLINK_C=$(find . -path \"*/net/phylink.c\" -maxdepth 4 | head -1)"
  print "if [ -n \"$PHYLINK_C\" ] && [ -f \"${CWD}/patch-phylink.py\" ]; then"
  print "  python3 \"${CWD}/patch-phylink.py\" \"$PHYLINK_C\""
  print "fi"
  print ""
  print "# FMD Shim: inject /dev/fm0* chardev module for DPDK fmlib RSS"
  print "if [ -f \"${CWD}/fsl_fmd_shim.c\" ]; then"
  print "  FMD_DIR=drivers/soc/fsl/fmd_shim"
  print "  mkdir -p \"$FMD_DIR\""
  print "  cp \"${CWD}/fsl_fmd_shim.c\" \"$FMD_DIR/\""
  print "  cat > \"$FMD_DIR/Kconfig\" <<KEOF"
  print "config FSL_FMD_SHIM"
  print "\tbool \"FMD Shim chardev for DPDK fmlib FMan RSS\""
  print "\tdepends on FSL_FMAN"
  print "\tdefault y"
  print "\thelp"
  print "\t  Minimal character device driver that creates /dev/fm0,"
  print "\t  /dev/fm0-pcd, and /dev/fm0-port-rxN devices for the"
  print "\t  DPDK DPAA PMD fmlib library to program FMan KeyGen RSS."
  print "\t  Safe to enable -- completely passive until ioctls called."
  print "KEOF"
  print "  echo \"obj-\\$(CONFIG_FSL_FMD_SHIM) += fsl_fmd_shim.o\" > \"$FMD_DIR/Makefile\""
  print "  # Hook into parent Kconfig and Makefile"
  print "  if ! grep -q fmd_shim drivers/soc/fsl/Kconfig 2>/dev/null; then"
  print "    echo 'source \"drivers/soc/fsl/fmd_shim/Kconfig\"' >> drivers/soc/fsl/Kconfig"
  print "  fi"
  print "  if ! grep -q fmd_shim drivers/soc/fsl/Makefile 2>/dev/null; then"
  print "    echo 'obj-$(CONFIG_FSL_FMD_SHIM) += fmd_shim/' >> drivers/soc/fsl/Makefile"
  print "  fi"
  print "  echo \"FMD Shim: injected into $FMD_DIR\""
  print "fi"
} { print }' "$KERNEL_BUILD/build-kernel.sh" > /tmp/build-kernel-patched.sh
mv /tmp/build-kernel-patched.sh "$KERNEL_BUILD/build-kernel.sh"
chmod +x "$KERNEL_BUILD/build-kernel.sh"

echo "### Kernel setup complete"