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

    ### Block linux-image-*-vyos from being pulled transitively from the
    ### VyOS apt repo. The 17-gen_initramfs.chroot hook aborts with
    ###   "there is more than one kernel image file installed!"
    ### when both linux-image-6.6.135-ask (our staged ASK kernel) and
    ### linux-image-6.6.135-vyos (pulled as a dep) are installed.
    ###
    ### live-build reads config/archives/*.pref.chroot and copies the
    ### file to /etc/apt/preferences.d/*.pref in the chroot BEFORE any
    ### apt install step — which is exactly when we need the pin active.
    ### vyos-build seeds config/ from data/live-build-config/, so dropping
    ### the file there propagates through `lb config`.
    LB_ARCHIVES=vyos-build/data/live-build-config/archives
    mkdir -p "$LB_ARCHIVES"
    cat > "$LB_ARCHIVES/no-vyos-kernel.pref.chroot" <<'PREF'
Package: linux-image-*-vyos linux-headers-*-vyos linux-image-vyos linux-headers-vyos
Pin: release *
Pin-Priority: -1
PREF
    echo "### Wrote apt pin to exclude linux-image-*-vyos:"
    cat "$LB_ARCHIVES/no-vyos-kernel.pref.chroot"
fi
