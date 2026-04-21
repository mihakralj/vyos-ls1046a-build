#!/bin/bash
# ci-build-packages.sh — Build vyos-1x (+ optionally linux-kernel) packages
# Called by: .github/workflows/auto-build.yml "Build Image Packages" step
# Expects: GITHUB_WORKSPACE set
#
# When ASK_KERNEL_TAG is set, the linux-kernel target is SKIPPED because the
# prebuilt ASK kernel .debs have already been staged into packages/ by
# bin/ci-consume-ask-kernel.sh. Building the kernel locally in that mode
# would just consume 20+ minutes and replace the ASK kernel with a vanilla
# one that lacks fast-path hooks.
set -ex
cd "${GITHUB_WORKSPACE:-.}/vyos-build/scripts/package-build"

if [ -n "${ASK_KERNEL_TAG:-}" ]; then
    echo "### ASK kernel in effect ($ASK_KERNEL_TAG) — skipping linux-kernel local build"
    packages="vyos-1x"
else
    packages="linux-kernel vyos-1x"
fi
ignore_packages=(amazon-cloudwatch-agent amazon-ssm-agent xen-guest-agent)

for package in $packages; do
  [ ! -d "$package" ] && continue
  [[ " ${ignore_packages[@]} " =~ " ${package} " ]] && continue
  cd "$package"

  [ "$package" == "keepalived" ] && apt-get install -y libsnmp-dev

  ./build.py

  [ "$package" == "keepalived" ] && apt-get remove -y libsnmp-dev

  ### Build Mono Gateway DTB from kernel source (before cleanup)
  if [ "$package" == "linux-kernel" ]; then
    KSRC=$(find . -maxdepth 1 -type d -name 'linux-*' | head -1)
    if [ -n "$KSRC" ] && [ -d "$KSRC/arch/arm64/boot/dts/freescale" ]; then
      DTS_DIR="$KSRC/arch/arm64/boot/dts/freescale"
      INCLUDES_BIN="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.binary"
      INCLUDES_CHR="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.chroot"

      # Always ensure base DTS is in the kernel tree
      cp "$GITHUB_WORKSPACE/data/dtb/mono-gateway-dk.dts" "$DTS_DIR/mono-gateway-dk.dts"

      # Check if SDK DTS exists (injected by ci-setup-kernel-sdk.sh)
      SDK_DTS="$DTS_DIR/mono-gateway-dk-sdk.dts"
      if [ -f "$SDK_DTS" ]; then
        echo "### Building SDK+ASK DTB from kernel source"
        make -C "$KSRC" freescale/mono-gateway-dk-sdk.dtb 2>&1 | tail -10 || true
        SDK_DTB="$DTS_DIR/mono-gateway-dk-sdk.dtb"
        if [ -f "$SDK_DTB" ]; then
          # SDK DTB is the primary DTB for U-Boot (replaces mono-gw.dtb)
          cp "$SDK_DTB" "$INCLUDES_BIN/mono-gw.dtb"
          cp "$SDK_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
          echo "### SDK DTB compiled and installed as mono-gw.dtb: $(stat -c '%s bytes' "$SDK_DTB")"
        else
          echo "WARNING: mono-gateway-dk-sdk.dtb build failed — falling back to mainline DTB"
        fi
      fi

      # Build mainline DTB (primary in mainline mode, fallback in SDK mode)
      echo "### Building mainline DTB from kernel source"
      make -C "$KSRC" freescale/mono-gateway-dk.dtb 2>&1 | tail -10 || true
      MONO_DTB="$DTS_DIR/mono-gateway-dk.dtb"
      if [ -f "$MONO_DTB" ]; then
        if [ ! -f "$SDK_DTS" ]; then
          # Mainline mode: use mainline DTB as primary
          cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw.dtb"
          cp "$MONO_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
          echo "### Mainline DTB compiled: $(stat -c '%s bytes' "$MONO_DTB")"
        else
          # SDK mode: keep mainline DTB as secondary (for reference/fallback)
          cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw-mainline.dtb"
          echo "### Mainline DTB saved as mono-gw-mainline.dtb (SDK DTB is primary)"
        fi
      else
        echo "WARNING: mono-gateway-dk.dtb build failed, keeping pre-built DTB"
      fi
    fi
  fi

  # clean
  df -Th
  apt-get autoremove -y
  rm -rf "$package" *.gz *.xz "$HOME/.cache/go-build" "$HOME/go/pkg/mod" "$HOME/.rustup"
  df -Th
  cd ..
done
