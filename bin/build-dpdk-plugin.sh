#!/bin/bash
# build-dpdk-plugin.sh — Build VPP DPDK plugin with static DPAA PMD
#
# Builds an out-of-tree VPP DPDK plugin against:
#   - vpp-dev headers extracted from upstream VyOS deb packages
#   - Static libdpdk.a with DPAA1 PMD (from build-dpdk.sh output)
#   - DPAA1 patches from patch-vpp-dpaa-mempool.sh
#
# The resulting dpdk_plugin.so (~12MB) replaces the upstream one (~15MB)
# and includes NXP DPAA1 BMan mempool + net_dpaa driver support.
#
# Prerequisites:
#   - aarch64-linux-gnu-gcc cross compiler
#   - ninja-build, cmake
#   - VPP source with DPAA patches at /opt/vyos-dev/vpp/src/plugins/dpdk/
#   - Static DPDK at /opt/vyos-dev/dpaa-pmd/output/dpdk/
#   - VyOS build cache at /opt/vyos-dev/vyos-build/build/cache/packages.chroot/
#
# Usage:
#   ./bin/build-dpdk-plugin.sh              # auto-detect everything
#   VPP_SRC=/path/to/vpp ./bin/build-dpdk.sh  # override VPP source

set -euo pipefail

# === Configuration ===
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VPP_SRC="${VPP_SRC:-/opt/vyos-dev/vpp/src/plugins/dpdk}"
DPDK_DIR="${DPDK_DIR:-/opt/vyos-dev/dpaa-pmd/output/dpdk}"
CACHE_DIR="${CACHE_DIR:-/opt/vyos-dev/vyos-build/build/cache/packages.chroot}"
BUILD_DIR="${BUILD_DIR:-/tmp/dpdk-plugin-build}"
OUTPUT_DIR="${OUTPUT_DIR:-/vpp_plugins}"

log() { echo -e "\033[0;32m[$(date +%H:%M:%S)]\033[0m $*"; }
err() { echo -e "\033[0;31m[$(date +%H:%M:%S)] ERROR:\033[0m $*" >&2; exit 1; }
warn() { echo -e "\033[0;33m[$(date +%H:%M:%S)] WARN:\033[0m $*" >&2; }

# === Step 0: Validate prerequisites ===
log "=== VPP DPDK Plugin Build (out-of-tree, static DPAA PMD) ==="

command -v aarch64-linux-gnu-gcc >/dev/null || err "aarch64-linux-gnu-gcc not found"
command -v cmake >/dev/null || err "cmake not found"
command -v ninja >/dev/null || err "ninja not found"

[ -d "$VPP_SRC" ] || err "VPP DPDK plugin source not found at $VPP_SRC"
[ -f "$DPDK_DIR/lib/libdpdk.a" ] || err "Static libdpdk.a not found at $DPDK_DIR/lib/libdpdk.a"
[ -d "$CACHE_DIR" ] || err "VyOS package cache not found at $CACHE_DIR"

# === Step 1: Detect VPP version from upstream debs ===
log "Step 1: Detecting VPP version from upstream packages..."

VPP_DEV_DEB=$(find "$CACHE_DIR" -name 'vpp-dev_*_arm64.deb' -type f 2>/dev/null | sort -V | tail -1)
[ -n "$VPP_DEV_DEB" ] || err "No vpp-dev_*_arm64.deb found in $CACHE_DIR"

VPP_DEB=$(find "$CACHE_DIR" -name 'vpp_*_arm64.deb' -type f 2>/dev/null | sort -V | tail -1)
[ -n "$VPP_DEB" ] || err "No vpp_*_arm64.deb found in $CACHE_DIR"

LIBVPPINFRA_DEB=$(find "$CACHE_DIR" -name 'libvppinfra_*_arm64.deb' -type f 2>/dev/null | sort -V | tail -1)
LIBVPPINFRA_DEV_DEB=$(find "$CACHE_DIR" -name 'libvppinfra-dev_*_arm64.deb' -type f 2>/dev/null | sort -V | tail -1)

# Extract version from deb filename: vpp-dev_25.10.0-48~vyos..._arm64.deb
VPP_VERSION=$(basename "$VPP_DEV_DEB" | sed -E 's/^vpp-dev_([0-9]+\.[0-9]+\.[0-9]+).*/\1/')
VPP_FULL_VERSION=$(basename "$VPP_DEV_DEB" | sed -E 's/^vpp-dev_([^_]+)_.*/\1/')

log "  VPP version: $VPP_VERSION (full: $VPP_FULL_VERSION)"
log "  vpp-dev:     $(basename "$VPP_DEV_DEB")"
log "  vpp:         $(basename "$VPP_DEB")"

# Detect DPDK version from static lib headers
# RTE_VER_* may be in rte_version.h or rte_build_config.h depending on DPDK version
DPDK_VERSION="unknown"
for hdr in "$DPDK_DIR/include/dpdk/rte_build_config.h" "$DPDK_DIR/include/dpdk/rte_version.h"; do
    if [ -f "$hdr" ] && grep -q 'RTE_VER_YEAR' "$hdr"; then
        DPDK_YEAR=$(grep '#define RTE_VER_YEAR' "$hdr" | awk '{print $3}')
        DPDK_MONTH=$(grep '#define RTE_VER_MONTH' "$hdr" | awk '{print $3}')
        DPDK_MINOR=$(grep '#define RTE_VER_MINOR' "$hdr" | awk '{print $3}')
        DPDK_VERSION="${DPDK_YEAR}.${DPDK_MONTH}.${DPDK_MINOR}"
        break
    fi
done
log "  DPDK version: $DPDK_VERSION (static, with DPAA PMD)"

# === Step 2: Extract vpp-dev headers ===
log "Step 2: Extracting vpp-dev headers..."

VPP_DEV_EXTRACT="$BUILD_DIR/vpp-dev"
rm -rf "$VPP_DEV_EXTRACT"
mkdir -p "$VPP_DEV_EXTRACT"

dpkg-deb -x "$VPP_DEV_DEB" "$VPP_DEV_EXTRACT"
dpkg-deb -x "$VPP_DEB" "$VPP_DEV_EXTRACT"
[ -n "$LIBVPPINFRA_DEB" ] && dpkg-deb -x "$LIBVPPINFRA_DEB" "$VPP_DEV_EXTRACT"
[ -n "$LIBVPPINFRA_DEV_DEB" ] && dpkg-deb -x "$LIBVPPINFRA_DEV_DEB" "$VPP_DEV_EXTRACT"

HEADER_COUNT=$(find "$VPP_DEV_EXTRACT" -name '*.h' | wc -l)
log "  Extracted $HEADER_COUNT headers"

# Remove upstream dpdk plugin headers (we use our DPAA-patched copy)
if [ -d "$VPP_DEV_EXTRACT/usr/include/vpp_plugins/dpdk" ]; then
    rm -rf "$VPP_DEV_EXTRACT/usr/include/vpp_plugins/dpdk"
    log "  Removed upstream dpdk plugin headers (using DPAA-patched source)"
fi

# === Step 3: Copy supplementary headers ===
# Some headers are referenced by the dpdk plugin but not shipped in vpp-dev
log "Step 3: Copying supplementary headers..."

VPP_FULL_SRC="${VPP_SRC%/plugins/dpdk}"  # /opt/vyos-dev/vpp/src
VPP_FULL_SRC="${VPP_FULL_SRC%/src}"       # /opt/vyos-dev/vpp

# vppinfra/linux/sysfs.c (inline-included by DPDK plugin)
if [ -f "$VPP_FULL_SRC/src/vppinfra/linux/sysfs.c" ]; then
    mkdir -p "$VPP_DEV_EXTRACT/usr/include/vppinfra/linux"
    cp "$VPP_FULL_SRC/src/vppinfra/linux/sysfs.c" "$VPP_DEV_EXTRACT/usr/include/vppinfra/linux/"
    log "  Copied vppinfra/linux/sysfs.c"
fi

# vxlan headers (referenced by dpdk plugin)
if [ -d "$VPP_FULL_SRC/src/plugins/vxlan" ]; then
    mkdir -p "$VPP_DEV_EXTRACT/usr/include/vpp_plugins/vxlan"
    cp "$VPP_FULL_SRC/src/plugins/vxlan/vxlan.h" "$VPP_DEV_EXTRACT/usr/include/vpp_plugins/vxlan/" 2>/dev/null || true
    cp "$VPP_FULL_SRC/src/plugins/vxlan/vxlan_packet.h" "$VPP_DEV_EXTRACT/usr/include/vpp_plugins/vxlan/" 2>/dev/null || true
    cp "$VPP_FULL_SRC/src/plugins/vxlan/vxlan_error.def" "$VPP_DEV_EXTRACT/usr/include/vpp_plugins/vxlan/" 2>/dev/null || true
    log "  Copied vxlan headers"
fi

# === Step 4: Set up out-of-tree build ===
log "Step 4: Setting up out-of-tree build..."

mkdir -p "$BUILD_DIR/plugins"

# Copy DPAA-patched dpdk plugin source
rm -rf "$BUILD_DIR/plugins/dpdk"
cp -a "$VPP_SRC" "$BUILD_DIR/plugins/dpdk"
log "  Copied DPAA-patched dpdk plugin source"

# Write top-level CMakeLists.txt
cat > "$BUILD_DIR/CMakeLists.txt" << 'CMAKE'
cmake_minimum_required(VERSION 3.16)
project(vpp-dpdk-plugin C)

# === Missing VPP internal macros (not shipped in vpp-dev) ===
macro(string_append var str)
  if (NOT ${var})
    set(${var} "${str}")
  else()
    set(${var} "${${var}} ${str}")
  endif()
endmacro()

macro(vpp_find_path var)
  cmake_parse_arguments(ARG "" "" "PATH_SUFFIXES;NAMES" ${ARGN})
  find_path(${var} PATH_SUFFIXES ${ARG_PATH_SUFFIXES} NAMES ${ARG_NAMES})
endmacro()

macro(vpp_plugin_find_library plugin var name)
  find_library(${var} NAMES ${name})
  if(NOT ${var})
    message(WARNING "-- ${plugin} plugin needs ${name} library - not found")
  else()
    message(STATUS "-- ${plugin} plugin needs ${name} library - found at ${${var}}")
  endif()
endmacro()
# === End missing macros ===

# Use VPP's out-of-tree plugin cmake infrastructure
find_package(VPP REQUIRED)

# Static DPDK linking — embed DPAA PMD into the plugin
set(VPP_USE_SYSTEM_DPDK OFF CACHE BOOL "" FORCE)

# PRE_DATA_SIZE must match DPDK headroom (128 bytes)
set(PRE_DATA_SIZE 128)

# No OpenSSL/cryptodev
unset(OPENSSL_FOUND)

add_subdirectory(plugins/dpdk)
CMAKE
log "  Generated CMakeLists.txt"

# === Step 5: Configure and build ===
log "Step 5: Configuring cmake..."

rm -rf "$BUILD_DIR/build"
mkdir -p "$BUILD_DIR/build"

cd "$BUILD_DIR/build"
cmake .. \
    -G Ninja \
    -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
    -DCMAKE_C_FLAGS="-O2 -I$BUILD_DIR/plugins" \
    -DCMAKE_PREFIX_PATH="$VPP_DEV_EXTRACT/usr/lib/aarch64-linux-gnu/cmake/vpp" \
    -DCMAKE_FIND_ROOT_PATH="/usr/aarch64-linux-gnu;$VPP_DEV_EXTRACT/usr;$DPDK_DIR" \
    -DVPP_INCLUDE_DIR="$VPP_DEV_EXTRACT/usr/include" \
    -DVPP_APIGEN="$VPP_DEV_EXTRACT/usr/bin/vppapigen" \
    -DCMAKE_SYSTEM_NAME=Linux \
    -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
    -DDPDK_INCLUDE_DIR="$DPDK_DIR/include/dpdk" \
    -DDPDK_LIB="$DPDK_DIR/lib/libdpdk.a" \
    2>&1 | grep -E '(Found DPDK|libdpdk|numa|Configuring done|ERROR|FATAL)'

log "Step 6: Building..."

ninja -j"$(nproc)" 2>&1

# === Step 7: Verify output ===
log "Step 7: Verifying output..."

PLUGIN="$OUTPUT_DIR/dpdk_plugin.so"
[ -f "$PLUGIN" ] || err "Build succeeded but plugin not found at $PLUGIN"

PLUGIN_SIZE=$(stat -c%s "$PLUGIN")
DPAA_SYMBOLS=$(aarch64-linux-gnu-nm -D "$PLUGIN" 2>/dev/null | grep -ci dpaa || true)
NET_DPAA=$(aarch64-linux-gnu-strings "$PLUGIN" 2>/dev/null | grep -c '^net_dpaa$' || true)
DPAA_BUS=$(aarch64-linux-gnu-strings "$PLUGIN" 2>/dev/null | grep -c '^dpaa_bus$' || true)
BMAN_INIT=$(aarch64-linux-gnu-nm -D "$PLUGIN" 2>/dev/null | grep -c 'bman_new_pool' || true)
PMD_INIT=$(aarch64-linux-gnu-nm "$PLUGIN" 2>/dev/null | grep -c 'dpaainitfn_net_dpaa' || true)

log ""
log "=== Build Results ==="
log "  Plugin:         $PLUGIN"
log "  Size:           $(numfmt --to=iec $PLUGIN_SIZE)"
log "  VPP version:    $VPP_VERSION"
log "  DPDK version:   $DPDK_VERSION"
log "  DPAA symbols:   $DPAA_SYMBOLS"
log "  net_dpaa PMD:   $NET_DPAA string(s)"
log "  dpaa_bus:       $DPAA_BUS string(s)"
log "  bman_new_pool:  $BMAN_INIT"
log "  PMD constructor:$PMD_INIT"

# Sanity checks
PASS=true
if [ "$PLUGIN_SIZE" -lt 5000000 ]; then
    warn "Plugin < 5MB — may be dynamically linked (DPAA PMD not embedded)"
    PASS=false
fi
if [ "$DPAA_SYMBOLS" -lt 50 ]; then
    warn "Fewer than 50 DPAA symbols — DPAA PMD may not be linked"
    PASS=false
fi
if [ "$PMD_INIT" -eq 0 ]; then
    warn "DPAA PMD constructor (dpaainitfn_net_dpaa) missing!"
    PASS=false
fi

if $PASS; then
    log ""
    log "=== BUILD SUCCESSFUL ==="
    log "Plugin ready for deployment: $PLUGIN"
else
    warn ""
    warn "=== BUILD COMPLETED WITH WARNINGS ==="
    warn "Plugin may not function correctly on DPAA1 hardware"
fi