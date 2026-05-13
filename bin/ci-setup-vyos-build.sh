#!/bin/bash
# ci-setup-vyos-build.sh — Patch vyos-build, install chroot files, hooks, and config
# Called by: .github/workflows/auto-build.yml "Setup vyos-build" step
# Expects: GITHUB_WORKSPACE set
set -ex -o pipefail
cd "${GITHUB_WORKSPACE:-.}"

# Resolve $FLAVOR (default | ask | vpp) so the per-flavor update-check feed
# URL can be substituted into config.boot.* before they're staged into the
# chroot. Sourcing common.sh sets FLAVOR via env / data/flavor.pin / "default".
# shellcheck disable=SC1091
. "$(dirname "$0")/common.sh"

CHROOT=vyos-build/data/live-build-config/includes.chroot
HOOKS=vyos-build/data/live-build-config/hooks/live

### vyos-build patches
# Default config selection:
#   The active default config that vyos-router applies on first boot when
#   /config/config.boot is absent is the one shipped at
#       /opt/vyatta/etc/config.boot.default
#   The legacy install tool (`install image`) also copies this file to
#       /config/config.boot
#   on the target disk after install, so the same content is the default
#   for live/USB/TFTP boot AND for the installed system on first boot.
#
#   For FLAVOR=default|ask we ship `config.boot.dhcp` (DHCP on all 5
#   LS1046A ports, SSH, NTP, syslog, watchdog, update-check) as that active
#   default. For FLAVOR=vpp we ship `config.boot.vpp` instead: eth0 stays as
#   the kernel-owned management port, while eth1-eth4 are assigned to VPP.
#   The other variants are kept alongside under their original names for
#   reference / `load` after login.
#     config.boot.default = lean reference (eth0 DHCP + SSH + console)
#     config.boot.full    = rich reference (routing/firewall/NAT/DNS/API)
case "$FLAVOR" in
  vpp) ACTIVE_DEFAULT=board/vyos-config/config.boot.vpp ;;
  *)   ACTIVE_DEFAULT=board/vyos-config/config.boot.dhcp ;;
esac
cp "$ACTIVE_DEFAULT"       "$CHROOT/opt/vyatta/etc/config.boot.default"
cp board/vyos-config/config.boot.default "$CHROOT/opt/vyatta/etc/config.boot.minimal"
cp board/vyos-config/config.boot.dhcp    "$CHROOT/opt/vyatta/etc/config.boot.dhcp"
cp board/vyos-config/config.boot.full    "$CHROOT/opt/vyatta/etc/config.boot.full"
cp board/vyos-config/config.boot.vpp     "$CHROOT/opt/vyatta/etc/config.boot.vpp"

# Per-flavor update-check feed: rewrite the hard-coded `version-default.json`
# URL in every staged config.boot.* to `version-${FLAVOR}.json`. Without this
# rewrite an ASK or VPP install would point at the default-flavor JSON feed
# and silently cross-upgrade to a default-flavor ISO on the next update check.
# The repo source files keep `version-default.json` as the literal so a `grep`
# of the working tree shows a sensible default; the substitution happens only
# at CI time and only on the staged copies inside the chroot.
FEED_URL="https://raw.githubusercontent.com/mihakralj/vyos-ls1046a-build/refs/heads/main/version-${FLAVOR}.json"
for f in \
    "$CHROOT/opt/vyatta/etc/config.boot.default" \
    "$CHROOT/opt/vyatta/etc/config.boot.minimal" \
    "$CHROOT/opt/vyatta/etc/config.boot.dhcp" \
    "$CHROOT/opt/vyatta/etc/config.boot.full" \
    "$CHROOT/opt/vyatta/etc/config.boot.vpp"; do
    if [ -f "$f" ] && grep -q 'version-default\.json' "$f"; then
        sed -i \
            -e "s#https://raw\.githubusercontent\.com/mihakralj/vyos-ls1046a-build/refs/heads/main/version-default\.json#${FEED_URL}#g" \
            "$f"
    fi
done
echo "### Update-check feed URLs after FLAVOR=${FLAVOR} rewrite:"
grep -H 'update-check\|version-' \
    "$CHROOT/opt/vyatta/etc/config.boot.default" \
    "$CHROOT/opt/vyatta/etc/config.boot.dhcp" \
    "$CHROOT/opt/vyatta/etc/config.boot.full" \
    "$CHROOT/opt/vyatta/etc/config.boot.vpp" 2>/dev/null \
    | grep -E 'version-[a-z]+\.json' || true
# Drop .gitattributes inside the upstream clone so Mergiraf is wired as the
# merge driver for source-language files when --3way needs to fall back to a
# real 3-way merge. git apply --3way only consults attributes in the target
# tree, hence this lives inside vyos-build/, not at the repo root.
cat > vyos-build/.gitattributes <<'GITATTR'
*.c     merge=mergiraf
*.h     merge=mergiraf
*.py    merge=mergiraf
*.json  merge=mergiraf
*.yml   merge=mergiraf
*.yaml  merge=mergiraf
*.toml  merge=mergiraf
*.xml   merge=mergiraf
GITATTR

# Apply with git apply --3way (refuses fuzz, falls back to real 3-way merge
# on context drift). Idempotent: skip patches that reverse-apply cleanly,
# treating that as "already merged upstream".
for p in data/vyos-build-005-add_vim_link.patch data/vyos-build-007-no_sbsign.patch; do
  if git -C vyos-build apply --reverse --check --whitespace=nowarn "../$p" >/dev/null 2>&1; then
    echo "### $p: skipped (already applied upstream)"
    continue
  fi
  if ! git -C vyos-build apply --3way --whitespace=nowarn "../$p"; then
    echo "::error::$p failed to apply with --3way — context drift, refresh patch" >&2
    exit 1
  fi
done

### Remove --uefi-secure-boot from grub-install
# U-Boot boots via booti (not bootefi) so no EFI runtime is present.
# grub-install --uefi-secure-boot calls efibootmgr which fails with exit 1
# when /sys/firmware/efi does not exist.
find vyos-build -name '*.py' -exec \
  grep -l 'uefi.secure.boot' {} \; | \
  xargs -r sed -i "s/'--uefi-secure-boot'[,]\?//g"

### LS1046A console: force ttyS0 (8250 UART at 0x21c0500) everywhere.
#
# Three places encode the console type and must all be flipped away from
# the ARM64 default ttyAMA0:
#   1. vyos-build/data/defaults.toml              — the build_config source
#                                                    of truth. live-build
#                                                    reads it to generate
#                                                    grub.cfg / isolinux.cfg
#                                                    and any systemd getty
#                                                    unit defaults.
#   2. data/live-build-config/hooks/live/01-live-serial.binary — the live
#                                                    boot serial hook script.
#   3. data/live-build-config/includes.chroot/opt/vyatta/etc/grub/default-union-grub-entry
#                                                  — the installed-system
#                                                    grub entry used by the
#                                                    legacy 1.3.x upgrade
#                                                    path.
#
# Without (1) the ISO's live-boot grub.cfg will pin console=ttyAMA0,115200
# and the first boot on LS1046A hardware is blind — ttyAMA0 is a PL011
# which does not exist on this SoC (the UART is a Synopsys 8250 at
# 0x21c0500, exposed as ttyS0).
if [ -f vyos-build/data/defaults.toml ]; then
  sed -i \
    -e 's/^\(\s*console_type\s*=\s*\)"ttyAMA"/\1"ttyS"/' \
    -e "s/^\\(\\s*console_type\\s*=\\s*\\)'ttyAMA'/\\1'ttyS'/" \
    vyos-build/data/defaults.toml
  echo "### defaults.toml console_type after sed:"
  grep -E '^\s*console_(type|num|speed)\s*=' vyos-build/data/defaults.toml || true

  ### mksquashfs compression: zstd-22 instead of upstream xz/x86-BCJ.
  #
  # Upstream defaults.toml ships:
  #   squashfs_compression_type = "xz -Xbcj x86 -b 256k -always-use-fragments -no-recovery"
  # which (a) uses xz — single-threaded compress phase that dominates the
  # `mksquashfs` step on Cobalt 100 — and (b) passes `-Xbcj x86`, which is
  # actively wrong on ARM64 (BCJ filter for x86 instruction encoding does
  # nothing useful on aarch64 binaries).
  #
  # Switch to zstd at level 22 with `-b 1M -Xcompression-level 22`. zstd
  # mksquashfs is parallel-by-default and runs ~3-4x faster on the 4-core
  # Cobalt 100 than xz; level 22 keeps compressed size within ~5-8% of xz.
  # Final ISO grows from ~340 MB → ~360 MB squashfs, well under GitHub's
  # 2 GB Release asset cap. The string is passed verbatim into
  # `lb config --chroot-squashfs-compression-type "$VAL"` by
  # vyos-build/scripts/image-build/build-vyos-image (Jinja2 template).
  #
  # If you ever need the old xz behaviour for size-critical experiments,
  # comment out the sed below and the upstream value will pass through.
  if grep -q '^squashfs_compression_type' vyos-build/data/defaults.toml; then
    sed -i -E 's|^(\s*squashfs_compression_type\s*=\s*).*$|\1"zstd -b 1M -Xcompression-level 22"|' \
      vyos-build/data/defaults.toml
    echo "### defaults.toml squashfs_compression_type after sed:"
    grep -E '^\s*squashfs_compression_type\s*=' vyos-build/data/defaults.toml || true
  fi
fi

### Pin kernel_version to the ASK kernel.
#
# vyos-build/data/defaults.toml carries upstream VyOS's current kernel
# choice (as of 2026-05-06: 6.18.26). The default is consumed by
# scripts/image-build/build-vyos-image which renders
#   --linux-packages "linux-image-{{kernel_version}}"
# into the `lb config` invocation. live-build then asks apt for
# `linux-image-<kernel_version>-<flavour>` (i.e. linux-image-6.18.26-vyos)
# from the configured mirrors during `lb chroot_linux-image`.
#
# We ship the ASK kernel (6.6.137-askN) as a prebuilt .deb staged into
# packages.chroot/ by ci-consume-ask-kernel.sh. The flavour suffix on
# our .deb is `-vyos` to match what build-vyos-image expects, so the
# only mismatch is the version number in the template.
#
# Read the kernel version from the staged .deb (this self-adjusts when
# a new askN release is published) and rewrite kernel_version in
# defaults.toml so build-vyos-image renders
# `linux-image-6.6.137` and live-build resolves
# `linux-image-6.6.137-vyos` against our packages.chroot/ .deb instead
# of asking the Debian/VyOS mirrors for 6.18.26-vyos (which doesn't
# exist there).
PKG_CHROOT="vyos-build/data/live-build-config/packages.chroot"
if [ -d "$PKG_CHROOT" ]; then
  ASK_KERNEL_DEB=$(find "$PKG_CHROOT" -maxdepth 1 -name 'linux-image-*-vyos_*_arm64.deb' ! -name '*-dbg*' 2>/dev/null | head -1 || true)
else
  ASK_KERNEL_DEB=
fi
if [ -n "$ASK_KERNEL_DEB" ]; then
  # linux-image-6.6.137-vyos_6.6.137-1_arm64.deb -> 6.6.137
  ASK_KVER=$(basename "$ASK_KERNEL_DEB" | sed -E 's/^linux-image-([0-9]+\.[0-9]+\.[0-9]+)-vyos_.*$/\1/')
  if [ -n "$ASK_KVER" ] && [ "$ASK_KVER" != "$(basename "$ASK_KERNEL_DEB")" ]; then
    echo "### Pinning defaults.toml kernel_version -> $ASK_KVER (from $(basename "$ASK_KERNEL_DEB"))"
    sed -i -E "s/^(\\s*kernel_version\\s*=\\s*)\"[^\"]+\"/\\1\"$ASK_KVER\"/" \
      vyos-build/data/defaults.toml
    grep -E '^\s*kernel_version\s*=' vyos-build/data/defaults.toml || true
  else
    echo "WARN: Could not parse kernel version from $(basename "$ASK_KERNEL_DEB"); leaving defaults.toml alone"
  fi
else
  echo "### No ASK kernel .deb staged in $PKG_CHROOT — leaving defaults.toml kernel_version untouched"
fi
sed -i 's/ttyAMA0/ttyS0/g' \
  vyos-build/data/live-build-config/hooks/live/01-live-serial.binary \
  vyos-build/data/live-build-config/includes.chroot/opt/vyatta/etc/grub/default-union-grub-entry

### Strip vyos-ipt-netflow from arm64.toml.
#
# vyos-build/data/architectures/arm64.toml ships a `packages = [...]` array
# whose entries are unconditionally appended to live-build's
# config/package-lists/custom.list.chroot. As of 2026-05-11 the upstream
# vyos.net apt repo no longer publishes `vyos-ipt-netflow*` for arm64
# (verified by curling
#   https://packages.vyos.net/repositories/current/dists/current/main/binary-arm64/Packages.gz
# and grepping — only `pmacct` matches "netflow", no `vyos-ipt-netflow*`
# package stanza exists). Live-build's `lb chroot_install-packages` then
# fails with:
#     E: Unable to locate package vyos-ipt-netflow
#     E: An unexpected failure occurred, exiting...
# killing the entire ISO build.
#
# Our config.boot.* defaults do NOT use `set system flow-accounting netflow`,
# so the OOT iptables-NETFLOW kmod + the small VyOS glue package are dead
# weight on this board. Strip the entry from arm64.toml before
# build-vyos-image renders custom.list.chroot. The companion sed normalizes
# the trailing comma on the preceding `"grub-efi-arm64"` entry so the TOML
# array stays syntactically valid after deletion.
#
# If upstream republishes the package later, this sed becomes a no-op
# (the line will simply not exist to delete and the grub entry will not
# have the trailing comma to strip), and we can revert this block.
if [ -f vyos-build/data/architectures/arm64.toml ]; then
  if grep -q '"vyos-ipt-netflow"' vyos-build/data/architectures/arm64.toml; then
    sed -i -E \
      -e '/^[[:space:]]*"vyos-ipt-netflow"[[:space:]]*,?[[:space:]]*$/d' \
      -e 's/^([[:space:]]*"grub-efi-arm64")[[:space:]]*,[[:space:]]*$/\1/' \
      vyos-build/data/architectures/arm64.toml
    echo "### Stripped vyos-ipt-netflow from arm64.toml (upstream apt repo no longer ships it):"
    cat vyos-build/data/architectures/arm64.toml
  fi
fi

### MOK certificate for kernel module signing
if [ -f board/mok/MOK.key ]; then
  cp board/mok/MOK.key vyos-build/data/certificates/vyos-dev-2025-linux.key
  cp board/mok/MOK.pem vyos-build/data/certificates/vyos-dev-2025-linux.pem
fi

### Apt preferences pin: block upstream linux-image-*-vyos.
#
# When VyOS upstream rebuilds vyos-1x against a newer kernel ABI than
# the one we vendor (e.g. 6.6.137 vs our 6.6.135), apt's resolver will
# pull `linux-image-<NEWER>-vyos` from packages.vyos.net to satisfy
# vyos-1x's dependency, AND keep our locally-staged
# `linux-image-6.6.135-vyos` from packages.chroot/. Two kernels in
# the chroot make `live-build`'s `17-gen_initramfs.chroot` hook abort
# with `E: there is more than one kernel image file installed!` and
# the build dies.
#
# Block any linux-image-*-vyos / linux-headers-*-vyos coming from
# `packages.vyos.net` so the ONLY candidate apt sees is the local
# .deb staged in packages.chroot/. vyos-1x's dependency on the kernel
# ABI then resolves against our 6.6.135 deb, single-kernel chroot,
# initramfs hook succeeds.
#
# This survives apt updates because Pin-Priority -1 means NotInstall
# regardless of version. The local `file:` repo (which holds the
# packages.chroot/ contents during live-build) is unaffected.
mkdir -p vyos-build/data/live-build-config/archives
cat > vyos-build/data/live-build-config/archives/00-pin-ask-kernel.pref.chroot <<'PREF'
Package: linux-image-*-vyos linux-headers-*-vyos
Pin: origin packages.vyos.net
Pin-Priority: -1
PREF
echo "### Pinned upstream linux-image-*-vyos from packages.vyos.net to NotInstall:"
cat vyos-build/data/live-build-config/archives/00-pin-ask-kernel.pref.chroot


### Minisign public key + DTB for ISO
cp data/vyos-ls1046a.minisign.pub vyos-build/data/live-build-config/includes.chroot/usr/share/vyos/keys/
mkdir -p vyos-build/data/live-build-config/includes.binary
cp board/dtb/mono-gw.dtb vyos-build/data/live-build-config/includes.binary/mono-gw.dtb

### DTB inside squashfs: install_image() copies all files from /boot/
mkdir -p "$CHROOT/boot"
cp board/dtb/mono-gw.dtb "$CHROOT/boot/mono-gw.dtb"

### U-Boot tools: fw_setenv config for updating boot env from Linux
cp board/scripts/fw_env.config "$CHROOT/etc/fw_env.config"

### LS1046A independent serial console (ls1046a-console.service)
# Bypasses VyOS's system_console.py policy on serial-getty@ttyS0.service so
# the login prompt on /dev/ttyS0 stays alive even when the seed config has
# no `system { console { ... } }` stanza (commit 1876cff1).
#
# Enable directly via RELATIVE symlinks in the chroot tree (NOT via the
# 96-enable-services.chroot hook). Rationale: on the 2026.05.12-0558-rolling
# build that hook silently failed to run — `udevadm info` and squashfs
# inspection on mono confirmed NONE of the hook's outputs were present in
# the live ISO (no zz-ls1046a-nodevbind.conf drop-in, no tuned ordering
# drop-in, no `multi-user.target.wants/ls1046a-console.service`). Result:
# `serial-getty@ttyS0` was torn down 9.143 s after start by VyOS's
# `system_console.py SERIAL` ExecStartPre and there was nothing else
# reading /dev/ttyS0 → admin locked out of the only physical console.
#
# Symlink targets are RELATIVE (`../ls1046a-console.service`) so that
# `shutil.copytree(..., symlinks=False)` and `cp -a` in the live-build
# pipeline preserve them as symlinks all the way into the squashfs.
# (Absolute symlinks get dereferenced into regular files, which systemd
# then ignores in `.wants/` directories — that is the bug that originally
# motivated the chroot-hook indirection.)
cp board/systemd/ls1046a-console.service "$CHROOT/etc/systemd/system/ls1046a-console.service"
mkdir -p "$CHROOT/etc/systemd/system/multi-user.target.wants" \
         "$CHROOT/etc/systemd/system/getty.target.wants"
ln -sf ../ls1046a-console.service \
       "$CHROOT/etc/systemd/system/multi-user.target.wants/ls1046a-console.service"
ln -sf ../ls1046a-console.service \
       "$CHROOT/etc/systemd/system/getty.target.wants/ls1046a-console.service"

### sysctl drop-in: quiet the kernel console AFTER userspace is up.
### Keeps early boot verbose (kernel cmdline has no loglevel=) but silences
### the NXP SDK fsl_dpa pr_err spam at T+62-64s that otherwise buries
### the login prompt on ttyS0. Applied by systemd-sysctl.service.
mkdir -p "$CHROOT/etc/sysctl.d"
cp board/scripts/99-ls1046a-quiet-console.conf "$CHROOT/etc/sysctl.d/99-ls1046a-quiet-console.conf"

### Post-install helper: writes /boot/vyos.env + one-time U-Boot env setup
mkdir -p "$CHROOT/usr/local/bin"
cp board/scripts/vyos-postinstall "$CHROOT/usr/local/bin/vyos-postinstall"
chmod +x "$CHROOT/usr/local/bin/vyos-postinstall"

### Systemd service for vyos-postinstall (from extracted data file)
cp board/systemd/vyos-postinstall.service "$CHROOT/etc/systemd/system/vyos-postinstall.service"

### tmpfiles.d: create .wants symlink at boot (live-build breaks systemctl enable)
mkdir -p "$CHROOT/usr/lib/tmpfiles.d"
cp board/systemd/vyos-postinstall.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/vyos-postinstall.conf"

### Fan control: PID daemon (fan-pid) replaces lm-sensors fancontrol.
###
### fan-pid is a self-contained Python 3 multi-zone PID controller that
### samples all 5 LS1046A thermal zones (ddr, serdes, fman, cluster, sec)
### and drives the EMC2305 PWM via max-policy combine + EMA smoothing +
### anti-windup integral clamp + hard-fault force-MAX at 100 C. Installed
### unconditionally for every flavor (default | ask | vpp) because all
### LS1046A boards share the same EMC2305 + thermal-zone topology.
### See data/hooks/98-fancontrol.chroot for the rationale on masking
### upstream `fancontrol.service` defensively (two PWM controllers must
### never run concurrently).
cp board/scripts/fan-pid "$CHROOT/usr/local/bin/fan-pid"
chmod +x "$CHROOT/usr/local/bin/fan-pid"
cp board/systemd/fan-pid.service "$CHROOT/etc/systemd/system/fan-pid.service"
cp board/systemd/fan-pid.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/fan-pid.conf"
# udev rule: start fan-pid.service at the moment the kernel binds the
# emc2305 driver to its i2c device. Defends against the multi-user.target
# vs i2c-bus-probe race that left the service `inactive (dead)` with an
# empty journal on 2026-05-11 (the previous ConditionPathExistsGlob= on
# the driver's bound-device symlink failed silently and was never
# re-evaluated). With this rule + the DT-only board gate in the unit, the
# daemon starts whichever way wins the race.
mkdir -p "$CHROOT/etc/udev/rules.d"
cp board/scripts/10-emc2305-fan-pid.rules "$CHROOT/etc/udev/rules.d/10-emc2305-fan-pid.rules"

### VPP/DPAA1 post-start: fix defunct interface MTU for AF_XDP TX
mkdir -p "$CHROOT/etc/systemd/system/vpp.service.d"
rm -f "$CHROOT/usr/local/bin/vpp-dpaa-rebind" \
  "$CHROOT/etc/systemd/system/vpp.service.d/dpaa-rebind.conf"
cp board/scripts/vpp-post-start.sh "$CHROOT/usr/local/bin/vpp-post-start.sh"
chmod +x "$CHROOT/usr/local/bin/vpp-post-start.sh"
cp board/systemd/vpp-post-start.conf "$CHROOT/etc/systemd/system/vpp.service.d/post-start.conf"

### Chroot hooks (from extracted data files)
# 95: set /etc/hostname=vyos + force vyos user password BEFORE live-config runs
cp data/hooks/95-vyos-hostname.chroot "$HOOKS/95-vyos-hostname.chroot"
chmod +x "$HOOKS/95-vyos-hostname.chroot"

# 92: defense-in-depth — pre-create /tmp/custom_mounts.list inside live-boot's
#     activate_custom_mounts() so the `done < $custom_mounts` redirection
#     never fires the `/init: line 1365: can't open /tmp/custom_mounts.list`
#     error if find_persistence_media() legitimately returns no candidates
#     (TFTP boot, nopersistence boot, fresh device pre-partition).
cp data/hooks/92-livescripts-defensive-mount-list.chroot "$HOOKS/92-livescripts-defensive-mount-list.chroot"
chmod +x "$HOOKS/92-livescripts-defensive-mount-list.chroot"

cp data/hooks/98-fancontrol.chroot "$HOOKS/98-fancontrol.chroot"
chmod +x "$HOOKS/98-fancontrol.chroot"

cp data/hooks/99-mask-services.chroot "$HOOKS/99-mask-services.chroot"
chmod +x "$HOOKS/99-mask-services.chroot"

### Ethernet port remapping: FMan MAC → physical port position
cp board/scripts/fman-port-name "$CHROOT/usr/local/bin/fman-port-name"
chmod +x "$CHROOT/usr/local/bin/fman-port-name"
mkdir -p "$CHROOT/etc/udev/rules.d"
cp board/scripts/10-fman-port-order.rules "$CHROOT/etc/udev/rules.d/10-fman-port-order.rules"
mkdir -p "$CHROOT/etc/systemd/network"
cp board/scripts/00-fman.link "$CHROOT/etc/systemd/network/00-fman.link"

### SFP+ inventory helper: `sfp-check` reports vendor/PN of every inserted
### module and emits a paste-ready SFP_QUIRK_F() line when a module looks
### like a 10GBASE-T rollball masquerading as SR fiber. Flavor-agnostic —
### only requires ethtool -m support, which is universal.
cp board/scripts/sfp-check "$CHROOT/usr/local/bin/sfp-check"
chmod +x "$CHROOT/usr/local/bin/sfp-check"

### Thermal/fan status helper: `fan-check` reports all 5 LS1046A thermal
### zone temps with [COOL]/[WARM]/[HOT]/[CRIT] tags, EMC2305 PWM duty + RPM,
### fan-pid daemon health (with journalctl tail), and flags genuine
### fancontrol/fan-pid concurrency conflicts. Exit 0 healthy / non-zero on
### fault — usable as a Nagios/monit probe. Mirrors sfp-check / ask-check
### style. Flavor-agnostic (every LS1046A board has the same EMC2305 +
### thermal-zone topology).
cp board/scripts/fan-check "$CHROOT/usr/local/bin/fan-check"
chmod +x "$CHROOT/usr/local/bin/fan-check"

### CAAM (NXP SEC 5.4) status helper: `caam-check` reports DT controller
### presence, kernel driver / built-in posture (caam, caam_jr, caamalg,
### caamhash, caamrng, …), Job Ring count, dmesg banner, /proc/crypto
### registrations sourced from CAAM, /dev/hwrng status with current_source,
### and (FLAVOR=ask only) CDX <-> SEC FQ wiring health. Exit 0 healthy /
### non-zero on fault — usable as a Nagios/monit probe. Mirrors
### sfp-check / fan-check / ask-check style. Flavor-agnostic at install
### time (CAAM is the same SEC 5.4 block on every LS1046A board); the
### script's section 7 is the only ASK-specific check and self-skips on
### default/vpp where cdx/dpa_ipsec are absent.
cp board/scripts/caam-check "$CHROOT/usr/local/bin/caam-check"
chmod +x "$CHROOT/usr/local/bin/caam-check"


### Boot-complete fan whistle is now produced by fan-pid itself
### (play_startup_whistle()).  The standalone boot-complete-notify
### service was deleted to eliminate the systemd-ordering race over
### /sys/.../pwm1 between fan-pid and the notify chirp.

### FQ qdisc for BBR pacing on 10G SFP+ interfaces
cp board/scripts/fman-fq-qdisc "$CHROOT/usr/local/bin/fman-fq-qdisc"
chmod +x "$CHROOT/usr/local/bin/fman-fq-qdisc"
cp board/systemd/fman-fq-qdisc.service "$CHROOT/etc/systemd/system/fman-fq-qdisc.service"
cp board/systemd/fman-fq-qdisc.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/fman-fq-qdisc.conf"

### SFP TX_DISABLE deassert for SDK kernel (no phylink SFP state machine)
# ASK-only: required because the NXP SDK MAC driver lacks the phylink SFP
# state machine that mainline DPAA + phylink uses to drive TX_DISABLE.
# For FLAVOR=default|vpp the mainline phylink path handles SFP TX enable
# via standard sfp_state_machine() — no helper service needed.
if [[ "${FLAVOR:-ask}" == "ask" ]]; then
    cp board/scripts/sfp-tx-enable-sdk.sh "$CHROOT/usr/local/bin/sfp-tx-enable-sdk.sh"
    chmod +x "$CHROOT/usr/local/bin/sfp-tx-enable-sdk.sh"
    cp board/systemd/sfp-tx-enable-sdk.service "$CHROOT/etc/systemd/system/sfp-tx-enable-sdk.service"
    cp board/systemd/sfp-tx-enable-sdk.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/sfp-tx-enable-sdk.conf"
fi

### Bind-mount /usr/lib/live/mount/medium → /usr/lib/live/mount/persistence (rw)
### Restores upstream find_persistence() semantics for LS1046A live-boot.
### See board/systemd/persistence-bindmount.service header comment for the
### full root cause (vyos-grub-update.service FileNotFoundError, broken
### `add system image`). Pairs with data/vyos-1x-020-find-persistence-by-label.patch
### which fixes the python layer for forward compatibility.
cp board/systemd/persistence-bindmount.service "$CHROOT/etc/systemd/system/persistence-bindmount.service"

### ====================================================================
### ASK (Application Solutions Kit) fast-path userspace components
### ====================================================================
# FLAVOR gate (PR 5): the entire ASK userspace stack — dpa_app, cmm, fmc,
# libcli/libcmm/libfci, CDX/FMC config XMLs, ASK module loader/health
# scripts, ASK chroot hooks — is meaningful only for FLAVOR=ask. For
# FLAVOR=default|vpp the ISO ships stock VyOS userspace with mainline
# DPAA (or AF_XDP/VPP) and none of these files belong in the chroot.
# Backward-compat: an UNSET FLAVOR is treated as "ask" so existing CI
# dispatches keep working unchanged.
if [[ "${FLAVOR:-ask}" != "ask" ]]; then
    echo "### FLAVOR=${FLAVOR:-} — skipping ASK userspace + module + service installation"
    echo "### vyos-build setup complete (FLAVOR=${FLAVOR:-}, ASK userspace omitted)"
    exit 0
fi
# ASK provides hardware flow offloading via FMan Coarse Classifier on LS1046A.
# Components: cdx.ko (control plane), fci.ko (conntrack interface),
# auto_bridge.ko (bridge offload), dpa_app (FMan programmer),
# cmm (connection manager), fmc (FMan compiler), shared libraries.
#
# Kernel modules (.ko) are built from source in ci-build-packages.sh
# and placed directly into $CHROOT/usr/local/lib/ask-modules/.
# Here we install userspace binaries, libraries, configs, and services.

### ASK userspace binaries
# CDX module calls /usr/bin/dpa_app via call_usermodehelper (hardcoded path)
mkdir -p "$CHROOT/usr/bin"
cp data/ask-userspace/dpa_app/dpa_app "$CHROOT/usr/bin/dpa_app"
chmod +x "$CHROOT/usr/bin/dpa_app"
cp data/ask-userspace/cmm/cmm "$CHROOT/usr/bin/cmm"
chmod +x "$CHROOT/usr/bin/cmm"
cp data/ask-userspace/fmc/fmc "$CHROOT/usr/local/bin/fmc"
chmod +x "$CHROOT/usr/local/bin/fmc"

### ASK shared libraries → /usr/local/lib/
mkdir -p "$CHROOT/usr/local/lib"

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

### ASK CDX config XMLs → /etc/ (dpa_app expects /etc/cdx_cfg.xml etc.)
# Use Mono Gateway config as default (3×1G + 2×10G + 2×OH)
cp data/ask-userspace/dpa_app/etc/cdx_cfg_mono_gw.xml "$CHROOT/etc/cdx_cfg.xml"
cp data/ask-userspace/dpa_app/etc/cdx_pcd.xml         "$CHROOT/etc/cdx_pcd.xml"
cp data/ask-userspace/dpa_app/etc/cdx_sp.xml          "$CHROOT/etc/cdx_sp.xml"
# Keep originals in /etc/cdx/ for reference
mkdir -p "$CHROOT/etc/cdx"
cp data/ask-userspace/dpa_app/etc/cdx_cfg.xml         "$CHROOT/etc/cdx/"
cp data/ask-userspace/dpa_app/etc/cdx_cfg_mono_gw.xml "$CHROOT/etc/cdx/"
cp data/ask-userspace/dpa_app/etc/cdx_pcd.xml         "$CHROOT/etc/cdx/"
cp data/ask-userspace/dpa_app/etc/cdx_sp.xml          "$CHROOT/etc/cdx/"

### FMC config: hxs_pdl_v3.xml (NetPDL protocol definitions for soft parser)
# dpa_app / libfmc statically links to FMC which reads /etc/fmc/config/hxs_pdl_v3.xml
# at init time. Without this file, dpa_app SIGSEGV (null pointer in XML parse).
mkdir -p "$CHROOT/etc/fmc/config"
cp data/ask-userspace/fmc/config/hxs_pdl_v3.xml "$CHROOT/etc/fmc/config/"
cp data/ask-userspace/fmc/config/netpcd.xsd     "$CHROOT/etc/fmc/config/"
cp data/ask-userspace/fmc/config/cfgdata.xsd    "$CHROOT/etc/fmc/config/"

### ASK kernel module loader service (insmod for out-of-tree .ko files)
cp board/scripts/ask-modules-load.sh "$CHROOT/usr/local/bin/ask-modules-load.sh"
chmod +x "$CHROOT/usr/local/bin/ask-modules-load.sh"
cp board/systemd/ask-modules-load.service "$CHROOT/etc/systemd/system/ask-modules-load.service"
cp board/systemd/ask-modules-load.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/ask-modules-load.conf"

### CMM service and config
cp ASK/config/cmm.service "$CHROOT/etc/systemd/system/cmm.service"
mkdir -p "$CHROOT/etc/config"
cp ASK/config/fastforward "$CHROOT/etc/config/fastforward"

### CMM service enablement via tmpfiles.d
cp board/systemd/cmm.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/cmm.conf"

### ASK health check script
cp board/scripts/ask-check.sh "$CHROOT/usr/local/bin/ask-check"
chmod +x "$CHROOT/usr/local/bin/ask-check"

### ASK conntrack fix: flush VyOS notrack rules for fast-path offload
cp board/scripts/ask-conntrack-fix.sh "$CHROOT/usr/local/bin/ask-conntrack-fix.sh"
chmod +x "$CHROOT/usr/local/bin/ask-conntrack-fix.sh"
cp board/systemd/ask-conntrack-fix.service "$CHROOT/etc/systemd/system/ask-conntrack-fix.service"
cp board/systemd/ask-conntrack-fix.tmpfiles "$CHROOT/usr/lib/tmpfiles.d/ask-conntrack-fix.conf"

### ASK kernel modules — shipped inside the kernel-6.6.137-askN release's
# ask-modules .deb (consumed via ci-consume-ask-kernel.sh, installed under
#  /lib/modules/<KVER>/extra/ask/). No staging needed here; the loader
#  ask-modules-load.sh now reads from /lib/modules/$(uname -r)/extra/ask.

### ASK chroot hook (ldconfig, depmod, runtime deps)
cp data/hooks/97-ask-userspace.chroot "$HOOKS/97-ask-userspace.chroot"
chmod +x "$HOOKS/97-ask-userspace.chroot"

### Service enablement chroot hook
# Must be a chroot hook (not includes.chroot symlinks) because build-vyos-image
# uses shutil.copytree() which follows symlinks → converts to regular files →
# systemd ignores non-symlink files in .wants/ directories.
cp data/hooks/96-enable-services.chroot "$HOOKS/96-enable-services.chroot"
chmod +x "$HOOKS/96-enable-services.chroot"

echo "### vyos-build setup complete (with ASK fast-path userspace)"
