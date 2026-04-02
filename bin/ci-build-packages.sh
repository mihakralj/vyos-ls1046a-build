#!/bin/bash
# ci-build-packages.sh — Build linux-kernel and vyos-1x packages
# Called by: .github/workflows/auto-build.yml "Build Image Packages" step
# Expects: GITHUB_WORKSPACE set
set -ex
cd "${GITHUB_WORKSPACE:-.}/vyos-build/scripts/package-build"

packages="linux-kernel vyos-1x"
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
      echo "### Building Mono Gateway DTB from kernel source"
      # Copy our custom DTS into the kernel tree
      cp "$GITHUB_WORKSPACE/data/dtb/mono-gateway-dk.dts" \
         "$KSRC/arch/arm64/boot/dts/freescale/mono-gateway-dk.dts"
      # Build it using the kernel's DTS infrastructure
      make -C "$KSRC" freescale/mono-gateway-dk.dtb 2>&1 | tail -10 || true
      MONO_DTB="$KSRC/arch/arm64/boot/dts/freescale/mono-gateway-dk.dtb"
      if [ -f "$MONO_DTB" ]; then
        # Replace the SDK-extracted DTB with our mainline-compiled one
        cp "$MONO_DTB" "$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.binary/mono-gw.dtb"
        # Also copy into squashfs (/boot/) so install_image() auto-copies it
        cp "$MONO_DTB" "$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.chroot/boot/mono-gw.dtb"
        echo "### Mono Gateway DTB compiled: $(stat -c '%s bytes' "$MONO_DTB")"
      else
        echo "WARNING: mono-gateway-dk.dtb build failed, keeping SDK DTB"
        ls "$KSRC/arch/arm64/boot/dts/freescale/"*.dtb 2>/dev/null | head -5 || true
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
