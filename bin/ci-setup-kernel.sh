#!/bin/bash
# ci-setup-kernel.sh — Kernel config overrides and build-kernel.sh injection
# Called by: .github/workflows/auto-build.yml "Setup kernel config" step
# Expects: GITHUB_WORKSPACE set
#
# When ASK_KERNEL_TAG is set, this script is a no-op: the kernel is consumed
# prebuilt from mihakralj/lts_6.6_ls1046a via bin/ci-consume-ask-kernel.sh,
# so defconfig mutations and build-kernel.sh injections are meaningless.
set -ex
cd "${GITHUB_WORKSPACE:-.}"

if [ -n "${ASK_KERNEL_TAG:-}" ]; then
    echo "### ASK kernel in effect ($ASK_KERNEL_TAG) — skipping kernel defconfig/patches/injection"
    exit 0
fi

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
# NOTE: ls1046a-sdk.config and ls1046a-ask.config are SDK+ASK only (ci-setup-kernel-sdk.sh)
for frag in data/kernel-config/ls1046a-*.config; do
  case "$(basename "$frag")" in
    ls1046a-sdk.config|ls1046a-ask.config) continue ;;
  esac
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

# Stage DPAA XDP queue_index fix for AF_XDP socket lookup
cp data/kernel-patches/patch-dpaa-xdp-queue-index.py "$KERNEL_BUILD/"

# Stage FMD Shim source for injection into build-kernel.sh
cp data/kernel-patches/fsl_fmd_shim.c "$KERNEL_BUILD/"

# Stage LP5812 LED driver source for injection into build-kernel.sh
cp -r data/kernel-patches/lp5812 "$KERNEL_BUILD/"

# Write injection block to temp file (heredoc avoids all quoting issues)
cat > /tmp/kernel-inject.sh << 'INJECT_EOF'

# Patch phylink: trust SFP link over PCS in INBAND mode (LS1046A XFI regression)
PHYLINK_C=$(find . -path "*/net/phylink.c" -maxdepth 4 | head -1)
if [ -n "$PHYLINK_C" ] && [ -f "${CWD}/patch-phylink.py" ]; then
  python3 "${CWD}/patch-phylink.py" "$PHYLINK_C"
fi

# Fix DPAA xdp_rxq_info queue_index: FQID (32768+) exceeds XSKMAP max_entries
# Without this, AF_XDP RX is completely non-functional on DPAA interfaces
DPAA_ETH_C=$(find . -path "*/freescale/dpaa/dpaa_eth.c" -maxdepth 6 | head -1)
if [ -n "$DPAA_ETH_C" ] && [ -f "${CWD}/patch-dpaa-xdp-queue-index.py" ]; then
  python3 "${CWD}/patch-dpaa-xdp-queue-index.py" "$DPAA_ETH_C"
fi

# FMD Shim: inject /dev/fm0* chardev module for DPDK fmlib RSS
if [ -f "${CWD}/fsl_fmd_shim.c" ]; then
  FMD_DIR=drivers/soc/fsl/fmd_shim
  mkdir -p "$FMD_DIR"
  cp "${CWD}/fsl_fmd_shim.c" "$FMD_DIR/"
  cat > "$FMD_DIR/Kconfig" <<-KEOF
	config FSL_FMD_SHIM
		bool "FMD Shim chardev for DPDK fmlib FMan RSS"
		depends on FSL_FMAN
		default y
		help
		  Minimal character device driver that creates /dev/fm0,
		  /dev/fm0-pcd, and /dev/fm0-port-rxN devices for the
		  DPDK DPAA PMD fmlib library to program FMan KeyGen RSS.
		  Safe to enable -- completely passive until ioctls called.
	KEOF
  echo 'obj-$(CONFIG_FSL_FMD_SHIM) += fsl_fmd_shim.o' > "$FMD_DIR/Makefile"
  # Hook into parent Kconfig and Makefile
  if ! grep -q fmd_shim drivers/soc/fsl/Kconfig 2>/dev/null; then
    echo 'source "drivers/soc/fsl/fmd_shim/Kconfig"' >> drivers/soc/fsl/Kconfig
  fi
  if ! grep -q fmd_shim drivers/soc/fsl/Makefile 2>/dev/null; then
    echo 'obj-$(CONFIG_FSL_FMD_SHIM) += fmd_shim/' >> drivers/soc/fsl/Makefile
  fi
  echo "FMD Shim: injected into $FMD_DIR"
fi

# LP5812: inject TI LP5812 I2C LED controller driver (out-of-tree, not in mainline 6.6)
if [ -d "${CWD}/lp5812" ]; then
  LP5812_DIR=drivers/leds/lp5812
  mkdir -p "$LP5812_DIR"
  cp "${CWD}/lp5812/leds-lp5812.c" "$LP5812_DIR/"
  cp "${CWD}/lp5812/leds-lp5812.h" "$LP5812_DIR/"
  cat > "$LP5812_DIR/Kconfig" <<-KEOF
	config LEDS_LP5812
		bool "LED Support for TI LP5812 I2C LED controller"
		depends on LEDS_CLASS && I2C && LEDS_CLASS_MULTICOLOR
		default y
		help
		  TI LP5812 12-channel I2C LED controller with per-LED
		  analog and PWM dimming. Used on Mono Gateway DK for
		  4 status indicator LEDs (white/blue/green/red).
	KEOF
  echo 'obj-$(CONFIG_LEDS_LP5812) += leds-lp5812.o' > "$LP5812_DIR/Makefile"
  # Hook into parent Kconfig and Makefile
  if ! grep -q lp5812 drivers/leds/Kconfig 2>/dev/null; then
    echo 'source "drivers/leds/lp5812/Kconfig"' >> drivers/leds/Kconfig
  fi
  if ! grep -q lp5812 drivers/leds/Makefile 2>/dev/null; then
    echo 'obj-$(CONFIG_LEDS_LP5812) += lp5812/' >> drivers/leds/Makefile
  fi
  echo "LP5812: injected into $LP5812_DIR"
fi
INJECT_EOF

# Insert injection block before "# Change name of Signing Cert" in build-kernel.sh
sed -i '/# Change name of Signing Cert/r /tmp/kernel-inject.sh' "$KERNEL_BUILD/build-kernel.sh"
rm -f /tmp/kernel-inject.sh

echo "### Kernel setup complete"