#!/bin/bash
# DPDK 24.11 Cross-Compile for DPAA1 PMD
# Run on LXC 200 (x86 cross-compiling to aarch64)
set -euo pipefail

WORKDIR="/opt/vyos-dev/dpaa-pmd"
DPDK_SRC="${WORKDIR}/src/dpdk"
DPDK_BUILD="${DPDK_SRC}/build"
DPDK_OUTPUT="${WORKDIR}/output/dpdk"
KERNEL_DIR="/opt/vyos-dev/linux"
CROSS_FILE="${WORKDIR}/dpdk-cross.ini"

phase_deps() {
    echo "=== Phase: Install Dependencies ==="
    dpkg --add-architecture arm64 2>/dev/null || true
    apt-get update -qq
    apt-get install -y -qq meson ninja-build pkg-config python3-pyelftools \
        libelf-dev:arm64 libnuma-dev:arm64 2>&1 | tail -5
    echo "meson $(meson --version), ninja $(ninja --version)"
}

phase_clone() {
    echo "=== Phase: Clone DPDK 24.11 ==="
    mkdir -p "${WORKDIR}/src"
    if [ -f "${DPDK_SRC}/VERSION" ]; then
        echo "DPDK already cloned: $(cat ${DPDK_SRC}/VERSION)"
    else
        cd "${WORKDIR}/src"
        rm -rf dpdk
        git clone --depth=1 -b v24.11 https://github.com/DPDK/dpdk.git
        echo "Cloned DPDK $(cat ${DPDK_SRC}/VERSION)"
    fi
}

phase_crossfile() {
    echo "=== Phase: Create Meson Cross-File ==="
    cat > "${CROSS_FILE}" << 'CROSSEOF'
[binaries]
c = 'aarch64-linux-gnu-gcc'
cpp = 'aarch64-linux-gnu-g++'
ar = 'aarch64-linux-gnu-ar'
strip = 'aarch64-linux-gnu-strip'
pkgconfig = 'pkg-config'

[built-in options]
c_args = ['-O2', '-fPIC', '-mcpu=cortex-a72+crc+crypto', '-I/usr/include/aarch64-linux-gnu']
cpp_args = ['-O2', '-fPIC', '-mcpu=cortex-a72+crc+crypto', '-I/usr/include/aarch64-linux-gnu']
c_link_args = ['-L/usr/lib/aarch64-linux-gnu']
cpp_link_args = ['-L/usr/lib/aarch64-linux-gnu']

[host_machine]
system = 'linux'
cpu_family = 'aarch64'
cpu = 'armv8-a'
endian = 'little'

[properties]
platform = 'dpaa'
sys_root = '/'
pkg_config_libdir = '/usr/lib/aarch64-linux-gnu/pkgconfig'
CROSSEOF
    echo "Cross-file written to ${CROSS_FILE}"
    cat "${CROSS_FILE}"
}

phase_setup() {
    echo "=== Phase: Meson Setup ==="
    cd "${DPDK_SRC}"

    # Clean previous build if exists
    if [ -d build ]; then
        echo "Removing previous build..."
        rm -rf build
    fi

    mkdir -p "${DPDK_OUTPUT}"

    # Check what DPAA drivers exist
    echo "DPAA bus driver: $(ls drivers/bus/dpaa/meson.build 2>/dev/null && echo EXISTS || echo MISSING)"
    echo "DPAA net driver: $(ls drivers/net/dpaa/meson.build 2>/dev/null && echo EXISTS || echo MISSING)"
    echo "DPAA mempool:    $(ls drivers/mempool/dpaa/meson.build 2>/dev/null && echo EXISTS || echo MISSING)"

    # Set PKG_CONFIG to find arm64 libraries
    export PKG_CONFIG_LIBDIR="/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig"
    export PKG_CONFIG_SYSROOT_DIR=""

    # Meson setup — cross-compile for aarch64 with DPAA support
    # Static build: all PMDs compiled into testpmd binary — zero .so deployment
    meson setup build \
        --cross-file "${CROSS_FILE}" \
        --prefix="${DPDK_OUTPUT}" \
        -Ddefault_library=static \
        -Dmax_lcores=4 \
        -Dkernel_dir="${KERNEL_DIR}" \
        -Denable_kmods=false \
        -Dtests=false \
        -Dexamples= \
        -Ddisable_drivers="net/mlx*,net/bnxt,net/ixg*,net/ice,net/i40e,net/e1000,crypto/qat,net/virtio,net/vmxnet*,net/ena,net/hns*,net/hinic,net/octeontx*,net/cnxk,net/thunderx" \
        2>&1
}

phase_build() {
    echo "=== Phase: Ninja Build ==="
    cd "${DPDK_SRC}"
    ninja -C build -j$(nproc) 2>&1

    echo "=== Phase: Install to output ==="
    ninja -C build install 2>&1 | tail -20
}

phase_check() {
    echo "=== Phase: Check Build Output ==="

    # Check for DPAA PMD libraries
    echo "--- DPAA Libraries ---"
    find "${DPDK_OUTPUT}" -name "*dpaa*" -type f 2>/dev/null | head -20
    find "${DPDK_SRC}/build" -name "*dpaa*" -type f 2>/dev/null | head -20

    # Check for testpmd
    echo "--- testpmd ---"
    find "${DPDK_SRC}/build" -name "dpdk-testpmd" -type f 2>/dev/null
    find "${DPDK_OUTPUT}" -name "dpdk-testpmd" -type f 2>/dev/null

    # Check binary architecture
    echo "--- Binary Architecture ---"
    TESTPMD=$(find "${DPDK_SRC}/build" "${DPDK_OUTPUT}" -name "dpdk-testpmd" -type f 2>/dev/null | head -1)
    if [ -n "${TESTPMD}" ]; then
        file "${TESTPMD}"
        echo "testpmd size: $(du -h ${TESTPMD} | cut -f1)"
    else
        echo "testpmd NOT FOUND"
    fi

    # Check which PMDs were built
    echo "--- Built PMDs ---"
    find "${DPDK_SRC}/build/drivers" -name "*.a" -o -name "*.so" 2>/dev/null | grep -i dpaa || echo "No DPAA PMD .so/.a found in build"
}

phase_deploy() {
    echo "=== Phase: Deploy testpmd to TFTP ==="
    TESTPMD=$(find "${DPDK_SRC}/build" "${DPDK_OUTPUT}" -name "dpdk-testpmd" -type f 2>/dev/null | head -1)
    if [ -n "${TESTPMD}" ]; then
        cp "${TESTPMD}" /srv/tftp/dpdk-testpmd
        echo "Deployed: /srv/tftp/dpdk-testpmd ($(du -h /srv/tftp/dpdk-testpmd | cut -f1))"
    else
        echo "ERROR: testpmd not found, cannot deploy"
        exit 1
    fi
}

# Main
PHASE="${1:-all}"
case "${PHASE}" in
    deps)      phase_deps ;;
    clone)     phase_clone ;;
    crossfile) phase_crossfile ;;
    setup)     phase_setup ;;
    build)     phase_build ;;
    check)     phase_check ;;
    deploy)    phase_deploy ;;
    all)
        phase_deps
        phase_clone
        phase_crossfile
        phase_setup
        phase_build
        phase_check
        ;;
    *)
        echo "Usage: $0 {deps|clone|crossfile|setup|build|check|deploy|all}"
        exit 1
        ;;
esac

echo "=== Done: ${PHASE} ==="
