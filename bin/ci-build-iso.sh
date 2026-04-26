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
set -ex
cd "${GITHUB_WORKSPACE:-.}/vyos-build"

### Pre-flight: verify custom kernel is present (defense-in-depth)
# In ASK-consume mode (ASK_KERNEL_TAG set) the prebuilt kernel .deb is staged
# into data/live-build-config/packages.chroot/ by ci-consume-ask-kernel.sh — NOT
# into vyos-build/packages/ — because that is the directory live-build's dpkg
# pass picks up during chroot. Check the chroot staging dir in that mode.
if [ -n "${ASK_KERNEL_TAG:-}" ]; then
  PKG_CHROOT="data/live-build-config/packages.chroot"
  KERNEL_IN_PACKAGES=$(find "$PKG_CHROOT" -maxdepth 1 -name 'linux-image-*_arm64.deb' ! -name '*-dbg*' 2>/dev/null | wc -l)
  SEARCH_DIR="$PKG_CHROOT/"
else
  KERNEL_IN_PACKAGES=$(find packages -name 'linux-image-*.deb' ! -name '*-dbg*' 2>/dev/null | wc -l)
  SEARCH_DIR="packages/"
fi
if [ "$KERNEL_IN_PACKAGES" -eq 0 ]; then
  echo ""
  echo "###############################################################"
  echo "### FATAL: No custom kernel .deb in $SEARCH_DIR"
  echo "### Refusing to build ISO with upstream fallback kernel.    ###"
  echo "###############################################################"
  echo ""
  echo "This check prevents shipping an ISO without ASK/SDK drivers."
  echo "The kernel build/consume likely failed silently in a previous step."
  echo ""
  exit 1
fi
echo "### Pre-flight OK: custom kernel present ($KERNEL_IN_PACKAGES .deb in $SEARCH_DIR)"

### Copy mainline RDB DTB if built during kernel step
if [ -f "$GITHUB_WORKSPACE/data/dtb/fsl-ls1046a-rdb.dtb" ]; then
  cp "$GITHUB_WORKSPACE/data/dtb/fsl-ls1046a-rdb.dtb" \
    data/live-build-config/includes.binary/fsl-ls1046a-rdb.dtb
  echo "### Mainline RDB DTB included in ISO"
fi

rm -rf packages/linux-headers-*

### Force live-build to install ASK-specific .debs from packages.chroot/.
#
# live-build only invokes `dpkg -i` against packages.chroot/ for packages
# that are explicitly named (via package-lists or --custom-package) OR that
# are pulled in as a Depends: of something else that is installed.
#
# linux-image-<KVER>-vyos is depended on by vyos-1x via the kernel ABI
# pin, so it lands automatically. ask-modules-<KVER>-vyos has NO reverse
# dependency in the VyOS stack — it sits in packages.chroot/ as a
# stand-alone deb and gets ignored by apt unless we name it explicitly.
#
# Derive the package name by reading the actual .deb file we staged, so
# this self-adjusts when the kernel version bumps.
ASK_CUSTOM_ARGS=()
if [ -n "${ASK_KERNEL_TAG:-}" ]; then
    PKG_CHROOT="data/live-build-config/packages.chroot"
    shopt -s nullglob
    for deb in "$PKG_CHROOT"/ask-modules-*_arm64.deb; do
        pkg=$(dpkg-deb -f "$deb" Package)
        ASK_CUSTOM_ARGS+=(--custom-package "$pkg")
        echo "### Forcing install of ASK package: $pkg (from $(basename "$deb"))"
    done
    shopt -u nullglob
    if [ ${#ASK_CUSTOM_ARGS[@]} -eq 0 ]; then
        echo "WARN: ASK_KERNEL_TAG set but no ask-modules-*.deb in $PKG_CHROOT/"
        echo "      The ISO will boot without OOT fast-path modules (cdx/fci)."
    fi
fi

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
  "${ASK_CUSTOM_ARGS[@]}" \
  generic

cd build
# Rename generic -> LS1046A in artifact filenames
ORIG_ISO=$(jq --raw-output .artifacts[0] manifest.json)
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

# Generate U-Boot boot script (boot.scr)
mkimage -A arm64 -T script -C none -n "VyOS LS1046A USB Boot" \
  -d "$GITHUB_WORKSPACE/data/scripts/boot.cmd" "$ISO_CONTENT/boot.scr"

# Collect DTBs (use includes.binary version — may have been updated by ci-build-packages.sh)
MONO_DTB_SRC="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.binary/mono-gw.dtb"
[ ! -f "$MONO_DTB_SRC" ] && MONO_DTB_SRC="$GITHUB_WORKSPACE/data/dtb/mono-gw.dtb"
cp "$MONO_DTB_SRC" "$ISO_CONTENT/mono-gw.dtb"
if [ -f "$GITHUB_WORKSPACE/data/dtb/fsl-ls1046a-rdb.dtb" ]; then
  cp "$GITHUB_WORKSPACE/data/dtb/fsl-ls1046a-rdb.dtb" "$ISO_CONTENT/fsl-ls1046a-rdb.dtb"
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

### Create FAT32 USB boot image
# U-Boot reads FAT32 natively. ISO9660 requires Rufus workarounds.
USB_IMG="${IMAGE_NAME}-usb.img"
mkdir -p /tmp/iso-mount
mount -o loop "$IMAGE_ISO" /tmp/iso-mount

# Assert every staged ASK package actually landed in the ISO before we
# spend another minute building the USB image / signing / uploading.
# Fails the build loudly if the ASK userspace regressed back to stock
# Debian (as happened in run 24794085304 before the packages.chroot/
# staging fix).
"$GITHUB_WORKSPACE/bin/ci-verify-ask-iso.sh" /tmp/iso-mount

truncate -s 4G "$USB_IMG"
mkdosfs -F 32 -n VYOSBOOT "$USB_IMG"
mmd   -i "$USB_IMG" ::/live
mcopy -i "$USB_IMG" /tmp/iso-mount/live/vmlinuz             ::/live/vmlinuz
mcopy -i "$USB_IMG" /tmp/iso-mount/live/initrd.img          ::/live/initrd.img
mcopy -i "$USB_IMG" /tmp/iso-mount/live/filesystem.squashfs ::/live/filesystem.squashfs
mcopy -i "$USB_IMG" "$GITHUB_WORKSPACE/data/dtb/mono-gw.dtb" ::mono-gw.dtb

# Generate U-Boot boot script (boot.scr)
mkimage -A arm64 -T script -C none -n "VyOS LS1046A USB Boot" \
  -d "$GITHUB_WORKSPACE/data/scripts/boot.cmd" /tmp/boot.scr
mcopy -i "$USB_IMG" /tmp/boot.scr ::boot.scr

umount /tmp/iso-mount

# Compress USB image — raw 4 GiB FAT32 exceeds GitHub's 2 GiB asset limit
zstd -T0 -19 --rm "${USB_IMG}"
USB_IMG="${USB_IMG}.zst"

if [ -f "$MINISIGN_SECKEY_FILE" ]; then
  "$GITHUB_WORKSPACE/bin/minisign" -s "$MINISIGN_SECKEY_FILE" -Sm "${USB_IMG}"
  "$GITHUB_WORKSPACE/bin/minisign" -Vm "${USB_IMG}" -x "${USB_IMG}.minisig" -p "$MINISIGN_PUBKEY_FILE"
else
  echo "fake sign" > "${USB_IMG}.minisig"
fi
echo "usb_img=${USB_IMG}" >> "$GITHUB_OUTPUT"

# Move all artifacts to workspace
mv manifest.json "${IMAGE_ISO}" "${IMAGE_ISO}.minisig" "$GITHUB_WORKSPACE"
