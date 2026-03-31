#!/bin/bash
# Rebuild DPDK 24.11 + dpdk_plugin.so WITHOUT DPAA drivers
# Matches VPP's dpdk.mk disable list to produce a kernel-panic-free plugin
#
# Root Cause Analysis (2026-03-28):
# - VPP's own dpdk.mk (DPDK 25.07) explicitly disables: bus/dpaa, crypto/dpaa_sec,
#   mempool/dpaa, and event/* — these NXP DPAA drivers are NOT in the stock VyOS plugin
# - Our custom build included ALL DPAA drivers from DPDK 24.11 via --whole-archive
# - The extra DPAA driver code in the plugin correlates with kernel panics
#   in BMan portal access (bman_release PTE=0) 500-1100s after VPP starts
# - Stock VyOS plugin (15,359,008 bytes) works; our plugin (15,910,576 bytes) crashes
#
# Strategy: Build DPDK 24.11 with same disable_drivers as VyOS to get a WORKING baseline.
# DPAA PMD support will be added incrementally in a separate build once baseline is stable.
#
# Run on LXC 200: cd /opt/vyos-dev && ./rebuild-no-dpaa.sh
set -euo pipefail

WORKDIR="/opt/vyos-dev/dpaa-pmd"
DPDK_SRC="${WORKDIR}/src/dpdk"
DPDK_BUILD="${DPDK_SRC}/build-no-dpaa"
DPDK_OUTPUT="${WORKDIR}/output/dpdk-no-dpaa"
KERNEL_DIR="/opt/vyos-dev/linux"
CROSS_FILE="${WORKDIR}/dpdk-cross.ini"
VPP_DIR="/opt/vyos-dev/vpp"
VPP_BUILD="${VPP_DIR}/build-no-dpaa"

PHASE="${1:-all}"

phase_dpdk_setup() {
    echo "================================================================"
    echo "=== DPDK 24.11 Setup — VPP-Compatible Disable List ==="
    echo "================================================================"

    cd "${DPDK_SRC}"

    # Clean previous no-dpaa build
    rm -rf "${DPDK_BUILD}" "${DPDK_OUTPUT}"
    mkdir -p "${DPDK_OUTPUT}"

    export PKG_CONFIG_LIBDIR="/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig"
    export PKG_CONFIG_SYSROOT_DIR=""

    # Match VPP dpdk.mk DPDK_DRIVERS_DISABLED list exactly
    # Plus some additional large drivers to reduce binary size
    DISABLE_DRIVERS="baseband/*"
    DISABLE_DRIVERS+=",bus/dpaa"
    DISABLE_DRIVERS+=",bus/ifpga"
    DISABLE_DRIVERS+=",common/cnxk"
    DISABLE_DRIVERS+=",compress/isal"
    DISABLE_DRIVERS+=",compress/octeontx"
    DISABLE_DRIVERS+=",compress/zlib"
    DISABLE_DRIVERS+=",crypto/ccp"
    DISABLE_DRIVERS+=",crypto/cnxk"
    DISABLE_DRIVERS+=",crypto/dpaa_sec"
    DISABLE_DRIVERS+=",crypto/openssl"
    DISABLE_DRIVERS+=",crypto/aesni_mb"
    DISABLE_DRIVERS+=",crypto/aesni_gcm"
    DISABLE_DRIVERS+=",crypto/kasumi"
    DISABLE_DRIVERS+=",crypto/snow3g"
    DISABLE_DRIVERS+=",crypto/zuc"
    DISABLE_DRIVERS+=",event/*"
    DISABLE_DRIVERS+=",mempool/dpaa"
    DISABLE_DRIVERS+=",mempool/cnxk"
    DISABLE_DRIVERS+=",net/af_packet"
    DISABLE_DRIVERS+=",net/bnx2x"
    DISABLE_DRIVERS+=",net/bonding"
    DISABLE_DRIVERS+=",net/cnxk"
    DISABLE_DRIVERS+=",net/ipn3ke"
    DISABLE_DRIVERS+=",net/liquidio"
    DISABLE_DRIVERS+=",net/pcap"
    DISABLE_DRIVERS+=",net/pfe"
    DISABLE_DRIVERS+=",net/sfc"
    DISABLE_DRIVERS+=",net/softnic"
    DISABLE_DRIVERS+=",net/thunderx"
    DISABLE_DRIVERS+=",raw/ifpga"
    DISABLE_DRIVERS+=",net/af_xdp"
    # Also disable DPAA1-specific remaining drivers  
    DISABLE_DRIVERS+=",net/dpaa"
    DISABLE_DRIVERS+=",net/dpaa2"
    DISABLE_DRIVERS+=",bus/fslmc"
    DISABLE_DRIVERS+=",common/dpaax"
    DISABLE_DRIVERS+=",crypto/dpaa2_sec"
    DISABLE_DRIVERS+=",mempool/dpaa2"
    DISABLE_DRIVERS+=",dma/dpaa"
    DISABLE_DRIVERS+=",dma/dpaa2"
    DISABLE_DRIVERS+=",raw/dpaa2_cmdif"
    DISABLE_DRIVERS+=",raw/dpaa2_qdma"
    # Disable other large unused drivers
    DISABLE_DRIVERS+=",net/mlx4"
    DISABLE_DRIVERS+=",net/mlx5"
    DISABLE_DRIVERS+=",common/mlx5"
    DISABLE_DRIVERS+=",net/bnxt"
    DISABLE_DRIVERS+=",net/ixgbe"
    DISABLE_DRIVERS+=",net/ice"
    DISABLE_DRIVERS+=",net/i40e"
    DISABLE_DRIVERS+=",net/e1000"
    DISABLE_DRIVERS+=",crypto/qat"
    DISABLE_DRIVERS+=",net/virtio"
    DISABLE_DRIVERS+=",net/vmxnet3"
    DISABLE_DRIVERS+=",net/ena"
    DISABLE_DRIVERS+=",net/hns3"
    DISABLE_DRIVERS+=",net/hinic"
    DISABLE_DRIVERS+=",net/octeontx"
    DISABLE_DRIVERS+=",net/octeontx2"

    echo "Disabled drivers: ${DISABLE_DRIVERS}"

    meson setup "${DPDK_BUILD}" \
        --cross-file "${CROSS_FILE}" \
        --prefix="${DPDK_OUTPUT}" \
        -Ddefault_library=static \
        -Dmax_lcores=4 \
        -Dkernel_dir="${KERNEL_DIR}" \
        -Denable_kmods=false \
        -Dtests=false \
        -Dexamples= \
        -Ddisable_drivers="${DISABLE_DRIVERS}" \
        2>&1

    echo "DPDK setup complete"
}

phase_dpdk_build() {
    echo "================================================================"
    echo "=== DPDK 24.11 Build — No DPAA ==="
    echo "================================================================"

    cd "${DPDK_SRC}"
    ninja -C "${DPDK_BUILD}" -j$(nproc) 2>&1
    ninja -C "${DPDK_BUILD}" install 2>&1 | tail -20

    echo ""
    echo "=== Verify no DPAA libraries ==="
    DPAA_LIBS=$(find "${DPDK_OUTPUT}" -name "*dpaa*" -type f 2>/dev/null | wc -l)
    echo "DPAA library count: ${DPAA_LIBS} (expected: 0)"
    if [ "${DPAA_LIBS}" -gt 0 ]; then
        echo "WARNING: Found DPAA libraries — disable list may be incomplete:"
        find "${DPDK_OUTPUT}" -name "*dpaa*" -type f
    fi

    echo ""
    echo "=== Library count ==="
    echo "Static archives: $(ls ${DPDK_OUTPUT}/lib/librte_*.a 2>/dev/null | wc -l)"
    ls -la "${DPDK_OUTPUT}/lib/librte_*.a" 2>/dev/null | head -5
}

phase_mega_archive() {
    echo "================================================================"
    echo "=== Create libdpdk.a mega archive ==="
    echo "================================================================"

    NLIBS=$(ls ${DPDK_OUTPUT}/lib/librte_*.a 2>/dev/null | wc -l)
    echo "Found ${NLIBS} individual DPDK static archives"

    MRI_SCRIPT=$(mktemp /tmp/dpdk-mri-XXXXXX)
    echo "CREATE ${DPDK_OUTPUT}/lib/libdpdk.a" > "${MRI_SCRIPT}"
    for a in ${DPDK_OUTPUT}/lib/librte_*.a; do
        echo "ADDLIB ${a}" >> "${MRI_SCRIPT}"
    done
    echo "SAVE" >> "${MRI_SCRIPT}"
    echo "END" >> "${MRI_SCRIPT}"

    rm -f "${DPDK_OUTPUT}/lib/libdpdk.a"
    aarch64-linux-gnu-ar -M < "${MRI_SCRIPT}"
    rm -f "${MRI_SCRIPT}"

    ls -lh "${DPDK_OUTPUT}/lib/libdpdk.a"
    echo "Object count: $(aarch64-linux-gnu-ar t "${DPDK_OUTPUT}/lib/libdpdk.a" | wc -l) .o files"
}

phase_vpp_build() {
    echo "================================================================"
    echo "=== Rebuild dpdk_plugin.so with no-DPAA DPDK ==="
    echo "================================================================"

    DPDK_PREFIX="${DPDK_OUTPUT}"

    # Ensure dpdk/ subfolder for headers
    if [ -f "${DPDK_PREFIX}/include/rte_config.h" ] && [ ! -f "${DPDK_PREFIX}/include/dpdk/rte_config.h" ]; then
        ln -sf "${DPDK_PREFIX}/include" "${DPDK_PREFIX}/include/dpdk"
    fi

    # Version file
    echo "v25.10.0-48~vyos" > "${VPP_DIR}/src/scripts/.version"

    # Clean and configure
    rm -rf "${VPP_BUILD}"
    mkdir -p "${VPP_BUILD}"
    cd "${VPP_BUILD}"

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
    echo "=== Build dpdk_plugin ==="
    ninja -j$(nproc) dpdk_plugin 2>&1

    echo ""
    echo "=== Verify ==="
    PLUGIN=$(find "${VPP_BUILD}" -name "dpdk_plugin.so" -type f 2>/dev/null | head -1)
    if [ -n "${PLUGIN}" ]; then
        echo "SUCCESS: ${PLUGIN}"
        ls -lh "${PLUGIN}"
        echo ""
        echo "=== NEEDED ==="
        aarch64-linux-gnu-readelf -d "${PLUGIN}" | grep NEEDED || echo "(none)"
        echo ""
        echo "=== DPAA symbols check (should be ZERO) ==="
        DPAA_SYMS=$(aarch64-linux-gnu-nm "${PLUGIN}" 2>/dev/null | grep -ciE 'dpaa|fslmc' || true)
        echo "DPAA symbol count: ${DPAA_SYMS}"
        echo ""
        echo "=== Size comparison ==="
        echo "No-DPAA plugin: $(stat -c%s "${PLUGIN}") bytes ($(du -h "${PLUGIN}" | cut -f1))"
        STOCK="/opt/vyos-dev/vpp/build-dpdk-static/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"
        if [ -f "${STOCK}" ]; then
            echo "With-DPAA plugin: $(stat -c%s "${STOCK}") bytes ($(du -h "${STOCK}" | cut -f1))"
        fi
        echo ""
        echo "=== Deploy ready ==="
        cp "${PLUGIN}" /tmp/dpdk_plugin.so.no-dpaa
        echo "Staged: /tmp/dpdk_plugin.so.no-dpaa"
    else
        echo "FAILED: dpdk_plugin.so not found"
        exit 1
    fi
}

case "${PHASE}" in
    dpdk-setup)    phase_dpdk_setup ;;
    dpdk-build)    phase_dpdk_build ;;
    mega-archive)  phase_mega_archive ;;
    vpp-build)     phase_vpp_build ;;
    all)
        phase_dpdk_setup
        phase_dpdk_build
        phase_mega_archive
        phase_vpp_build
        ;;
    *)
        echo "Usage: $0 {dpdk-setup|dpdk-build|mega-archive|vpp-build|all}"
        exit 1
        ;;
esac

echo ""
echo "=== Done: ${PHASE} ==="
