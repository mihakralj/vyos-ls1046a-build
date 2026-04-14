#!/bin/bash
# ci-setup-kernel-ask.sh — Inject ASK fast-path into VyOS LS1046A kernel build
#
# Called AFTER ci-setup-kernel.sh to layer ASK on top of the base LS1046A config.
# This script:
#   1. Appends ASK kernel config (disables mainline DPAA ETH, enables SDK DPAA)
#   2. Copies the ASK kernel hooks patch to the kernel patches dir
#   3. Stages SDK sources tarball + injection block into build-kernel.sh
#
# Build-kernel.sh flow (relevant lines):
#   L12:  cd ${KERNEL_SRC}
#   L44:  cat config snippets >> KERNEL_CONFIG
#   L50:  PATCH_DIR=... ; for patch in ... ; patch -p1  <-- patches applied here
#   L58:  (injection: copy fsl_usdpaa_mainline)
#   L62:  # Change name of Signing Cert  <-- ci-setup-kernel.sh injects here
#
# SDK sources must be extracted BEFORE L50 (patches loop) because the
# hooks patch modifies Makefiles/Kconfigs that reference sdk_dpaa/sdk_fman.
#
# WARNING: ASK replaces mainline DPAA ETH with NXP SDK DPAA ETH.
#          AF_XDP/VPP path is NOT compatible — ASK uses its own fast-path.
#          SFP phylink is NOT available in SDK mode (use fixed-link DTS).

set -ex
cd "${GITHUB_WORKSPACE:-.}"

ASK_REPO="${ASK_REPO:-ask-ls1046a-6.6}"

# Verify ASK repo is present (submodule or cloned)
if [ ! -d "$ASK_REPO/patches/kernel" ]; then
  echo "ERROR: ASK repo not found at $ASK_REPO/"
  echo "       Clone it: git clone https://github.com/mihakralj/ask-ls1046a-6.6.git"
  exit 1
fi

### 1. Kernel config — append ASK fragment and handle conflicts
DEFCONFIG=vyos-build/scripts/package-build/linux-kernel/config/arm64/vyos_defconfig

# Remove mainline DPAA ETH — ASK SDK replaces it
sed -i '/CONFIG_FSL_DPAA_ETH/d' "$DEFCONFIG"

# Append ASK config fragment
echo "### Appending ASK kernel config fragment"
cat data/kernel-config/ls1046a-ask.config >> "$DEFCONFIG"

### 2. Kernel hooks patch — copy to patches dir (applied by build-kernel.sh L50-55)
KERNEL_BUILD=vyos-build/scripts/package-build/linux-kernel
KERNEL_PATCHES="$KERNEL_BUILD/patches/kernel"
mkdir -p "$KERNEL_PATCHES"

# Use our local fixed copy (has curr_time declaration in struct xfrm_state)
# NOT the ASK repo copy which is missing this field
cp data/kernel-patches/003-ask-kernel-hooks.patch "$KERNEL_PATCHES/"
# swphy patch: maps 10G/5G/2.5G to SWMII_SPEED_1000 for SDK fixed-link 10G MACs
cp data/kernel-patches/4004-swphy-support-10g-fixed-link-speed.patch "$KERNEL_PATCHES/"
echo "### ASK hooks + swphy patches staged at $KERNEL_PATCHES/"

### 3. SDK sources — stage tarball and inject extraction BEFORE patches loop
#
# The hooks patch (003-ask-kernel-hooks.patch) modifies Makefiles and Kconfigs
# that reference sdk_dpaa/, sdk_fman/, and fsl_qbman/ directories.
# These must exist in the kernel tree BEFORE patches are applied.
#
# Injection anchor: "PATCH_DIR=" line in build-kernel.sh (line 50).
# We insert the tarball extraction immediately BEFORE this line.

cp data/kernel-patches/ask-nxp-sdk-sources.tar.gz "$KERNEL_BUILD/"

cat > /tmp/ask-inject.sh << 'ASK_INJECT_EOF'

# ASK: Extract NXP SDK driver sources into kernel tree
# These files (sdk_dpaa, sdk_fman, fsl_qbman) don't exist in mainline Linux.
# Must be present before patches are applied (hooks patch references these dirs).
if [ -f "${CWD}/ask-nxp-sdk-sources.tar.gz" ]; then
  echo "I: ASK — Extracting NXP SDK sources (67 files)..."
  tar xzf "${CWD}/ask-nxp-sdk-sources.tar.gz" --no-same-owner

  # Wire SDK Kconfig/Makefile into kernel build system
  # (SDK directories are standalone — no parent-level source/obj entries)
  grep -q 'fsl_qbman/Kconfig' drivers/staging/Kconfig 2>/dev/null || \
    echo 'source "drivers/staging/fsl_qbman/Kconfig"' >> drivers/staging/Kconfig
  grep -q 'fsl_qbman/' drivers/staging/Makefile 2>/dev/null || \
    echo 'obj-y += fsl_qbman/' >> drivers/staging/Makefile
  grep -q 'sdk_fman/Kconfig' drivers/net/ethernet/freescale/Kconfig 2>/dev/null || \
    echo 'source "drivers/net/ethernet/freescale/sdk_fman/Kconfig"' >> drivers/net/ethernet/freescale/Kconfig
  grep -q 'sdk_dpaa/Kconfig' drivers/net/ethernet/freescale/Kconfig 2>/dev/null || \
    echo 'source "drivers/net/ethernet/freescale/sdk_dpaa/Kconfig"' >> drivers/net/ethernet/freescale/Kconfig
  grep -q 'sdk_fman/' drivers/net/ethernet/freescale/Makefile 2>/dev/null || \
    echo 'obj-$(CONFIG_FSL_SDK_FMAN) += sdk_fman/' >> drivers/net/ethernet/freescale/Makefile
  grep -q 'sdk_dpaa/' drivers/net/ethernet/freescale/Makefile 2>/dev/null || \
    echo 'obj-$(CONFIG_FSL_SDK_DPAA_ETH) += sdk_dpaa/' >> drivers/net/ethernet/freescale/Makefile

  # Enable enhanced ehash PCD ioctl handlers (required for dpa_app/FMC to program FMan)
  # Without this, LnxwrpFmIOCTL only handles basic FM ioctls — all PCD ioctls
  # (NetEnvSet, KgSchemeSet, CcRootBuild, etc.) silently return success with NULL handles.
  sed -i 's/ccflags-y.*+= -DVERSION=\\"\\"/& -DUSE_ENHANCED_EHASH/' \
    drivers/net/ethernet/freescale/sdk_fman/Makefile
  echo "I: ASK — USE_ENHANCED_EHASH enabled in sdk_fman Makefile"

  echo "I: ASK — SDK sources + build integration injected into kernel tree"
fi

ASK_INJECT_EOF

# Insert BEFORE the patch loop (anchor: "PATCH_DIR=${CWD}")
# sed 'i' inserts BEFORE the matching line
sed -i '/^PATCH_DIR=\${CWD}\/patches\/kernel/r /tmp/ask-inject.sh' "$KERNEL_BUILD/build-kernel.sh"

# Verify injection succeeded
if grep -q 'ask-nxp-sdk-sources' "$KERNEL_BUILD/build-kernel.sh"; then
  echo "### ASK SDK extraction injected into build-kernel.sh"
else
  echo "WARNING: Failed to inject ASK SDK extraction — anchor line not found"
  echo "         Manually add tarball extraction before patch loop in build-kernel.sh"
fi

rm -f /tmp/ask-inject.sh

### 4. Post-defconfig config override — force ASK/LS1046A configs AFTER VyOS snippets
#
# Problem: build-kernel.sh appends VyOS config/*.config snippets to the defconfig
# AFTER our custom fragments. These snippets override critical settings:
#   - CONFIG_DEVTMPFS_MOUNT=y → disabled (causes /dev/console warning)
#   - CONFIG_USB_STORAGE=y → =m (module instead of built-in)
#   - CONFIG_SQUASHFS=y → =m, CONFIG_OVERLAY_FS=y → =m
# Additionally, SDK Kconfig symbols may be silently dropped during `make vyos_defconfig`
# if the Kconfig wiring order doesn't match dependency resolution expectations.
#
# Fix: inject `scripts/config` commands AFTER `make vyos_defconfig` to force all
# critical configs, then re-run `make olddefconfig` to resolve dependencies.
#
# Anchor: the "make vyos_defconfig" line in build-kernel.sh
#
cat > /tmp/ask-post-defconfig.sh << 'ASK_POSTDEFCONFIG_EOF'

# ASK: Force critical kernel configs after make vyos_defconfig
# VyOS config snippets override our defconfig settings — re-apply them here.
echo "I: ASK — Forcing critical kernel configs after vyos_defconfig"

# --- Disable mainline DPAA (mutual exclusion with SDK) ---
scripts/config --disable CONFIG_FSL_DPAA
scripts/config --disable CONFIG_FSL_FMAN
scripts/config --disable CONFIG_FSL_DPAA_ETH
scripts/config --enable CONFIG_FSL_XGMAC_MDIO

# --- Staging drivers (SDK QBMan is in drivers/staging/fsl_qbman/) ---
scripts/config --enable CONFIG_STAGING

# --- Enable SDK DPAA stack (order matters: DPA first, then BMAN/QMAN, then FMAN, then ETH) ---
scripts/config --enable CONFIG_FSL_SDK_DPA
scripts/config --enable CONFIG_FSL_SDK_BMAN
scripts/config --enable CONFIG_FSL_SDK_QMAN
scripts/config --enable CONFIG_FSL_BMAN_CONFIG
scripts/config --enable CONFIG_FSL_QMAN_CONFIG
scripts/config --enable CONFIG_FSL_SDK_FMAN
scripts/config --set-val CONFIG_FSL_SDK_DPAA_ETH y
scripts/config --enable CONFIG_FSL_DPAA_HOOKS
scripts/config --enable CONFIG_FSL_DPAA_1588
scripts/config --set-val CONFIG_FSL_DPAA_ETH_MAX_BUF_COUNT 640
scripts/config --enable CONFIG_FMAN_ARM

# --- ASK fast-path ---
scripts/config --enable CONFIG_CPE_FAST_PATH

# --- IPsec offload ---
scripts/config --enable CONFIG_INET_IPSEC_OFFLOAD
scripts/config --enable CONFIG_INET6_IPSEC_OFFLOAD
# ipsec_nlkey_flow() is defined in net/key/af_key.c (CONFIG_NET_KEY) but called
# from built-in xfrm_policy.c — must be =y, not =m, to avoid linker error
scripts/config --set-val CONFIG_NET_KEY y

# --- ASK netfilter extensions ---
scripts/config --enable CONFIG_NETFILTER_XT_QOSMARK
scripts/config --enable CONFIG_NETFILTER_XT_QOSCONNMARK

# --- CAAM crypto (built-in for IPsec offload) ---
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_JR y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_COMMON y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_CRYPTO_API_DESC y
scripts/config --set-val CONFIG_CRYPTO_DEV_FSL_CAAM_AHASH_API_DESC y

# --- LS1046A board: force built-in for boot-critical configs ---
scripts/config --enable CONFIG_DEVTMPFS_MOUNT
scripts/config --set-val CONFIG_USB_STORAGE y
scripts/config --set-val CONFIG_VFAT_FS y
scripts/config --set-val CONFIG_FAT_FS y
scripts/config --set-val CONFIG_NLS_CODEPAGE_437 y
scripts/config --set-val CONFIG_NLS_ISO8859_1 y
scripts/config --set-val CONFIG_NLS_UTF8 y
scripts/config --set-val CONFIG_SQUASHFS y
scripts/config --set-val CONFIG_OVERLAY_FS y
scripts/config --set-val CONFIG_QORIQ_CPUFREQ y
scripts/config --set-val CONFIG_FSL_EDMA y
scripts/config --set-val CONFIG_SERIAL_OF_PLATFORM y
scripts/config --set-val CONFIG_MAXLINEAR_GPHY y
scripts/config --set-val CONFIG_IMX2_WDT y
scripts/config --set-val CONFIG_SPI_FSL_QUADSPI y
scripts/config --enable CONFIG_MODVERSIONS
scripts/config --disable CONFIG_DEBUG_PREEMPT

# --- LED subsystem + LP5812 (built-in for status indicators) ---
scripts/config --set-val CONFIG_NEW_LEDS y
scripts/config --set-val CONFIG_LEDS_CLASS y
scripts/config --set-val CONFIG_LEDS_CLASS_MULTICOLOR y
scripts/config --set-val CONFIG_LEDS_GPIO y
scripts/config --set-val CONFIG_LEDS_LP5812 y
scripts/config --set-val CONFIG_LEDS_TRIGGERS y
scripts/config --set-val CONFIG_LEDS_TRIGGER_NETDEV y

# --- Resolve dependencies after forced overrides ---
echo "I: ASK — Re-running olddefconfig to resolve dependencies"
make olddefconfig

# --- Verify critical configs ---
echo "I: ASK — Verifying critical kernel configs:"
for sym in STAGING FSL_SDK_DPA FSL_SDK_BMAN FSL_SDK_QMAN FSL_BMAN_CONFIG \
           FSL_QMAN_CONFIG FSL_SDK_FMAN FSL_SDK_DPAA_ETH FSL_XGMAC_MDIO \
           CPE_FAST_PATH NET_KEY INET_IPSEC_OFFLOAD INET6_IPSEC_OFFLOAD \
           DEVTMPFS_MOUNT USB_STORAGE SQUASHFS OVERLAY_FS LEDS_LP5812 MAXLINEAR_GPHY; do
  val=$(scripts/config --state "CONFIG_${sym}" 2>/dev/null || echo "UNKNOWN")
  echo "   CONFIG_${sym}=${val}"
done

ASK_POSTDEFCONFIG_EOF

# Insert AFTER "make vyos_defconfig" line in build-kernel.sh
sed -i '/^make vyos_defconfig$/r /tmp/ask-post-defconfig.sh' "$KERNEL_BUILD/build-kernel.sh"

# Verify injection succeeded
if grep -q 'ASK.*Forcing critical kernel configs' "$KERNEL_BUILD/build-kernel.sh"; then
  echo "### ASK post-defconfig override injected into build-kernel.sh"
else
  echo "WARNING: Failed to inject post-defconfig override — 'make vyos_defconfig' line not found"
  echo "         The kernel build may produce incorrect configs"
fi

rm -f /tmp/ask-post-defconfig.sh

### 5. SDK DTS — copy to data/dtb/ so ci-build-packages.sh can find and build it
# The SDK DTS uses fixed-link for 10G MACs (SDK fsl_mac has no phylink/SFP support)
# and deletes managed/sfp properties. ci-build-packages.sh checks for this file
# at $DTS_DIR/mono-gateway-dk-sdk.dts and builds the SDK DTB if present.
if [ -f archive/data/dtb/mono-gateway-dk-sdk.dts ]; then
  cp archive/data/dtb/mono-gateway-dk-sdk.dts data/dtb/mono-gateway-dk-sdk.dts
  echo "### SDK DTS copied to data/dtb/ for kernel DTB build"
fi

echo ""
echo "### ASK kernel setup complete"
echo "###"
echo "### The VyOS kernel build will now include:"
echo "###   - NXP SDK DPAA drivers (sdk_dpaa, sdk_fman) replacing mainline DPAA ETH"
echo "###   - ASK fast-path hooks in netfilter, bridge, xfrm, net/core (74 files)"
echo "###   - CONFIG_CPE_FAST_PATH=y, CONFIG_FSL_SDK_DPA=y"
echo "###   - IPsec hardware offload, CEETM QoS, xt_QOSMARK"
echo "###"
echo "### After ISO build, also compile out-of-tree ASK modules:"
echo "###   cd $ASK_REPO && make -C cdx KDIR=/path/to/kernel-headers"
echo "###   cd $ASK_REPO && make -C fci KDIR=/path/to/kernel-headers"
echo "###   cd $ASK_REPO && make -C auto_bridge KDIR=/path/to/kernel-headers"