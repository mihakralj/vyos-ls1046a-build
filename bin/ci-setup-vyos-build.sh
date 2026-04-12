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

### SFP TX_DISABLE deassert for SDK kernel (no phylink SFP state machine)
cp data/scripts/sfp-tx-enable-sdk.sh "$CHROOT/usr/local/bin/sfp-tx-enable-sdk.sh"
chmod +x "$CHROOT/usr/local/bin/sfp-tx-enable-sdk.sh"
cp data/systemd/sfp-tx-enable-sdk.service "$CHROOT/etc/systemd/system/sfp-tx-enable-sdk.service"
cp data/systemd/sfp-tx-enable-sdk.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/sfp-tx-enable-sdk.conf"

### ====================================================================
### ASK (Application Solutions Kit) fast-path userspace components
### ====================================================================
# ASK provides hardware flow offloading via FMan Coarse Classifier on LS1046A.
# Components: cdx.ko (control plane), fci.ko (conntrack interface),
# auto_bridge.ko (bridge offload), dpa_app (FMan programmer),
# cmm (connection manager), fmc (FMan compiler), shared libraries.
#
# Kernel modules (.ko) are built from source in ci-build-packages.sh
# and placed directly into $CHROOT/usr/local/lib/ask-modules/.
# Here we install userspace binaries, libraries, configs, and services.

### ASK userspace binaries
cp data/ask-userspace/dpa_app/dpa_app "$CHROOT/usr/local/bin/dpa_app"
chmod +x "$CHROOT/usr/local/bin/dpa_app"
mkdir -p "$CHROOT/usr/bin"
cp data/ask-userspace/cmm/cmm "$CHROOT/usr/bin/cmm"
chmod +x "$CHROOT/usr/bin/cmm"
cp data/ask-userspace/fmc/fmc "$CHROOT/usr/local/bin/fmc"
chmod +x "$CHROOT/usr/local/bin/fmc"

### ASK shared libraries → /usr/local/lib/
# libcli (CLI library for dpa_app)
cp data/ask-userspace/libcli/libcli.so.1.10.8  "$CHROOT/usr/local/lib/"
ln -sf libcli.so.1.10.8  "$CHROOT/usr/local/lib/libcli.so.1.10"
ln -sf libcli.so.1.10    "$CHROOT/usr/local/lib/libcli.so"

# libfci (fast-path conntrack interface library)
cp data/ask-userspace/fci/libfci.so.0.1  "$CHROOT/usr/local/lib/"
ln -sf libfci.so.0.1  "$CHROOT/usr/local/lib/libfci.so.0"
ln -sf libfci.so.0    "$CHROOT/usr/local/lib/libfci.so"

# libcmm (CMM shared library)
cp data/ask-userspace/cmm/libcmm.so.0.0.0  "$CHROOT/usr/local/lib/"
ln -sf libcmm.so.0.0.0  "$CHROOT/usr/local/lib/libcmm.so.0"
ln -sf libcmm.so.0      "$CHROOT/usr/local/lib/libcmm.so"

# libnfnetlink (NXP-patched: nonblocking + heap buffer extensions)
cp data/ask-userspace/libnfnetlink/libnfnetlink.so.0.2.0  "$CHROOT/usr/local/lib/"
ln -sf libnfnetlink.so.0.2.0  "$CHROOT/usr/local/lib/libnfnetlink.so.0"
ln -sf libnfnetlink.so.0      "$CHROOT/usr/local/lib/libnfnetlink.so"

# libnetfilter_conntrack (NXP-patched: comcerto fast-path extensions)
cp data/ask-userspace/libnetfilter-conntrack/libnetfilter_conntrack.so.3.8.0  "$CHROOT/usr/local/lib/"
ln -sf libnetfilter_conntrack.so.3.8.0  "$CHROOT/usr/local/lib/libnetfilter_conntrack.so.3"
ln -sf libnetfilter_conntrack.so.3      "$CHROOT/usr/local/lib/libnetfilter_conntrack.so"

### ASK CDX config XMLs → /etc/cdx/
mkdir -p "$CHROOT/etc/cdx"
cp data/ask-userspace/dpa_app/etc/cdx_cfg.xml        "$CHROOT/etc/cdx/"
cp data/ask-userspace/dpa_app/etc/cdx_cfg_mono_gw.xml "$CHROOT/etc/cdx/"
cp data/ask-userspace/dpa_app/etc/cdx_pcd.xml         "$CHROOT/etc/cdx/"
cp data/ask-userspace/dpa_app/etc/cdx_sp.xml          "$CHROOT/etc/cdx/"

### ASK kernel module loader service (insmod for out-of-tree .ko files)
cp data/scripts/ask-modules-load.sh "$CHROOT/usr/local/bin/ask-modules-load.sh"
chmod +x "$CHROOT/usr/local/bin/ask-modules-load.sh"
cp data/systemd/ask-modules-load.service "$CHROOT/etc/systemd/system/ask-modules-load.service"
cp data/systemd/ask-modules-load.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/ask-modules-load.conf"

### CMM service and config
cp ask-ls1046a-6.6/config/cmm.service "$CHROOT/etc/systemd/system/cmm.service"
mkdir -p "$CHROOT/etc/config"
cp ask-ls1046a-6.6/config/fastforward "$CHROOT/etc/config/fastforward"

### CMM service enablement via tmpfiles.d
cp data/systemd/cmm.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/cmm.conf"

### ASK conntrack fix: flush VyOS notrack rules for fast-path offload
cp data/scripts/ask-conntrack-fix.sh "$CHROOT/usr/local/bin/ask-conntrack-fix.sh"
chmod +x "$CHROOT/usr/local/bin/ask-conntrack-fix.sh"
cp data/systemd/ask-conntrack-fix.service "$CHROOT/etc/systemd/system/ask-conntrack-fix.service"
cp data/systemd/ask-conntrack-fix.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/ask-conntrack-fix.conf"

### ASK kernel modules directory (modules built by ci-build-packages.sh)
# Pre-create the directory; ci-build-packages.sh populates it with .ko files
mkdir -p "$CHROOT/usr/local/lib/ask-modules"

### ASK chroot hook (ldconfig, depmod, runtime deps)
cp data/hooks/97-ask-userspace.chroot "$HOOKS/97-ask-userspace.chroot"
chmod +x "$HOOKS/97-ask-userspace.chroot"

echo "### vyos-build setup complete (with ASK fast-path userspace)"
