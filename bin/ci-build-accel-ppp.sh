#!/bin/bash
# ci-build-accel-ppp.sh — Build accel-ppp-ng ARM64 packages (daemon + kernel modules)
#
# Called from: ci-build-packages.sh, after kernel build while $KSRC still exists
# Expects: GITHUB_WORKSPACE set, kernel source tree at $1 (absolute path)
#
# Produces: accel-ppp-ng_*_arm64.deb (daemon + ipoe.ko + vlan_mon.ko)
#           copied into the calling directory (linux-kernel package-build dir)
#
# Approach: Mirrors VyOS upstream build-accel-ppp-ng.sh (cmake + cpack -G DEB)
#           Attempts VPP plugin if dev headers available, falls back without
#
# Source: github.com/accel-ppp/accel-ppp-ng  (same repo VyOS uses in package.toml)
#
# Reference: plans/ACCEL-PPP-ARM64.md
set -exo pipefail

KSRC_ABS="${1:?Usage: ci-build-accel-ppp.sh /path/to/kernel-source}"
DEST_DIR="${2:-$(pwd)}"
WORKSPACE="${GITHUB_WORKSPACE:-.}"

# Same commit VyOS uses in vyos-build/scripts/package-build/linux-kernel/package.toml
ACCEL_COMMIT="64e351d"
ACCEL_SRC="$WORKSPACE/accel-ppp-ng"

### Preflight checks
if [ ! -d "$KSRC_ABS" ]; then
  echo "ERROR: Kernel source not found at $KSRC_ABS"
  exit 1
fi

### Clone accel-ppp-ng source (VyOS's own fork, not vyos-accel-ppp)
if [ ! -d "$ACCEL_SRC" ]; then
  echo "### Cloning accel-ppp-ng source"
  git clone https://github.com/accel-ppp/accel-ppp-ng.git "$ACCEL_SRC"
fi
cd "$ACCEL_SRC"
git checkout "$ACCEL_COMMIT" 2>/dev/null || echo "WARNING: commit $ACCEL_COMMIT not found, using HEAD"

### Extract kernel version from built linux-image .deb
KIMAGE_DEB=$(find "$DEST_DIR" -maxdepth 1 -name 'linux-image-*.deb' ! -name '*-dbg*' | head -1)
if [ -z "$KIMAGE_DEB" ]; then
  echo "ERROR: No linux-image .deb found in $DEST_DIR — kernel must be built first"
  exit 1
fi

# Extract full kernel version string (e.g., "6.6.133-vyos")
KVER=$(basename "$KIMAGE_DEB" | sed 's/^linux-image-//; s/_.*$//')
echo "### Kernel version for module ABI matching: $KVER"

### Prepare kernel tree for out-of-tree module builds
echo "### Preparing kernel tree for module builds"
make -C "$KSRC_ABS" modules_prepare ARCH=arm64 2>&1 | tail -5 || true

### Install build dependencies
# accel-ppp-ng uses libpcre2 (NOT libpcre3 like the old vyos-accel-ppp)
echo "### Installing accel-ppp-ng build dependencies"
apt-get update -qq 2>/dev/null || true
apt-get install -y --no-install-recommends \
  cmake \
  libpcre2-dev \
  libssl-dev \
  libsnmp-dev \
  liblua5.3-dev \
  libnl-genl-3-dev \
  2>/dev/null || echo "WARNING: Some build deps may be missing"

### Check for VPP development headers (for accel-ppp VPP plugin)
# NOTE: VPP itself is a SEPARATE package that stays in the ISO regardless.
# This only controls whether accel-ppp builds its optional VPP dataplane plugin.
# The VPP plugin allows PPPoE sessions to use VPP's fast-path instead of kernel.
VPP_AVAILABLE=0
if apt-get install -y --no-install-recommends vpp-dev libvppinfra-dev 2>/dev/null; then
  # Check if VPP VAPI headers are actually present
  if [ -f /usr/include/vapi/vapi.h ] || [ -f /usr/include/vpp-api/client/vppapiclient.h ]; then
    VPP_AVAILABLE=1
    echo "### VPP dev headers found — will build with VPP plugin support"
  fi
fi
if [ "$VPP_AVAILABLE" -eq 0 ]; then
  echo "### VPP dev headers not available — building without VPP plugin"
  echo "### (VPP itself remains in the ISO as a separate package)"
fi

### Apply VyOS patches if present
PATCH_DIR="$WORKSPACE/vyos-build/scripts/package-build/linux-kernel/patches/accel-ppp-ng"
if [ -d "$PATCH_DIR" ]; then
  cd "$ACCEL_SRC"
  for patch in "$PATCH_DIR"/*; do
    [ -f "$patch" ] || continue
    echo "I: Apply patch: $(basename "$patch")"
    patch -p1 < "$patch" || echo "WARNING: $(basename "$patch") failed"
  done
fi

### Build with cmake + make + cpack (same approach as VyOS's build-accel-ppp-ng.sh)
echo "### Configuring accel-ppp-ng with cmake"
mkdir -p "$ACCEL_SRC/build"
cd "$ACCEL_SRC/build"

CMAKE_VPP_FLAGS=""
if [ "$VPP_AVAILABLE" -eq 1 ]; then
  CMAKE_VPP_FLAGS="-DHAVE_VPP=1 -DHAVE_SESSION_HOOKS=1"
  echo "### cmake: VPP plugin ENABLED"
fi

cmake -DBUILD_IPOE_DRIVER=TRUE \
    -DBUILD_VLAN_MON_DRIVER=TRUE \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DKDIR="$KSRC_ABS" \
    -DLUA=5.3 \
    -DMODULES_KDIR="$KVER" \
    -DCPACK_TYPE=Debian12 \
    $CMAKE_VPP_FLAGS \
    .. 2>&1

echo "### Building accel-ppp-ng"
make -j$(nproc) 2>&1

### Sign kernel modules if sign-modules.sh is available
SIGN_SCRIPT="$WORKSPACE/vyos-build/scripts/package-build/linux-kernel/sign-modules.sh"
if [ -x "$SIGN_SCRIPT" ]; then
  echo "### Signing kernel modules"
  "$SIGN_SCRIPT" . || echo "WARNING: Module signing failed (continuing)"
fi

### Package with cpack
echo "### Creating .deb with cpack"
cpack -G DEB 2>&1

### Rename and collect output
# cpack produces: accel-ppp-ng.deb (from CPACK_PACKAGE_FILE_NAME)
# VyOS renames to: accel-ppp-ng_<version>_<arch>.deb
ACCEL_VER=$(cd "$ACCEL_SRC" && git describe --always --tags 2>/dev/null || echo "unknown")
ARCH=$(dpkg --print-architecture)
DEBS_FOUND=0

for deb in "$ACCEL_SRC/build/"*.deb; do
  [ -f "$deb" ] || continue
  FINAL_NAME="accel-ppp-ng_${ACCEL_VER}_${ARCH}.deb"
  cp "$deb" "$DEST_DIR/$FINAL_NAME"
  echo "  → $FINAL_NAME → $DEST_DIR/"
  DEBS_FOUND=$((DEBS_FOUND + 1))
done

if [ "$DEBS_FOUND" -eq 0 ]; then
  echo "ERROR: accel-ppp-ng build produced no .deb files"
  exit 1
fi

echo "### accel-ppp-ng ARM64 build complete: $DEBS_FOUND package(s)"