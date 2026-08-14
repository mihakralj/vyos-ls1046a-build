#!/bin/bash
# ci-build-packages.sh — Build vyos-1x + vpp (+ optionally linux-kernel) packages
# Called by: .github/workflows/auto-build.yml "Build Image Packages" step
# Expects: GITHUB_WORKSPACE set
#
# When ASK_KERNEL_TAG is set, the linux-kernel target is SKIPPED because the
# prebuilt ASK kernel .debs have already been staged into packages/ by
# bin/ci-consume-ask-kernel.sh. Building the kernel locally in that mode
# would just consume 20+ minutes and replace the ASK kernel with a vanilla
# one that lacks fast-path hooks.
#
# vpp is built from source (not the prebuilt VyOS repo .deb) so
# data/vyos-build-008-vpp-libxdp.patch's [dependencies] addition
# (libxdp-dev + libbpf-dev) actually gets installed before VPP's own
# build — required for the af_xdp plugin's xsk_socket__create() path.
# Without "vpp" in this list, scripts/package-build/vpp/package.toml
# (what that patch modifies) is never invoked at all, and patch 008
# becomes dead code — this was the actual cause of the M4 ZC libxdp
# regression after commit 957b8f3 reverted the earlier attempt to wire
# this in (that revert was chasing a different, since-fixed CI caching
# bug — see 1cee5b2 — and inadvertently took this down with it).
set -ex -o pipefail

# Source common.sh before changing CWD so it can resolve REPO_ROOT.
# shellcheck source=common.sh
. "${GITHUB_WORKSPACE:-.}/bin/common.sh"

cd "${GITHUB_WORKSPACE:-.}/vyos-build/scripts/package-build"

if [ -n "${ASK_KERNEL_TAG:-}" ]; then
    echo "### ASK kernel in effect ($ASK_KERNEL_TAG) — skipping linux-kernel local build"
    packages="vyos-1x vpp"
else
    packages="linux-kernel vyos-1x vpp"
fi
ignore_packages=(amazon-cloudwatch-agent amazon-ssm-agent xen-guest-agent)

for package in $packages; do
  [ ! -d "$package" ] && continue
  [[ " ${ignore_packages[@]} " =~ " ${package} " ]] && continue
  cd "$package"

  [ "$package" == "keepalived" ] && apt-get install -y libsnmp-dev

  ### RAM-back the kernel build tree on tmpfs (30GB runner disk headroom)
  #
  # A cold kernel build peaks at ~14GB transient (10GB tree + ~2GB debs +
  # ccache growth) on top of ~17GB persistent baseline — beyond the 30GB
  # runner disk at bindeb-pkg time. ENOSPC on runs 31674687093 and
  # 31718015742 (2026-08-13). This host has 94GB RAM.
  #
  # The kernel build compiles the persistent canonical tree at
  # ~/kernel-git-cache/linux (the package dir's `linux` symlink; build.py
  # skips the tarball when it exists), so the ~10GB build tree grows
  # inside the cache — mount the cache on tmpfs (28G) and the whole
  # compile runs in RAM. The mount lives OUTSIDE $GITHUB_WORKSPACE:
  # actions/checkout aborts with EBUSY when workspace cleanup hits a
  # mounted directory (run 31724821391, 2026-08-13), and build.py
  # resolves relative paths from the package dir's physical location
  # (../../../data/defaults.toml — a symlink-swapped cwd broke that on
  # run 31725325868). The on-disk cache copy is moved into the new mount
  # and the disk copy deleted, restoring headroom; the mount persists
  # across runs (no-op when already mounted). Runs as root (job shell is
  # sudo -E bash).
  if [ "$package" == "linux-kernel" ] && [ -z "${ASK_KERNEL_TAG:-}" ]; then
    CACHE="${HOME:-/home/vyos}/kernel-git-cache/linux"
    if [ -d "$CACHE" ] && ! mountpoint -q "$CACHE"; then
      mv "$CACHE" "${CACHE}.disk"
      mkdir -p "$CACHE"
      mount -t tmpfs -o size=28G,mode=755 tmpfs "$CACHE"
      cp -a "${CACHE}.disk/." "$CACHE/"
      rm -rf "${CACHE}.disk"
      echo "### Mounted tmpfs (28G) on $CACHE (kernel git cache + build tree)"
    elif [ -d "$CACHE" ]; then
      echo "### tmpfs already mounted on $CACHE"
    fi
    df -h "$CACHE" 2>/dev/null || true

    # Pin the cache checkout to the kernel version from defaults.toml. The
    # clone step's default (KERNEL_VERSION:-6.18.38 in auto-build.yml) lags
    # upstream bumps; compiling a stale checkout yields linux-image of the
    # wrong version under a newer linux-kernel-cache key (poisons the cache).
    KVER=$(awk -F'"' '/^kernel_version/ {print $2}' "$GITHUB_WORKSPACE/vyos-build/data/defaults.toml" 2>/dev/null | head -1)
    if [ -n "$KVER" ] && [ -d "$CACHE/.git" ]; then
      echo "### Pinning kernel git cache checkout to v${KVER}"
      git -C "$CACHE" fetch --depth=1 origin "tag v${KVER}" 2>/dev/null || true
      git -C "$CACHE" checkout -f "v${KVER}" 2>/dev/null || \
        git -C "$CACHE" checkout -f "refs/tags/v${KVER}" 2>/dev/null || true
      ACTUAL=$(git -C "$CACHE" describe --tags 2>/dev/null || true)
      if [ "$ACTUAL" != "v${KVER}" ]; then
        # A stale-version cache clone silently downgrades build.py to the
        # tarball path (build.py prefers linux/ when it exists, regardless
        # of what version it holds) and the tarball path has no upstream
        # blobs for git apply --3way. Never reuse a wrong-version clone:
        # wipe it and re-clone at the pinned version. (ARM64-runner2
        # failure 2026-08-14: cache stuck at v6.18.38 while the build
        # tracked 6.18.44.)
        echo "::warning::kernel cache at ${ACTUAL:-<unknown>}, want v${KVER} — re-cloning"
        rm -rf "$CACHE"
        git clone --depth=1 --branch "v${KVER}" \
          https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git "$CACHE"
        git -C "$CACHE" fetch --unshallow 2>/dev/null || git -C "$CACHE" fetch --depth=100000 2>/dev/null || true
      fi
      git -C "$CACHE" describe --tags 2>/dev/null || true
    fi
  fi

  ### linux-kernel .deb + DTB + accel-ppp-ng cache
  #
  # Skip the ~20-minute kernel compile (and the ~3-minute accel-ppp-ng kmod
  # build that depends on it) when none of the inputs that affect the
  # produced .debs have changed.
  #
  # Cache key derivation:
  #   * KVER         — kernel_version from vyos-build/data/defaults.toml
  #                    (pins which upstream tarball gets downloaded)
  #   * KERNEL_HASH  — sha256(first 16 chars) of every input that mutates the
  #                    kernel source tree or .config:
  #                      - data/kernel-config/*.config  (defconfig fragments)
  #                      - data/kernel-patches/*        (kernel patches + DTS
  #                        patchers)
  #                      - bin/ci-setup-kernel.sh       (the integrator that
  #                        appends fragments and injects patches into
  #                        vyos-build/scripts/package-build/linux-kernel/)
  #                      - bin/ci-setup-vyos-build.sh   (also patches
  #                        build-kernel.sh)
  #                      - board/dtb/                   (DTS — the compiled
  #                        DTB ships under the same cache key)
  #                      - bin/ci-build-accel-ppp.sh    (its .deb rides in
  #                        the same cache entry)
  #
  # Cache scope: every kernel build. The old "excluded when FLAVOR=ask" gate
  # went away with the flavor split (2026-07-26) — it existed for the ASK 1.x
  # SDK userspace tree (cmm, dpa_app, fci, cdx, auto_bridge) that had to be
  # built against $KSRC, and that tree no longer exists. ASK2's ask.ko is an
  # OOT module whose .deb is cached alongside the kernel .debs (see the OOT
  # sources in KERNEL_HASH below). ASK_KERNEL_TAG mode (which consumes a
  # prebuilt kernel from a frozen release) stays excluded — there is no
  # kernel build to cache.
  #
  # Cache layout: one directory per key under $CACHE_DIR/<key>/ containing:
  #   linux-image-<kver>_arm64.deb
  #   linux-headers-<kver>_arm64.deb
  #   linux-libc-dev_<kver>_arm64.deb
  #   mono-gateway-dk.dtb              (mainline DTB — what default/vpp ship)
  #   accel-ppp-ng_*_arm64.deb         (optional — only if the build produced
  #                                     one; absence on hit is non-fatal)
  #
  # 14-day mtime GC matches the vyos-1x cache. .debs are 60-100MB each so the
  # cache footprint stays bounded (~3-4 keys retained ~= 1GB).
  # Validate cached .deb(s) with dpkg-deb --info before trusting a HIT. The
  # content-hash key already detects "source changed" staleness (a MISS is
  # the correct, cheap response to that). This function catches the other
  # kind of staleness: a cache entry whose key still matches but whose
  # payload is truncated/corrupt because a prior job died mid-write (OOM
  # kill, cancelled run, disk full) before completing. Callers evict just
  # the affected entry and fall through to a normal rebuild — never wipe
  # the whole cache root for this.
  cache_debs_intact() {
    local f
    for f in "$@"; do
      [ -f "$f" ] || continue
      if ! dpkg-deb --info "$f" >/dev/null 2>&1; then
        echo "### cache entry corrupt (dpkg-deb --info failed): $f"
        return 1
      fi
    done
    return 0
  }

  SKIP_KERNEL_BUILD=0
  KERNEL_CACHE_KEY=""
  KERNEL_CACHE_HIT_DIR=""
  if [ "$package" == "linux-kernel" ] && [ -z "${ASK_KERNEL_TAG:-}" ]; then
    KVER=$(awk -F'"' '/^kernel_version/ {print $2}' "$GITHUB_WORKSPACE/vyos-build/data/defaults.toml" 2>/dev/null | head -1)
    KERNEL_HASH=$( {
      find "$GITHUB_WORKSPACE/data/kernel-config" -maxdepth 1 -name '*.config' -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      find "$GITHUB_WORKSPACE/data/kernel-patches" -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      # kernel/common/ is the canonical home for kernel config fragments and
      # patches (board/, fixes/, vyos/). Any change here MUST bust the cache;
      # leaving it out caused CI run 26488626587 to silently hit a stale
      # cache entry that predated patches 0078+0079 and was missing the DTB.
      find "$GITHUB_WORKSPACE/kernel/common/kernel-config" -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      find "$GITHUB_WORKSPACE/kernel/common/vyos-base"     -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      find "$GITHUB_WORKSPACE/kernel/common/patches"       -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      find "$GITHUB_WORKSPACE/kernel/common/files"         -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      find "$GITHUB_WORKSPACE/kernel/common/scripts"       -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      # bin/kernel-fixups/*.py are invoked BY ci-setup-kernel.sh (which IS
      # hashed below) but their own CONTENT was not, so editing a fixup
      # script silently replayed a stale cached kernel .deb built with the
      # OLD fixup logic (discovered 2026-07-22: F-115 v2->v3 change was
      # silently ignored across two full CI cycles because only
      # ci-setup-kernel.sh's *invocation* of F_115.py was hashed, not
      # F_115.py itself). Hash the whole directory so ANY fixup edit busts
      # the cache.
      find "$GITHUB_WORKSPACE/bin/kernel-fixups"           -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      cat "$GITHUB_WORKSPACE/bin/ci-setup-kernel.sh" 2>/dev/null
      cat "$GITHUB_WORKSPACE/bin/ci-stage-kernel.sh" 2>/dev/null
      cat "$GITHUB_WORKSPACE/bin/ci-setup-vyos-build.sh" 2>/dev/null
      cat "$GITHUB_WORKSPACE/bin/ci-build-accel-ppp.sh" 2>/dev/null
      cat "$GITHUB_WORKSPACE/bin/ci-compile-mono-dtb.sh" 2>/dev/null
      find "$GITHUB_WORKSPACE/board/dtb" -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      # ASK2 OOT module sources (ask.ko) are built unconditionally in every
      # single-image build (see the OOT block below) and the resulting
      # ask-modules-*.deb is cached/replayed alongside the kernel .debs. Any
      # change to the OOT sources or to this script's OOT build invocation
      # MUST bust the kernel cache, otherwise a stale ask-modules-*.deb would
      # be replayed on a hit (cf. the kernel/common staleness trap, run
      # 26488626587). Hashing this script itself also covers the OOT build env.
      find "$GITHUB_WORKSPACE/kernel/ask/oot-modules" -type f -print0 2>/dev/null | sort -z | xargs -0 cat 2>/dev/null
      cat "$GITHUB_WORKSPACE/bin/ci-build-packages.sh" 2>/dev/null
    } | sha256sum | cut -c1-16)
    KERNEL_CACHE_ROOT="${RUNNER_TOOL_CACHE:-/tmp}/linux-kernel-cache"
    mkdir -p "$KERNEL_CACHE_ROOT"
    # GC stale entries (14 days)
    find "$KERNEL_CACHE_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null || true
    if [ -n "$KVER" ] && [ -n "$KERNEL_HASH" ]; then
      KERNEL_CACHE_KEY="linux-kernel_${KVER}_${KERNEL_HASH}"
      KERNEL_CACHE_HIT_DIR="$KERNEL_CACHE_ROOT/$KERNEL_CACHE_KEY"
      if [ -d "$KERNEL_CACHE_HIT_DIR" ] && \
         ls "$KERNEL_CACHE_HIT_DIR"/linux-image-*_arm64.deb >/dev/null 2>&1; then
        if cache_debs_intact "$KERNEL_CACHE_HIT_DIR"/*.deb; then
          echo "### linux-kernel cache HIT (key=$KERNEL_CACHE_KEY)"
          # Replay .debs into package-build/linux-kernel/ (current cwd)
          for d in "$KERNEL_CACHE_HIT_DIR"/*.deb; do
            [ -f "$d" ] || continue
            cp -v "$d" "./$(basename "$d")"
          done
          # Refresh mtime on a HIT so the GC doesn't evict warm entries
          touch "$KERNEL_CACHE_HIT_DIR"
          SKIP_KERNEL_BUILD=1
        else
          echo "### linux-kernel cache MISS (key=$KERNEL_CACHE_KEY, corrupt entry evicted) — building"
          rm -rf "$KERNEL_CACHE_HIT_DIR"
        fi
      else
        echo "### linux-kernel cache MISS (key=$KERNEL_CACHE_KEY) — building"
      fi
    else
      echo "### linux-kernel cache: could not derive key (KVER='$KVER' KERNEL_HASH='$KERNEL_HASH') — building"
    fi
  fi

  ### vyos-1x .deb cache (skip ~6m build when patches+upstream commit unchanged)
  #
  # Cache key derivation (must be deterministic and survive across runs):
  #   * UPSTREAM_SHA — short SHA from the `commit_id = "..."` line of the
  #     vyos-1x entry in `package.toml`. This is the upstream pin in the
  #     vyos-build tree, BEFORE build.py clones+checks-out vyos-1x/. Reading
  #     it from the toml lets us compute the key without first having to
  #     clone vyos-1x (which `./build.py` does on a cache miss).
  #   * PATCH_HASH — sha256 of `cat data/vyos-1x-*.patch` ++ the contents
  #     of `bin/ci-setup-vyos1x.sh` (which carries the pre_build_hook —
  #     including the debian/control sed loop that strips jool / nat-rtsp /
  #     unbuilt-nic guard blocks). Patch files are applied in filesystem-sort
  #     order by build.py (`sorted(patch_dir.glob('*'))`), so renaming or
  #     reordering them legitimately invalidates the cache — this is
  #     intentional. New patches with new numbers also bump the hash.
  #     INVARIANT: any logic that mutates the upstream vyos-1x source tree
  #     before `dpkg-buildpackage` runs MUST be reflected in this hash —
  #     otherwise a stale cached .deb will be replayed and overrule the fix.
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
    PATCH_HASH=$( { cat "$GITHUB_WORKSPACE"/data/vyos-1x-*.patch 2>/dev/null; cat "$GITHUB_WORKSPACE"/bin/ci-setup-vyos1x.sh 2>/dev/null; } | sha256sum | cut -c1-16)
    CACHE_DIR="${RUNNER_TOOL_CACHE:-/tmp}/vyos-1x-cache"
    mkdir -p "$CACHE_DIR"
    find "$CACHE_DIR" -maxdepth 1 -name 'vyos-1x_*' -mtime +14 -delete 2>/dev/null || true
    if [ -n "$UPSTREAM_SHA" ] && [ -n "$PATCH_HASH" ]; then
      CACHE_KEY="vyos-1x_${UPSTREAM_SHA}_${PATCH_HASH}"
      CACHED=$(find "$CACHE_DIR" -maxdepth 1 -name "${CACHE_KEY}__*_arm64.deb" 2>/dev/null | sort)
      if [ -n "$CACHED" ] && cache_debs_intact $CACHED; then
        echo "### vyos-1x cache HIT (key=$CACHE_KEY)"
        for c in $CACHED; do
          orig=$(basename "$c" | sed -E "s/^${CACHE_KEY}__//")
          cp -v "$c" "../$orig"
        done
        SKIP_VYOS1X_BUILD=1
      else
        if [ -n "$CACHED" ]; then
          echo "### vyos-1x cache MISS (key=$CACHE_KEY, corrupt entry evicted) — building"
          rm -f $CACHED
        else
          echo "### vyos-1x cache MISS (key=$CACHE_KEY) — building"
        fi
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
    # Defensive: nuke any stale linux source tree / symlink left over from a
    # previous build on this persistent self-hosted runner. build.py's
    # build_kernel() guards source download with `if not os.path.exists('linux'):`
    # so a stale `linux -> linux-6.18.29/` symlink (from an interrupted earlier
    # build experiment) silently skips the curl of `linux-{kernel_version}.tar.xz`
    # and reuses whatever happens to be on disk. `make kernelversion` then reads
    # the wrong version from the stale tree → dpkg builds linux-image-6.18.29-vyos
    # while defaults.toml says 6.18.28 → upstream OOT kmods (jool/nat-rtsp/
    # ipt-netflow built against linux-image-6.18.28-vyos) become uninstallable in
    # the chroot. Symptom seen on run 25699468756 (vpp): chroot_install-packages
    # failed with "jool : Depends: linux-image-6.18.28-vyos but it is not installable".
    # This rm is a no-op on hosted runners (fresh workspace) and on cold self-hosted
    # workspaces; it costs nothing on warm ones except the re-download of the
    # canonical tarball from kernel.org (~150MB, ~5s on the Cobalt 100 link).
    if [ "$SKIP_KERNEL_BUILD" -eq 1 ]; then
      echo "### Skipping ./build.py for linux-kernel (cache hit)"
    else
      echo "### Removing any stale linux source tree before kernel build (force re-download)"
      rm -rf linux linux-[0-9]* linux-*.tar.xz linux-*.tar.sign 2>/dev/null || true
      ./build.py --packages linux-kernel
    fi
  elif [ "$SKIP_VYOS1X_BUILD" -eq 1 ]; then
    echo "### Skipping ./build.py for vyos-1x (cache hit)"
  else
    # Defensive: reset any pre-existing $package/$package checkout to clean
    # tracked state right before build.py runs. For vyos-1x, ci-setup-
    # vyos1x.sh does an earlier reset in its own "Setup vyos-1x patches"
    # step, but on this persistent self-hosted/local runner that step and
    # this one can be minutes apart with kernel/DTB/vyos-build setup
    # running in between — 2026-08-04 debugging found the vyos-1x checkout
    # re-dirtied (python/vyos/system/grub.py showing modified) by the time
    # build.py's own `git checkout <commit_id>` ran here, even though it
    # was verified clean immediately after the earlier reset. For vpp
    # (no earlier reset exists), the SAME persistent-checkout class of
    # problem showed up as a worse failure: a previous build's incomplete
    # `git am` left .git/rebase-apply behind, which makes every subsequent
    # `git am` in build_cmd's patch loop fail outright with "previous
    # rebase directory still exists" until cleared — plus separate leftover
    # unstaged modifications from a prior successful build. `git am --abort`
    # needs a committer identity; use the same -c override the build_cmd
    # patch loop itself already uses (data/vyos-build-008-vpp-libxdp.patch's
    # `git -c user.email=... -c user.name=vyos am`) rather than touching
    # global git config. Neither exact intervening re-dirty cause was
    # pinned down; reset again here, closest to the point of actual use,
    # rather than leave it to chance.
    if [ -d "$package/.git" ]; then
      if [ -d "$package/.git/rebase-apply" ]; then
        git -c user.email=maintainers@vyos.net -c user.name=vyos -C "$package" am --abort || true
      fi
      # 2026-08-04: a plain `reset --hard HEAD` only fixes DIRTY state, not
      # STALE state -- build.py's own `git checkout <commit_id>` never
      # fetches, so a persistent local checkout can silently fall behind
      # its remote branch forever. Hit this exact case for vpp: the local
      # checkout was one commit behind origin/stable/2510, and the missing
      # commit's absence made an otherwise-clean upstream VyOS patch
      # (0001-linux-cp-add-support-for-xfrm-netlink-notifcation.patch)
      # fail to apply -- confirmed by testing the identical patch against
      # a freshly-fetched worktree, where it applied with zero errors.
      # Extract this package's OWN commit_id from package.toml (a toml
      # file can define multiple [[packages]] blocks with different
      # commit_ids -- e.g. vpp/package.toml has both "vyos-vpp-patches"
      # @ rolling and "vpp" @ stable/2510 -- so match name==$package, not
      # just the first commit_id line in the file) and fetch+reset to it.
      # Falls back to a plain HEAD reset if the fetch fails for any
      # reason (offline, ref renamed, etc.) rather than hard-failing.
      PKG_COMMIT_ID=$(awk -v pkg="$package" '
        /^\[\[packages\]\]/ { name=""; cid="" }
        /^name[ \t]*=/ { gsub(/"/,""); n=$0; sub(/^name[ \t]*=[ \t]*/,"",n); name=n }
        /^commit_id[ \t]*=/ { gsub(/"/,""); c=$0; sub(/^commit_id[ \t]*=[ \t]*/,"",c); cid=c }
        name==pkg && cid!="" { print cid; exit }
      ' package.toml 2>/dev/null)
      if [ -n "$PKG_COMMIT_ID" ] && git -C "$package" fetch origin "$PKG_COMMIT_ID" 2>/dev/null; then
        git -C "$package" reset --hard FETCH_HEAD
        echo "### $package/: fetched + reset to origin's current $PKG_COMMIT_ID immediately before build.py"
      else
        git -C "$package" reset --hard HEAD
        echo "### $package/: fetch unavailable/failed, reset to local HEAD immediately before build.py"
      fi
      git -C "$package" clean -fdq
      # 2026-08-05: git clean -fdq (no -x) never touches gitignored paths.
      # vpp/.gitignore excludes /build-root/build-*/ and /build-root/
      # install-*/ (the actual CMake/ninja build+install trees) precisely
      # so `git clean` won't nuke /build-root/.ccache alongside them --
      # but that means those build-output directories silently persist
      # and accumulate state across every local build attempt on this
      # persistent runner. Traced a real failure to this: "make: ***
      # No rule to make target 'gcc'. Stop." inside build-root/, which
      # did not reproduce when the exact same `make ... pkg-deb` command
      # was rerun by hand once, immediately after -- consistent with a
      # corrupted CMakeCache.txt/build tree from an earlier interrupted
      # run, not a real Makefile bug. Remove just the build-*/install-*
      # trees (glob, not -x) so /build-root/.ccache is preserved.
      rm -rf "$package"/build-root/build-*/ "$package"/build-root/install-*/ 2>/dev/null || true
    fi
    ./build.py
  fi

  ### Populate vyos-1x cache after a successful build (cache miss path)
  #
  # cwd here is `vyos-build/scripts/package-build/vyos-1x/` (we cd'd into
  # $package above). build.py's `repo_dir = Path('vyos-1x')` makes the
  # actual git clone live at `./vyos-1x/`. dpkg-buildpackage runs with
  # cwd=repo_dir and emits .debs to repo_dir.parent — which is `./` here,
  # NOT `../`. (`../` would be `package-build/` itself, and that's where
  # build.py's copy_packages() *eventually* lifts them — but copy_packages
  # is called by build.py's __main__ AFTER this script's per-package step
  # has already finished, and only if it walks back to build.py's main
  # loop. By that time we're already in the post-build guard.) Glob both
  # locations defensively so future refactors of build.py / copy_packages
  # don't silently re-break the guard.
  if [ "$package" == "vyos-1x" ] && [ "$SKIP_VYOS1X_BUILD" -eq 0 ] && [ -n "${CACHE_KEY:-}" ]; then
    cached_count=0
    for built in vyos-1x_*_arm64.deb ../vyos-1x_*_arm64.deb; do
      [ -f "$built" ] || continue
      # Stage then atomically rename (same filesystem) so a job killed
      # mid-copy never leaves a truncated .deb visible under the final
      # cache-key filename — matches the linux-kernel cache's stage->mv
      # pattern below. Without this, a later run's cache-HIT glob would
      # see the partial file (it only checked existence, not integrity)
      # before the dpkg-deb --info validation was added.
      dest="$CACHE_DIR/${CACHE_KEY}__$(basename "$built")"
      stage="$CACHE_DIR/.staging.$$.${CACHE_KEY}__$(basename "$built")"
      cp "$built" "$stage"
      mv "$stage" "$dest"
      cached_count=$((cached_count + 1))
    done
    if [ "$cached_count" -gt 0 ]; then
      echo "### Cached $cached_count vyos-1x .deb(s) under key $CACHE_KEY"
      ls -lh "$CACHE_DIR/${CACHE_KEY}__"*.deb 2>/dev/null || true
    else
      # HARD FAIL: build.py's per-package error swallower ("Failed to build
      # package vyos-1x: ... ignoring") used to let CI continue with NO
      # vyos-1x .deb produced. live-build chroot_install would then silently
      # substitute the unpatched upstream vyos-1x from the VyOS apt repo,
      # shipping an ISO without our LS1046A patches → `add system image`
      # writes boot dirs with no mono-gw.dtb → unbootable installs. (CI run
      # 26142046765, 2026-05-20.) Refuse to proceed instead.
      echo "::error::vyos-1x build produced no vyos-1x_*_arm64.deb in either ./ or ../ — refusing to build ISO with unpatched upstream vyos-1x" >&2
      echo "::error::This usually means dpkg-buildpackage failed inside build.py and the failure was swallowed. Search the log above for 'Failed to build package vyos-1x' and look at the preceding output." >&2
      echo "### Debug: contents of cwd ($(pwd)):" >&2
      ls -la . 2>&1 | head -40 >&2 || true
      echo "### Debug: contents of parent ($(cd .. && pwd)):" >&2
      ls -la .. 2>&1 | head -40 >&2 || true
      exit 1
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
      echo "  - Patch failed to apply"
      echo "  - Kconfig symbol conflict"
      echo "  - Missing kernel dependency"
      echo ""
      exit 1
    fi
    echo "### Kernel build OK: found $KERNEL_DEB_COUNT .deb file(s)"
    ls -lh linux-image-*.deb 2>/dev/null || true
  fi

  ### Build Mono Gateway DTB from kernel source (before cleanup)
  if [ "$package" == "linux-kernel" ] && [ "$SKIP_KERNEL_BUILD" -eq 1 ]; then
    ### Cache-hit DTB replay — kernel source tree does not exist, so we
    ### restore the DTB straight from the cache directory into the
    ### includes.binary / includes.chroot trees. accel-ppp-ng .debs were
    ### already replayed at the top of this iteration alongside the kernel
    ### .debs, so this block has no other side effects.
    echo "### linux-kernel cache HIT: replaying cached DTB (skipping kernel-source-dependent steps)"
    INCLUDES_BIN="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.binary"
    INCLUDES_CHR="$GITHUB_WORKSPACE/vyos-build/data/live-build-config/includes.chroot"
    CACHED_DTB="$KERNEL_CACHE_HIT_DIR/mono-gateway-dk.dtb"
    if [ -f "$CACHED_DTB" ]; then
      mkdir -p "$INCLUDES_BIN" "$INCLUDES_CHR/boot"
      cp "$CACHED_DTB" "$INCLUDES_BIN/mono-gw.dtb"
      cp "$CACHED_DTB" "$INCLUDES_BIN/mono-gw-mainline.dtb"
      cp "$CACHED_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
      echo "### DTB replayed from cache: $(stat -c '%s bytes' "$CACHED_DTB")"
    else
      echo "FATAL: linux-kernel cache HIT but cached DTB missing at $CACHED_DTB"
      echo "FATAL: refusing to ship without a board DTB"
      exit 1
    fi
    echo "### accel-ppp-ng .debs (replayed from cache, if any):"
    ls -lh accel-ppp*.deb 2>/dev/null || echo "  (none in cache)"
    echo "### ASK2 ask-modules .deb (replayed from cache):"
    ls -lh ask-modules-*.deb 2>/dev/null || { echo "FATAL: cache HIT but no ask-modules-*.deb in cached bundle"; echo "FATAL: rm -rf '$KERNEL_CACHE_HIT_DIR' and rebuild to repopulate"; exit 1; }
  elif [ "$package" == "linux-kernel" ]; then
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
      cp "$GITHUB_WORKSPACE/board/dtb/mono-gateway-dk.dts" "$DTS_DIR/mono-gateway-dk.dts"

      # DTB selection (flavor-free since 2026-07-26).
      #
      # The MAINLINE DTB is the one and only primary. The kernel uses mainline
      # DPAA1 + the phylink/SFP state machine; the old SDK DTB forced phylink
      # into fixed/10gbase-r fallback and rejected every SFP+ module with
      # "unsupported SFP module: no common interface modes".
      #
      # The SDK DTB path that used to run under FLAVOR=ask is gone: its inputs
      # (board/dtb/mono-gateway-dk-sdk.dts and board/dtb/sdk-dtsi/) were
      # deleted with the ASK 1.x SDK overlay, so the `if [ -f ... ]` guarding
      # it had been permanently false. ci-compile-mono-dtb.sh already builds
      # only board/dtb/mono-gateway-dk.dts.
      echo "### Building mainline DTB from kernel source"
      MAKE_RC=0
      make -C "$KSRC" freescale/mono-gateway-dk.dtb 2>&1 | tail -10 || MAKE_RC=$?
      MONO_DTB="$DTS_DIR/mono-gateway-dk.dtb"
      if [ -f "$MONO_DTB" ]; then
        cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw.dtb"
        cp "$MONO_DTB" "$INCLUDES_CHR/boot/mono-gw.dtb"
        # Named alias kept for diagnostic parity with the fallback path.
        cp "$MONO_DTB" "$INCLUDES_BIN/mono-gw-mainline.dtb"
        echo "### Mainline DTB built: $(stat -c '%s bytes' "$MONO_DTB") → mono-gw.dtb"
      elif [ -f "$GITHUB_WORKSPACE/board/dtb/mono-gw.dtb" ] \
           && [ "$(stat -c %Y "$GITHUB_WORKSPACE/board/dtb/mono-gw.dtb")" -gt "$(stat -c %Y "$GITHUB_WORKSPACE/board/dtb/mono-gateway-dk.dts")" ]; then
        # ci-compile-mono-dtb.sh ran earlier in this CI run (workflow step
        # "Compile Mono DTB from DTS"; bin/local-build.sh for local builds) and
        # produced board/dtb/mono-gw.dtb FROM the current DTS in this workspace
        # by sparse-cloning linux-stable at the matching kernel tag. We verify
        # by mtime ordering, so a stale committed DTB cannot sneak past. The
        # in-kernel-tree build above blew up because vyos-build's bindeb-pkg
        # wipes .config after packaging, leaving the tree unconfigured for the
        # dtbs target. Re-using the pre-compiled DTB is safe — same DTS, same
        # kernel tag.
        cp "$GITHUB_WORKSPACE/board/dtb/mono-gw.dtb" "$INCLUDES_BIN/mono-gw.dtb"
        cp "$GITHUB_WORKSPACE/board/dtb/mono-gw.dtb" "$INCLUDES_CHR/boot/mono-gw.dtb"
        cp "$GITHUB_WORKSPACE/board/dtb/mono-gw.dtb" "$INCLUDES_BIN/mono-gw-mainline.dtb"
        echo "### Fell back to pre-compiled board/dtb/mono-gw.dtb (mtime > DTS mtime; in-tree make failed rc=$MAKE_RC because bindeb-pkg cleaned .config)"
      else
        # The historical fallback — shipping the possibly-stale committed
        # board/dtb/mono-gw.dtb unconditionally — is exactly how the missing
        # DWC3 USB stability quirks slipped past CI. Refuse instead.
        echo "FATAL: mainline DTB build failed (rc=$MAKE_RC) and no fresh pre-compiled DTB available;"
        echo "FATAL: refusing to ship a stale board/dtb/mono-gw.dtb."
        exit 1
      fi
    fi

    ### ASK2 OOT kernel modules (ask.ko, future ask_bridge.ko)
    #
    # Build and sign the ASK2 OOT module .ko against the kernel source
    # tree we just compiled. Must run BEFORE the post-build cleanup that
    # deletes $KSRC at the end of this iteration — the OOT build needs
    # Module.symvers, scripts/sign-file, and certs/signing_key.{pem,x509}
    # all of which live inside $KSRC.
    #
    # The signed .ko is packaged as a .deb under $PKG_DIR (the current
    # `linux-kernel/` package-build dir), where bin/ci-pick-packages.sh's
    # `find scripts/package-build -name '*.deb'` sweep will pick it up.
    #
    # Single-image: ask.ko is built UNCONDITIONALLY and ships dormant in
    # every ISO (the FLAVOR=ask gate was retired with the 2026-06-14 flavor
    # collapse). ask.ko links only against the common board fman_cc_*/fman_hm_*
    # substrate — the dead ask-flavor in-tree patches stay unapplied — so it
    # compiles in the default build. The operator engages the datapath at
    # runtime per plans/DUAL-DATAPLANE.md.
    #
    # Userspace components (askd, ask-load, libask_fci) are not yet
    # implemented — see specs/ask2-rewrite-spec.md §§4–9.
    ASK_OOT_BUILDER="$GITHUB_WORKSPACE/kernel/ask/oot-modules/ask/ci-build.sh"
    if [ -n "$KSRC" ] && [ -x "$ASK_OOT_BUILDER" ]; then
      KSRC_ABS_ASK="$(cd "$KSRC" && pwd)"
      echo "### single-image: building ASK2 OOT kernel modules (ask.ko, dormant)"
      # F-069a-FIX: change extern→definition in dpaa_eth.c so the linker
      # can resolve fman_pcd_ic_vaddr.  Both ci-setup-kernel.sh fixups
      # and build-kernel.sh inject extern declarations; exactly one
      # compilation unit must provide the definition.
      if [ -f "$KSRC_ABS_ASK/drivers/net/ethernet/freescale/dpaa/dpaa_eth.c" ]; then
        sed -i 's/^extern void \*fman_pcd_ic_vaddr;$/void *fman_pcd_ic_vaddr;/' "$KSRC_ABS_ASK/drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
        sed -i 's/^extern void \*fman_pcd_ic_buf_base;$/void *fman_pcd_ic_buf_base;/' "$KSRC_ABS_ASK/drivers/net/ethernet/freescale/dpaa/dpaa_eth.c"
        echo "### F-069a-FIX: extern→definition in dpaa_eth.c"
      fi
      # F-094-FIX: ensure the OOT module's kernel header snapshot has the
      # struct definition and updated signature.  The ci-setup-kernel.sh
      # fixups target the main kernel tree, but the OOT module builds
      # against the snapshot at ask-kernel-snapshot/extracted/.
      SNAP_HDR="$KSRC_ABS_ASK/../ask-kernel-snapshot/extracted/usr/src/linux-headers-6.18.38-vyos/include/linux/fsl/fman_pcd.h"
      if [ -f "$SNAP_HDR" ]; then
        if ! grep -q 'struct fman_pcd_fe_flow_action {' "$SNAP_HDR"; then
          sed -i '/^int fman_pcd_fe_engage/i/* F-094: Structured flow action.\n */\n#define FMAN_FE_FLOW_KEY_MAX   56\nstruct fman_pcd_fe_flow_action {\n\tu8   key[FMAN_FE_FLOW_KEY_MAX];\n\tu8   key_size;\n\tunsigned long enq_off;\n\tu32  flags;\n};\n' "$SNAP_HDR"
          echo "### F-094-FIX: added struct to snapshot header"
        fi
        if grep -q 'const u8 \*key, u8 key_size, unsigned long enq_off' "$SNAP_HDR"; then
          sed -i 's/const u8 \*key, u8 key_size, unsigned long enq_off);/const struct fman_pcd_fe_flow_action *action);/' "$SNAP_HDR"
          echo "### F-094-FIX: updated flow_add signature in snapshot header"
        fi
      fi
      # Cross-build env is already exported by the kernel build above
      # (ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu-). Pass through.
      ARCH=arm64 CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}" \
        "$ASK_OOT_BUILDER" "$KSRC_ABS_ASK" "$(pwd)"
      echo "### ASK OOT module .deb(s) in package dir:"
      ls -lh ask-modules-*.deb 2>/dev/null || { echo "FATAL: no ask-modules-*.deb produced"; exit 1; }
    else
      echo "FATAL: cannot build ASK2 OOT modules:"
      echo "FATAL:   KSRC='$KSRC'"
      echo "FATAL:   ASK_OOT_BUILDER='$ASK_OOT_BUILDER' (must be executable)"
      exit 1
    fi

    ### Build accel-ppp-ng ARM64 packages (daemon + kernel modules)
    # Must happen while kernel source tree ($KSRC) still exists
    if [ -n "$KSRC" ] && [ -x "$GITHUB_WORKSPACE/bin/ci-build-accel-ppp.sh" ]; then
      KSRC_ABS_ACCEL="$(cd "$KSRC" && pwd)"
      # bindeb-pkg runs `make clean` which removes .config. Regenerate it
      # so OOT module builds (accel-ppp-ng, ask.ko) can find the kernel config.
      if [ ! -f "$KSRC_ABS_ACCEL/.config" ]; then
        echo "### Regenerating .config after bindeb-pkg clean"
        make -C "$KSRC_ABS_ACCEL" olddefconfig ARCH=arm64 2>&1 | tail -3
      fi
      echo "### Building accel-ppp-ng ARM64 packages"
      "$GITHUB_WORKSPACE/bin/ci-build-accel-ppp.sh" "$KSRC_ABS_ACCEL" "$(pwd)" || \
        echo "WARNING: accel-ppp-ng build failed (non-fatal) — PPPoE/L2TP will be unavailable"
      echo "### accel-ppp-ng .debs in package dir:"
      ls -lh accel-ppp*.deb 2>/dev/null || echo "  (none produced)"
    fi

    ### Populate linux-kernel cache after a successful build (cache miss path)
    #
    # Captures the linux-image/linux-headers/linux-libc-dev .debs that
    # build.py just produced, the mainline DTB that the `make
    # freescale/mono-gateway-dk.dtb` step just compiled, the accel-ppp-ng
    # .deb that bin/ci-build-accel-ppp.sh just emitted, and the ASK2
    # ask-modules-*.deb that the OOT build above just signed+packaged, all
    # under one key. Next run with identical inputs (same kernel_version,
    # config fragments, patches, DTS, OOT sources, setup scripts) will replay
    # the entire bundle and skip the ~25-minute combined build. The ASK2 OOT
    # sources are folded into KERNEL_HASH above so an ask.ko source edit busts
    # the cache and never replays a stale module.
    #
    # Excludes the -dbg variant (hundreds of MB) — VyOS doesn't ship it
    # and ci-pick-packages.sh ignores it anyway.
    if [ "$SKIP_KERNEL_BUILD" -eq 0 ] && [ -n "${KERNEL_CACHE_KEY:-}" ]; then
      KERNEL_CACHE_NEW_DIR="$KERNEL_CACHE_ROOT/$KERNEL_CACHE_KEY"
      # Stage into a sibling tmp dir then rename, so a partial population
      # never poisons the cache for a concurrent reader.
      KERNEL_CACHE_STAGE="$KERNEL_CACHE_ROOT/.staging.$$.$KERNEL_CACHE_KEY"
      rm -rf "$KERNEL_CACHE_STAGE" "$KERNEL_CACHE_NEW_DIR"
      mkdir -p "$KERNEL_CACHE_STAGE"
      cached_count=0
      for built in linux-image-*_arm64.deb linux-headers-*_arm64.deb linux-libc-dev_*_arm64.deb accel-ppp-ng_*_arm64.deb ask-modules-*_arm64.deb; do
        [ -f "$built" ] || continue
        case "$built" in *-dbg*) continue ;; esac
        cp "$built" "$KERNEL_CACHE_STAGE/$(basename "$built")"
        cached_count=$((cached_count + 1))
      done
      # DTB for the cache. The cache-HIT replay path (above) REQUIRES a
      # mono-gateway-dk.dtb in the entry and FATALs without it. Prefer the
      # in-tree-built mainline DTB ($MONO_DTB); but when the in-tree `make
      # freescale/mono-gateway-dk.dtb` failed (rc=2 — bindeb-pkg wipes
      # .config after packaging) the DTB-selection block above already fell
      # back to the pre-compiled board/dtb/mono-gw.dtb and copied it to
      # $INCLUDES_BIN/mono-gw.dtb. Cache THAT shipped DTB so the entry is
      # always self-consistent. Without this fallback, a DTB-build failure
      # produced a poisoned cache entry (.debs but no DTB) that the next run
      # HIT and FATAL'd on — a self-perpetuating loop (CI runs 26693861376 /
      # 26693929733 / 26694069303, 2026-05-30).
      DTB_FOR_CACHE=""
      if [ -n "${MONO_DTB:-}" ] && [ -f "$MONO_DTB" ]; then
        DTB_FOR_CACHE="$MONO_DTB"
      elif [ -f "${INCLUDES_BIN:-}/mono-gw.dtb" ]; then
        DTB_FOR_CACHE="$INCLUDES_BIN/mono-gw.dtb"
      fi
      dtb_staged=0
      if [ -n "$DTB_FOR_CACHE" ]; then
        cp "$DTB_FOR_CACHE" "$KERNEL_CACHE_STAGE/mono-gateway-dk.dtb"
        dtb_staged=1
        cached_count=$((cached_count + 1))
      fi
      # INVARIANT: never create a cache entry without a DTB. The replay path
      # FATALs on a DTB-less entry, so a poisoned entry blocks every
      # subsequent run until manually rm -rf'd. Require BOTH .debs AND a DTB.
      if [ "$cached_count" -gt 0 ] && [ "$dtb_staged" -eq 1 ]; then
        mv "$KERNEL_CACHE_STAGE" "$KERNEL_CACHE_NEW_DIR"
        echo "### Cached $cached_count linux-kernel artifact(s) under key $KERNEL_CACHE_KEY"
        ls -lh "$KERNEL_CACHE_NEW_DIR"/ 2>/dev/null || true
      else
        rm -rf "$KERNEL_CACHE_STAGE"
        if [ "$dtb_staged" -eq 0 ]; then
          echo "### WARNING: refusing to cache linux-kernel under key $KERNEL_CACHE_KEY — no DTB available to stage (would poison the cache; replay path FATALs without a DTB)"
        else
          echo "### WARNING: linux-kernel build produced nothing cacheable under key $KERNEL_CACHE_KEY"
        fi
      fi
    fi
  fi

  if [ "$package" == "linux-kernel" ]; then
    # Free the ~10GB kernel build tree (linux-6.18.x/) BEFORE the ISO phase.
    # All tree-dependent work (DTB build, ASK2 OOT modules, accel-ppp-ng,
    # cache population) has already completed above; only the .debs in the
    # package dir and the staged DTB are needed downstream (Pick Packages /
    # ci-build-iso.sh). Without this, a cold kernel build (~14GB transient
    # peak: 10GB tree + ~2GB debs + ccache) exhausts the 30GB runner disk
    # mid-packaging — seen 2026-08-13 (runs 31674687093 / 31718015742,
    # linux-6.18.44 upstream bump busted the linux-kernel-cache key).
    # Pattern `linux-[0-9]*` matches the tarball-extracted tree only, never
    # linux-firmware/ nor the `linux` symlink to the persistent git cache.
    rm -rf linux-[0-9]* 2>/dev/null || true
    df -h /
  fi

  # clean
  df -Th
  apt-get autoremove -y
  rm -rf "$package" *.gz *.xz "$HOME/.cache/go-build" "$HOME/go/pkg/mod" "$HOME/.rustup"
  df -Th
  cd ..
done

### ASK2 (rewrite-in-progress): the legacy ASK-consume mode userspace
### rebuild block was removed on the ask20 branch along with
### ci-consume-ask-kernel.sh and ci-build-ask-userspace.sh. The ASK2
### userspace stack (askd, ask-load, libask_fci) will be built by new
### scripts under bin/ci-build-ask-*.sh once the components land per
### specs/ask2-rewrite-spec.md.
