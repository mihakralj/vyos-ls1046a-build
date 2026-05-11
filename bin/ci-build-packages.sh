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

# Source FLAVOR before changing CWD so common.sh can resolve REPO_ROOT.
# shellcheck source=common.sh
. "${GITHUB_WORKSPACE:-.}/bin/common.sh"

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

  ### vyos-1x .deb cache (skip ~6m build when patches+upstream commit unchanged)
  #
  # Cache key derivation (must be deterministic and survive across runs):
  #   * UPSTREAM_SHA — short SHA from the `commit_id = "..."` line of the
  #     vyos-1x entry in `package.toml`. This is the upstream pin in the
  #     vyos-build tree, BEFORE build.py clones+checks-out vyos-1x/. Reading
  #     it from the toml lets us compute the key without first having to
  #     clone vyos-1x (which `./build.py` does on a cache miss).
  #   * PATCH_HASH — sha256 of `cat data/vyos-1x-*.patch`. Patch files are
  #     applied in filesystem-sort order by build.py (`sorted(patch_dir.glob('*'))`),
  #     so renaming or reordering them legitimately invalidates the cache —
  #     this is intentional. New patches with new numbers also bump the hash.
  #
  # Cache lives under $RUNNER_TOOL_CACHE (set by GitHub Actions on the
  # self-hosted runner — survives across runs). On non-Actions runs the
  # /tmp fallback cache is volatile by design (local dev iteration).
  #
  # Eviction: 14-day mtime GC at the top of every cache check; harmless to
  # delete entries that are still hot — they'll just be rebuilt on next miss.
  SKIP_VYOS1X_BUILD=0
  if [ "$package" == "vyos-1x" ]; then
    UPSTREAM_SHA=$(awk -F'"' '/^commit_id/ {print $2}' package.toml 2>/dev/null | head -1 | cut -c1-12)
    PATCH_HASH=$(cat "$GITHUB_WORKSPACE"/data/vyos-1x-*.patch 2>/dev/null | sha256sum | cut -c1-16)
    CACHE_DIR="${RUNNER_TOOL_CACHE:-/tmp}/vyos-1x-cache"
    mkdir -p "$CACHE_DIR"
    find "$CACHE_DIR" -maxdepth 1 -name 'vyos-1x_*' -mtime +14 -delete 2>/dev/null || true
    if [ -n "$UPSTREAM_SHA" ] && [ -n "$PATCH_HASH" ]; then
      CACHE_KEY="vyos-1x_${UPSTREAM_SHA}_${PATCH_HASH}"
      CACHED=$(find "$CACHE_DIR" -maxdepth 1 -name "${CACHE_KEY}__*_arm64.deb" 2>/dev/null | sort)
      if [ -n "$CACHED" ]; then
        echo "### vyos-1x cache HIT (key=$CACHE_KEY)"
        for c in $CACHED; do
          orig=$(basename "$c" | sed -E "s/^${CACHE_KEY}__//")
          cp -v "$c" "../$orig"
        done
        SKIP_VYOS1X_BUILD=1
      else
        echo "### vyos-1x cache MISS (key=$CACHE_KEY) — building"
      fi
    else
      echo "### vyos-1x cache: could not derive key (UPSTREAM_SHA='$UPSTREAM_SHA' PATCH_HASH='$PATCH_HASH') — building"
      CACHE_KEY=""
    fi
  fi

  # Restrict linux-kernel/build.py to only the kernel sub-package.
  #
  # The upstream `package.toml` defines 13 sub-packages (linux-kernel,
  # linux-firmware, accel-ppp-ng, nat-rtsp, qat, igb, ixgbe, ixgbevf, jool,
  # mlnx, realtek-r8126, realtek-r8152, ipt-netflow). With no `--packages`
  # filter, build.py iterates ALL of them, which on the LS1046A target wastes
  # ~5 minutes per build:
  #   * linux-firmware     — full git clone of huge Debian linux-firmware
  #                          tree (~2m); we don't ship the resulting .deb
  #                          (none of our config.boot.* depends on it and
  #                          ci-pick-packages.sh has no opinion either way).
  #   * accel-ppp-ng       — invokes vyos-build/.../build-accel-ppp-ng.sh,
  #                          which on ARM64 ALWAYS fails because it requires
  #                          building VPP from source first. We rebuild
  #                          accel-ppp-ng correctly below via
  #                          bin/ci-build-accel-ppp.sh (no VPP plugin).
  #   * igb/ixgbe/ixgbevf  — Intel x86-cloud NIC OOT modules; the LS1046A
  #                          has no Intel NICs (DPAA1 FMan).
  #   * qat/mlnx/realtek/jool/nat-rtsp/ipt-netflow — none of these are in
  #                          our package-lists or config.boot.* defaults,
  #                          and none of our hooks reference them.
  # `build.py` swallows individual package failures (prints "Failed to build
  # package X" and continues), so dropping these is purely a time saving and
  # does not change the ISO contents.
  #
  # vyos-1x has no sub-packages — invoke unfiltered.
  if [ "$package" == "linux-kernel" ]; then
    ./build.py --packages linux-kernel
  elif [ "$SKIP_VYOS1X_BUILD" -eq 1 ]; then
    echo "### Skipping ./build.py for vyos-1x (cache hit)"
  else
    ./build.py
  fi

  ### Populate vyos-1x cache after a successful build (cache miss path)
  if [ "$package" == "vyos-1x" ] && [ "$SKIP_VYOS1X_BUILD" -eq 0 ] && [ -n "${CACHE_KEY:-}" ]; then
    cached_count=0
    for built in ../vyos-1x_*_arm64.deb; do
      [ -f "$built" ] || continue
      cp "$built" "$CACHE_DIR/${CACHE_KEY}__$(basename "$built")"
      cached_count=$((cached_count + 1))
    done
    if [ "$cached_count" -gt 0 ]; then
      echo "### Cached $cached_count vyos-1x .deb(s) under key $CACHE_KEY"
      ls -lh "$CACHE_DIR/${CACHE_KEY}__"*.deb 2>/dev/null || true
    else
      echo "### WARNING: vyos-1x build produced no ../vyos-1x_*_arm64.deb to cache"
    fi
  fi

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

      # Copy SDK DTS if present (sourced from data/dtb/mono-gateway-dk-sdk.dts)
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

      # FLAVOR-aware DTB selection:
      #   FLAVOR=ask           → SDK DTB is PRIMARY (SDK fsl_mac needs fixed-link
      #                          on 10G MACs; mainline phylink path is bypassed)
      #   FLAVOR=default|vpp   → MAINLINE DTB is PRIMARY (kernel uses mainline
      #                          DPAA1 + phylink/SFP state machine; shipping the
      #                          SDK DTB here forces phylink into fixed/10gbase-r
      #                          fallback and rejects all SFP+ modules with
      #                          "unsupported SFP module: no common interface modes")
      #
      # Both DTBs are still BUILT (when sources are present) and SHIPPED so
      # diagnostics can compare. Only the `mono-gw.dtb` filename — what U-Boot
      # actually loads — switches based on FLAVOR.
      SDK_DTS="$DTS_DIR/mono-gateway-dk-sdk.dts"
      SDK_DTB_OK=false
      if [ -f "$SDK_DTS" ]; then
        echo "### Building SDK+ASK DTB from kernel source"
        make -C "$KSRC" freescale/mono-gateway-dk-sdk.dtb 2>&1 | tail -10 || true
        SDK_DTB="$DTS_DIR/mono-gateway-dk-sdk.dtb"
        if [ -f "$SDK_DTB" ]; then
          SDK_DTB_OK=true
          # Always ship SDK DTB under its named alias for diagnostics.
          cp "$SDK_DTB" "$INCLUDES_BIN/mono-gw-sdk.dtb"
          cp "$SDK_DTB" "$INCLUDES_CHR/boot/mono-gw-sdk.dtb"
          echo "### SDK DTB built: $(stat -c '%s bytes' "$SDK_DTB") → mono-gw-sdk.dtb"
        else
          echo "WARNING: mono-gateway-dk-sdk.dtb build failed"
        fi
      fi

      # Build mainline DTB. FATAL when neither this nor a usable primary
      # alternative exists, because the historical fallback (shipping the
      # potentially-stale data/dtb/mono-gw.dtb committed in the repo) is
      # exactly how the missing DWC3 USB stability quirks slipped past CI.
      echo "### Building mainline DTB from kernel source"
      MAKE_RC=0
      make -C "$KSRC" freescale/mono-gateway-dk.dtb 2>&1 | tail -10 || MAKE_RC=$?
      MONO_DTB="$DTS_DIR/mono-gateway-dk.dtb"
      MAINLINE_DTB_OK=false
      if [ -f "$MONO_DTB" ]; then
        MAINLINE_DTB_OK=true
        # Always ship mainline DTB under its named alias for diagnostics.
        cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw-mainline.dtb"
        echo "### Mainline DTB built: $(stat -c '%s bytes' "$MONO_DTB") → mono-gw-mainline.dtb"
      else
        echo "WARNING: mainline DTB build failed (rc=$MAKE_RC)"
      fi

      # Select PRIMARY mono-gw.dtb based on FLAVOR.
      case "$FLAVOR" in
        ask)
          if [ "$SDK_DTB_OK" = true ]; then
            cp "$SDK_DTB" "$INCLUDES_BIN/mono-gw.dtb"
            cp "$SDK_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
            echo "### FLAVOR=ask → SDK DTB selected as PRIMARY mono-gw.dtb"
          elif [ "$MAINLINE_DTB_OK" = true ]; then
            cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw.dtb"
            cp "$MONO_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
            echo "WARNING: FLAVOR=ask but SDK DTB unavailable — falling back to mainline DTB as PRIMARY"
          else
            echo "FATAL: FLAVOR=ask and neither SDK nor mainline DTB built; refusing to ship stale data/dtb/mono-gw.dtb."
            exit 1
          fi
          ;;
        default|vpp)
          if [ "$MAINLINE_DTB_OK" = true ]; then
            cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw.dtb"
            cp "$MONO_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
            echo "### FLAVOR=$FLAVOR → mainline DTB selected as PRIMARY mono-gw.dtb"
          elif [ "$SDK_DTB_OK" = true ]; then
            echo "FATAL: FLAVOR=$FLAVOR and mainline DTB build failed (rc=$MAKE_RC)."
            echo "FATAL: refusing to ship SDK DTB as primary on a non-ASK flavor — that"
            echo "FATAL: forces mainline phylink into fixed-link fallback and rejects all SFP+ modules."
            exit 1
          else
            echo "FATAL: FLAVOR=$FLAVOR and no DTB built; refusing to ship stale data/dtb/mono-gw.dtb."
            exit 1
          fi
          ;;
        *)
          echo "FATAL: unknown FLAVOR='$FLAVOR' in DTB selection"
          exit 1
          ;;
      esac
    fi

    ### ASK out-of-tree kernel modules (cdx, fci, auto_bridge, iptables-extensions)
    #
    # OOT module SOURCES live in this repo under
    # `kernel/flavors/ask/oot-modules/`. When `ASK_KERNEL_TAG` is set, the
    # corresponding signed .ko binaries are pulled in via
    # `ci-consume-ask-kernel.sh` from the formerly-separate kernel build
    # repo's GitHub Releases (mihakralj/kernel-ls1046a-build, now frozen
    # and absorbed into this tree) — they ride inside the kernel-6.6.137-askN
    # release tarball alongside the kernel image and linux-headers .deb.
    #
    # The legacy local-build path that compiled cdx/fci/auto_bridge here
    # from a sibling clone of the archived ask-ls1046a-6.6 repo has been
    # removed: the sources now live in-tree under `kernel/flavors/ask/`
    # and the archived repo is no longer cloned by auto-build.yml.

    ### Build ASK userspace binaries from source (cmm, dpa_app, libcli, libfci)
    # FLAVOR-gated: ASK userspace (cmm, dpa_app, fmlib, fmc, cdx, fci,
    # auto_bridge) is only meaningful when running the NXP SDK + ASK
    # fast-path kernel. On default/vpp flavors the underlying kernel has
    # no SDK fsl_dpa driver and no /dev/fm* chardev — building these
    # userspace components produces .debs that cannot install (missing
    # kernel symbols) and pollutes the ISO.
    if [ "${FLAVOR:-default}" = "ask" ] && [ -n "$KSRC" ] && [ -x "$GITHUB_WORKSPACE/bin/ci-build-ask-userspace.sh" ]; then
      KSRC_ABS_ASK="$(cd "$KSRC" && pwd)"
      echo "### Building ASK userspace from source (FLAVOR=ask)"
      "$GITHUB_WORKSPACE/bin/ci-build-ask-userspace.sh" "$KSRC_ABS_ASK" "$INCLUDES_CHR" || \
        echo "WARNING: ASK userspace build failed (non-fatal) — using pre-built binaries"
    elif [ "${FLAVOR:-default}" != "ask" ]; then
      echo "### Skipping ASK userspace build (FLAVOR=${FLAVOR:-default}, not 'ask')"
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

### ASK userspace rebuild in ASK-consume mode ##########################
#
# In ASK-consume mode the linux-kernel package is skipped, so the entire
# `if [ "$package" == "linux-kernel" ]` block above (which contains the
# DTB build, ASK kernel-module build, AND the ASK userspace rebuild) is
# never entered. That left dpa_app/cmm/fmlib/fmc shipping as the stale
# prebuilt blobs from data/ask-userspace/ -- they were compiled against
# an older kernel ABI and SIGSEGV at runtime under cdx_module_init's
# call_usermodehelper, producing the cascade:
#
#   cdx_module_init::start_dpa_app failed rc 11
#   get_phys_port_poolinfo_bysize::failed
#   cdx_create_fragment_bufpool::failed to locate eth bman pool
#
# Fix: extract the pinned linux-headers-*.deb that ci-consume-ask-kernel.sh
# already staged (which ships the FMD UAPI under usr/src/linux-headers-*/include/uapi/linux/fmd/)
# and re-run ci-build-ask-userspace.sh against it. dpa_app, libfm.a,
# libfmc.a, libcli, libfci, and cmm get rebuilt against the same kernel
# headers we are about to ship, killing the ABI drift.
if [ -n "${ASK_KERNEL_TAG:-}" ]; then
  echo ""
  echo "### ASK-consume mode: rebuilding ASK userspace against pinned kernel-headers .deb"
  VB_PKG_CHROOT="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/packages.chroot"
  INCLUDES_CHR="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.chroot"
  HDR_DEB=$(ls "$VB_PKG_CHROOT"/linux-headers-*_arm64.deb 2>/dev/null | head -1)
  if [ -z "$HDR_DEB" ]; then
    echo "WARNING: no linux-headers-*_arm64.deb in $VB_PKG_CHROOT/ — ASK userspace will NOT be rebuilt"
    echo "         (ci-consume-ask-kernel.sh must have run first)"
  elif [ ! -x "$GITHUB_WORKSPACE/bin/ci-build-ask-userspace.sh" ]; then
    echo "WARNING: bin/ci-build-ask-userspace.sh missing or not executable — skipping userspace rebuild"
  else
    KHDR_EXTRACT="/tmp/ask-khdr"
    rm -rf "$KHDR_EXTRACT"
    mkdir -p "$KHDR_EXTRACT"
    dpkg-deb -x "$HDR_DEB" "$KHDR_EXTRACT"
    KSRC_ASK=$(find "$KHDR_EXTRACT/usr/src" -maxdepth 1 -type d -name 'linux-headers-*' | head -1)
    if [ -z "$KSRC_ASK" ] || [ ! -d "$KSRC_ASK/include/uapi/linux/fmd" ]; then
      echo "WARNING: extracted headers missing FMD UAPI tree at $KSRC_ASK/include/uapi/linux/fmd"
      echo "         — ASK userspace will NOT be rebuilt"
    else
      echo "### KSRC_ASK=$KSRC_ASK (FMD UAPI present)"
      echo "### INCLUDES_CHR=$INCLUDES_CHR"
      if ! "$GITHUB_WORKSPACE/bin/ci-build-ask-userspace.sh" "$KSRC_ASK" "$INCLUDES_CHR"; then
        if [ "${ASK_USERSPACE_STRICT:-1}" = "1" ]; then
          echo "ERROR: ASK userspace rebuild failed — aborting (set ASK_USERSPACE_STRICT=0 to override; ships stale prebuilt dpa_app → SIGSEGV)" >&2
          exit 1
        fi
        echo "WARNING: ASK userspace rebuild failed — falling back to prebuilt blobs (dpa_app likely SIGSEGV)"
      fi
    fi
    rm -rf "$KHDR_EXTRACT"
  fi
fi
