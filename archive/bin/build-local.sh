#!/bin/bash
# bin/build-local.sh — VyOS LS1046A local dev build
#
# Designed to run inside LXC 200 (root@192.168.1.137).
# SSH in or use VS Code Remote-SSH, then:
#   cd /opt/vyos-dev && ./build-local.sh kernel
#
# Modes:
#   kernel          Cross-compile kernel + DTB → /srv/tftp/     (~2 min incr / ~8 min full)
#   dtb             Compile DTB only → /srv/tftp/               (~5 sec)
#   extract [iso]   Extract vmlinuz+initrd+DTB from ISO → TFTP  (~30 sec)
#   vyos1x          Rebuild vyos-1x .deb via Docker binfmt      (~20 min)
#   iso             Full ISO via Docker — same steps as CI       (~60 min, unsigned)

set -eo pipefail

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; YEL='\033[1;33m'; GRN='\033[0;32m'; BLU='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLU}[•]${NC} $*"; }
ok()    { echo -e "${GRN}[✓]${NC} $*"; }
warn()  { echo -e "${YEL}[!]${NC} $*"; }
die()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
hdr()   { echo -e "\n${BLU}━━━ $* ━━━${NC}"; }
elapsed() { echo -e "${GRN}[✓]${NC} Done in $(( SECONDS - _T0 ))s"; }

# ─── Configuration ────────────────────────────────────────────────────────────
MODE="${1:-help}"
WORK_DIR="/opt/vyos-dev"
TFTP_DIR="/srv/tftp"
LINUX_DIR="$WORK_DIR/linux-6.6.y"
VYOS_BUILD_DIR="$WORK_DIR/vyos-build"
BUILD_REPO_DIR="$WORK_DIR/vyos-ls1046a-build"

CROSS_COMPILE="aarch64-linux-gnu-"
ARCH="arm64"
NPROC=$(nproc)

LINUX_REPO="https://github.com/vyos/vyos-linux-kernel.git"
LINUX_BRANCH="linux-6.6.y"
VYOS_BUILD_REPO="https://github.com/vyos/vyos-build.git"
VYOS_BUILD_BRANCH="current"
BUILD_REPO="https://github.com/mihakralj/vyos-ls1046a-build.git"

BUILDER_IMAGE="ghcr.io/huihuimoe/vyos-arm64-build/vyos-builder:current-arm64"

# ─── Help ────────────────────────────────────────────────────────────────────
[[ "$MODE" == "help" || "$MODE" == "-h" || "$MODE" == "--help" ]] && {
    echo "Usage: $0 <mode> [args]"
    echo ""
    echo "  kernel          Cross-compile kernel + DTB → /srv/tftp/ (fast TFTP dev loop)"
    echo "  dtb             Compile DTB only → /srv/tftp/"
    echo "  extract [iso]   Extract vmlinuz+initrd+DTB from ISO → /srv/tftp/"
    echo "  vyos1x          Rebuild vyos-1x .deb (same CI steps, unsigned)"
    echo "  iso             Full ISO build (same CI steps, unsigned)"
    echo ""
    echo "  For signed releases: gh workflow run 'VyOS LS1046A build' --ref main"
    echo ""
    echo "Paths:"
    echo "  LINUX_DIR    = $LINUX_DIR"
    echo "  VYOS_BUILD   = $VYOS_BUILD_DIR"
    echo "  BUILD_REPO   = $BUILD_REPO_DIR"
    echo "  TFTP         = $TFTP_DIR"
    echo "  NPROC        = $NPROC"
    exit 0
}

# ─── Helper: ensure git repo present and up to date ──────────────────────────
ensure_repo() {
    local dir="$1" url="$2" branch="${3:-main}" desc="$4"
    if [[ ! -d "$dir/.git" ]]; then
        info "Cloning $desc …"
        git clone --depth=1 --branch "$branch" "$url" "$dir"
    else
        info "Updating $desc …"
        git -C "$dir" fetch --depth=1 origin "$branch" 2>/dev/null \
            || warn "Fetch failed (offline?), using cached"
        git -C "$dir" reset --hard "origin/$branch" 2>/dev/null || true
    fi
}

# ─── Helper: apply patch only if not already applied ─────────────────────────
apply_patch() {
    local patchfile="$1" dir="${2:-.}"
    local name; name=$(basename "$patchfile")
    if patch -d "$dir" --dry-run -p1 -R --quiet < "$patchfile" 2>/dev/null; then
        info "Patch already applied: $name"
    elif patch -d "$dir" --dry-run -p1 --quiet < "$patchfile" 2>/dev/null; then
        info "Applying patch: $name"
        patch --no-backup-if-mismatch -p1 -d "$dir" < "$patchfile"
    else
        warn "Patch cannot apply (conflict?), skipping: $name"
    fi
}

# ═══════════════════════════════════════════════════════════════════════════════
# setup_vyos_build — mirrors CI "Fix vyos-build" job step exactly
#
# Modifies the local vyos-build checkout with all LS1046A-specific changes
# that auto-build.yml applies before running build.py / build-vyos-image.
# Call this after ensure_repo() for vyos-build and vyos-ls1046a-build.
# ═══════════════════════════════════════════════════════════════════════════════
setup_vyos_build() {
    hdr "Setting up vyos-build (mirrors CI 'Fix vyos-build' step)"

    local VYOS1X_BUILD="$VYOS_BUILD_DIR/scripts/package-build/vyos-1x"
    local PATCH_STAGING="$VYOS1X_BUILD/ls1046a-patches"
    local KERNEL_BUILD="$VYOS_BUILD_DIR/scripts/package-build/linux-kernel"
    local KERNEL_PATCHES="$KERNEL_BUILD/patches/kernel"
    local DEFCONFIG="$KERNEL_BUILD/config/arm64/vyos_defconfig"
    local CHROOT="$VYOS_BUILD_DIR/data/live-build-config/includes.chroot"
    local HOOKS="$VYOS_BUILD_DIR/data/live-build-config/hooks/live"

    # ── vyos-1x: package.toml with pre_build_hook that applies our patches ──
    info "Staging vyos-1x patches …"
    mkdir -p "$PATCH_STAGING"
    cp "$BUILD_REPO_DIR"/data/vyos-1x-*.patch "$PATCH_STAGING/"
    cp "$BUILD_REPO_DIR/data/reftree.cache" "$PATCH_STAGING/"
    cat > "$VYOS1X_BUILD/package.toml" <<'TOML'
[[packages]]
name = "vyos-1x"
commit_id = "current"
scm_url = "https://github.com/vyos/vyos-1x.git"
pre_build_hook = """
  set -ex
  cp ../ls1046a-patches/reftree.cache data/reftree.cache
  sed -i 's/all: clean copyright/all: clean/' Makefile
  for p in ../ls1046a-patches/vyos-1x-*.patch; do
    patch --no-backup-if-mismatch -p1 < "$p"
  done
"""
TOML

    # ── vyos-build patches ──
    info "Applying vyos-build patches …"
    apply_patch "$BUILD_REPO_DIR/data/vyos-build-005-add_vim_link.patch" "$VYOS_BUILD_DIR"
    apply_patch "$BUILD_REPO_DIR/data/vyos-build-007-no_sbsign.patch"    "$VYOS_BUILD_DIR"

    # ── default configs ──
    info "Copying config.boot files …"
    cp "$BUILD_REPO_DIR/data/config.boot.default" "$CHROOT/opt/vyatta/etc/"
    cp "$BUILD_REPO_DIR/data/config.boot.dhcp"    "$CHROOT/opt/vyatta/etc/"

    # ── LS1046A kernel config overrides (appended to vyos_defconfig) ──
    info "Appending LS1046A kernel config overrides to vyos_defconfig …"
    [[ -f "$DEFCONFIG" ]] || die "vyos_defconfig not found: $DEFCONFIG"
    # Remove upstream explicit disables that conflict with our overrides.
    sed -i '/CONFIG_DEVTMPFS_MOUNT/d'          "$DEFCONFIG"
    sed -i '/CONFIG_CPU_FREQ_DEFAULT_GOV/d'     "$DEFCONFIG"
    sed -i '/CONFIG_STRICT_DEVMEM/d'            "$DEFCONFIG"
    sed -i '/CONFIG_IO_STRICT_DEVMEM/d'         "$DEFCONFIG"
    sed -i '/CONFIG_DEBUG_PREEMPT/d'             "$DEFCONFIG"
    printf '%s\n' \
        '# === LS1046A / NXP Layerscape DPAA1 (Mono Gateway DK) ===' \
        'CONFIG_DEVTMPFS_MOUNT=y' \
        'CONFIG_FSL_FMAN=y' \
        'CONFIG_FSL_DPAA=y' \
        'CONFIG_FSL_DPAA_ETH=y' \
        'CONFIG_FSL_DPAA_MACSEC=y' \
        'CONFIG_FSL_XGMAC_MDIO=y' \
        'CONFIG_PHY_FSL_LYNX_28G=y' \
        'CONFIG_FSL_BMAN=y' \
        'CONFIG_FSL_QMAN=y' \
        'CONFIG_FSL_PAMU=y' \
        'CONFIG_HWMON=y' \
        'CONFIG_MAXLINEAR_GPHY=y' \
        'CONFIG_MMC_SDHCI_OF_ESDHC=y' \
        'CONFIG_FSL_EDMA=y' \
        'CONFIG_SERIAL_OF_PLATFORM=y' \
        'CONFIG_MTD=y' \
        'CONFIG_MTD_SPI_NOR=y' \
        'CONFIG_SPI=y' \
        'CONFIG_SPI_FSL_DSPI=y' \
        'CONFIG_SPI_FSL_QSPI=y' \
        'CONFIG_SPI_FSL_QUADSPI=y' \
        'CONFIG_CDX_BUS=y' \
        '# CONFIG_DEBUG_PREEMPT is not set' \
        'CONFIG_QORIQ_CPUFREQ=y' \
        'CONFIG_CPU_FREQ_DEFAULT_GOV_PERFORMANCE=y' \
        '# CONFIG_CPU_FREQ_DEFAULT_GOV_SCHEDUTIL is not set' \
        '# === SFP support (10G copper/fiber transceivers) ===' \
        'CONFIG_SFP=y' \
        'CONFIG_PHYLINK=y' \
        'CONFIG_PHY_FSL_LYNX_10G=y' \
        'CONFIG_I2C_MUX=y' \
        'CONFIG_I2C_MUX_PCA954x=y' \
        'CONFIG_AQUANTIA_PHY=y' \
        'CONFIG_REALTEK_PHY=y' \
        '# === I2C + GPIO controllers ===' \
        'CONFIG_I2C_IMX=y' \
        'CONFIG_GPIO_MPC8XXX=y' \
        '# === Board peripherals (fan, thermal, RTC, power sensors) ===' \
        'CONFIG_SENSORS_EMC2305=y' \
        'CONFIG_SENSORS_INA2XX=y' \
        'CONFIG_RTC_DRV_PCF2127=y' \
        '# === DPDK USDPAA support ===' \
        'CONFIG_FSL_USDPAA_MAINLINE=y' \
        '# === Disable STRICT_DEVMEM for DPDK DPAA PMD ===' \
        '# CONFIG_STRICT_DEVMEM is not set' \
        '# CONFIG_IO_STRICT_DEVMEM is not set' \
        '# === USB live boot support ===' \
        'CONFIG_USB_STORAGE=y' \
        'CONFIG_VFAT_FS=y' \
        'CONFIG_FAT_FS=y' \
        'CONFIG_NLS_CODEPAGE_437=y' \
        'CONFIG_NLS_ISO8859_1=y' \
        'CONFIG_NLS_UTF8=y' \
        >> "$DEFCONFIG"

    # ── Kernel patches staging ──
    info "Staging kernel patches …"
    mkdir -p "$KERNEL_PATCHES"
    cp "$BUILD_REPO_DIR/data/kernel-patches/4002-hwmon-ina2xx-add-INA234-support.patch"    "$KERNEL_PATCHES/"
    cp "$BUILD_REPO_DIR/data/kernel-patches/9001-usdpaa-bman-qman-exports-and-driver.patch" "$KERNEL_PATCHES/"

    # fsl_usdpaa_mainline.c: too large for a unified diff — copy to kernel build dir,
    # then inject a cp command into build-kernel.sh (before signing cert setup).
    cp "$BUILD_REPO_DIR/data/kernel-patches/fsl_usdpaa_mainline.c" "$KERNEL_BUILD/"
    awk '/# Change name of Signing Cert/ {
        print "# Copy USDPAA mainline driver source (too large for unified diff)"
        print "if [ -f \"${CWD}/fsl_usdpaa_mainline.c\" ]; then"
        print "  echo \"I: Copy fsl_usdpaa_mainline.c to drivers/soc/fsl/qbman/\""
        print "  cp \"${CWD}/fsl_usdpaa_mainline.c\" drivers/soc/fsl/qbman/fsl_usdpaa_mainline.c"
        print "fi"
    } { print }' "$KERNEL_BUILD/build-kernel.sh" > /tmp/build-kernel-patched.sh
    mv /tmp/build-kernel-patched.sh "$KERNEL_BUILD/build-kernel.sh"
    chmod +x "$KERNEL_BUILD/build-kernel.sh"

    # ── Remove --uefi-secure-boot (U-Boot boards have no EFI runtime) ──
    local VYOS1X=""
    find "$VYOS1X" -name '*.py' -exec grep -l 'uefi.secure.boot' {} \; \
        | xargs -r sed -i "s/'--uefi-secure-boot'[,]\?//g" 2>/dev/null || true

    # ── Serial console: revert ttyAMA0 → ttyS0 (8250 UART at 0x21c0500) ──
    info "Fixing serial console references (ttyAMA0 → ttyS0) …"
    sed -i 's/ttyAMA0/ttyS0/g' \
        "$VYOS_BUILD_DIR/data/live-build-config/hooks/live/01-live-serial.binary" \
        "$VYOS_BUILD_DIR/data/live-build-config/includes.chroot/opt/vyatta/etc/grub/default-union-grub-entry" \
        2>/dev/null || true

    # ── MOK certificate (skip — private key is a CI secret) ──
    if [[ -f "$BUILD_REPO_DIR/data/mok/MOK.key" ]]; then
        cp "$BUILD_REPO_DIR/data/mok/MOK.key" "$VYOS_BUILD_DIR/data/certificates/vyos-dev-2025-linux.key"
        cp "$BUILD_REPO_DIR/data/mok/MOK.pem" "$VYOS_BUILD_DIR/data/certificates/vyos-dev-2025-linux.pem"
        info "MOK certificate installed"
    else
        warn "MOK.key not found — kernel modules will not be Secure Boot signed"
    fi

    # ── Minisign public key ──
    mkdir -p "$CHROOT/usr/share/vyos/keys"
    cp "$BUILD_REPO_DIR/data/vyos-ls1046a.minisign.pub" \
        "$CHROOT/usr/share/vyos/keys/"

    # ── DTB: ISO root (U-Boot fatload) + squashfs /boot/ (install_image copies it) ──
    info "Staging DTB …"
    mkdir -p "$VYOS_BUILD_DIR/data/live-build-config/includes.binary"
    cp "$BUILD_REPO_DIR/data/dtb/mono-gw.dtb" \
        "$VYOS_BUILD_DIR/data/live-build-config/includes.binary/mono-gw.dtb"
    mkdir -p "$CHROOT/boot"
    cp "$BUILD_REPO_DIR/data/dtb/mono-gw.dtb" "$CHROOT/boot/mono-gw.dtb"

    # ── U-Boot env config for fw_setenv ──
    cp "$BUILD_REPO_DIR/data/scripts/fw_env.config" "$CHROOT/etc/fw_env.config"

    # ── vyos-postinstall helper ──
    mkdir -p "$CHROOT/usr/local/bin"
    cp "$BUILD_REPO_DIR/data/scripts/vyos-postinstall" "$CHROOT/usr/local/bin/vyos-postinstall"
    chmod +x "$CHROOT/usr/local/bin/vyos-postinstall"

    # ── systemd service for vyos-postinstall (enabled via tmpfiles.d, not systemctl) ──
    printf '%s\n' \
        '[Unit]' \
        'Description=Sync /boot/vyos.env with running VyOS image (LS1046A)' \
        'After=local-fs.target' \
        'ConditionPathExists=/proc/device-tree/compatible' \
        '' \
        '[Service]' \
        'Type=oneshot' \
        'ExecStart=/usr/local/bin/vyos-postinstall' \
        'RemainAfterExit=yes' \
        'StandardOutput=journal' \
        > "$CHROOT/etc/systemd/system/vyos-postinstall.service"
    mkdir -p "$CHROOT/usr/lib/tmpfiles.d"
    printf '%s\n' \
        '# Create systemd .wants symlink for vyos-postinstall at boot.' \
        '# Cannot use systemctl enable — live-build squashfs converts the' \
        '# symlink to a regular file, which systemd ignores.' \
        'L+ /etc/systemd/system/multi-user.target.wants/vyos-postinstall.service - - - - /etc/systemd/system/vyos-postinstall.service' \
        > "$CHROOT/usr/lib/tmpfiles.d/vyos-postinstall.conf"

    # ── Fan control (EMC2305 PWM via standard fancontrol daemon) ──
    info "Staging fancontrol …"
    cp "$BUILD_REPO_DIR/data/scripts/fancontrol.conf" "$CHROOT/etc/fancontrol"
    cp "$BUILD_REPO_DIR/data/scripts/fancontrol-setup.sh" "$CHROOT/usr/local/bin/fancontrol-setup"
    chmod +x "$CHROOT/usr/local/bin/fancontrol-setup"
    mkdir -p "$CHROOT/etc/systemd/system/fancontrol.service.d"
    printf '%s\n' \
        '[Service]' \
        'ExecStartPre=/usr/local/bin/fancontrol-setup' \
        > "$CHROOT/etc/systemd/system/fancontrol.service.d/hwmon-setup.conf"

    # Chroot hook: install fancontrol package and enable
    cat > "$HOOKS/98-fancontrol.chroot" <<'FANHOOK'
#!/bin/bash
apt-get update -qq && apt-get install -y --no-install-recommends fancontrol
systemctl enable fancontrol.service
apt-get clean
FANHOOK
    chmod +x "$HOOKS/98-fancontrol.chroot"

    # Chroot hook: mask ACPI (useless on ARM64/DT), keep kexec active
    cat > "$HOOKS/99-mask-services.chroot" <<'MASKHOOK'
#!/bin/bash
# kexec: NOT masked — mainline 6.6 QBMan fix allows kexec on DPAA1.
# Remove SysV init scripts to prevent duplicate systemd unit generation.
rm -f /etc/init.d/kexec-load /etc/init.d/kexec
# ACPI daemon: useless on ARM64/DeviceTree platforms
ln -sf /dev/null /etc/systemd/system/acpid.service
ln -sf /dev/null /etc/systemd/system/acpid.socket
ln -sf /dev/null /etc/systemd/system/acpid.path
MASKHOOK
    chmod +x "$HOOKS/99-mask-services.chroot"

    ok "vyos-build setup complete"
}

# ═══════════════════════════════════════════════════════════════════════════════
# TFTP fast-path helpers (kernel / dtb / extract)
# These do NOT use Docker — cross-compile directly on LXC 200 for speed.
# ═══════════════════════════════════════════════════════════════════════════════

ensure_kernel_source() {
    hdr "Kernel source"
    ensure_repo "$LINUX_DIR"       "$LINUX_REPO"       "$LINUX_BRANCH"       "vyos-linux-kernel"
    ensure_repo "$VYOS_BUILD_DIR"  "$VYOS_BUILD_REPO"  "$VYOS_BUILD_BRANCH"  "vyos-build"
    ensure_repo "$BUILD_REPO_DIR"  "$BUILD_REPO"       "main"                "vyos-ls1046a-build"
}

apply_kernel_patches() {
    hdr "Kernel patches"
    cd "$LINUX_DIR"

    local patch_dir="$BUILD_REPO_DIR/data/kernel-patches"
    [[ -d "$patch_dir" ]] || die "Patch dir not found: $patch_dir"

    apply_patch "$patch_dir/4002-hwmon-ina2xx-add-INA234-support.patch"
    apply_patch "$patch_dir/9001-usdpaa-bman-qman-exports-and-driver.patch"

    local usdpaa_src="$patch_dir/fsl_usdpaa_mainline.c"
    local usdpaa_dst="$LINUX_DIR/drivers/soc/fsl/qbman/fsl_usdpaa_mainline.c"
    if [[ -f "$usdpaa_src" ]]; then
        if [[ ! -f "$usdpaa_dst" ]] || ! diff -q "$usdpaa_src" "$usdpaa_dst" &>/dev/null; then
            info "Copying fsl_usdpaa_mainline.c → drivers/soc/fsl/qbman/"
            cp "$usdpaa_src" "$usdpaa_dst"
        else
            info "fsl_usdpaa_mainline.c already up to date"
        fi
    else
        warn "fsl_usdpaa_mainline.c not found — FSL_USDPAA_MAINLINE driver will not build"
    fi
    ok "Patches applied"
}

prepare_config() {
    hdr "Kernel config"
    cd "$LINUX_DIR"

    local defconfig="$VYOS_BUILD_DIR/scripts/package-build/linux-kernel/config/arm64/vyos_defconfig"
    local frag_dir="$VYOS_BUILD_DIR/scripts/package-build/linux-kernel/config"

    [[ -f "$defconfig" ]] || die "Missing vyos_defconfig: $defconfig"

    info "Copying vyos_defconfig …"
    cp "$defconfig" .config

    info "Merging VyOS config fragments …"
    shopt -s nullglob
    for frag in "$frag_dir"/*.config; do
        info "  + $(basename "$frag")"
        cat "$frag" >> .config
    done
    shopt -u nullglob

    make ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" olddefconfig

    info "Applying LS1046A overrides (--set-val forces =y; --enable cannot upgrade =m) …"
    scripts/config --set-val FSL_FMAN                       y
    scripts/config --set-val FSL_DPAA                       y
    scripts/config --set-val FSL_DPAA_ETH                   y
    scripts/config --set-val FSL_DPAA_MACSEC                y
    scripts/config --set-val FSL_XGMAC_MDIO                 y
    scripts/config --set-val PHY_FSL_LYNX_28G               y
    scripts/config --set-val FSL_BMAN                       y
    scripts/config --set-val FSL_QMAN                       y
    scripts/config --set-val FSL_PAMU                       y
    scripts/config --set-val QORIQ_CPUFREQ                  y
    scripts/config --set-val CPU_FREQ_DEFAULT_GOV_PERFORMANCE y
    scripts/config --disable CPU_FREQ_DEFAULT_GOV_SCHEDUTIL
    scripts/config --set-val DEVTMPFS_MOUNT                 y
    scripts/config --set-val HWMON                          y
    scripts/config --set-val MAXLINEAR_GPHY                 y
    scripts/config --set-val MMC_SDHCI_OF_ESDHC             y
    scripts/config --set-val FSL_EDMA                       y
    scripts/config --set-val SERIAL_OF_PLATFORM             y
    scripts/config --set-val SENSORS_EMC2305                y
    scripts/config --set-val SENSORS_INA2XX                 y
    scripts/config --set-val RTC_DRV_PCF2127                y
    scripts/config --set-val MTD                            y
    scripts/config --set-val MTD_SPI_NOR                    y
    scripts/config --set-val SPI                            y
    scripts/config --set-val SPI_FSL_DSPI                   y
    scripts/config --set-val SPI_FSL_QSPI                   y
    scripts/config --set-val SPI_FSL_QUADSPI                y
    scripts/config --set-val CDX_BUS                        y
    scripts/config --set-val SFP                            y
    scripts/config --set-val PHYLINK                        y
    scripts/config --set-val PHY_FSL_LYNX_10G               y
    scripts/config --set-val I2C_MUX                        y
    scripts/config --set-val I2C_MUX_PCA954x                y
    scripts/config --set-val AQUANTIA_PHY                   y
    scripts/config --set-val REALTEK_PHY                    y
    scripts/config --set-val I2C_IMX                        y
    scripts/config --set-val GPIO_MPC8XXX                   y
    scripts/config --set-val FSL_USDPAA_MAINLINE            y
    scripts/config --disable STRICT_DEVMEM
    scripts/config --disable IO_STRICT_DEVMEM
    scripts/config --set-val USB_STORAGE                    y
    scripts/config --set-val VFAT_FS                        y
    scripts/config --set-val FAT_FS                         y
    scripts/config --set-val NLS_CODEPAGE_437               y
    scripts/config --set-val NLS_ISO8859_1                  y
    scripts/config --set-val NLS_UTF8                       y
    scripts/config --disable DEBUG_PREEMPT

    make ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" olddefconfig
    ok "Config ready"
}

build_kernel() {
    hdr "Building kernel (${NPROC} jobs)"
    cd "$LINUX_DIR"
    _T0=$SECONDS
    make ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" -j"$NPROC" Image 2>&1 | tail -5
    elapsed
    local img="$LINUX_DIR/arch/arm64/boot/Image"
    [[ -f "$img" ]] || die "Kernel build failed — arch/arm64/boot/Image not found"
    mkdir -p "$TFTP_DIR"
    cp "$img" "$TFTP_DIR/vmlinuz"
    ok "vmlinuz → $TFTP_DIR/vmlinuz ($(du -sh "$TFTP_DIR/vmlinuz" | cut -f1))"
}

build_dtb() {
    hdr "Building DTB"
    cd "$LINUX_DIR"
    local dts_src="$BUILD_REPO_DIR/data/dtb/mono-gateway-dk.dts"
    local dts_dst="$LINUX_DIR/arch/arm64/boot/dts/freescale/mono-gateway-dk.dts"
    local dtb_out="$LINUX_DIR/arch/arm64/boot/dts/freescale/mono-gateway-dk.dtb"

    [[ -f "$dts_src" ]] || die "DTS not found: $dts_src"
    cp "$dts_src" "$dts_dst"

    _T0=$SECONDS
    make ARCH="$ARCH" CROSS_COMPILE="$CROSS_COMPILE" freescale/mono-gateway-dk.dtb 2>&1 || {
        warn "DTB compile failed — falling back to pre-built data/dtb/mono-gw.dtb"
        mkdir -p "$TFTP_DIR"
        cp "$BUILD_REPO_DIR/data/dtb/mono-gw.dtb" "$TFTP_DIR/mono-gw.dtb"
        ok "mono-gw.dtb (pre-built) → $TFTP_DIR/mono-gw.dtb"
        return
    }
    elapsed

    [[ -f "$dtb_out" ]] || {
        warn "DTB not found after build — falling back to pre-built"
        cp "$BUILD_REPO_DIR/data/dtb/mono-gw.dtb" "$TFTP_DIR/mono-gw.dtb"
        return
    }
    mkdir -p "$TFTP_DIR"
    cp "$dtb_out" "$TFTP_DIR/mono-gw.dtb"
    ok "mono-gw.dtb → $TFTP_DIR/mono-gw.dtb ($(du -sh "$TFTP_DIR/mono-gw.dtb" | cut -f1))"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Modes
# ═══════════════════════════════════════════════════════════════════════════════

cmd_kernel() {
    _T0=$SECONDS
    ensure_kernel_source
    apply_kernel_patches
    prepare_config
    build_kernel
    build_dtb
    echo ""
    ok "TFTP ready:"
    ls -lh "$TFTP_DIR/"
    echo ""
    info "From U-Boot serial: run dev_boot"
    echo -e "  Total: ${GRN}$(( SECONDS - _T0 ))s${NC}"
}

cmd_dtb() {
    _T0=$SECONDS
    ensure_repo "$BUILD_REPO_DIR" "$BUILD_REPO" "main" "vyos-ls1046a-build"
    [[ -d "$LINUX_DIR" ]] || die "Kernel source not found — run '$0 kernel' first"
    ensure_repo "$VYOS_BUILD_DIR" "$VYOS_BUILD_REPO" "$VYOS_BUILD_BRANCH" "vyos-build"
    build_dtb
    ok "TFTP DTB updated in $(( SECONDS - _T0 ))s"
    info "From U-Boot serial: run dev_boot"
}

cmd_extract() {
    _T0=$SECONDS
    local iso="${2:-}"
    if [[ -z "$iso" ]]; then
        iso=$(ls -1t "$WORK_DIR"/*.iso 2>/dev/null | head -1 || true)
        [[ -n "$iso" ]] || die "No ISO specified and none found in $WORK_DIR"
        info "Auto-selected: $iso"
    fi
    [[ -f "$iso" ]] || die "ISO not found: $iso"
    command -v 7z &>/dev/null || die "7z not installed (apt-get install -y p7zip-full)"

    hdr "Extracting from $(basename "$iso")"
    mkdir -p "$TFTP_DIR"

    info "Extracting vmlinuz …"
    if 7z e -so "$iso" live/vmlinuz > "$TFTP_DIR/vmlinuz" 2>/dev/null \
            && [[ -s "$TFTP_DIR/vmlinuz" ]]; then
        ok "vmlinuz ($(du -sh "$TFTP_DIR/vmlinuz" | cut -f1))"
    else
        local kver
        kver=$(7z l "$iso" live/ 2>/dev/null | grep -oP 'vmlinuz-[\d\.\-]+vyos' | head -1 || true)
        [[ -n "$kver" ]] || die "Cannot find vmlinuz in ISO"
        7z e -so "$iso" "live/$kver" > "$TFTP_DIR/vmlinuz"
        ok "vmlinuz ← live/$kver ($(du -sh "$TFTP_DIR/vmlinuz" | cut -f1))"
    fi

    info "Extracting initrd.img …"
    if 7z e -so "$iso" live/initrd.img > "$TFTP_DIR/initrd.img" 2>/dev/null \
            && [[ -s "$TFTP_DIR/initrd.img" ]]; then
        ok "initrd.img ($(du -sh "$TFTP_DIR/initrd.img" | cut -f1))"
    else
        local kver
        kver=$(7z l "$iso" live/ 2>/dev/null | grep -oP 'initrd\.img-[\d\.\-]+vyos' | head -1 || true)
        [[ -n "$kver" ]] || die "Cannot find initrd.img in ISO"
        7z e -so "$iso" "live/$kver" > "$TFTP_DIR/initrd.img"
        ok "initrd.img ← live/$kver ($(du -sh "$TFTP_DIR/initrd.img" | cut -f1))"
    fi

    info "Extracting mono-gw.dtb …"
    if 7z e -so "$iso" mono-gw.dtb > "$TFTP_DIR/mono-gw.dtb" 2>/dev/null \
            && [[ -s "$TFTP_DIR/mono-gw.dtb" ]]; then
        ok "mono-gw.dtb ($(du -sh "$TFTP_DIR/mono-gw.dtb" | cut -f1))"
    else
        ensure_repo "$BUILD_REPO_DIR" "$BUILD_REPO" "main" "vyos-ls1046a-build"
        cp "$BUILD_REPO_DIR/data/dtb/mono-gw.dtb" "$TFTP_DIR/mono-gw.dtb"
        ok "mono-gw.dtb (pre-built from repo)"
    fi

    echo ""
    ok "TFTP ready from ISO in $(( SECONDS - _T0 ))s:"
    ls -lh "$TFTP_DIR/"
    info "From U-Boot serial: run dev_boot"
}

# ── vyos1x: build vyos-1x .deb via Docker — same CI logic, unsigned ──────────
cmd_vyos1x() {
    _T0=$SECONDS
    hdr "Rebuilding vyos-1x (Docker ARM64, same CI steps)"
    command -v docker &>/dev/null || die "Docker not installed"
    [[ -f /proc/sys/fs/binfmt_misc/qemu-aarch64 ]] || \
        warn "qemu-aarch64 binfmt not registered — install qemu-user-static on Proxmox host"

    ensure_repo "$BUILD_REPO_DIR" "$BUILD_REPO"       "main"                "vyos-ls1046a-build"
    ensure_repo "$VYOS_BUILD_DIR" "$VYOS_BUILD_REPO"  "$VYOS_BUILD_BRANCH"  "vyos-build"

    setup_vyos_build

    info "Building vyos-1x via Docker ($BUILDER_IMAGE) …"
    docker run --rm --platform linux/arm64 \
        -v "$VYOS_BUILD_DIR:/vyos-build" \
        -w /vyos-build/scripts/package-build/vyos-1x \
        "$BUILDER_IMAGE" \
        bash -c "set -ex; ./build.py"

    local debs
    debs=$(find "$VYOS_BUILD_DIR/scripts/package-build/vyos-1x" -name "vyos-1x_*.deb" 2>/dev/null || true)
    if [[ -n "$debs" ]]; then
        ok "Built:"
        echo "$debs" | while IFS= read -r d; do echo "  $d"; done
    else
        warn "No .deb found — check Docker build output"
    fi
    echo -e "  Total: ${GRN}$(( SECONDS - _T0 ))s${NC}"
}

# ── iso: full ISO build — mirrors CI job steps exactly, unsigned ──────────────
cmd_iso() {
    _T0=$SECONDS
    hdr "Full ISO build (mirrors CI steps — unsigned)"
    warn "This produces an UNSIGNED ISO — no MOK.key (CI secret) or minisign."
    warn "For signed production releases: gh workflow run 'VyOS LS1046A build' --ref main"
    echo ""
    command -v docker &>/dev/null || die "Docker not installed"
    [[ -f /proc/sys/fs/binfmt_misc/qemu-aarch64 ]] || \
        warn "qemu-aarch64 binfmt not registered — install qemu-user-static on Proxmox host"

    ensure_repo "$BUILD_REPO_DIR" "$BUILD_REPO"       "main"                "vyos-ls1046a-build"
    ensure_repo "$VYOS_BUILD_DIR" "$VYOS_BUILD_REPO"  "$VYOS_BUILD_BRANCH"  "vyos-build"

    # Apply all CI "Fix vyos-build" changes to the local checkout
    setup_vyos_build

    local build_version
    build_version="$(date -u +%Y.%m.%d-%H%M)-rolling"

    info "Running Docker build ($BUILDER_IMAGE) — this mirrors the CI 'Build Image Packages' + 'Build VyOS ISO' steps …"
    docker run --rm --privileged --platform linux/arm64 \
        --sysctl net.ipv6.conf.lo.disable_ipv6=0 \
        -v "$VYOS_BUILD_DIR:/vyos-build" \
        -v "$BUILD_REPO_DIR:/ls1046a" \
        -w /vyos-build \
        "$BUILDER_IMAGE" \
        bash -c "
set -ex

# ── Install additional packages (mirrors CI 'Install Dependencies' step) ──
apt-get update -qq
apt-get install -y \
    libsystemd-dev libglib2.0-dev libip4tc-dev libipset-dev libnfnetlink-dev \
    libnftnl-dev libnl-nf-3-dev libpopt-dev libpcap-dev libbpf-dev \
    bubblewrap git-lfs kpartx clang llvm cmake \
    protobuf-compiler python3-cracklib python3-protobuf \
    libreadline-dev liblua5.3-dev byacc flex \
    dosfstools mtools zstd u-boot-tools 2>/dev/null || true

# ── Build Image Packages (mirrors CI 'Build Image Packages' step) ──
cd /vyos-build/scripts/package-build
packages='linux-kernel vyos-1x'
ignore_packages='amazon-cloudwatch-agent amazon-ssm-agent xen-guest-agent'

for package in \$packages; do
    [ ! -d \$package ] && continue
    echo \$ignore_packages | grep -qw \$package && continue
    cd \$package

    ./build.py

    # After linux-kernel: compile Mono Gateway DTB from built kernel source
    if [ \"\$package\" = 'linux-kernel' ]; then
        KSRC=\$(find . -maxdepth 1 -type d -name 'linux-*' | head -1)
        if [ -n \"\$KSRC\" ] && [ -d \"\$KSRC/arch/arm64/boot/dts/freescale\" ]; then
            echo '### Building Mono Gateway DTB from kernel source'
            cp /ls1046a/data/dtb/mono-gateway-dk.dts \
               \"\$KSRC/arch/arm64/boot/dts/freescale/mono-gateway-dk.dts\"
            make -C \"\$KSRC\" ARCH=arm64 freescale/mono-gateway-dk.dtb 2>&1 | tail -5 || true
            MONO_DTB=\"\$KSRC/arch/arm64/boot/dts/freescale/mono-gateway-dk.dtb\"
            if [ -f \"\$MONO_DTB\" ]; then
                cp \"\$MONO_DTB\" /vyos-build/data/live-build-config/includes.binary/mono-gw.dtb
                cp \"\$MONO_DTB\" /vyos-build/data/live-build-config/includes.chroot/boot/mono-gw.dtb
                echo '### Mono Gateway DTB compiled successfully'
            else
                echo 'WARNING: mono-gateway-dk.dtb build failed, keeping pre-built DTB'
            fi
        fi
        # Cleanup to save disk space (mirrors CI)
        rm -rf \$package *.gz *.xz \$HOME/.cache/go-build \$HOME/go/pkg/mod \$HOME/.rustup
    fi

    df -Th
    cd ..
done

# ── Pick Packages (mirrors CI 'Pick Packages' step) ──
cd /vyos-build
deb_files=\$(find scripts/package-build -name '*.deb' -type f \
    | grep -v -- -dbg | grep -v -- -dev | grep -v -- -doc)
ignore_debs='charon-cmd dropbear-initramfs dropbear-run eapoltest frr-test-tools
    isc-dhcp-client-ddns isc-dhcp-common isc-dhcp-keama isc-dhcp-server
    isc-dhcp-server-ldap libnetsnmptrapd40 libsnmp-perl libtac2-bin
    libyang-modules libyang-tools rtr-tools sflowovsd snmptrapd
    strongswan-nm strongswan-pki tkmib waagent wide-dhcpv6-relay
    wide-dhcpv6-server vpp-plugin-devtools accel-ppp'
for deb_file in \$deb_files; do
    pkg=\$(basename \$deb_file | cut -d_ -f1)
    echo \$ignore_debs | grep -qw \$pkg && { echo \"ignore \$deb_file\"; continue; }
    cp \$deb_file packages/
done
ls -lh packages/

# ── Build VyOS ISO (mirrors CI 'Build VyOS ISO' step) ──
rm -rf packages/linux-headers-*

./build-vyos-image \
    --architecture arm64 \
    --build-by local@ls1046a-dev \
    --build-type release \
    --version $build_version \
    --custom-package vim-tiny \
    --custom-package neofetch \
    --custom-package tree \
    --custom-package btop \
    --custom-package ripgrep \
    --custom-package wget \
    --custom-package ncdu \
    --custom-package fastnetmon \
    --custom-package containernetworking-plugins \
    --custom-package mokutil \
    --custom-package grub-efi-arm64-signed \
    --custom-package u-boot-tools \
    --custom-package libubootenv-tool \
    generic

# Rename generic → LS1046A in artifact filename
cd build
ORIG_ISO=\$(python3 -c \"import json; print(json.load(open('manifest.json'))['artifacts'][0])\" 2>/dev/null \
    || ls *.iso 2>/dev/null | head -1)
if [ -n \"\$ORIG_ISO\" ] && [ -f \"\$ORIG_ISO\" ]; then
    IMAGE_ISO=\"\${ORIG_ISO/generic/LS1046A}\"
    mv \"\$ORIG_ISO\" \"\$IMAGE_ISO\"
    echo \"### ISO: \$IMAGE_ISO\"
fi
"

    echo ""
    ok "Build complete in $(( SECONDS - _T0 ))s"
    local iso_path
    iso_path=$(find "$VYOS_BUILD_DIR/build" -name "*.iso" 2>/dev/null | head -1 || true)
    if [[ -n "$iso_path" ]]; then
        ok "ISO: $iso_path ($(du -sh "$iso_path" | cut -f1))"
        echo ""
        info "To install on device: add system image file://$iso_path"
        info "To use for TFTP test: ./build-local.sh extract $iso_path"
    else
        warn "No ISO found in $VYOS_BUILD_DIR/build — check Docker output above"
    fi
}

# ─── Dispatch ─────────────────────────────────────────────────────────────────
case "$MODE" in
    kernel)  cmd_kernel  ;;
    dtb)     cmd_dtb     ;;
    extract) cmd_extract "$@" ;;
    vyos1x)  cmd_vyos1x  ;;
    iso)     cmd_iso     ;;
    help|-h|--help) exec "$0" help ;;
    *) die "Unknown mode: $MODE  (kernel | dtb | extract | vyos1x | iso)" ;;
esac