#!/bin/bash
# Rebuild dpdk_plugin.so with DPDK STATICALLY linked (matching original VyOS 15MB plugin)
#
# Root cause: Dynamic DPDK linking fails because VPP's custom mempool ops "vpp"
# (registered in VPP binary/libvppinfra) are not visible to separate librte_mempool.so.25.
# Static linking embeds DPDK into the .so, sharing the mempool ops table with VPP.
#
# Approach: Create libdpdk.a mega archive from 172 individual .a files,
# then use VPP_USE_SYSTEM_DPDK=OFF cmake path which finds libdpdk.a directly
# and links with --whole-archive.
#
# CRITICAL: Must also patch CMakeLists.txt to add system library deps
# (-lz -lelf -latomic etc.) AFTER the --whole-archive block.
# Without them: "undefined symbol: inflateEnd" at dlopen time.
#
# Run on LXC 200 (x86_64 → aarch64 cross-compile)
set -euo pipefail

DPDK_PREFIX="/opt/vyos-dev/dpaa-pmd/output/dpdk"
DPDK_LIB="${DPDK_PREFIX}/lib"
DPDK_INC="${DPDK_PREFIX}/include"
VPP_DIR="/opt/vyos-dev/vpp"
BUILD_DIR="${VPP_DIR}/build-dpdk-static"

echo "================================================================"
echo "=== Phase 1: Create libdpdk.a mega archive from individual .a ==="
echo "================================================================"

# Count individual archives
NLIBS=$(ls ${DPDK_LIB}/librte_*.a 2>/dev/null | wc -l)
echo "Found ${NLIBS} individual DPDK static archives"

if [ "${NLIBS}" -lt 10 ]; then
    echo "ERROR: Expected 100+ .a files, found only ${NLIBS}"
    exit 1
fi

# Create MRI script for ar to merge all .a into one mega archive
MRI_SCRIPT=$(mktemp /tmp/dpdk-mri-XXXXXX)
echo "CREATE ${DPDK_LIB}/libdpdk.a" > "${MRI_SCRIPT}"
for a in ${DPDK_LIB}/librte_*.a; do
    echo "ADDLIB ${a}" >> "${MRI_SCRIPT}"
done
echo "SAVE" >> "${MRI_SCRIPT}"
echo "END" >> "${MRI_SCRIPT}"

echo "MRI script: $(wc -l < "${MRI_SCRIPT}") entries"

# Remove stale archive if exists
rm -f "${DPDK_LIB}/libdpdk.a"

# Use cross-ar to create the mega archive
aarch64-linux-gnu-ar -M < "${MRI_SCRIPT}"
rm -f "${MRI_SCRIPT}"

# Verify
ls -lh "${DPDK_LIB}/libdpdk.a"
echo "Object count: $(aarch64-linux-gnu-ar t "${DPDK_LIB}/libdpdk.a" | wc -l) .o files"

echo ""
echo "================================================================"
echo "=== Phase 2: Patch CMakeLists.txt for system library deps ==="
echo "================================================================"

CMAKELISTS="${VPP_DIR}/src/plugins/dpdk/CMakeLists.txt"

# Check if already patched
if grep -q '\-lz -lelf -latomic' "${CMAKELISTS}"; then
    echo "CMakeLists.txt already patched with system library deps"
else
    echo "Patching CMakeLists.txt to add -lz -lelf -latomic -lpthread -lm -ldl"
    # Insert after the --whole-archive line (line containing --no-whole-archive)
    sed -i '/--whole-archive.*--no-whole-archive/a\      string_append(DPDK_LINK_FLAGS "-lz -lelf -latomic -lpthread -lm -ldl")' "${CMAKELISTS}"
    echo "Patched. Verify:"
    grep -n 'lz.*lelf\|whole-archive' "${CMAKELISTS}"
fi

echo ""
echo "================================================================"
echo "=== Phase 3: Write VPP version file ==="
echo "================================================================"
echo "v25.10.0-48~vyos" > "${VPP_DIR}/src/scripts/.version"

echo ""
echo "================================================================"
echo "=== Phase 4: Ensure DPDK headers have dpdk/ subfolder ==="
echo "================================================================"

if [ -f "${DPDK_INC}/dpdk/rte_config.h" ]; then
    echo "Headers at: ${DPDK_INC}/dpdk/ (correct)"
elif [ -f "${DPDK_INC}/rte_config.h" ]; then
    echo "Headers at: ${DPDK_INC}/ (flat layout)"
    if [ ! -d "${DPDK_INC}/dpdk" ] || [ ! -f "${DPDK_INC}/dpdk/rte_config.h" ]; then
        echo "Creating dpdk/ subfolder symlink..."
        ln -sf "${DPDK_INC}" "${DPDK_INC}/dpdk"
    fi
fi

echo ""
echo "================================================================"
echo "=== Phase 5: Configure VPP with VPP_USE_SYSTEM_DPDK=OFF ==="
echo "================================================================"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

cmake -G Ninja "${VPP_DIR}/src" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_SYSTEM_NAME=Linux \
    -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
    -DCMAKE_C_COMPILER=aarch64-linux-gnu-gcc \
    "-DCMAKE_C_FLAGS=-O2" \
    -DVPP_USE_SYSTEM_DPDK=OFF \
    -DCMAKE_PREFIX_PATH="${DPDK_PREFIX}" \
    -DCMAKE_FIND_ROOT_PATH="/usr/aarch64-linux-gnu;${DPDK_PREFIX}" \
    -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=BOTH \
    -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=BOTH \
    -DCMAKE_SHARED_LINKER_FLAGS="-Wl,--allow-multiple-definition" \
    2>&1

echo ""
echo "================================================================"
echo "=== Phase 6: Build dpdk_plugin ==="
echo "================================================================"

ninja -j$(nproc) dpdk_plugin 2>&1

echo ""
echo "================================================================"
echo "=== Phase 7: Verify ==="
echo "================================================================"

PLUGIN=$(find "${BUILD_DIR}" -name "dpdk_plugin.so" -type f 2>/dev/null | head -1)
if [ -n "${PLUGIN}" ]; then
    echo "SUCCESS: ${PLUGIN}"
    ls -lh "${PLUGIN}"
    echo ""
    echo "=== NEEDED (dynamic deps) ==="
    aarch64-linux-gnu-readelf -d "${PLUGIN}" | grep NEEDED || echo "(none)"
    echo ""
    echo "=== Key symbol checks ==="
    echo -n "inflateEnd: "
    aarch64-linux-gnu-nm -D "${PLUGIN}" 2>/dev/null | grep inflateEnd || echo "NOT FOUND"
    echo -n "rte_dpaa_bus_scan: "
    aarch64-linux-gnu-nm -D "${PLUGIN}" 2>/dev/null | grep rte_dpaa_bus_scan || echo "NOT FOUND"
    echo -n "dpaa_eth_dev_init: "
    aarch64-linux-gnu-nm -D "${PLUGIN}" 2>/dev/null | grep dpaa_eth_dev_init || echo "NOT FOUND"
    echo ""
    echo "=== Size comparison ==="
    echo "New static plugin: $(du -h "${PLUGIN}" | cut -f1)"
    DYNAMIC="/opt/vyos-dev/vpp/build-dpdk-plugin/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
    if [ -f "${DYNAMIC}" ]; then
        echo "Old dynamic plugin: $(du -h "${DYNAMIC}" | cut -f1)"
    fi
    echo "Expected: ~16MB (DPDK 24.11 with DPAA PMDs statically linked)"
else
    echo "FAILED: dpdk_plugin.so not found"
    exit 1
fi
