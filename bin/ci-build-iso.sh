#!/bin/bash
# ci-build-iso.sh — Build VyOS ISO, make it hybrid (ISO9660 + FAT32 boot partition)
#
# The hybrid ISO serves two purposes from a SINGLE file:
#   1. add system image <url>  — VyOS downloads and loop-mounts as ISO9660
#   2. dd if=image.iso of=/dev/sdX bs=4M — creates USB bootable by U-Boot
#
# How it works:
#   ISO9660 System Area (bytes 0-32767) is spec-defined as unused.
#   We write an MBR partition table at byte 440, then append a small FAT32
#   partition (~100MB) containing boot.scr, vmlinuz, initrd, DTB.
#   The file is simultaneously valid ISO9660 and a valid MBR disk image.
#
#   On USB boot: U-Boot's fatload auto-detects FAT32 partition 2, loads
#   kernel/initrd/DTB, boots Linux. live-boot finds squashfs on partition 1
#   (ISO9660). No squashfs duplication — only ~70MB of boot files duplicated.
#
# Called by: .github/workflows/auto-build.yml "Build VyOS ISO" step
# Expects: GITHUB_WORKSPACE, BUILD_BY, BUILD_VERSION, DEBIAN_MIRROR,
#          DEBIAN_SECURITY_MIRROR, VYOS_MIRROR in env
set -ex -o pipefail
# Single-image build: one ISO named vyos-<version>-LS1046A-arm64.iso.
BC_QUIET=1 source "${GITHUB_WORKSPACE:-.}/bin/common.sh"

cd "${GITHUB_WORKSPACE:-.}/vyos-build"

### Pre-flight: verify custom kernel is present (defense-in-depth)
# ASK2 (rewrite-in-progress): the legacy ASK-consume path
# (ASK_KERNEL_TAG → data/live-build-config/packages.chroot/) was removed
# on the ask20 branch. The kernel is now always built locally via
# ci-build-packages.sh and stage it under vyos-build/packages/.
KERNEL_IN_PACKAGES=$(find packages -name 'linux-image-*.deb' ! -name '*-dbg*' 2>/dev/null | wc -l)
SEARCH_DIR="packages/"
if [ "$KERNEL_IN_PACKAGES" -eq 0 ]; then
  echo ""
  echo "###############################################################"
  echo "### FATAL: No custom kernel .deb in $SEARCH_DIR"
  echo "### Refusing to build ISO with upstream fallback kernel.    ###"
  echo "###############################################################"
  echo ""
  echo "The kernel build likely failed silently in a previous step."
  echo ""
  exit 1
fi
echo "### Pre-flight OK: custom kernel present ($KERNEL_IN_PACKAGES .deb in $SEARCH_DIR)"

### Copy mainline RDB DTB if built during kernel step
if [ -f "$GITHUB_WORKSPACE/board/dtb/fsl-ls1046a-rdb.dtb" ]; then
  cp "$GITHUB_WORKSPACE/board/dtb/fsl-ls1046a-rdb.dtb" \
    data/live-build-config/includes.binary/fsl-ls1046a-rdb.dtb
  echo "### Mainline RDB DTB included in ISO"
fi

rm -rf packages/linux-headers-*

### ASK2 (rewrite-in-progress): the legacy ask-modules-*.deb
### --custom-package injection (for the OOT cdx/fci/auto_bridge modules
### shipped via ASK_KERNEL_TAG) was removed on the ask20 branch. ASK2
### will ship ask.ko + ask_bridge.ko via a new packaging path per
### specs/ask2-rewrite-spec.md.

# libssl3/openssl downgrade fix: VyOS rolling repo has older versions than
# Debian bookworm.  Modify live-build's Apt() function to always pass
# -o APT::Get::Allow-Downgrades=true.  This is the single funnel through
# which ALL apt-get calls pass (bootstrap_archives, chroot_archives,
# chroot_install-packages), so fixing it once covers everything.
#
# Same funnel also gets an `eatmydata` prefix (conditional on the binary
# already existing inside $CHROOT, since bootstrap_archives runs before
# debootstrap has unpacked anything — see data/vyos-build-009). Once
# eatmydata is unpacked (its debootstrap --include entry), every later
# apt-get call in this funnel — critically chroot_install-packages, which
# is where the ~250s of dpkg unpack/configure actually happens — runs
# under LD_PRELOAD=libeatmydata.so and skips fsync() on every file.
# eatmydata ships no apt/dpkg auto-hook of its own; it only does anything
# if something actually invokes the `eatmydata` binary, hence this sed.
#
# This runner is persistent and live-build is never reinstalled between
# builds, so /usr/share/live/build/functions/wrapper.sh stays patched
# from whichever run last modified it — a sed targeting the pristine
# line would silently no-op forever after the first successful patch.
# Force a known-pristine baseline first so the sed below is idempotent
# regardless of what a previous run left on disk.
sudo apt-get install --reinstall -y live-build >/dev/null
sudo sed -i 's/Chroot ${CHROOT} apt-get ${APT_OPTIONS} "${@}"/Chroot ${CHROOT} $([ -x "${CHROOT}\/usr\/bin\/eatmydata" ] \&\& echo eatmydata) apt-get ${APT_OPTIONS} -o APT::Get::Allow-Downgrades=true "${@}"/' \
  /usr/share/live/build/functions/wrapper.sh
grep 'Allow-Downgrades' /usr/share/live/build/functions/wrapper.sh && echo "Apt() patched" || echo "ERROR: Apt() patch failed"
grep -F 'eatmydata' /usr/share/live/build/functions/wrapper.sh && echo "eatmydata prefix wired" || echo "ERROR: eatmydata prefix missing"

# Reuse the populated chroot across CI runs on this persistent self-hosted
# runner instead of a full debootstrap+dpkg-unpack every build (see
# data/vyos-build-010-persist-chroot.patch). Only the delta — new kernel
# .deb, vyos-1x .deb, any bumped custom package — actually gets
# unpacked/configured; unchanged packages are a fast apt no-op.
# VYOS_PERSIST_CHROOT is set at the workflow env: level (auto-build.yml)
# because the earlier "Unmount stale chroot bind-mounts" step needs to
# see it too, before this script ever runs; default here covers local/
# dev-build.sh invocations that don't go through that workflow.
export VYOS_PERSIST_CHROOT="${VYOS_PERSIST_CHROOT:-1}"

./build-vyos-image \
  --architecture arm64 \
  --build-by "$BUILD_BY" \
  --build-type release \
  --debian-mirror "$DEBIAN_MIRROR" \
  --debian-security-mirror "$DEBIAN_SECURITY_MIRROR" \
  --version "$BUILD_VERSION" \
  --vyos-mirror "$VYOS_MIRROR" \
  --custom-package vim-tiny \
  --custom-package tree \
  --custom-package btop \
  --custom-package ripgrep \
  --custom-package wget \
  --custom-package ncdu \
  --custom-package fastnetmon \
  --custom-package containernetworking-plugins \
  --custom-package grub-efi-arm64-signed \
  --custom-package u-boot-tools \
  --custom-package libubootenv-tool \
  --custom-package binutils \
  --custom-package mtr-tiny \
  --custom-package iperf3 \
  --custom-package ethtool \
  --custom-package iftop \
  --custom-package socat \
  --custom-package hping3 \
  --custom-package conntrack \
  --custom-package strace \
  --custom-package lsof \
  --custom-package psmisc \
  --custom-package tmux \
  --custom-package jq \
  --custom-package sysstat \
  --custom-package netperf \
  --custom-package nuttcp \
  --custom-package flent \
  --custom-package nftables \
  --custom-package iproute2 \
  --custom-package fping \
  --custom-package ngrep \
  --custom-package skopeo \
  --custom-package catatonit \
  --custom-package uidmap \
  --custom-package fuse-overlayfs \
  generic

cd build
# Rename generic -> LS1046A in artifact filenames. Single image:
#   vyos-2026.05.09-1830-rolling-LS1046A-arm64.iso
if command -v jq >/dev/null 2>&1; then
  ORIG_ISO=$(jq --raw-output .artifacts[0] manifest.json)
else
  ORIG_ISO=$(python3 -c 'import json,sys; print(json.load(open("manifest.json"))["artifacts"][0])')
fi
IMAGE_ISO="${ORIG_ISO/generic/LS1046A}"
IMAGE_NAME="${IMAGE_ISO%.iso}"
mv "$ORIG_ISO" "$IMAGE_ISO"
echo "image_name=${IMAGE_NAME}" >> "$GITHUB_OUTPUT"
echo "image_iso=${IMAGE_ISO}" >> "$GITHUB_OUTPUT"

### ─── Make ISO hybrid: append FAT32 boot partition for U-Boot ──────────────
#
# After this section, $IMAGE_ISO is simultaneously:
#   • Valid ISO9660 (PVD at byte 32768 is untouched)
#   • Valid MBR disk image (partition table at byte 440, boot sig at 510)
#     - Partition 1: ISO9660 data (type 0x83)
#     - Partition 2: FAT32 with boot.scr + vmlinuz + initrd + DTB (type 0x0C)

echo ""
echo "### Creating hybrid ISO (ISO9660 + FAT32 boot partition)"

# Extract boot files from ISO using xorriso (no loop mount needed)
ISO_CONTENT=/tmp/iso-content
mkdir -p "$ISO_CONTENT/live"
xorriso -osirrox on -indev "$IMAGE_ISO" \
  -extract /live/vmlinuz    "$ISO_CONTENT/live/vmlinuz" \
  -extract /live/initrd.img "$ISO_CONTENT/live/initrd.img"

# Verify extraction succeeded (xorriso errors may be silent with set -e + pipes)
for f in "$ISO_CONTENT/live/vmlinuz" "$ISO_CONTENT/live/initrd.img"; do
  [ -s "$f" ] || { echo "FATAL: xorriso failed to extract $f from ISO"; exit 1; }
done

# ── F-217/kernel-skew assertion ───────────────────────────────────────────
# The ISO's /live/vmlinuz MUST be byte-identical to the freshly-built
# linux-image .deb's packaged vmlinuz. If the persistent chroot's apt install
# was a no-op (stale kernel, same version), the ISO would ship the PREVIOUS
# run's kernel (old module-signing keyring) against a fresh squashfs/ask.ko ->
# "Key was rejected by service" on the board. Fail the build here instead.
KIMG_DEB=$(find "$GITHUB_WORKSPACE/vyos-build/scripts/package-build" \
  -maxdepth 2 -name 'linux-image-*-vyos_*_arm64.deb' 2>/dev/null | head -1)
if [ -n "$KIMG_DEB" ]; then
  KDEB_EXTRACT=/tmp/kimg-deb; rm -rf "$KDEB_EXTRACT"; mkdir -p "$KDEB_EXTRACT"
  dpkg-deb -x "$KIMG_DEB" "$KDEB_EXTRACT"
  DEB_VMLINUZ=$(find "$KDEB_EXTRACT/boot" -maxdepth 1 -name 'vmlinuz-*-vyos' 2>/dev/null | head -1)
  if [ -n "$DEB_VMLINUZ" ]; then
    if cmp -s "$ISO_CONTENT/live/vmlinuz" "$DEB_VMLINUZ"; then
      echo "### F-217: ISO /live/vmlinuz matches freshly-built linux-image .deb (no kernel skew)"
    else
      echo "::error::ISO /live/vmlinuz does NOT match the freshly-built linux-image .deb"
      echo "::error::(stale persistent chroot shipped an old kernel — kernel/module signing skew)"
      echo "ISO vmlinuz: $(sha256sum "$ISO_CONTENT/live/vmlinuz" | cut -d' ' -f1)"
      echo "deb vmlinuz: $(sha256sum "$DEB_VMLINUZ" | cut -d' ' -f1) ($KIMG_DEB)"
      exit 1
    fi
  else
    echo "::warning::F-217: no vmlinuz-*-vyos in $KIMG_DEB — skipping skew assertion"
  fi
else
  echo "::warning::F-217: no linux-image-*-vyos .deb found — skipping kernel-skew assertion"
fi
# ──────────────────────────────────────────────────────────────────────────

# Generate U-Boot boot script (boot.scr)
mkimage -A arm64 -T script -C none -n "VyOS LS1046A USB Boot" \
  -d "$GITHUB_WORKSPACE/board/scripts/boot.cmd" "$ISO_CONTENT/boot.scr"

# Collect DTBs (use includes.binary version — may have been updated by ci-build-packages.sh)
MONO_DTB_SRC="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.binary/mono-gw.dtb"
[ ! -f "$MONO_DTB_SRC" ] && MONO_DTB_SRC="$GITHUB_WORKSPACE/board/dtb/mono-gw.dtb"
cp "$MONO_DTB_SRC" "$ISO_CONTENT/mono-gw.dtb"
if [ -f "$GITHUB_WORKSPACE/board/dtb/fsl-ls1046a-rdb.dtb" ]; then
  cp "$GITHUB_WORKSPACE/board/dtb/fsl-ls1046a-rdb.dtb" "$ISO_CONTENT/fsl-ls1046a-rdb.dtb"
fi

# Auto-size FAT32 partition: content + 32 MiB headroom, 4 MiB aligned
BOOT_BYTES=$(du -sb "$ISO_CONTENT" | cut -f1)
FAT_BYTES=$(( BOOT_BYTES + 32*1024*1024 ))
FAT_BYTES=$(( (FAT_BYTES + 4*1024*1024 - 1) / (4*1024*1024) * (4*1024*1024) ))
echo "### FAT32 content: $(( BOOT_BYTES / 1024 / 1024 )) MiB, partition: $(( FAT_BYTES / 1024 / 1024 )) MiB"

# Create FAT32 partition image with boot files
FAT_IMG=/tmp/fat-boot.img
truncate -s "$FAT_BYTES" "$FAT_IMG"
mkdosfs -F 32 -n VYOSBOOT "$FAT_IMG"
mmd   -i "$FAT_IMG" ::/live
mcopy -i "$FAT_IMG" "$ISO_CONTENT/live/vmlinuz"    ::/live/vmlinuz
mcopy -i "$FAT_IMG" "$ISO_CONTENT/live/initrd.img" ::/live/initrd.img
mcopy -i "$FAT_IMG" "$ISO_CONTENT/mono-gw.dtb"     ::mono-gw.dtb
mcopy -i "$FAT_IMG" "$ISO_CONTENT/boot.scr"        ::boot.scr
if [ -f "$ISO_CONTENT/fsl-ls1046a-rdb.dtb" ]; then
  mcopy -i "$FAT_IMG" "$ISO_CONTENT/fsl-ls1046a-rdb.dtb" ::fsl-ls1046a-rdb.dtb
fi
rm -rf "$ISO_CONTENT"

# Pad ISO to 1 MiB boundary, then append FAT32 partition
ISO_ORIG_SIZE=$(stat -c %s "$IMAGE_ISO")
ISO_ALIGN=$(( 1024 * 1024 ))
ISO_PADDED=$(( (ISO_ORIG_SIZE + ISO_ALIGN - 1) / ISO_ALIGN * ISO_ALIGN ))
truncate -s "$ISO_PADDED" "$IMAGE_ISO"
cat "$FAT_IMG" >> "$IMAGE_ISO"
rm -f "$FAT_IMG"

# Write MBR partition table into ISO System Area (bytes 440-511)
# ISO9660 spec: bytes 0-32767 are "System Area" — unused by the filesystem.
# Writing an MBR here makes the file a valid disk image when dd'd to USB.
# The ISO9660 PVD at byte 32768 remains untouched.
ISO_SECTORS=$(( ISO_PADDED / 512 ))
FAT_SECTORS=$(( FAT_BYTES / 512 ))

# IMPORTANT: Partition 1 MUST start at sector 0 (the standard isohybrid approach).
# This makes the partition self-referential (contains its own MBR) but is required
# so that ISO9660 PVD at disk byte 32768 = partition-relative byte 32768.
# If partition 1 started at sector 64, the PVD would be at partition byte 0,
# and mount -t iso9660 /dev/sda1 would fail (it looks for PVD at byte 32768).
# live-boot scans /dev/sda1 → mount -t iso9660 → finds PVD → locates squashfs.
python3 -c "
import struct
iso_s, fat_s = $ISO_SECTORS, $FAT_SECTORS
with open('$IMAGE_ISO', 'r+b') as f:
    f.seek(440)
    # Disk ID + reserved
    f.write(struct.pack('<IH', 0x56594F53, 0))  # 'VYOS' as disk ID
    # Partition 1: ISO9660 data starting at sector 0 (isohybrid convention)
    # Type 0x17 (Hidden IFS) — standard for isohybrid; U-Boot skips non-FAT types
    f.write(struct.pack('<BBBBBBBBII',
        0x00, 0xFE, 0xFF, 0xFF, 0x17, 0xFE, 0xFF, 0xFF, 0, iso_s))
    # Partition 2: FAT32 boot partition (type 0x0C W95 FAT32 LBA — U-Boot auto-detects)
    f.write(struct.pack('<BBBBBBBBII',
        0x80, 0xFE, 0xFF, 0xFF, 0x0C, 0xFE, 0xFF, 0xFF, iso_s, fat_s))
    # Partitions 3-4: empty
    f.write(b'\x00' * 32)
    # MBR boot signature
    f.write(struct.pack('<H', 0xAA55))
"

HYBRID_SIZE=$(stat -c %s "$IMAGE_ISO")
echo "### Hybrid ISO created: $(( HYBRID_SIZE / 1024 / 1024 )) MiB"
echo "###   Partition 1: ISO9660 (sectors 0–$((ISO_SECTORS-1)), type 0x17 Hidden IFS)"
echo "###   Partition 2: FAT32  (sectors ${ISO_SECTORS}–$((ISO_SECTORS + FAT_SECTORS - 1)))"
echo "###"
echo "###   dd if=$IMAGE_ISO of=/dev/sdX bs=4M   → USB boot via U-Boot"
echo "###   add system image <url>                → install from ISO9660"

# Cryptographically sign the hybrid ISO (must be AFTER hybrid creation)
MINISIGN_PUBKEY_FILE=$GITHUB_WORKSPACE/data/vyos-ls1046a.minisign.pub
MINISIGN_SECKEY_FILE=$GITHUB_WORKSPACE/data/vyos-ls1046a.minisign.key
if [ -f "$MINISIGN_SECKEY_FILE" ]; then
  "$GITHUB_WORKSPACE/bin/minisign" -s "$MINISIGN_SECKEY_FILE" -Sm "${IMAGE_ISO}"
  "$GITHUB_WORKSPACE/bin/minisign" -Vm "${IMAGE_ISO}" -x "${IMAGE_ISO}.minisig" -p "$MINISIGN_PUBKEY_FILE"
else
  echo "fake sign" > "${IMAGE_ISO}.minisig"
fi

### ASK2 (rewrite-in-progress): the ci-verify-ask-iso.sh post-build
### check was removed on the ask20 branch along with the ASK 1.x
### userspace stack. A new verifier will be added once ASK2
### components land per specs/ask2-rewrite-spec.md.

# Move all artifacts to workspace
mv manifest.json "${IMAGE_ISO}" "${IMAGE_ISO}.minisig" "$GITHUB_WORKSPACE"
