#!/bin/bash
# ci-build-accel-ppp.sh — Build accel-ppp-ng ARM64 packages (daemon + kernel modules)
#
# Called from: ci-build-packages.sh, after kernel build while $KSRC still exists
# Expects: GITHUB_WORKSPACE set, kernel source tree at $1 (absolute path)
#
# Produces: accel-ppp-ng_*_arm64.deb + accel-ppp-ng-ipoe-kmod_*_arm64.deb
#           copied into the calling directory (linux-kernel package-build dir)
#
# Sources:
#   github.com/accel-ppp/accel-ppp         — upstream source
#   github.com/vyos/vyos-accel-ppp         — Debian packaging overlay (-ng rename)
#
# Reference: plans/ACCEL-PPP-ARM64.md
set -exo pipefail

KSRC_ABS="${1:?Usage: ci-build-accel-ppp.sh /path/to/kernel-source}"
DEST_DIR="${2:-$(pwd)}"
WORKSPACE="${GITHUB_WORKSPACE:-.}"

ACCEL_UPSTREAM="$WORKSPACE/accel-ppp"
ACCEL_PACKAGING="$WORKSPACE/vyos-accel-ppp"

### Preflight checks
if [ ! -d "$KSRC_ABS" ]; then
  echo "ERROR: Kernel source not found at $KSRC_ABS"
  exit 1
fi

### Clone repos if not already present
if [ ! -d "$ACCEL_UPSTREAM" ]; then
  echo "### Cloning accel-ppp upstream source"
  git clone --depth 1 https://github.com/accel-ppp/accel-ppp.git "$ACCEL_UPSTREAM"
fi

if [ ! -d "$ACCEL_PACKAGING" ]; then
  echo "### Cloning vyos-accel-ppp packaging"
  git clone --depth 1 https://github.com/vyos/vyos-accel-ppp.git "$ACCEL_PACKAGING"
fi

### Extract kernel version from built linux-image .deb
# Pattern: linux-image-6.6.75-amd64-vyos_6.6.75-1_arm64.deb → 6.6.75-amd64-vyos
KIMAGE_DEB=$(find "$DEST_DIR" -maxdepth 1 -name 'linux-image-*.deb' ! -name '*-dbg*' | head -1)
if [ -z "$KIMAGE_DEB" ]; then
  echo "ERROR: No linux-image .deb found in $DEST_DIR — kernel must be built first"
  exit 1
fi

# Extract full kernel version string (e.g., "6.6.75-amd64-vyos")
KVER=$(basename "$KIMAGE_DEB" | sed 's/^linux-image-//; s/_.*$//')
echo "### Kernel version for kmod ABI matching: $KVER"

### Prepare kernel tree for out-of-tree module builds
echo "### Preparing kernel tree for module builds"
make -C "$KSRC_ABS" modules_prepare ARCH=arm64 2>&1 | tail -5 || true

### Install build dependencies
echo "### Installing accel-ppp build dependencies"
apt-get update -qq 2>/dev/null || true
apt-get install -y --no-install-recommends \
  cdbs \
  cmake \
  debhelper \
  liblua5.1-dev \
  libpcre3-dev \
  libssl-dev \
  libsnmp-dev \
  libnl-3-dev \
  libnl-route-3-dev \
  libnl-genl-3-dev \
  2>/dev/null || echo "WARNING: Some build deps may be missing"

### Merge source into packaging tree
# vyos-accel-ppp has debian/ directory; copy upstream source alongside it
BUILD_DIR=$(mktemp -d /tmp/accel-ppp-build.XXXXXX)
cp -a "$ACCEL_PACKAGING"/. "$BUILD_DIR/"
# Copy upstream source into the build tree (preserving debian/ from packaging)
cp -a "$ACCEL_UPSTREAM"/accel-pppd "$BUILD_DIR/" 2>/dev/null || true
cp -a "$ACCEL_UPSTREAM"/drivers "$BUILD_DIR/" 2>/dev/null || true
cp -a "$ACCEL_UPSTREAM"/CMakeLists.txt "$BUILD_DIR/" 2>/dev/null || true
cp -a "$ACCEL_UPSTREAM"/cmake "$BUILD_DIR/" 2>/dev/null || true

### Patch debian/rules with our kernel version
# The VyOS Jenkinsfile does exactly this rewrite for amd64 — replicate for arm64
if [ -f "$BUILD_DIR/debian/rules" ]; then
  echo "### Patching debian/rules with kernel version: $KVER"
  sed -i "s|KERNELDIR :=.*|KERNELDIR := $KSRC_ABS|g" "$BUILD_DIR/debian/rules"
  # Replace any hardcoded kernel version references
  sed -i "s|KVER :=.*|KVER := $KVER|g" "$BUILD_DIR/debian/rules"
fi

### Patch debian/control: vyos-accel-ppp must satisfy Depends: accel-ppp-ng
# VyOS vyos-1x depends on "accel-ppp-ng" but this packaging produces "vyos-accel-ppp"
# Add Provides/Conflicts/Replaces so the .deb satisfies the dependency
if [ -f "$BUILD_DIR/debian/control" ]; then
  echo "### Patching debian/control: adding Provides: accel-ppp-ng"
  sed -i '/^Package: vyos-accel-ppp$/,/^Package:\|^$/{
    /^Depends:/{
      a Provides: accel-ppp-ng
      a Conflicts: accel-ppp-ng
      a Replaces: accel-ppp-ng
    }
  }' "$BUILD_DIR/debian/control"
  echo "### debian/control after patch:"
  head -20 "$BUILD_DIR/debian/control"
fi

# Patch kmod .install files if they reference kernel version paths
for f in "$BUILD_DIR"/debian/*.install; do
  [ -f "$f" ] || continue
  # Replace kernel version in module install paths
  sed -i "s|/lib/modules/[^/]*/|/lib/modules/$KVER/|g" "$f" 2>/dev/null || true
done

### Build with dpkg-buildpackage
echo "### Building accel-ppp-ng packages"
cd "$BUILD_DIR"

# Set KERNELDIR for cmake (picked up by debian/rules or CMakeLists.txt)
export KERNELDIR="$KSRC_ABS"

dpkg-buildpackage -b -us -uc -tc -j$(nproc) 2>&1 || {
  echo "### dpkg-buildpackage failed — check output above"
  # Debs may have been partially produced, continue to collection
  true
}

### Collect output .debs
echo "### Collecting accel-ppp-ng .deb packages"
DEBS_FOUND=0
for deb in /tmp/accel-ppp-build.*/../*.deb "$BUILD_DIR"/../*.deb; do
  [ -f "$deb" ] || continue
  cp "$deb" "$DEST_DIR/"
  echo "  → $(basename "$deb") → $DEST_DIR/"
  DEBS_FOUND=$((DEBS_FOUND + 1))
done

### Cleanup
rm -rf "$BUILD_DIR"

if [ "$DEBS_FOUND" -eq 0 ]; then
  echo "WARNING: accel-ppp-ng build produced no .deb files"
  echo "ISO build will fail on unmet vyos-1x dependency — fix the build or strip the dep manually"
  exit 1
fi

echo "### accel-ppp-ng ARM64 build complete: $DEBS_FOUND package(s)"