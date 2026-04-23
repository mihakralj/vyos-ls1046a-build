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
    # Find the actual kernel source tree (has Makefile + arch/arm64).
    # `find -name 'linux-*'` matches both linux-6.6.x/ (the kernel) AND
    # linux-firmware/ (just firmware blobs). We must exclude the latter,
    # and also require the presence of arch/arm64/ to distinguish.
    KSRC=""
    for candidate in $(find . -maxdepth 1 -type d -name 'linux-*' | sort); do
      case "$(basename "$candidate")" in
        linux-firmware|linux-headers*|linux-libc-dev*|linux-doc*) continue ;;
      esac
      if [ -f "$candidate/Makefile" ] && [ -d "$candidate/arch/arm64" ]; then
        KSRC="$candidate"
        break
      fi
    done
    if [ -z "$KSRC" ]; then
      echo ""
      echo "################################################################"
      echo "### FATAL: Could not locate kernel source tree under $(pwd)"
      echo "### Directories found:"
      find . -maxdepth 1 -type d -name 'linux-*' | sed 's/^/###   /'
      echo "################################################################"
      exit 1
    fi
    echo "### Kernel source tree: $KSRC"
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

      # Build mainline DTB (primary only if SDK DTB not available).
      # Failure here is FATAL when SDK DTB is also unavailable, because
      # the fallback path silently ships the (potentially stale) DTB
      # committed under data/dtb/.  That is exactly how the missing
      # DWC3 USB stability quirks slipped past CI: dts gained the
      # quirks, dtb was never recompiled, and live-boot from USB stick
      # panicked with "Attempted to kill init!" on the Mono device.
      echo "### Building mainline DTB from kernel source"
      MAKE_RC=0
      make -C "$KSRC" freescale/mono-gateway-dk.dtb 2>&1 | tail -10 || MAKE_RC=$?
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
        if [ "$SDK_DTB_OK" = true ]; then
          echo "WARNING: mainline DTB build failed (rc=$MAKE_RC) — SDK DTB will be used as primary"
        else
          echo "FATAL: mono-gateway-dk.dtb build failed (rc=$MAKE_RC) and no SDK DTB available."
          echo "FATAL: refusing to fall back to potentially stale data/dtb/mono-gw.dtb."
          exit 1
        fi
      fi
    fi

    ### Build ASK out-of-tree kernel modules (cdx, fci, auto_bridge)
    # Must happen while kernel source tree ($KSRC) still exists (before cleanup)
    ASK_SRC="$GITHUB_WORKSPACE/ask-ls1046a-6.6"
    ASK_DST="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.chroot/usr/local/lib/ask-modules"

    # Apply CDX bugfixes to ASK source (fixes from dev_boot testing)
    # The ask-ls1046a-6.6 repo may not have all fixes yet — apply patch
    ASK_PATCH="$ASK_SRC/patches/cdx/01-mono-defensive-rewrite.patch"
    if [ -d "$ASK_SRC/cdx" ] && [ -f "$ASK_PATCH" ]; then
      echo "### Applying ASK CDX bugfixes patch to ask-ls1046a-6.6"
      patch --no-backup-if-mismatch -p1 -d "$ASK_SRC" < "$ASK_PATCH" || \
        echo "WARNING: ASK CDX patch partially failed — some fixes may be missing"
    fi

    # NR_CPUS=4 fix: CDX defines MAX_SCHEDULER_QUEUES=16 (NUM_PQS+NUM_WBFQS) but
    # DPAA_ETH_TX_QUEUES=NR_CPUS=4 on LS1046A. The unconditional #error in
    # control_qm.c is only meaningful when CEETM (ENABLE_EGRESS_QOS) is enabled —
    # which it is NOT in our build. Guard the assertion under #ifdef ENABLE_EGRESS_QOS.
    QM_FILE="$ASK_SRC/cdx/control_qm.c"
    if [ -f "$QM_FILE" ] && grep -q "^#if MAX_SCHEDULER_QUEUES > DPAA_ETH_TX_QUEUES" "$QM_FILE"; then
      echo "### Guarding MAX_SCHEDULER_QUEUES #error under ENABLE_EGRESS_QOS in $QM_FILE"
      python3 - "$QM_FILE" <<'PYEOF'
import sys, re
p = sys.argv[1]
s = open(p).read()
old = "#if MAX_SCHEDULER_QUEUES > DPAA_ETH_TX_QUEUES\n#error MAX_SCHEDULER_QUEUES exceeds DPAA_ETH_TX_QUEUES\n#endif"
new = "#ifdef ENABLE_EGRESS_QOS\n#if MAX_SCHEDULER_QUEUES > DPAA_ETH_TX_QUEUES\n#error MAX_SCHEDULER_QUEUES exceeds DPAA_ETH_TX_QUEUES\n#endif\n#endif"
if old not in s:
    sys.stderr.write("FATAL: expected #error block not found in control_qm.c\n")
    sys.exit(1)
open(p, "w").write(s.replace(old, new, 1))
print("   Patched control_qm.c (NR_CPUS=4 CEETM guard)")
PYEOF
    fi

    # Relax -Werror on CDX Makefile — NXP SDK source has many -Wunused-* warnings
    # (cdx_ehash.c:359 unused variable 'i', util.c:867 unused parameters, etc).
    # -Werror makes them fatal. Keep warnings visible but don't fail the build.
    CDX_MK="$ASK_SRC/cdx/Makefile"
    if [ -f "$CDX_MK" ] && grep -q "^ccflags-y += -Werror" "$CDX_MK"; then
      echo "### Relaxing -Werror in $CDX_MK (NXP SDK has many benign warnings)"
      sed -i 's/^ccflags-y += -Werror /ccflags-y += -Wno-error /' "$CDX_MK"
    fi

    if [ -n "$KSRC" ] && [ -d "$ASK_SRC/cdx" ]; then
      KSRC_ABS="$(cd "$KSRC" && pwd)"
      echo "### Building ASK kernel modules against $KSRC_ABS"
      mkdir -p "$ASK_DST"

      # Module signing helper — CONFIG_MODULE_SIG_FORCE=y requires all modules signed
      # The kernel auto-generates certs/signing_key.pem during build; sign-file uses it
      # FATAL if signing key is missing — unsigned modules will be rejected at load time.
      sign_module() {
        local mod="$1"
        if [ ! -f "$KSRC_ABS/scripts/sign-file" ] || [ ! -f "$KSRC_ABS/certs/signing_key.pem" ]; then
          echo ""
          echo "################################################################"
          echo "### FATAL: Kernel signing key not found — cannot sign $(basename "$mod")"
          echo "### CONFIG_MODULE_SIG_FORCE=y will reject unsigned modules at boot."
          echo "### Expected: $KSRC_ABS/scripts/sign-file"
          echo "###           $KSRC_ABS/certs/signing_key.pem"
          echo "################################################################"
          exit 1
        fi
        "$KSRC_ABS/scripts/sign-file" sha512 \
          "$KSRC_ABS/certs/signing_key.pem" \
          "$KSRC_ABS/certs/signing_key.x509" \
          "$mod"
        echo "   Signed: $(basename "$mod")"
      }

      # Fail-fast helper — no stale pre-built fallback.
      # Pre-built .ko files in data/ask-userspace/ are built against a different
      # kernel version (6.6.129) and will be rejected by vermagic AND by
      # CONFIG_MODULE_SIG_FORCE=y. Shipping them guarantees boot-time rejection.
      fail_build() {
        local mod="$1"
        echo ""
        echo "################################################################"
        echo "### FATAL: $mod build failed against kernel $KSRC_ABS"
        echo "### Refusing to ship stale pre-built module (wrong vermagic +"
        echo "### unsigned → guaranteed rejection by MODULE_SIG_FORCE kernel)."
        echo "### Fix the in-tree build in ask-ls1046a-6.6/$(dirname "$mod")/"
        echo "################################################################"
        exit 1
      }

      # Aggressive clean: remove ALL stale build artifacts from prior kernels.
      # `make clean` is not enough — stale .ko/.o from pre-existing 6.6.129 build
      # in the checked-in ask-ls1046a-6.6/ tree can confuse the build.
      echo "### Cleaning stale ASK build artifacts"
      find "$ASK_SRC/cdx" "$ASK_SRC/fci" "$ASK_SRC/auto_bridge" \
        \( -name '*.ko' -o -name '*.o' -o -name '*.mod' -o -name '*.mod.c' \
           -o -name '.*.cmd' -o -name 'Module.symvers' -o -name 'modules.order' \) \
        -delete 2>/dev/null || true

      # cdx.ko — main ASK control-plane module
      echo "I: Building cdx.ko..."
      make -C "$KSRC_ABS" M="$ASK_SRC/cdx" \
        PLATFORM=LS1043A ARCH=arm64 modules 2>&1 | tail -30
      if [ ! -f "$ASK_SRC/cdx/cdx.ko" ]; then
        fail_build "cdx.ko"
      fi
      cp "$ASK_SRC/cdx/cdx.ko" "$ASK_DST/"
      cp "$ASK_SRC/cdx/Module.symvers" "$ASK_DST/cdx.symvers"
      sign_module "$ASK_DST/cdx.ko"
      echo "### cdx.ko built: $(stat -c '%s bytes' "$ASK_DST/cdx.ko")"

      # auto_bridge.ko — bridge fast-path offload
      echo "I: Building auto_bridge.ko..."
      make -C "$KSRC_ABS" M="$ASK_SRC/auto_bridge" \
        PLATFORM=LS1043A ARCH=arm64 modules 2>&1 | tail -30
      if [ ! -f "$ASK_SRC/auto_bridge/auto_bridge.ko" ]; then
        fail_build "auto_bridge.ko"
      fi
      cp "$ASK_SRC/auto_bridge/auto_bridge.ko" "$ASK_DST/"
      sign_module "$ASK_DST/auto_bridge.ko"
      echo "### auto_bridge.ko built: $(stat -c '%s bytes' "$ASK_DST/auto_bridge.ko")"

      # fci.ko — fast-path conntrack interface (depends on cdx symbols)
      echo "I: Building fci.ko..."
      CDX_SYMVERS="$ASK_DST/cdx.symvers"
      make -C "$KSRC_ABS" M="$ASK_SRC/fci" \
        KBUILD_EXTRA_SYMBOLS="$CDX_SYMVERS" \
        ARCH=arm64 modules 2>&1 | tail -30
      if [ ! -f "$ASK_SRC/fci/fci.ko" ]; then
        fail_build "fci.ko"
      fi
      cp "$ASK_SRC/fci/fci.ko" "$ASK_DST/"
      sign_module "$ASK_DST/fci.ko"
      echo "### fci.ko built: $(stat -c '%s bytes' "$ASK_DST/fci.ko")"

      echo "### ASK kernel modules: $(ls -la "$ASK_DST/"*.ko 2>/dev/null | wc -l) modules installed and signed"
    else
      echo ""
      echo "################################################################"
      echo "### FATAL: ASK source tree not found at $ASK_SRC"
      echo "### Cannot build ASK modules — refusing to use stale pre-built."
      echo "################################################################"
      exit 1
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
