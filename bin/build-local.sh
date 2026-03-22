#!/bin/bash
# build-local.sh — Fast local build for VyOS LS1046A dev iteration
#
# Run inside LXC 200 (vyos-builder) on heidi:
#   ./build-local.sh kernel     # Cross-compile kernel → TFTP (~8 min)
#   ./build-local.sh dtb        # Compile DTB only → TFTP (~5 sec)
#   ./build-local.sh vyos1x     # Rebuild vyos-1x package (~20 min)
#   ./build-local.sh iso        # Full ISO build (~25 min)
#   ./build-local.sh extract    # Extract vmlinuz+initrd from existing ISO → TFTP
#
# After kernel/dtb, reboot Mono Gateway with 'run dev_boot' from serial.

set -euo pipefail

WORKSPACE="/opt/vyos-dev"
LINUX_SRC="$WORKSPACE/linux"
BUILD_SCRIPTS="$WORKSPACE/build-scripts"
TFTP_DIR="/srv/tftp"
VYOS_BUILD_DIR="$WORKSPACE/vyos-build"

# Cross-compile settings
export ARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-

# VyOS kernel version (match the upstream branch)
KERNEL_BRANCH="linux-6.6.y"

#--- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

#=============================================================================
# MODE: kernel — Cross-compile kernel for ARM64 → TFTP
#=============================================================================
build_kernel() {
    info "=== Cross-compiling kernel (ARM64 target on AMD64 host) ==="
    local start_time=$SECONDS

    cd "$LINUX_SRC"

    # Ensure we have the VyOS defconfig
    if [ ! -f "$BUILD_SCRIPTS/data/vyos_defconfig" ]; then
        # Generate defconfig from vyos-build repo if available
        if [ -f "$VYOS_BUILD_DIR/scripts/package-build/linux-kernel/config/arm64/vyos_defconfig" ]; then
            cp "$VYOS_BUILD_DIR/scripts/package-build/linux-kernel/config/arm64/vyos_defconfig" \
                "$BUILD_SCRIPTS/data/vyos_defconfig"
            info "Copied vyos_defconfig from vyos-build"
        else
            warn "No vyos_defconfig found. Using kernel defconfig + manual overrides."
            make defconfig
        fi
    fi

    # Apply VyOS defconfig
    if [ -f "$BUILD_SCRIPTS/data/vyos_defconfig" ]; then
        cp "$BUILD_SCRIPTS/data/vyos_defconfig" .config
        info "Applied vyos_defconfig"
    fi

    # Merge VyOS config fragments (00-filesystems, 10-networking, 20-netfilter, etc.)
    local config_frag_dir="$VYOS_BUILD_DIR/scripts/package-build/linux-kernel/config"
    if [ -d "$config_frag_dir" ]; then
        info "Merging VyOS config fragments..."
        for frag in "$config_frag_dir"/*.config; do
            [ -f "$frag" ] || continue
            info "  + $(basename "$frag")"
            cat "$frag" >> .config
        done
        # Resolve any conflicts from fragment merge
        make olddefconfig 2>&1 | tail -5
        info "Config fragments merged and resolved"
    else
        warn "VyOS config fragment directory not found: $config_frag_dir"
    fi

    # Apply LS1046A-specific config additions (same as auto-build.yml)
    info "Applying LS1046A kernel config overrides..."
    scripts/config --enable DEVTMPFS_MOUNT
    # DPAA1/FMan networking (ALL must be =y, never =m)
    scripts/config --enable FSL_FMAN
    scripts/config --enable FSL_DPAA
    scripts/config --enable FSL_DPAA_ETH
    scripts/config --enable FSL_DPAA_MACSEC
    scripts/config --enable FSL_XGMAC_MDIO
    scripts/config --enable PHY_FSL_LYNX_28G
    scripts/config --enable FSL_BMAN
    scripts/config --enable FSL_QMAN
    scripts/config --enable FSL_PAMU
    # PHY drivers
    scripts/config --enable HWMON
    scripts/config --enable MAXLINEAR_GPHY
    # eMMC — MUST use --set-val y to force built-in (--enable keeps =m if already set)
    scripts/config --set-val MMC y
    scripts/config --set-val MMC_BLOCK y
    scripts/config --set-val MMC_SDHCI y
    scripts/config --set-val MMC_SDHCI_PLTFM y
    scripts/config --set-val MMC_SDHCI_OF_ESDHC y
    scripts/config --set-val FSL_EDMA y
    # Serial console
    scripts/config --enable SERIAL_OF_PLATFORM
    # MTD/SPI (FMan firmware access)
    scripts/config --enable MTD
    scripts/config --module MTD_SPI_NOR
    scripts/config --enable SPI
    scripts/config --enable SPI_FSL_DSPI
    scripts/config --enable CDX_BUS
    # Suppress debug noise
    scripts/config --disable DEBUG_PREEMPT
    # CPU frequency
    scripts/config --enable QORIQ_CPUFREQ
    scripts/config --enable CPU_FREQ_DEFAULT_GOV_PERFORMANCE
    scripts/config --disable CPU_FREQ_DEFAULT_GOV_SCHEDUTIL
    # SFP support
    scripts/config --enable SFP
    scripts/config --enable PHYLINK
    scripts/config --enable PHY_FSL_LYNX_10G
    scripts/config --enable I2C_MUX
    scripts/config --enable I2C_MUX_PCA954x
    scripts/config --enable AQUANTIA_PHY
    scripts/config --enable REALTEK_PHY
    # I2C + GPIO
    scripts/config --enable I2C_IMX
    scripts/config --enable GPIO_MPC8XXX
    # Board peripherals
    scripts/config --enable SENSORS_EMC2305
    scripts/config --enable RTC_DRV_PCF2127
    # VyOS initramfs/runtime essentials (not in upstream defconfig)
    scripts/config --enable TMPFS
    scripts/config --enable TMPFS_POSIX_ACL
    scripts/config --enable TMPFS_XATTR
    scripts/config --enable UNIX
    scripts/config --enable NETFILTER
    scripts/config --enable NF_CONNTRACK
    scripts/config --enable NETFILTER_XTABLES
    scripts/config --enable IP_NF_IPTABLES
    scripts/config --enable IP_NF_FILTER
    scripts/config --enable IP_NF_NAT
    scripts/config --enable IP_NF_MANGLE
    scripts/config --enable IP6_NF_IPTABLES
    scripts/config --enable IP6_NF_FILTER
    scripts/config --enable NF_NAT
    scripts/config --enable NF_TABLES
    scripts/config --enable NF_TABLES_INET
    scripts/config --enable NFT_NAT
    scripts/config --enable NFT_MASQ
    scripts/config --enable BRIDGE
    scripts/config --enable VLAN_8021Q
    scripts/config --enable TUN
    scripts/config --enable VETH
    scripts/config --enable BONDING
    scripts/config --enable CGROUPS
    scripts/config --enable MEMCG
    scripts/config --enable CGROUP_SCHED
    # Force critical subsystems built-in for TFTP boot (fragments set =m)
    # --set-val forces =y; --enable may not override =m
    # Filesystems (initramfs needs these before modules are available)
    scripts/config --set-val SQUASHFS y
    scripts/config --set-val SQUASHFS_XZ y
    scripts/config --set-val SQUASHFS_ZSTD y
    scripts/config --set-val SQUASHFS_LZ4 y
    scripts/config --set-val SQUASHFS_ZLIB y
    scripts/config --set-val OVERLAY_FS y
    scripts/config --set-val EXT4_FS y
    scripts/config --set-val FUSE_FS y
    scripts/config --set-val JBD2 y
    # Block devices (loop needed for squashfs mount, dm for VyOS)
    scripts/config --set-val BLK_DEV_LOOP y
    scripts/config --set-val BLK_DEV_DM y
    scripts/config --set-val DM_SNAPSHOT y
    # Network filesystems
    scripts/config --set-val AUTOFS_FS y
    # Force nftables core + conntrack built-in (VyOS router init needs these)
    scripts/config --set-val NF_CONNTRACK y
    scripts/config --set-val NF_NAT y
    scripts/config --set-val NF_TABLES y
    scripts/config --set-val NF_TABLES_INET y
    scripts/config --set-val NF_CT_NETLINK y
    scripts/config --set-val NFT_CT y
    scripts/config --set-val NFT_NAT y
    scripts/config --set-val NFT_MASQ y
    scripts/config --set-val NFT_LOG y
    scripts/config --set-val NFT_LIMIT y
    scripts/config --set-val NFT_REJECT y
    scripts/config --set-val NFT_REJECT_INET y
    scripts/config --set-val NFT_COMPAT y
    scripts/config --set-val NFT_HASH y
    scripts/config --set-val NFT_FIB y
    scripts/config --set-val NFT_FIB_INET y
    scripts/config --set-val NFT_FIB_IPV4 y
    scripts/config --set-val NFT_FIB_IPV6 y
    scripts/config --set-val NFT_FLOW_OFFLOAD y
    scripts/config --set-val NF_FLOW_TABLE y
    scripts/config --set-val NF_FLOW_TABLE_INET y
    scripts/config --set-val NF_LOG_SYSLOG y
    scripts/config --set-val NF_REJECT_IPV4 y
    scripts/config --set-val NF_REJECT_IPV6 y
    scripts/config --set-val NFT_REJECT_IPV4 y
    scripts/config --set-val NFT_REJECT_IPV6 y
    scripts/config --set-val NF_LOG_IPV4 y
    scripts/config --set-val NF_LOG_IPV6 y
    scripts/config --set-val NF_TABLES_BRIDGE y
    scripts/config --set-val NF_CONNTRACK_BRIDGE y
    scripts/config --set-val NFT_BRIDGE_REJECT y
    scripts/config --set-val BRIDGE_NETFILTER y

    # Resolve any dependency issues
    make olddefconfig

    # Remove any broken custom DTS from previous runs (prevents dtbs target failure)
    rm -f arch/arm64/boot/dts/freescale/mono-gateway-dk.dts
    rm -f arch/arm64/boot/dts/freescale/mono-gateway-dk.dtb

    # Build kernel Image + modules (no dtbs — custom DTS breaks mainline dtbs target)
    info "Building kernel Image with $(nproc) threads..."
    make -j$(nproc) Image 2>&1 | tail -20

    # Try to compile our custom DTS separately (non-fatal)
    if [ -f "$BUILD_SCRIPTS/data/dtb/mono-gateway-dk.dts" ]; then
        cp "$BUILD_SCRIPTS/data/dtb/mono-gateway-dk.dts" \
            arch/arm64/boot/dts/freescale/mono-gateway-dk.dts
        info "Attempting custom DTS compilation..."
        if make freescale/mono-gateway-dk.dtb 2>&1; then
            info "  Custom DTB compiled successfully"
        else
            warn "  Custom DTS compilation failed (using pre-built DTB fallback)"
            rm -f arch/arm64/boot/dts/freescale/mono-gateway-dk.dts
        fi
    fi

    # Deploy to TFTP
    info "Deploying to TFTP ($TFTP_DIR)..."
    cp arch/arm64/boot/Image "$TFTP_DIR/vmlinuz"
    info "  vmlinuz: $(stat -c '%s' "$TFTP_DIR/vmlinuz") bytes"

    if [ -f arch/arm64/boot/dts/freescale/mono-gateway-dk.dtb ]; then
        cp arch/arm64/boot/dts/freescale/mono-gateway-dk.dtb "$TFTP_DIR/mono-gw.dtb"
        info "  mono-gw.dtb: $(stat -c '%s' "$TFTP_DIR/mono-gw.dtb") bytes"
    elif [ -f "$BUILD_SCRIPTS/data/dtb/mono-gw.dtb" ]; then
        cp "$BUILD_SCRIPTS/data/dtb/mono-gw.dtb" "$TFTP_DIR/mono-gw.dtb"
        warn "  Using pre-built mono-gw.dtb (DTS compile failed)"
    fi

    local elapsed=$(( SECONDS - start_time ))
    info "=== Kernel build complete in ${elapsed}s ==="
    info "TFTP ready. From U-Boot serial: run dev_boot"
}

#=============================================================================
# MODE: dtb — Compile DTB only → TFTP (seconds)
#=============================================================================
build_dtb() {
    info "=== Compiling DTB only ==="

    cd "$LINUX_SRC"

    if [ ! -f "$BUILD_SCRIPTS/data/dtb/mono-gateway-dk.dts" ]; then
        error "DTS not found: $BUILD_SCRIPTS/data/dtb/mono-gateway-dk.dts"
        exit 1
    fi

    cp "$BUILD_SCRIPTS/data/dtb/mono-gateway-dk.dts" \
        arch/arm64/boot/dts/freescale/mono-gateway-dk.dts

    make freescale/mono-gateway-dk.dtb 2>&1

    cp arch/arm64/boot/dts/freescale/mono-gateway-dk.dtb "$TFTP_DIR/mono-gw.dtb"
    info "mono-gw.dtb: $(stat -c '%s' "$TFTP_DIR/mono-gw.dtb") bytes → $TFTP_DIR"
    info "TFTP ready. From U-Boot serial: run dev_boot"
}

#=============================================================================
# MODE: extract — Extract vmlinuz + initrd from existing ISO → TFTP
#=============================================================================
extract_iso() {
    local iso_path="${2:-}"
    if [ -z "$iso_path" ]; then
        # Find the latest ISO
        iso_path=$(find "$WORKSPACE" -name '*.iso' -type f 2>/dev/null | sort -r | head -1)
    fi

    if [ -z "$iso_path" ] || [ ! -f "$iso_path" ]; then
        error "No ISO found. Provide path: ./build-local.sh extract /path/to/vyos.iso"
        error "Or download: wget -P $WORKSPACE https://github.com/mihakralj/vyos-ls1046a-build/releases/latest/download/vyos-...-LS1046A-arm64.iso"
        exit 1
    fi

    info "=== Extracting from ISO: $iso_path ==="

    local extract_dir=$(mktemp -d)

    # Use 7z to extract (loop mount not permitted in LXC containers)
    info "Extracting with 7z..."
    7z x -o"$extract_dir" "$iso_path" live/ mono-gw.dtb -y 2>&1 | tail -3

    # Extract kernel (prefer non-versioned name)
    local vmlinuz=$(find "$extract_dir/live" -name 'vmlinuz' 2>/dev/null | head -1)
    [ -z "$vmlinuz" ] && vmlinuz=$(find "$extract_dir/live" -name 'vmlinuz-*' 2>/dev/null | head -1)
    if [ -n "$vmlinuz" ]; then
        cp "$vmlinuz" "$TFTP_DIR/vmlinuz"
        info "vmlinuz: $(stat -c '%s' "$TFTP_DIR/vmlinuz") bytes"
    fi

    # Extract initrd (prefer non-versioned name)
    local initrd=$(find "$extract_dir/live" -name 'initrd.img' 2>/dev/null | head -1)
    [ -z "$initrd" ] && initrd=$(find "$extract_dir/live" -name 'initrd.img-*' 2>/dev/null | head -1)
    if [ -n "$initrd" ]; then
        cp "$initrd" "$TFTP_DIR/initrd.img"
        info "initrd.img: $(stat -c '%s' "$TFTP_DIR/initrd.img") bytes"
    fi

    # Extract DTB
    if [ -f "$extract_dir/mono-gw.dtb" ]; then
        cp "$extract_dir/mono-gw.dtb" "$TFTP_DIR/mono-gw.dtb"
        info "mono-gw.dtb: $(stat -c '%s' "$TFTP_DIR/mono-gw.dtb") bytes"
    fi

    # Extract squashfs (for NFS root, optional)
    local squashfs=$(find "$extract_dir/live" -name '*.squashfs' 2>/dev/null | head -1)
    if [ -n "$squashfs" ]; then
        cp "$squashfs" "$WORKSPACE/filesystem.squashfs"
        info "squashfs: $(stat -c '%s' "$WORKSPACE/filesystem.squashfs") bytes → $WORKSPACE/"
    fi

    rm -rf "$extract_dir"

    info "=== TFTP populated from ISO ==="
    ls -lh "$TFTP_DIR/"
}

#=============================================================================
# MODE: vyos1x — Rebuild vyos-1x package (ARM64 via Docker/binfmt)
#=============================================================================
build_vyos1x() {
    info "=== Building vyos-1x package (ARM64 via Docker binfmt) ==="
    local start_time=$SECONDS

    cd "$WORKSPACE"

    # Clone vyos-1x if not present
    if [ ! -d vyos-1x ]; then
        git clone --recursive https://github.com/vyos/vyos-1x \
            -b current --single-branch vyos-1x
    else
        cd vyos-1x && git pull && cd ..
    fi

    # Apply patches
    info "Applying patches..."
    cd vyos-1x
    git checkout -- . 2>/dev/null || true
    cp "$BUILD_SCRIPTS/data/reftree.cache" data/reftree.cache
    for patch in "$BUILD_SCRIPTS"/data/vyos-1x-*.patch; do
        [ -f "$patch" ] || continue
        info "  Applying $(basename "$patch")..."
        patch --no-backup-if-mismatch -p1 < "$patch" || warn "Patch may have already been applied"
    done
    sed -i 's/all: clean copyright/all: clean/' Makefile
    cd ..

    # Build inside the vyos-builder container
    info "Building vyos-1x inside ARM64 container..."
    docker run --rm --platform linux/arm64 \
        -v "$WORKSPACE/vyos-1x:/build/vyos-1x" \
        -w /build/vyos-1x \
        ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64 \
        bash -c './build.py'

    # Collect .deb packages
    local debs=$(find vyos-1x -name '*.deb' -not -name '*-dbg*' -not -name '*-dev*' | wc -l)
    info "Built $debs .deb packages"

    local elapsed=$(( SECONDS - start_time ))
    info "=== vyos-1x build complete in ${elapsed}s ==="
}

#=============================================================================
# MODE: iso — Full ISO build (same as CI, but local on heidi)
#=============================================================================
build_iso() {
    info "=== Full ISO build (local, same as CI) ==="
    local start_time=$SECONDS

    cd "$WORKSPACE"

    # Clone vyos-build if not present
    if [ ! -d vyos-build ]; then
        git clone https://github.com/vyos/vyos-build vyos-build
    else
        cd vyos-build && git pull && cd ..
    fi

    info "Running full build inside vyos-builder container..."
    info "This replicates auto-build.yml logic locally (~25 min)"
    info ""
    info "For now, this mode is a placeholder."
    info "Use the GitHub Actions CI for full ISO builds until this is implemented."
    info ""
    warn "TODO: Implement full local ISO build by extracting auto-build.yml steps"

    local elapsed=$(( SECONDS - start_time ))
    info "=== ISO build placeholder (${elapsed}s) ==="
}

#=============================================================================
# Entrypoint
#=============================================================================
usage() {
    echo "Usage: $0 <mode> [args]"
    echo ""
    echo "Modes:"
    echo "  kernel    Cross-compile kernel → TFTP (~8 min)"
    echo "  dtb       Compile DTB only → TFTP (~5 sec)"
    echo "  extract   Extract vmlinuz+initrd from ISO → TFTP"
    echo "  vyos1x    Rebuild vyos-1x package (~20 min)"
    echo "  iso       Full ISO build (~25 min)"
    echo ""
    echo "After kernel/dtb, reboot Mono Gateway with 'run dev_boot' from serial."
}

MODE="${1:-}"
case "$MODE" in
    kernel)  build_kernel ;;
    dtb)     build_dtb ;;
    extract) extract_iso "$@" ;;
    vyos1x)  build_vyos1x ;;
    iso)     build_iso ;;
    *)       usage; exit 1 ;;
esac
