#!/bin/bash
# ci-build-iso.sh — Build VyOS ISO, sign it, create USB boot image
# Called by: .github/workflows/auto-build.yml "Build VyOS ISO" step
# Expects: GITHUB_WORKSPACE, BUILD_BY, BUILD_VERSION, DEBIAN_MIRROR,
#          DEBIAN_SECURITY_MIRROR, VYOS_MIRROR in env
set -ex
cd "${GITHUB_WORKSPACE:-.}/vyos-build"

### Copy mainline RDB DTB if built during kernel step
if [ -f "$GITHUB_WORKSPACE/data/dtb/fsl-ls1046a-rdb.dtb" ]; then
  cp "$GITHUB_WORKSPACE/data/dtb/fsl-ls1046a-rdb.dtb" \
    data/live-build-config/includes.binary/fsl-ls1046a-rdb.dtb
  echo "### Mainline RDB DTB included in ISO"
fi

rm -rf packages/linux-headers-*

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
  --custom-package skopeo \
  --custom-package catatonit \
  --custom-package uidmap \
  --custom-package fuse-overlayfs \
  generic

cd build
# Rename generic -> LS1046A in artifact filenames
ORIG_ISO=$(jq --raw-output .artifacts[0] manifest.json)
IMAGE_ISO="${ORIG_ISO/generic/LS1046A}"
IMAGE_NAME="${IMAGE_ISO%.iso}"
mv "$ORIG_ISO" "$IMAGE_ISO"
echo "image_name=${IMAGE_NAME}" >> "$GITHUB_OUTPUT"
echo "image_iso=${IMAGE_ISO}" >> "$GITHUB_OUTPUT"

# Cryptographically sign the image
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
mv manifest.json "${IMAGE_ISO}" "${IMAGE_ISO}.minisig" \
   "${USB_IMG}" "${USB_IMG}.minisig" "$GITHUB_WORKSPACE"
