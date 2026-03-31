#!/bin/bash
# build-vpp-dpdk-plugin.sh — Build VPP dpdk_plugin.so with DPAA1 PMD support
#
# This script rebuilds ONLY the dpdk_plugin.so from VPP stable/2510,
# linking against our custom DPDK 24.11 with DPAA1 PMDs enabled.
#
# Usage: ./build-vpp-dpdk-plugin.sh <phase>
#   phases: deps, clone, sysroot, configure, build, deploy, all
#
# Run on LXC 200 (Debian 12 aarch64 cross-compile environment)
# Deploy to gateway at 192.168.1.175

set -euo pipefail

# === Configuration ===
VPP_BRANCH="stable/2510"
VPP_REPO="https://github.com/FDio/vpp"
VPP_PATCHES_REPO="https://github.com/vyos/vyos-vpp-patches"
VPP_DIR="/opt/vyos-dev/vpp"
VPP_BUILD_DIR="/opt/vyos-dev/vpp/build-root/build-vpp-native/vpp"
DPDK_PREFIX="/opt/vyos-dev/dpaa-pmd/output/dpdk"
SYSROOT_DIR="/opt/vyos-dev/vpp-sysroot"
GATEWAY="192.168.1.175"
GATEWAY_USER="vyos"
CROSS_COMPILE="aarch64-linux-gnu-"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARN:${NC} $*"; }
err() { echo -e "${RED}[$(date +%H:%M:%S)] ERROR:${NC} $*"; exit 1; }

# === Phase: Install dependencies ===
phase_deps() {
    log "Installing build dependencies..."
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        cmake \
        ninja-build \
        python3 \
        python3-pip \
        python3-ply \
        python3-jsonschema \
        uuid-dev \
        libssl-dev \
        libelf-dev:arm64 \
        libnuma-dev:arm64 \
        libssl-dev:arm64 \
        uuid-dev:arm64 \
        git \
        ca-certificates \
        pkg-config

    # Verify cmake
    cmake --version || err "cmake not installed"
    log "Dependencies installed successfully"
}

# === Phase: Clone VPP and apply VyOS patches ===
phase_clone() {
    log "Cloning VPP ${VPP_BRANCH}..."

    if [ -d "${VPP_DIR}" ]; then
        warn "VPP directory exists, checking branch..."
        cd "${VPP_DIR}"
        current_branch=$(git branch --show-current 2>/dev/null || echo "unknown")
        if [ "$current_branch" = "stable/2510" ]; then
            log "VPP already on correct branch, pulling latest..."
            git pull --ff-only || true
            cd -
            return
        fi
        warn "Wrong branch ($current_branch), removing and re-cloning..."
        rm -rf "${VPP_DIR}"
    fi

    git clone --depth 1 --branch "${VPP_BRANCH}" "${VPP_REPO}" "${VPP_DIR}"

    # Clone VyOS patches
    log "Cloning VyOS VPP patches..."
    local patches_dir="/opt/vyos-dev/vyos-vpp-patches"
    if [ -d "${patches_dir}" ]; then
        cd "${patches_dir}" && git pull --ff-only || true && cd -
    else
        git clone --depth 1 "${VPP_PATCHES_REPO}" "${patches_dir}"
    fi

    # Apply patches
    log "Applying VyOS patches to VPP..."
    cd "${VPP_DIR}"
    for patch in "${patches_dir}"/patches/vpp/*.patch; do
        if [ -f "$patch" ]; then
            log "  Applying: $(basename $patch)"
            git -c user.email=build@local -c user.name=build am "$patch" || {
                warn "Patch $(basename $patch) failed to apply (may already be applied)"
                git am --abort 2>/dev/null || true
            }
        fi
    done
    cd -
    log "VPP cloned and patched"
}

# === Phase: Create cross-compile sysroot from gateway ===
phase_sysroot() {
    log "Creating sysroot from gateway VPP installation..."
    mkdir -p "${SYSROOT_DIR}/usr/include" "${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu"

    # Copy VPP headers from gateway
    log "Copying VPP headers from gateway..."
    rsync -az --delete \
        "${GATEWAY_USER}@${GATEWAY}:/usr/include/vapi/" \
        "${SYSROOT_DIR}/usr/include/vapi/" 2>/dev/null || warn "vapi headers skip"

    rsync -az --delete \
        "${GATEWAY_USER}@${GATEWAY}:/usr/include/vpp_plugins/" \
        "${SYSROOT_DIR}/usr/include/vpp_plugins/" 2>/dev/null || warn "vpp_plugins headers skip"

    rsync -az --delete \
        "${GATEWAY_USER}@${GATEWAY}:/usr/include/vlib/" \
        "${SYSROOT_DIR}/usr/include/vlib/" 2>/dev/null || warn "vlib headers skip"

    rsync -az --delete \
        "${GATEWAY_USER}@${GATEWAY}:/usr/include/vnet/" \
        "${SYSROOT_DIR}/usr/include/vnet/" 2>/dev/null || warn "vnet headers skip"

    rsync -az --delete \
        "${GATEWAY_USER}@${GATEWAY}:/usr/include/vppinfra/" \
        "${SYSROOT_DIR}/usr/include/vppinfra/" 2>/dev/null || warn "vppinfra headers skip"

    rsync -az --delete \
        "${GATEWAY_USER}@${GATEWAY}:/usr/include/svm/" \
        "${SYSROOT_DIR}/usr/include/svm/" 2>/dev/null || warn "svm headers skip"

    rsync -az --delete \
        "${GATEWAY_USER}@${GATEWAY}:/usr/include/vlibapi/" \
        "${SYSROOT_DIR}/usr/include/vlibapi/" 2>/dev/null || warn "vlibapi headers skip"

    rsync -az --delete \
        "${GATEWAY_USER}@${GATEWAY}:/usr/include/vlibmemory/" \
        "${SYSROOT_DIR}/usr/include/vlibmemory/" 2>/dev/null || warn "vlibmemory headers skip"

    # Copy VPP shared libraries from gateway
    log "Copying VPP libraries from gateway..."
    for lib in libvppinfra libvnet libvlib libvlibapi libvlibmemory libvlibmemoryclient libvppapiclient libvppcom; do
        scp "${GATEWAY_USER}@${GATEWAY}:/usr/lib/aarch64-linux-gnu/${lib}.so.25.10.0" \
            "${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu/" 2>/dev/null || warn "Skip ${lib}"
        # Create versioned symlink
        ln -sf "${lib}.so.25.10.0" "${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu/${lib}.so" 2>/dev/null || true
    done

    log "Sysroot created at ${SYSROOT_DIR}"
    log "Headers: $(find ${SYSROOT_DIR}/usr/include -name '*.h' | wc -l) files"
    log "Libs: $(ls ${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu/*.so 2>/dev/null | wc -l) files"
}

# === Phase: Configure VPP cmake for cross-compile with system DPDK ===
phase_configure() {
    log "Configuring VPP cmake for cross-compile..."

    local build_dir="${VPP_DIR}/build-dpdk-plugin"
    mkdir -p "${build_dir}"

    # Create cmake cross-compile toolchain file
    cat > "${build_dir}/toolchain-aarch64.cmake" << 'TOOLCHAIN'
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)
set(CMAKE_ASM_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_AR aarch64-linux-gnu-ar)
set(CMAKE_RANLIB aarch64-linux-gnu-ranlib)
set(CMAKE_STRIP aarch64-linux-gnu-strip)

# Search paths
set(CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY BOTH)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE BOTH)

# Compiler flags
set(CMAKE_C_FLAGS "-mcpu=cortex-a72+crc+crypto -O2 -fPIC" CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS "-mcpu=cortex-a72+crc+crypto -O2 -fPIC" CACHE STRING "" FORCE)
TOOLCHAIN

    # Create a wrapper pkg-config that finds our DPDK
    cat > "${build_dir}/dpdk-pkg-config" << PKGCONF
#!/bin/bash
# Wrapper pkg-config that prioritizes our DPDK
export PKG_CONFIG_PATH="${DPDK_PREFIX}/lib/pkgconfig:\${PKG_CONFIG_PATH:-}"
export PKG_CONFIG_LIBDIR="${DPDK_PREFIX}/lib/pkgconfig:/usr/lib/aarch64-linux-gnu/pkgconfig"
exec pkg-config "\$@"
PKGCONF
    chmod +x "${build_dir}/dpdk-pkg-config"

    # Run cmake configure
    cd "${build_dir}"
    PKG_CONFIG_PATH="${DPDK_PREFIX}/lib/pkgconfig" \
    PKG_CONFIG_LIBDIR="${DPDK_PREFIX}/lib/pkgconfig:/usr/lib/aarch64-linux-gnu/pkgconfig" \
    cmake "${VPP_DIR}/src" \
        -G Ninja \
        -DCMAKE_TOOLCHAIN_FILE="${build_dir}/toolchain-aarch64.cmake" \
        -DCMAKE_BUILD_TYPE=Release \
        -DVPP_USE_SYSTEM_DPDK=ON \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_INSTALL_LIBDIR=lib/aarch64-linux-gnu \
        -DCMAKE_PREFIX_PATH="${SYSROOT_DIR}/usr;${DPDK_PREFIX}" \
        -DCMAKE_LIBRARY_PATH="${SYSROOT_DIR}/usr/lib/aarch64-linux-gnu;${DPDK_PREFIX}/lib" \
        -DCMAKE_INCLUDE_PATH="${SYSROOT_DIR}/usr/include;${DPDK_PREFIX}/include" \
        -DVPP_PLATFORM=default \
        2>&1 | tee configure.log

    log "cmake configure complete. Check ${build_dir}/configure.log"
    cd -
}

# === Phase: Build dpdk_plugin.so ===
phase_build() {
    local build_dir="${VPP_DIR}/build-dpdk-plugin"
    log "Building dpdk_plugin target..."

    cd "${build_dir}"

    # Try to build just the dpdk_plugin target
    ninja -v dpdk_plugin 2>&1 | tee build.log || {
        warn "Direct plugin build failed, trying full plugins..."
        ninja -v plugins 2>&1 | tee -a build.log || {
            err "VPP plugin build failed! Check ${build_dir}/build.log"
        }
    }

    # Find the built plugin
    local plugin_so=$(find "${build_dir}" -name "dpdk_plugin.so" -type f 2>/dev/null | head -1)
    if [ -z "$plugin_so" ]; then
        err "dpdk_plugin.so not found after build!"
    fi

    log "Built: ${plugin_so}"
    file "${plugin_so}"
    ls -lh "${plugin_so}"

    # Verify it contains DPAA symbols
    ${CROSS_COMPILE}readelf -d "${plugin_so}" 2>/dev/null | head -30 || true
    ${CROSS_COMPILE}nm -D "${plugin_so}" 2>/dev/null | grep -i dpaa | head -10 || warn "No DPAA symbols found in dynamic symbols (may be statically linked)"

    # Check for DPAA in the binary
    strings "${plugin_so}" | grep -i dpaa | head -10 || warn "No DPAA strings found"

    log "Build complete!"
    cd -
}

# === Phase: Deploy to gateway ===
phase_deploy() {
    local build_dir="${VPP_DIR}/build-dpdk-plugin"
    local plugin_so=$(find "${build_dir}" -name "dpdk_plugin.so" -type f 2>/dev/null | head -1)

    if [ -z "$plugin_so" ]; then
        err "dpdk_plugin.so not found! Run 'build' phase first."
    fi

    log "Deploying dpdk_plugin.so to gateway..."

    # Backup original on gateway
    ssh "${GATEWAY_USER}@${GATEWAY}" "sudo cp /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so.orig 2>/dev/null || true"

    # Copy new plugin
    scp "${plugin_so}" "${GATEWAY_USER}@${GATEWAY}:/tmp/dpdk_plugin.so"
    ssh "${GATEWAY_USER}@${GATEWAY}" "sudo cp /tmp/dpdk_plugin.so /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so && sudo chmod 644 /usr/lib/aarch64-linux-gnu/vpp_plugins/dpdk_plugin.so"

    # If DPDK is shared-linked, deploy shared libs too
    local dpdk_lib_dir="${DPDK_PREFIX}/lib"
    if ${CROSS_COMPILE}readelf -d "${plugin_so}" 2>/dev/null | grep -q "librte_"; then
        log "Plugin uses shared DPDK — deploying DPDK shared libs..."
        ssh "${GATEWAY_USER}@${GATEWAY}" "sudo mkdir -p /usr/lib/aarch64-linux-gnu/dpdk-dpaa"
        rsync -az "${dpdk_lib_dir}"/librte_*.so* "${GATEWAY_USER}@${GATEWAY}:/tmp/dpdk-libs/"
        ssh "${GATEWAY_USER}@${GATEWAY}" "sudo cp /tmp/dpdk-libs/* /usr/lib/aarch64-linux-gnu/dpdk-dpaa/ && sudo ldconfig"
    else
        log "Plugin has DPDK statically linked (no shared lib deployment needed)"
    fi

    log "Deployment complete. Restart VPP with: sudo systemctl restart vpp"
}

# === Phase: All ===
phase_all() {
    phase_deps
    phase_clone
    phase_sysroot
    phase_configure
    phase_build
    phase_deploy
}

# === Main ===
case "${1:-help}" in
    deps)      phase_deps ;;
    clone)     phase_clone ;;
    sysroot)   phase_sysroot ;;
    configure) phase_configure ;;
    build)     phase_build ;;
    deploy)    phase_deploy ;;
    all)       phase_all ;;
    *)
        echo "Usage: $0 <phase>"
        echo "  deps      — Install cmake, ninja, python3-ply"
        echo "  clone     — Clone VPP stable/2510 + apply VyOS patches"
        echo "  sysroot   — Copy VPP headers/libs from gateway"
        echo "  configure — Run cmake with cross-compile + system DPDK"
        echo "  build     — Build dpdk_plugin.so"
        echo "  deploy    — Deploy to gateway"
        echo "  all       — Run all phases"
        exit 1
        ;;
esac
