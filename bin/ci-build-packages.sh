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

  ### Kernel build validation — fail fast on silent failures
  if [ "$package" == "linux-kernel" ]; then
    KERNEL_DEB_COUNT=$(find . -maxdepth 1 -name 'linux-image-*.deb' ! -name '*-dbg*' | wc -l)
    if [ "$KERNEL_DEB_COUNT" -eq 0 ]; then
      echo ""
      echo "###############################################################"
      echo "### FATAL: Kernel build produced NO linux-image .deb files! ###"
      echo "###############################################################"
      echo ""
      echo "The VyOS build.py swallowed the kernel build failure."
      echo "Check build-kernel.sh output above for the actual error."
      echo ""
      echo "Common causes:"
      echo "  - Patch failed to apply (check 003-ask-kernel-hooks.patch)"
      echo "  - SDK source extraction failed (ask-nxp-sdk-sources.tar.gz)"
      echo "  - Kconfig symbol conflict (mainline vs SDK DPAA)"
      echo "  - Missing kernel dependency"
      echo ""
      exit 1
    fi
    echo "### Kernel build OK: found $KERNEL_DEB_COUNT .deb file(s)"
    ls -lh linux-image-*.deb 2>/dev/null || true
  fi

  ### Build Mono Gateway DTB from kernel source (before cleanup)
  if [ "$package" == "linux-kernel" ]; then
    KSRC=$(find . -maxdepth 1 -type d -name 'linux-*' | head -1)
    if [ -n "$KSRC" ] && [ -d "$KSRC/arch/arm64/boot/dts/freescale" ]; then
      DTS_DIR="$KSRC/arch/arm64/boot/dts/freescale"
      INCLUDES_BIN="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.binary"
      INCLUDES_CHR="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.chroot"

      # Always ensure base DTS is in the kernel tree
      cp "$GITHUB_WORKSPACE/data/dtb/mono-gateway-dk.dts" "$DTS_DIR/mono-gateway-dk.dts"

      # Copy SDK DTS if present (injected by ci-setup-kernel-ask.sh from archive/)
      if [ -f "$GITHUB_WORKSPACE/data/dtb/mono-gateway-dk-sdk.dts" ]; then
        cp "$GITHUB_WORKSPACE/data/dtb/mono-gateway-dk-sdk.dts" "$DTS_DIR/mono-gateway-dk-sdk.dts"
        # Add to Makefile if not already present
        FMAKEFILE="$DTS_DIR/Makefile"
        if ! grep -q 'mono-gateway-dk-sdk' "$FMAKEFILE" 2>/dev/null; then
          echo 'dtb-$(CONFIG_ARCH_LAYERSCAPE) += mono-gateway-dk-sdk.dtb' >> "$FMAKEFILE"
        fi
      fi

      # Copy SDK dtsi files required by mono-gateway-dk-sdk.dts
      # These are NXP SDK-specific includes not present in the mainline kernel tree
      SDK_DTSI_DIR="$GITHUB_WORKSPACE/data/dtb/sdk-dtsi"
      if [ -d "$SDK_DTSI_DIR" ]; then
        echo "### Installing SDK dtsi files into kernel DTS directory"
        cp -v "$SDK_DTSI_DIR"/*.dtsi "$DTS_DIR/" 2>/dev/null || true
      fi

      # Check if SDK DTS exists (injected by ci-setup-kernel-ask.sh)
      SDK_DTS="$DTS_DIR/mono-gateway-dk-sdk.dts"
      SDK_DTB_OK=false
      if [ -f "$SDK_DTS" ]; then
        echo "### Building SDK+ASK DTB from kernel source"
        make -C "$KSRC" freescale/mono-gateway-dk-sdk.dtb 2>&1 | tail -10 || true
        SDK_DTB="$DTS_DIR/mono-gateway-dk-sdk.dtb"
        if [ -f "$SDK_DTB" ]; then
          # SDK DTB is PRIMARY — SDK fsl_mac driver needs fixed-link properties
          # (not mainline phylink/SFP which causes "phy device not initialized").
          # Previous working build used 35KB SDK DTB; OpenWrt 92KB DTB breaks SDK kernel.
          cp "$SDK_DTB" "$INCLUDES_BIN/mono-gw.dtb"
          cp "$SDK_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
          # Also keep a named copy for reference
          cp "$SDK_DTB" "$INCLUDES_BIN/mono-gw-sdk.dtb"
          cp "$SDK_DTB" "$INCLUDES_CHR/boot/mono-gw-sdk.dtb"
          SDK_DTB_OK=true
          echo "### SDK DTB compiled as PRIMARY mono-gw.dtb: $(stat -c '%s bytes' "$SDK_DTB")"
        else
          echo "WARNING: mono-gateway-dk-sdk.dtb build failed — falling back to mainline DTB"
        fi
      fi

      # Build mainline DTB (primary only if SDK DTB not available)
      echo "### Building mainline DTB from kernel source"
      make -C "$KSRC" freescale/mono-gateway-dk.dtb 2>&1 | tail -10 || true
      MONO_DTB="$DTS_DIR/mono-gateway-dk.dtb"
      if [ -f "$MONO_DTB" ]; then
        if [ "$SDK_DTB_OK" = true ]; then
          # SDK mode: mainline DTB is secondary (for reference/fallback only)
          cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw-mainline.dtb"
          echo "### Mainline DTB saved as mono-gw-mainline.dtb (SDK DTB is primary)"
        else
          # Mainline mode (or SDK build failed): use mainline DTB as primary
          cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw.dtb"
          cp "$MONO_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
          echo "### Mainline DTB compiled as primary: $(stat -c '%s bytes' "$MONO_DTB")"
        fi
      else
        echo "WARNING: mono-gateway-dk.dtb build failed, keeping pre-built DTB"
      fi
    fi

    ### Build ASK out-of-tree kernel modules (cdx, fci, auto_bridge)
    # Must happen while kernel source tree ($KSRC) still exists (before cleanup)
    ASK_SRC="$GITHUB_WORKSPACE/ask-ls1046a-6.6"
    ASK_DST="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.chroot/usr/local/lib/ask-modules"

    # Apply CDX bugfixes to ASK source (fixes from dev_boot testing)
    # The ask-ls1046a-6.6 repo may not have all fixes yet — apply patch
    ASK_PATCH="$GITHUB_WORKSPACE/data/kernel-patches/ask-cdx-bugfixes.patch"
    if [ -d "$ASK_SRC/cdx" ] && [ -f "$ASK_PATCH" ]; then
      echo "### Applying ASK CDX bugfixes patch to ask-ls1046a-6.6"
      patch --no-backup-if-mismatch -p1 -d "$ASK_SRC" < "$ASK_PATCH" || \
        echo "WARNING: ASK CDX patch partially failed — some fixes may be missing"
    fi

    if [ -n "$KSRC" ] && [ -d "$ASK_SRC/cdx" ]; then
      KSRC_ABS="$(cd "$KSRC" && pwd)"
      echo "### Building ASK kernel modules against $KSRC_ABS"
      mkdir -p "$ASK_DST"

      # Module signing helper — CONFIG_MODULE_SIG_FORCE=y requires all modules signed
      # The kernel auto-generates certs/signing_key.pem during build; sign-file uses it
      sign_module() {
        local mod="$1"
        if [ -f "$KSRC_ABS/scripts/sign-file" ] && [ -f "$KSRC_ABS/certs/signing_key.pem" ]; then
          "$KSRC_ABS/scripts/sign-file" sha512 \
            "$KSRC_ABS/certs/signing_key.pem" \
            "$KSRC_ABS/certs/signing_key.x509" \
            "$mod"
          echo "   Signed: $(basename "$mod")"
        else
          echo "   WARNING: Cannot sign $(basename "$mod") — signing key not found"
          echo "   Module will be rejected by CONFIG_MODULE_SIG_FORCE=y kernel"
        fi
      }

      # cdx.ko — main ASK control-plane module
      echo "I: Building cdx.ko..."
      make -C "$ASK_SRC/cdx" clean 2>/dev/null || true
      make -C "$KSRC_ABS" M="$ASK_SRC/cdx" \
        PLATFORM=LS1043A ARCH=arm64 modules 2>&1 | tail -20
      if [ -f "$ASK_SRC/cdx/cdx.ko" ]; then
        cp "$ASK_SRC/cdx/cdx.ko" "$ASK_DST/"
        cp "$ASK_SRC/cdx/Module.symvers" "$ASK_DST/cdx.symvers"
        sign_module "$ASK_DST/cdx.ko"
        echo "### cdx.ko built: $(stat -c '%s bytes' "$ASK_DST/cdx.ko")"
      else
        echo "WARNING: cdx.ko build failed — using pre-built from data/ask-userspace/"
        cp "$GITHUB_WORKSPACE/data/ask-userspace/cdx/cdx.ko" "$ASK_DST/" || true
      fi

      # auto_bridge.ko — bridge fast-path offload
      echo "I: Building auto_bridge.ko..."
      make -C "$ASK_SRC/auto_bridge" clean 2>/dev/null || true
      make -C "$KSRC_ABS" M="$ASK_SRC/auto_bridge" \
        PLATFORM=LS1043A ARCH=arm64 modules 2>&1 | tail -20
      if [ -f "$ASK_SRC/auto_bridge/auto_bridge.ko" ]; then
        cp "$ASK_SRC/auto_bridge/auto_bridge.ko" "$ASK_DST/"
        sign_module "$ASK_DST/auto_bridge.ko"
        echo "### auto_bridge.ko built: $(stat -c '%s bytes' "$ASK_DST/auto_bridge.ko")"
      else
        echo "WARNING: auto_bridge.ko build failed — using pre-built"
        cp "$GITHUB_WORKSPACE/data/ask-userspace/auto_bridge/auto_bridge.ko" "$ASK_DST/" || true
      fi

      # fci.ko — fast-path conntrack interface (depends on cdx symbols)
      echo "I: Building fci.ko..."
      make -C "$ASK_SRC/fci" clean 2>/dev/null || true
      CDX_SYMVERS="$ASK_DST/cdx.symvers"
      make -C "$KSRC_ABS" M="$ASK_SRC/fci" \
        KBUILD_EXTRA_SYMBOLS="$CDX_SYMVERS" \
        ARCH=arm64 modules 2>&1 | tail -20
      if [ -f "$ASK_SRC/fci/fci.ko" ]; then
        cp "$ASK_SRC/fci/fci.ko" "$ASK_DST/"
        sign_module "$ASK_DST/fci.ko"
        echo "### fci.ko built: $(stat -c '%s bytes' "$ASK_DST/fci.ko")"
      else
        echo "WARNING: fci.ko build failed — using pre-built"
        cp "$GITHUB_WORKSPACE/data/ask-userspace/fci/fci.ko" "$ASK_DST/" || true
      fi

      echo "### ASK kernel modules: $(ls -la "$ASK_DST/"*.ko 2>/dev/null | wc -l) modules installed"
    elif [ -d "$GITHUB_WORKSPACE/data/ask-userspace/cdx" ]; then
      echo "### ASK source not available — installing pre-built modules"
      mkdir -p "$ASK_DST"
      cp "$GITHUB_WORKSPACE/data/ask-userspace/cdx/cdx.ko" "$ASK_DST/" || true
      cp "$GITHUB_WORKSPACE/data/ask-userspace/auto_bridge/auto_bridge.ko" "$ASK_DST/" || true
      cp "$GITHUB_WORKSPACE/data/ask-userspace/fci/fci.ko" "$ASK_DST/" || true
    fi

    ### Build ASK userspace binaries from source (cmm, dpa_app, libcli, libfci)
    # Overwrites pre-built binaries installed by ci-setup-vyos-build.sh with source-built versions
    if [ -n "$KSRC" ] && [ -x "$GITHUB_WORKSPACE/bin/ci-build-ask-userspace.sh" ]; then
      KSRC_ABS_ASK="$(cd "$KSRC" && pwd)"
      echo "### Building ASK userspace from source"
      "$GITHUB_WORKSPACE/bin/ci-build-ask-userspace.sh" "$KSRC_ABS_ASK" "$INCLUDES_CHR" || \
        echo "WARNING: ASK userspace build failed (non-fatal) — using pre-built binaries"
    fi

    ### Build accel-ppp-ng ARM64 packages (daemon + kernel modules)
    # Must happen while kernel source tree ($KSRC) still exists
    if [ -n "$KSRC" ] && [ -x "$GITHUB_WORKSPACE/bin/ci-build-accel-ppp.sh" ]; then
      KSRC_ABS_ACCEL="$(cd "$KSRC" && pwd)"
      echo "### Building accel-ppp-ng ARM64 packages"
      "$GITHUB_WORKSPACE/bin/ci-build-accel-ppp.sh" "$KSRC_ABS_ACCEL" "$(pwd)" || \
        echo "WARNING: accel-ppp-ng build failed (non-fatal) — PPPoE/L2TP will be unavailable"
      echo "### accel-ppp-ng .debs in package dir:"
      ls -lh accel-ppp*.deb 2>/dev/null || echo "  (none produced)"
    fi
  fi

  # clean
  df -Th
  apt-get autoremove -y
  rm -rf "$package" *.gz *.xz "$HOME/.cache/go-build" "$HOME/go/pkg/mod" "$HOME/.rustup"
  df -Th
  cd ..
done
