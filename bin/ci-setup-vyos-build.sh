#!/bin/bash
# ci-setup-vyos-build.sh — Patch vyos-build, install chroot files, hooks, and config
# Called by: .github/workflows/auto-build.yml "Setup vyos-build" step
# Expects: GITHUB_WORKSPACE set
set -ex
cd "${GITHUB_WORKSPACE:-.}"

CHROOT=vyos-build/data/live-build-config/includes.chroot
HOOKS=vyos-build/data/live-build-config/hooks/live

### vyos-build patches
cp data/config.boot.default "$CHROOT/opt/vyatta/etc/"
cp data/config.boot.dhcp "$CHROOT/opt/vyatta/etc/"
patch --no-backup-if-mismatch -p1 -d vyos-build < data/vyos-build-005-add_vim_link.patch
patch --no-backup-if-mismatch -p1 -d vyos-build < data/vyos-build-007-no_sbsign.patch

### Remove --uefi-secure-boot from grub-install
# U-Boot boots via booti (not bootefi) so no EFI runtime is present.
# grub-install --uefi-secure-boot calls efibootmgr which fails with exit 1
# when /sys/firmware/efi does not exist.
find vyos-build -name '*.py' -exec \
  grep -l 'uefi.secure.boot' {} \; | \
  xargs -r sed -i "s/'--uefi-secure-boot'[,]\?//g" 2>/dev/null || true

### LS1046A console: revert ttyAMA0 -> ttyS0 (8250 UART at 0x21c0500)
sed -i 's/ttyAMA0/ttyS0/g' \
  vyos-build/data/live-build-config/hooks/live/01-live-serial.binary \
  vyos-build/data/live-build-config/includes.chroot/opt/vyatta/etc/grub/default-union-grub-entry \
  2>/dev/null || true

### MOK certificate for kernel module signing
if [ -f data/mok/MOK.key ]; then
  cp data/mok/MOK.key vyos-build/data/certificates/vyos-dev-2025-linux.key
  cp data/mok/MOK.pem vyos-build/data/certificates/vyos-dev-2025-linux.pem
fi

### Minisign public key + DTB for ISO
cp data/vyos-ls1046a.minisign.pub vyos-build/data/live-build-config/includes.chroot/usr/share/vyos/keys/
mkdir -p vyos-build/data/live-build-config/includes.binary
cp data/dtb/mono-gw.dtb vyos-build/data/live-build-config/includes.binary/mono-gw.dtb

### DTB inside squashfs: install_image() copies all files from /boot/
mkdir -p "$CHROOT/boot"
cp data/dtb/mono-gw.dtb "$CHROOT/boot/mono-gw.dtb"

### U-Boot tools: fw_setenv config for updating boot env from Linux
cp data/scripts/fw_env.config "$CHROOT/etc/fw_env.config"

### Post-install helper: writes /boot/vyos.env + one-time U-Boot env setup
mkdir -p "$CHROOT/usr/local/bin"
cp data/scripts/vyos-postinstall "$CHROOT/usr/local/bin/vyos-postinstall"
chmod +x "$CHROOT/usr/local/bin/vyos-postinstall"

### Systemd service for vyos-postinstall (from extracted data file)
cp data/systemd/vyos-postinstall.service "$CHROOT/etc/systemd/system/vyos-postinstall.service"

### tmpfiles.d: create .wants symlink at boot (live-build breaks systemctl enable)
mkdir -p "$CHROOT/usr/lib/tmpfiles.d"
cp data/systemd/vyos-postinstall.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/vyos-postinstall.conf"

### Fan control: EMC2305 PWM thermal management via standard fancontrol
cp data/scripts/fancontrol.conf "$CHROOT/etc/fancontrol"
cp data/scripts/fancontrol-setup.sh "$CHROOT/usr/local/bin/fancontrol-setup"
chmod +x "$CHROOT/usr/local/bin/fancontrol-setup"

### Systemd drop-in: run fancontrol-setup before fancontrol starts
mkdir -p "$CHROOT/etc/systemd/system/fancontrol.service.d"
cp data/systemd/fancontrol-dropin.conf "$CHROOT/etc/systemd/system/fancontrol.service.d/hwmon-setup.conf"

### VPP/DPAA1 rebind: restore kernel netdev ownership when VPP stops
cp data/scripts/vpp-dpaa-rebind "$CHROOT/usr/local/bin/vpp-dpaa-rebind"
chmod +x "$CHROOT/usr/local/bin/vpp-dpaa-rebind"
mkdir -p "$CHROOT/etc/systemd/system/vpp.service.d"
cp data/systemd/vpp-dpaa-rebind.conf "$CHROOT/etc/systemd/system/vpp.service.d/dpaa-rebind.conf"

### VPP/DPAA1 post-start: fix defunct interface MTU for AF_XDP TX
cp data/scripts/vpp-post-start.sh "$CHROOT/usr/local/bin/vpp-post-start.sh"
chmod +x "$CHROOT/usr/local/bin/vpp-post-start.sh"
cp data/systemd/vpp-post-start.conf "$CHROOT/etc/systemd/system/vpp.service.d/post-start.conf"

### Chroot hooks (from extracted data files)
cp data/hooks/98-fancontrol.chroot "$HOOKS/98-fancontrol.chroot"
chmod +x "$HOOKS/98-fancontrol.chroot"

cp data/hooks/99-mask-services.chroot "$HOOKS/99-mask-services.chroot"
chmod +x "$HOOKS/99-mask-services.chroot"

### Ethernet port remapping: FMan MAC → physical port position
cp data/scripts/fman-port-name "$CHROOT/usr/local/bin/fman-port-name"
chmod +x "$CHROOT/usr/local/bin/fman-port-name"
mkdir -p "$CHROOT/etc/udev/rules.d"
cp data/scripts/10-fman-port-order.rules "$CHROOT/etc/udev/rules.d/10-fman-port-order.rules"
mkdir -p "$CHROOT/etc/systemd/network"
cp data/scripts/00-fman.link "$CHROOT/etc/systemd/network/00-fman.link"


### FQ qdisc for BBR pacing on 10G SFP+ interfaces
cp data/scripts/fman-fq-qdisc "$CHROOT/usr/local/bin/fman-fq-qdisc"
chmod +x "$CHROOT/usr/local/bin/fman-fq-qdisc"
cp data/systemd/fman-fq-qdisc.service "$CHROOT/etc/systemd/system/fman-fq-qdisc.service"
cp data/systemd/fman-fq-qdisc.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/fman-fq-qdisc.conf"

echo "### vyos-build setup complete"

### ASK kernel: switch vyos-build's kernel_flavor so live-build picks
### linux-image-6.6.135-ask (staged into packages/) instead of pulling
### linux-image-6.6.135-vyos from the VyOS apt repo.
if [ -n "${ASK_KERNEL_TAG:-}" ]; then
    echo "### ASK kernel in effect — rewriting vyos-build/data/defaults.toml kernel_flavor to 'ask'"
    sed -i 's/^kernel_flavor *=.*/kernel_flavor = "ask"/' vyos-build/data/defaults.toml
    grep -E '^kernel_(version|flavor)' vyos-build/data/defaults.toml

    ### Satisfy out-of-tree kernel-module deps on linux-image-6.6.135-vyos
    ### (jool, nat-rtsp, openvpn-dco, vyos-ipt-netflow) by shipping a
    ### transitional empty package named linux-image-6.6.135-vyos that
    ### Depends on our ASK kernel. dpkg -i runs on packages.chroot/*.deb
    ### BEFORE apt's install pass, so apt then sees the -vyos name
    ### already satisfied and never pulls the real VyOS kernel from
    ### packages.vyos.net. One kernel ends up installed (the ASK one)
    ### so 17-gen_initramfs.chroot is happy, and the module packages
    ### resolve cleanly.
    KVER=$(tr -d '[:space:]' < vyos-build/data/live-build-config/packages.chroot/.kver 2>/dev/null || true)
    if [ -z "$KVER" ]; then
        # Derive from the staged linux-image .deb name.
        IMG=$(ls packages/linux-image-*-ask_*_arm64.deb 2>/dev/null | head -1)
        if [ -n "$IMG" ]; then
            # linux-image-6.6.135-ask_6.6.135-1_arm64.deb -> 6.6.135
            KVER=$(basename "$IMG" | sed -E 's/^linux-image-([0-9.]+)-ask_.*/\1/')
        fi
    fi
    if [ -n "$KVER" ]; then
        echo "### Building transitional stub: linux-image-${KVER}-vyos → Depends linux-image-${KVER}-ask"
        STUB=$(mktemp -d)
        mkdir -p "$STUB/DEBIAN"
        cat > "$STUB/DEBIAN/control" <<EOF
Package: linux-image-${KVER}-vyos
Version: ${KVER}-askstub1
Architecture: arm64
Maintainer: ASK CI <mihakralj@users.noreply.github.com>
Depends: linux-image-${KVER}-ask
Section: kernel
Priority: optional
Multi-Arch: foreign
Description: Transitional stub mapping linux-image-${KVER}-vyos onto the ASK kernel
 Empty package installed via packages.chroot/ before apt runs so that
 out-of-tree VyOS kernel-module packages (jool, nat-rtsp, openvpn-dco,
 vyos-ipt-netflow) whose control fields hard-depend on
 linux-image-${KVER}-vyos resolve against the ASK kernel.
EOF
        STUB_DEB="vyos-build/data/live-build-config/packages.chroot/linux-image-${KVER}-vyos_${KVER}-askstub1_arm64.deb"
        mkdir -p "$(dirname "$STUB_DEB")"
        dpkg-deb --build "$STUB" "$STUB_DEB"
        ls -la "$STUB_DEB"
        rm -rf "$STUB"
    else
        echo "WARN: could not derive KVER for -vyos stub package; out-of-tree modules may fail to install."
    fi
fi
