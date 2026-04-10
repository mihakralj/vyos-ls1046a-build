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

cp "$ASK_REPO/patches/kernel/003-ask-kernel-hooks.patch" "$KERNEL_PATCHES/"
echo "### ASK hooks patch staged at $KERNEL_PATCHES/"

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