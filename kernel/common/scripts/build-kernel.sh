#!/usr/bin/env bash
# build-kernel.sh — natively build an ASK-ready linux-6.6.y tree on arm64 and
# produce Debian packages (linux-image, linux-headers, linux-libc-dev).
#
# Prerequisite: the kernel tree must already be ASK-applied, i.e.
# `apply-to-tree.sh` has run and left a `.ask-applied` marker.
#
# Required host packages (Debian/Ubuntu, arm64):
#   build-essential libssl-dev bc flex bison libelf-dev
#   fakeroot kmod dpkg-dev rsync cpio
#
# Usage:
#   ./scripts/build-kernel.sh                    # build .deb packages
#   ./scripts/build-kernel.sh --kdir /path/src   # build external tree
#   ./scripts/build-kernel.sh --target Image     # raw Image instead of .debs
#   ./scripts/build-kernel.sh --jobs 8           # override -j (default: nproc)
#   ./scripts/build-kernel.sh --localversion -ask-ls1046a
#
# Outputs:
#   work/build/*.deb         (linux-image-*, linux-headers-*, linux-libc-dev)
#   work/build/build.log     (full compile log)
#
# Exit codes:
#   0  build succeeded; .deb(s) copied to work/build/
#   1  missing toolchain, unapplied tree, or build failure

set -euo pipefail
source "$(dirname "$0")/common.sh"

ARCH="${ARCH:-arm64}"
TARGET="bindeb-pkg"
KDIR_ARG=""
JOBS="$(nproc_any)"
# LOCALVERSION is baked into the kernel release string and the resulting
# linux-image package name. We use "-vyos" so the kernel is a drop-in
# replacement for VyOS's own kernel: the package name, module path
# (/lib/modules/<KVER>-vyos/), `uname -r`, and all out-of-tree module
# Depends: linux-image-<KVER>-vyos already ship throughout the VyOS
# package ecosystem (jool, nat-rtsp, openvpn-dco, vyos-ipt-netflow, …).
# ASK identity is preserved in the Debian package *version* (e.g.
# 6.6.137-ask5), the Git tag (kernel-6.6.137-askN), and release/manifest.json.
LOCALVERSION="${LOCALVERSION:--vyos}"
KDEB_PKGVERSION="${KDEB_PKGVERSION:-}"

while (( $# )); do
    case "$1" in
        --kdir)         KDIR_ARG="${2:?--kdir needs arg}";         shift 2 ;;
        --target)       TARGET="${2:?--target needs arg}";         shift 2 ;;
        --jobs|-j)      JOBS="${2:?--jobs needs arg}";             shift 2 ;;
        --localversion) LOCALVERSION="${2:?--localversion needs arg}"; shift 2 ;;
        --pkgversion)   KDEB_PKGVERSION="${2:?--pkgversion needs arg}"; shift 2 ;;
        -h|--help)      sed -n '1,28p' "$0"; exit 0 ;;
        *) err "unknown arg: $1" ;;
    esac
done

need make gcc
host_arch=$(dpkg --print-architecture 2>/dev/null || uname -m)
[[ "$host_arch" == "arm64" || "$host_arch" == "aarch64" ]] \
    || err "this build must run on an arm64 host; got $host_arch"

# ── Resolve kernel tree ─────────────────────────────────────────────────
if [[ -n "$KDIR_ARG" ]]; then
    KDIR="$KDIR_ARG"
else
    [[ -f "$WORK_DIR/.kernel-version" ]] || err "no kernel fetched; run fetch-kernel.sh or pass --kdir"
    KVER=$(cat "$WORK_DIR/.kernel-version")
    KDIR="$WORK_DIR/linux-$KVER"
fi
[[ -f "$KDIR/Makefile" ]] || err "$KDIR is not a kernel source tree"
[[ -f "$KDIR/.ask-applied" ]] \
    || err "tree is not ASK-applied (no $KDIR/.ask-applied) — run ./scripts/apply-to-tree.sh first"
[[ -f "$KDIR/.config" ]] \
    || err "$KDIR/.config missing — apply-to-tree.sh should have generated it"

KVER=$(awk '/^VERSION/{v=$3} /^PATCHLEVEL/{p=$3} /^SUBLEVEL/{s=$3} END{print v"."p"."s}' "$KDIR/Makefile")
[[ -z "$KDEB_PKGVERSION" ]] && KDEB_PKGVERSION="${KVER}-1"

BUILD_DIR="$WORK_DIR/build"
mkdir -p "$BUILD_DIR"
LOG="$BUILD_DIR/build.log"

info "building kernel"
dim "   tree:          $KDIR"
dim "   version:       linux-$KVER"
dim "   compiler:      $(gcc --version 2>/dev/null | head -1)"
dim "   target:        $TARGET"
dim "   jobs:          -j$JOBS"
dim "   localversion:  $LOCALVERSION"
dim "   pkgversion:    $KDEB_PKGVERSION"
dim "   log:           $LOG"

setup_ccache
if [[ "${CCACHE_ENABLED:-0}" == "1" ]]; then
    dim "   ccache:        $(ccache --version | head -1) (dir=$CCACHE_DIR max=$CCACHE_MAXSIZE)"
fi

# ── Build ───────────────────────────────────────────────────────────────
export ARCH LOCALVERSION KDEB_PKGVERSION
START=$(date +%s)

set +e
(
    cd "$KDIR" \
        && make -j"$JOBS" "${CCACHE_MAKE_ARGS[@]}" "$TARGET" 2>&1
) | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e
ELAPSED=$(( $(date +%s) - START ))

if (( RC != 0 )); then
    err "kernel build failed (exit $RC) — see $LOG (elapsed: ${ELAPSED}s)"
fi

# ── Collect artefacts ───────────────────────────────────────────────────
case "$TARGET" in
    bindeb-pkg|deb-pkg)
        # bindeb-pkg drops .deb files in the PARENT of KDIR.
        PARENT="$(dirname "$KDIR")"
        shopt -s nullglob
        DEBS=( "$PARENT"/linux-*"${KVER}"*.deb )
        shopt -u nullglob
        if (( ${#DEBS[@]} == 0 )); then
            err "build completed but no .deb files found in $PARENT"
        fi
        info "collected ${#DEBS[@]} .deb file(s) → $BUILD_DIR"
        for d in "${DEBS[@]}"; do
            mv -f "$d" "$BUILD_DIR/"
            ok "  $(basename "$d")"
        done
        # Also surface the matching .changes/.buildinfo if present
        shopt -s nullglob
        for f in "$PARENT"/linux-*"${KVER}"*.{changes,buildinfo}; do
            mv -f "$f" "$BUILD_DIR/"
        done
        shopt -u nullglob
        ;;
    Image|Image.gz|vmlinux|dtbs|modules)
        info "build artefacts remain in $KDIR (target=$TARGET)"
        ;;
    *)
        info "build artefacts remain in $KDIR (target=$TARGET)"
        ;;
esac

echo
ok "build complete in ${ELAPSED}s"
ccache_status_line
if [[ "$TARGET" == "bindeb-pkg" || "$TARGET" == "deb-pkg" ]]; then
    echo
    info "installable packages:"
    find "$BUILD_DIR" -maxdepth 1 -name '*.deb' -printf '   %f\n' | sort
fi