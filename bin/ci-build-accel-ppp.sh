#!/bin/bash
# ci-build-accel-ppp.sh — Build accel-ppp-ng ARM64 packages (daemon + kernel modules)
#
# Called from: ci-build-packages.sh, after kernel build while $KSRC still exists
# Expects: GITHUB_WORKSPACE set, kernel source tree at $1 (absolute path)
#
# Produces: accel-ppp-ng_*_arm64.deb (daemon + ipoe.ko + vlan_mon.ko)
#           copied into the calling directory (linux-kernel package-build dir)
#
# Why this exists: VyOS upstream build-accel-ppp-ng.sh requires building VPP
# from source first (cd ../vpp/ && ./build.py). This ALWAYS fails on ARM64
# because VPP source libraries aren't available in the ARM64 builder image.
# Our script builds accel-ppp-ng without VPP plugin support (VPP itself stays
# in the ISO as a pre-built package — only the optional PPPoE→VPP fast-path
# plugin is skipped).
#
# Source: github.com/accel-ppp/accel-ppp-ng (same repo as VyOS's package.toml)
set -xo pipefail
# NOTE: intentionally NOT using set -e. We handle errors explicitly below
# to provide clear diagnostics instead of silent failures.

KSRC_ABS="${1:?Usage: ci-build-accel-ppp.sh /path/to/kernel-source}"
DEST_DIR="${2:-$(pwd)}"
WORKSPACE="${GITHUB_WORKSPACE:-.}"

# Match VyOS upstream package.toml commit
ACCEL_COMMIT="3e30d9b"
ACCEL_SRC=""

### Locate accel-ppp-ng source — reuse build.py's clone if available
# build.py clones into the linux-kernel package-build dir
BUILDPY_CLONE="$DEST_DIR/accel-ppp-ng"
WORKSPACE_CLONE="$WORKSPACE/accel-ppp-ng"

if [ -d "$BUILDPY_CLONE/.git" ]; then
  echo "### Reusing build.py's accel-ppp-ng clone at $BUILDPY_CLONE"
  ACCEL_SRC="$BUILDPY_CLONE"
elif [ -d "$WORKSPACE_CLONE/.git" ]; then
  echo "### Reusing workspace accel-ppp-ng clone at $WORKSPACE_CLONE"
  ACCEL_SRC="$WORKSPACE_CLONE"
else
  echo "### Cloning accel-ppp-ng source"
  if git clone --depth=50 https://github.com/accel-ppp/accel-ppp-ng.git "$WORKSPACE_CLONE" 2>&1; then
    ACCEL_SRC="$WORKSPACE_CLONE"
  else
    echo "ERROR: Failed to clone accel-ppp-ng repository"
    exit 1
  fi
fi

cd "$ACCEL_SRC"
git reset --hard HEAD 2>/dev/null || true
git clean --force -d -x 2>/dev/null || true
git checkout "$ACCEL_COMMIT" 2>/dev/null || echo "WARNING: commit $ACCEL_COMMIT not found, using HEAD"

### Extract kernel version from built linux-image .deb
KIMAGE_DEB=$(find "$DEST_DIR" -maxdepth 1 -name 'linux-image-*.deb' ! -name '*-dbg*' | head -1)
if [ -z "$KIMAGE_DEB" ]; then
  echo "ERROR: No linux-image .deb found in $DEST_DIR — kernel must be built first"
  exit 1
fi
KVER=$(basename "$KIMAGE_DEB" | sed 's/^linux-image-//; s/_.*$//')
echo "### Kernel version for module ABI matching: $KVER"

### Prepare kernel tree for out-of-tree module builds
echo "### Preparing kernel tree for module builds"
make -C "$KSRC_ABS" modules_prepare ARCH=arm64 2>&1 | tail -5 || true

### Install build dependencies (install individually so one failure doesn't block others)
echo "### Installing accel-ppp-ng build dependencies"
apt-get update -qq 2>/dev/null || true

# These are critical — cmake WILL fail without them
CRITICAL_DEPS="cmake libpcre2-dev libssl-dev"
for dep in $CRITICAL_DEPS; do
  if ! dpkg -l "$dep" 2>/dev/null | grep -q '^ii'; then
    echo "### Installing critical dependency: $dep"
    apt-get install -y --no-install-recommends "$dep" 2>&1 || {
      echo "ERROR: Failed to install critical dependency: $dep"
      echo "  cmake requires libpcre2-dev (PCRE2) and libssl-dev"
      echo "  (Note: VyOS's package.toml only installs libpcre3-dev = PCRE1)"
      exit 1
    }
  fi
done

# Optional deps — nice to have but not fatal
for dep in libsnmp-dev liblua5.3-dev libnl-genl-3-dev; do
  dpkg -l "$dep" 2>/dev/null | grep -q '^ii' || \
    apt-get install -y --no-install-recommends "$dep" 2>/dev/null || \
    echo "WARNING: optional dependency $dep not available"
done

### Verify critical libraries exist before cmake
for lib in pcre2-8 ssl; do
  if ! find /usr/lib -name "lib${lib}*.so" -o -name "lib${lib}*.so.*" 2>/dev/null | head -1 | grep -q .; then
    echo "ERROR: lib${lib} not found — cmake will fail"
    exit 1
  fi
done
echo "### Dependencies verified: libpcre2-8 and libssl present"

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
rm -rf "$ACCEL_SRC/build"
mkdir -p "$ACCEL_SRC/build"
cd "$ACCEL_SRC/build"

# No VPP plugin on ARM64 — VPP build-from-source isn't available
# VPP itself remains in the ISO as a pre-built package
echo "### cmake: VPP plugin DISABLED (ARM64 — no VPP source build)"

if ! cmake -DBUILD_IPOE_DRIVER=TRUE \
    -DBUILD_VLAN_MON_DRIVER=TRUE \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DKDIR="$KSRC_ABS" \
    -DLUA=5.3 \
    -DMODULES_KDIR="$KVER" \
    -DCPACK_TYPE=Debian12 \
    .. 2>&1; then
  echo ""
  echo "### cmake configuration FAILED. Diagnostics:"
  echo "  KSRC_ABS=$KSRC_ABS"
  echo "  KVER=$KVER"
  echo "  ACCEL_SRC=$ACCEL_SRC"
  dpkg -l libpcre2-dev libssl-dev 2>/dev/null || true
  find /usr/lib -name "libpcre2*" 2>/dev/null || true
  exit 1
fi

echo "### Building accel-ppp-ng"
if ! make -j$(nproc) 2>&1; then
  echo ""
  echo "### make FAILED. This is likely a kernel module build failure."
  echo "### Retrying WITHOUT kernel modules (daemon-only)..."
  rm -rf "$ACCEL_SRC/build"
  mkdir -p "$ACCEL_SRC/build"
  cd "$ACCEL_SRC/build"
  cmake -DBUILD_IPOE_DRIVER=FALSE \
      -DBUILD_VLAN_MON_DRIVER=FALSE \
      -DCMAKE_INSTALL_PREFIX=/usr \
      -DLUA=5.3 \
      -DCPACK_TYPE=Debian12 \
      .. 2>&1 || { echo "ERROR: cmake daemon-only also failed"; exit 1; }
  make -j$(nproc) 2>&1 || { echo "ERROR: make daemon-only also failed"; exit 1; }
  echo "### Built daemon-only (no ipoe.ko/vlan_mon.ko kernel modules)"
fi

### Sign kernel modules if sign-modules.sh is available
SIGN_SCRIPT="$WORKSPACE/vyos-build/scripts/package-build/linux-kernel/sign-modules.sh"
if [ -x "$SIGN_SCRIPT" ]; then
  echo "### Signing kernel modules"
  "$SIGN_SCRIPT" . || echo "WARNING: Module signing failed (continuing)"
fi

### Package with cpack
echo "### Creating .deb with cpack"
if ! cpack -G DEB 2>&1; then
  echo "ERROR: cpack -G DEB failed"
  ls -la *.deb 2>/dev/null || true
  exit 1
fi

### Rename and collect output
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